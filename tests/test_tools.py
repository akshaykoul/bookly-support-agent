import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.db import get_connection, init_db
from app.seed import seed
from app.security import mask_email, mask_id, scrub_text
from app.tools import (
    check_return_eligibility,
    confirm_return,
    get_order_status,
    initiate_return,
    lookup_policy,
    reset_password,
)

VALID_ORDER = "BK-10234"          # delivered, within return window
VALID_EMAIL = "priya.sharma@example.com"
EXPIRED_ORDER = "BK-09876"        # delivered, past return window
EXPIRED_EMAIL = "daniel.osei@example.com"


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    init_db(c)
    seed(c)
    yield c
    c.close()


@pytest.fixture
def session():
    return {"verified_order_id": None, "pending_return": None}


# --- Verification / anti-enumeration ---

def test_wrong_email_and_nonexistent_order_give_identical_message(conn, session):
    wrong_email_result = get_order_status(conn, dict(session), VALID_ORDER, "nobody@example.com")
    nonexistent_order_result = get_order_status(conn, dict(session), "BK-99999", VALID_EMAIL)

    assert wrong_email_result["found"] is False
    assert nonexistent_order_result["found"] is False
    assert wrong_email_result["message"] == nonexistent_order_result["message"]


def test_successful_lookup_marks_session_verified(conn, session):
    result = get_order_status(conn, session, VALID_ORDER, VALID_EMAIL)
    assert result["found"] is True
    assert session["verified_order_id"] == VALID_ORDER
    assert len(result["items"]) == 2


def test_email_match_is_case_insensitive(conn, session):
    result = get_order_status(conn, session, VALID_ORDER, VALID_EMAIL.upper())
    assert result["found"] is True


# --- Return eligibility gating ---

def test_eligibility_check_blocked_without_verification(conn, session):
    result = check_return_eligibility(conn, session, VALID_ORDER, 1)
    assert "error" in result
    assert "verified" in result["error"].lower()


def test_eligible_item_within_window(conn, session):
    get_order_status(conn, session, VALID_ORDER, VALID_EMAIL)
    result = check_return_eligibility(conn, session, VALID_ORDER, 1)
    assert result["eligible"] is True


def test_ineligible_item_past_window(conn, session):
    get_order_status(conn, session, EXPIRED_ORDER, EXPIRED_EMAIL)
    # item id 4 corresponds to the Dune item on BK-09876 per seed order
    item_id = conn.execute(
        "SELECT id FROM order_items WHERE order_id = ?", (EXPIRED_ORDER,)
    ).fetchone()["id"]
    result = check_return_eligibility(conn, session, EXPIRED_ORDER, item_id)
    assert result["eligible"] is False


# --- initiate_return / confirm_return two-step guardrail ---

def test_initiate_return_requires_verification(conn, session):
    result = initiate_return(conn, session, VALID_ORDER, 1, "damaged")
    assert "error" in result


def test_confirm_without_initiate_fails(conn, session):
    get_order_status(conn, session, VALID_ORDER, VALID_EMAIL)
    result = confirm_return(conn, session, VALID_ORDER, 1)
    assert "error" in result


def test_initiate_then_confirm_happy_path(conn, session):
    get_order_status(conn, session, VALID_ORDER, VALID_EMAIL)
    item_id = conn.execute(
        "SELECT id FROM order_items WHERE order_id = ? LIMIT 1", (VALID_ORDER,)
    ).fetchone()["id"]

    dry_run = initiate_return(conn, session, VALID_ORDER, item_id, "changed my mind")
    assert dry_run["eligible"] is True
    assert dry_run["requires_confirmation"] is True
    assert session["pending_return"]["item_id"] == item_id

    confirmed = confirm_return(conn, session, VALID_ORDER, item_id)
    assert confirmed["success"] is True
    assert session["pending_return"] is None

    row = conn.execute(
        "SELECT * FROM returns WHERE order_item_id = ?", (item_id,)
    ).fetchone()
    assert row is not None
    assert row["status"] == "completed"


def test_initiate_return_rejects_expired_item(conn, session):
    get_order_status(conn, session, EXPIRED_ORDER, EXPIRED_EMAIL)
    item_id = conn.execute(
        "SELECT id FROM order_items WHERE order_id = ?", (EXPIRED_ORDER,)
    ).fetchone()["id"]
    result = initiate_return(conn, session, EXPIRED_ORDER, item_id, "too late")
    assert result["eligible"] is False
    assert session["pending_return"] is None


# --- General Q&A tools ---

def test_lookup_known_policy(conn):
    result = lookup_policy(conn, "shipping")
    assert result["found"] is True
    assert "shipping" in result["content"].lower() or "days" in result["content"].lower()


def test_lookup_unknown_policy(conn):
    result = lookup_policy(conn, "gift_cards")
    assert result["found"] is False
    assert "available_topics" in result


def test_reset_password_same_message_regardless_of_match(conn):
    known = reset_password(conn, VALID_EMAIL)
    unknown = reset_password(conn, "totally-not-a-customer@example.com")
    assert known["message"] == unknown["message"]


# --- Security / masking ---

def test_mask_email():
    assert mask_email("priya.sharma@example.com") == "p***@e***.com"
    assert mask_email(None) is None


def test_mask_id():
    assert mask_id("BK-10234") == "****0234"
    assert mask_id("ab") == "**"


def test_scrub_text_masks_email_in_free_text():
    scrubbed = scrub_text("You can reach me at priya.sharma@example.com about this")
    assert "priya.sharma@example.com" not in scrubbed
    assert "p***@e***.com" in scrubbed


def test_scrub_text_masks_order_id_in_free_text():
    scrubbed = scrub_text("My order BK-10234 hasn't arrived")
    assert "BK-10234" not in scrubbed
    assert "0234" in scrubbed  # last 4 digits still visible, rest masked


def test_scrub_text_leaves_non_pii_text_alone():
    scrubbed = scrub_text("Where is my order? It hasn't shipped yet.")
    assert scrubbed == "Where is my order? It hasn't shipped yet."
