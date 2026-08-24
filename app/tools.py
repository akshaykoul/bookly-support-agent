"""
The six tools the agent can call, plus their Claude tool_use schemas.

Design principles enforced here (not just in the system prompt):

1. Verification is structural. get_order_status is the only way to verify an
   order (order_id + email must both match), and it's the only place that
   sets session["verified_order_id"]. check_return_eligibility,
   initiate_return, and confirm_return all refuse to act on an order_id that
   doesn't match what's already verified in this session -- so the model
   can't route around verification just by not calling the right tool.

2. Anti-enumeration. A wrong email and a nonexistent order return the exact
   same message from get_order_status. reset_password always returns the
   same "if an account exists..." message regardless of whether it does.
   Neither endpoint should let a caller learn what does or doesn't exist.

3. Returns require a separate confirm step. initiate_return is a dry run --
   it reports eligibility and a plain-language summary of what would happen,
   and stores it as a *pending* return on the session. Nothing is written to
   the database until confirm_return is called, which only succeeds if it
   matches that pending return. This makes "no irreversible action without
   explicit confirmation" a property of the tool design, not a prompt ask.

4. Data minimization. Tool results only include the fields a flow actually
   needs (e.g. get_order_status never returns payment info).
"""

from datetime import datetime, date
from typing import Any, Optional

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_order_status",
        "description": (
            "Look up an order's status and contents, AND verify the caller's identity "
            "in one step. Requires both the order ID and the email on the order -- both "
            "must match or the lookup fails. This is the only way to verify an order in "
            "this conversation; call it before check_return_eligibility, initiate_return, "
            "or confirm_return."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "e.g. BK-10234"},
                "email": {"type": "string", "description": "Email address on the order"},
            },
            "required": ["order_id", "email"],
        },
    },
    {
        "name": "check_return_eligibility",
        "description": (
            "Check whether a specific item on a specific order is still within its "
            "return window. The order must already be verified in this conversation via "
            "get_order_status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "item_id": {"type": "integer", "description": "item_id from get_order_status"},
            },
            "required": ["order_id", "item_id"],
        },
    },
    {
        "name": "initiate_return",
        "description": (
            "Start a return for an item. This is a DRY RUN: it checks eligibility and "
            "returns a summary of what will happen (refund amount, timeline) but does NOT "
            "actually process anything. You must read the summary back to the customer and "
            "get their explicit yes before calling confirm_return. The order must already "
            "be verified via get_order_status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "item_id": {"type": "integer"},
                "reason": {"type": "string", "description": "Customer's stated reason for the return"},
            },
            "required": ["order_id", "item_id", "reason"],
        },
    },
    {
        "name": "confirm_return",
        "description": (
            "Actually process a return that was already proposed via initiate_return. "
            "Only call this AFTER the customer has explicitly confirmed (said yes/go "
            "ahead/etc) to the summary initiate_return gave you. This writes to the "
            "database and cannot be undone by calling it again."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "item_id": {"type": "integer"},
            },
            "required": ["order_id", "item_id"],
        },
    },
    {
        "name": "lookup_policy",
        "description": (
            "Look up Bookly's official policy text on a topic. ALWAYS use this for "
            "questions about shipping, returns, or password reset instead of answering "
            "from memory -- the answer must come from this tool so it's guaranteed to "
            "match Bookly's actual policy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": ["shipping", "returns", "password_reset", "general"],
                }
            },
            "required": ["topic"],
        },
    },
    {
        "name": "reset_password",
        "description": (
            "Send a password reset link to an email address. Always reports success in "
            "the same way regardless of whether the email is on file -- this is "
            "intentional (don't change your phrasing based on the internal result)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"email": {"type": "string"}},
            "required": ["email"],
        },
    },
]


def _today() -> date:
    return datetime.now().date()


def get_order_status(conn, session: dict, order_id: str, email: str) -> dict:
    row = conn.execute(
        """
        SELECT o.id, o.status, o.order_date, o.total, o.tracking_number, c.email
        FROM orders o JOIN customers c ON o.customer_id = c.id
        WHERE o.id = ?
        """,
        (order_id,),
    ).fetchone()

    generic_failure = {
        "found": False,
        "message": (
            "I couldn't verify an order with that order number and email. Please "
            "double-check both and try again."
        ),
    }

    # Same response whether the order doesn't exist or the email doesn't match --
    # never reveal which one it was.
    if row is None or row["email"].strip().lower() != email.strip().lower():
        return generic_failure

    items = conn.execute(
        "SELECT id, book_title, price, quantity, return_eligible_until FROM order_items WHERE order_id = ?",
        (order_id,),
    ).fetchall()

    session["verified_order_id"] = order_id

    return {
        "found": True,
        "order_id": row["id"],
        "status": row["status"],
        "order_date": row["order_date"],
        "total": row["total"],
        "tracking_number": row["tracking_number"],
        "items": [
            {
                "item_id": item["id"],
                "book_title": item["book_title"],
                "price": item["price"],
                "quantity": item["quantity"],
                "return_eligible": item["return_eligible_until"] >= _today().isoformat(),
                "return_eligible_until": item["return_eligible_until"],
            }
            for item in items
        ],
    }


