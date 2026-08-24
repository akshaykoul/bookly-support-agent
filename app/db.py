"""
SQLite access layer for the Bookly support agent.

Design note: this is deliberately SQLite, not a raw JSON blob and not a
Postgres server. It's a documented scope decision — see README "What I'd do
differently" — that gets us real queryable schema + a real (if mocked)
observability trace table, without spending the exercise's time budget on
infrastructure that isn't what's being evaluated.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = os.environ.get("BOOKLY_DB_PATH", "bookly.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,              -- e.g. "BK-10234"
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL,             -- placed | shipped | delivered | cancelled
    order_date TEXT NOT NULL,         -- ISO date
    total REAL NOT NULL,
    tracking_number TEXT
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(id),
    book_title TEXT NOT NULL,
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    return_eligible_until TEXT NOT NULL  -- ISO date; item is returnable up to this date
);

CREATE TABLE IF NOT EXISTS returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_item_id INTEGER NOT NULL REFERENCES order_items(id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL,             -- pending | completed
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    topic TEXT PRIMARY KEY,           -- shipping | returns | password_reset | general
    content TEXT NOT NULL
);

-- Observability: one row per model turn / tool call. Values that could carry
-- PII (tool_args, tool_result) are masked before being written here — see
-- app/security.py. This table is the homemade stand-in for a real tracing
-- vendor (Langfuse/Datadog/OTel) in a production deployment.
CREATE TABLE IF NOT EXISTS agent_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    role TEXT NOT NULL,               -- user | assistant | tool_call | tool_result | guardrail
    content TEXT,
    tool_name TEXT,
    tool_args TEXT,                   -- masked, JSON-encoded
    tool_result TEXT,                 -- masked, JSON-encoded
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    guardrail_flag TEXT,
    created_at TEXT NOT NULL
);
"""


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def session_scope(db_path: str | None = None):
    """Convenience context manager: yields a connection, commits on success,
    rolls back on exception, always closes."""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def db_is_empty(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) AS n FROM customers").fetchone()
    return row["n"] == 0
