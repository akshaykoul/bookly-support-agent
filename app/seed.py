"""
Mock data for Bookly. Dates are anchored near "today" (see SEED_TODAY below)
so the eligible-vs-expired return window demo works regardless of when this
is actually run. One order (BK-09876) is deliberately past its return
window, to demonstrate the agent correctly refusing an ineligible return.
"""

import sqlite3
from datetime import datetime, timedelta

from app.db import db_is_empty

# Anchor date for the mock data. Real "today" is used for anything logged
# (created_at timestamps), but order/eligibility dates are computed relative
# to this fixed anchor so the demo scenarios (eligible vs. expired return)
# stay consistent across runs.
SEED_TODAY = datetime(2026, 8, 24)


def _d(days_offset: int) -> str:
    return (SEED_TODAY + timedelta(days=days_offset)).date().isoformat()


POLICIES = {
    "shipping": (
        "Standard shipping is free on orders over $25 and arrives in 5-7 business "
        "days. Expedited shipping (2-3 business days) is $6.99. Bookly ships within "
        "the US and Canada only. Once an order leaves our warehouse you'll receive a "
        "tracking number by email."
    ),
    "returns": (
        "Books can be returned within 30 days of delivery for a full refund, "
        "provided they're in resellable condition (no writing, water damage, or "
        "broken spines). Refunds are issued to the original payment method and "
        "typically appear within 5-7 business days after we receive the item. "
        "Digital/ebook purchases are final sale."
    ),
    "password_reset": (
        "To reset your password, use the 'Forgot password' link on the Bookly "
        "sign-in page, or ask this assistant to send a reset link to the email on "
        "your account. Reset links expire after 24 hours."
    ),
    "general": (
        "Bookly customer support is available via this chat 24/7. For anything "
        "this assistant can't resolve, you can reach a human agent at "
        "support@bookly.example (mocked contact — no real mailbox)."
    ),
}

CUSTOMERS = [
    (1, "Priya Sharma", "priya.sharma@example.com"),
    (2, "Daniel Osei", "daniel.osei@example.com"),
    (3, "Mei Lin", "mei.lin@example.com"),
]

ORDERS = [
    # id, customer_id, status, order_date, total, tracking_number
    ("BK-10234", 1, "delivered", _d(-14), 42.50, "1Z999AA10123456784"),
    ("BK-10198", 1, "shipped", _d(-6), 15.00, "1Z999AA10123456785"),
    ("BK-09876", 2, "delivered", _d(-84), 30.00, "1Z999AA10123456786"),  # past return window
    ("BK-10301", 3, "placed", _d(-2), 55.00, None),
]

# order_id, book_title, price, quantity, return_eligible_until
ORDER_ITEMS = [
    ("BK-10234", "The Midnight Library", 18.00, 1, _d(16)),   # delivered 14d ago, 30d window -> still eligible
    ("BK-10234", "Atomic Habits", 24.50, 1, _d(16)),
    ("BK-10198", "Project Hail Mary", 15.00, 1, _d(24)),      # still in transit, window hasn't really started but kept simple
    ("BK-09876", "Dune", 30.00, 1, _d(-54)),                  # delivered 84d ago -> window expired 54d ago
    ("BK-10301", "Sapiens", 27.50, 2, _d(28)),
]


def seed(conn: sqlite3.Connection) -> None:
    if not db_is_empty(conn):
        return

    conn.executemany(
        "INSERT INTO customers (id, name, email) VALUES (?, ?, ?)", CUSTOMERS
    )
    conn.executemany(
        "INSERT INTO orders (id, customer_id, status, order_date, total, tracking_number) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ORDERS,
    )
    conn.executemany(
        "INSERT INTO order_items (order_id, book_title, price, quantity, return_eligible_until) "
        "VALUES (?, ?, ?, ?, ?)",
        ORDER_ITEMS,
    )
    conn.executemany(
        "INSERT INTO policies (topic, content) VALUES (?, ?)",
        list(POLICIES.items()),
    )
    conn.commit()