def _require_verified(session: dict, order_id: str) -> Optional[dict]:
    if session.get("verified_order_id") != order_id:
        return {
            "error": (
                "This order hasn't been verified in this conversation yet. Call "
                "get_order_status with the order ID and email first."
            )
        }
    return None


def _get_item(conn, order_id: str, item_id: int):
    return conn.execute(
        "SELECT * FROM order_items WHERE id = ? AND order_id = ?",
        (item_id, order_id),
    ).fetchone()


def check_return_eligibility(conn, session: dict, order_id: str, item_id: int) -> dict:
    guard = _require_verified(session, order_id)
    if guard:
        return guard

    item = _get_item(conn, order_id, item_id)
    if item is None:
        return {"error": "No such item on that order."}

    eligible = item["return_eligible_until"] >= _today().isoformat()
    return {
        "eligible": eligible,
        "book_title": item["book_title"],
        "return_eligible_until": item["return_eligible_until"],
        "reason_if_not_eligible": None if eligible else "Return window has closed.",
    }


def initiate_return(conn, session: dict, order_id: str, item_id: int, reason: str) -> dict:
    guard = _require_verified(session, order_id)
    if guard:
        return guard

    item = _get_item(conn, order_id, item_id)
    if item is None:
        return {"error": "No such item on that order."}

    eligible = item["return_eligible_until"] >= _today().isoformat()
    if not eligible:
        return {
            "eligible": False,
            "message": (
                f"'{item['book_title']}' is no longer eligible for return -- its return "
                f"window closed on {item['return_eligible_until']}."
            ),
        }

    session["pending_return"] = {"order_id": order_id, "item_id": item_id, "reason": reason}

    return {
        "eligible": True,
        "requires_confirmation": True,
        "summary": (
            f"Return '{item['book_title']}' from order {order_id} (reason: {reason}). "
            f"A refund of ${item['price']:.2f} will go to the original payment method "
            f"within 5-7 business days once we receive the item. Call confirm_return "
            f"only after the customer explicitly agrees to this."
        ),
    }


def confirm_return(conn, session: dict, order_id: str, item_id: int) -> dict:
    pending = session.get("pending_return")
    if not pending or pending["order_id"] != order_id or pending["item_id"] != item_id:
        return {
            "error": (
                "No matching pending return to confirm. Call initiate_return first and "
                "get the customer's explicit confirmation."
            )
        }

    item = _get_item(conn, order_id, item_id)
    if item is None:
        return {"error": "No such item on that order."}

    conn.execute(
        "INSERT INTO returns (order_item_id, reason, status, created_at) VALUES (?, ?, 'completed', ?)",
        (item_id, pending["reason"], datetime.now().isoformat()),
    )
    conn.commit()
    session["pending_return"] = None

    return {
        "success": True,
        "message": (
            f"Return confirmed for '{item['book_title']}'. You'll get a confirmation "
            f"email with a prepaid return shipping label."
        ),
    }


def lookup_policy(conn, topic: str) -> dict:
    row = conn.execute(
        "SELECT topic, content FROM policies WHERE topic = ?", (topic.strip().lower(),)
    ).fetchone()
    if row is None:
        available = [r["topic"] for r in conn.execute("SELECT topic FROM policies")]
        return {
            "found": False,
            "message": "No policy on file for that topic.",
            "available_topics": available,
        }
    return {"found": True, "topic": row["topic"], "content": row["content"]}


def reset_password(conn, email: str) -> dict:
    # Intentionally does not branch its return message on whether the email
    # matches a customer -- see module docstring point 2.
    conn.execute(
        "SELECT id FROM customers WHERE lower(email) = lower(?)", (email,)
    ).fetchone()
    return {
        "message": (
            "If an account exists for that email, we've sent a password reset link. "
            "It expires in 24 hours."
        )
    }


DISPATCH = {
    "get_order_status": get_order_status,
    "check_return_eligibility": check_return_eligibility,
    "initiate_return": initiate_return,
    "confirm_return": confirm_return,
    "lookup_policy": lookup_policy,
    "reset_password": reset_password,
}


def dispatch_tool(name: str, tool_input: dict, conn, session: dict) -> dict:
    fn = DISPATCH.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        if name == "lookup_policy":
            return fn(conn, tool_input.get("topic", ""))
        if name == "reset_password":
            return fn(conn, tool_input.get("email", ""))
        # order/session-aware tools
        kwargs = dict(tool_input)
        if "item_id" in kwargs and kwargs["item_id"] is not None:
            kwargs["item_id"] = int(kwargs["item_id"])
        return fn(conn, session, **kwargs)
    except TypeError as e:
        return {"error": f"Invalid arguments for {name}: {e}"}
