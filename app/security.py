"""
Masking utilities used only at the *logging* boundary.

The rest of the app (tools.py, orchestrator.py) works with real customer
data because it needs to in order to do its job — look up a real order,
match a real email. What must NOT happen is that real PII ends up sitting
in cleartext in agent_traces, which is the artifact a security reviewer
would actually look at. So every value written to agent_traces goes through
these helpers first; nothing else in the codebase should need them.
"""

import re
from typing import Any

_EMAIL_RE = re.compile(r"^([^@]+)@(.+)$")


def mask_email(email: str | None) -> str | None:
    if not email:
        return email
    m = _EMAIL_RE.match(email)
    if not m:
        return "***"
    local, domain = m.group(1), m.group(2)
    domain_parts = domain.split(".")
    masked_domain = domain_parts[0][:1] + "***"
    if len(domain_parts) > 1:
        masked_domain += "." + ".".join(domain_parts[1:])
    return f"{local[:1]}***@{masked_domain}"


def mask_id(value: str | None, keep: int = 4) -> str | None:
    """Show only the last `keep` characters of an identifier (order id,
    tracking number, etc)."""
    if not value:
        return value
    value = str(value)
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]


# Field names that should be masked wherever they appear in a dict being
# logged, keyed by which masking function applies.
_EMAIL_FIELDS = {"email"}
_ID_FIELDS = {"order_id", "tracking_number", "item_id"}


def redact(value: Any) -> Any:
    """Recursively redact a dict/list/scalar for safe logging."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in _EMAIL_FIELDS and isinstance(v, str):
                out[k] = mask_email(v)
            elif k in _ID_FIELDS and isinstance(v, str):
                out[k] = mask_id(v)
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


# --- Free-text scrubbing ---
#
# redact() above only catches PII that arrives in a *named* field (email=,
# order_id=, ...), which is everything tools.py passes it. It does NOT catch
# PII embedded in unstructured text -- and the raw user/assistant chat
# messages logged to agent_traces ARE unstructured text. If a customer types
# "my email is priya@example.com", there's no field name to key off of.
#
# scrub_text() is a pattern-based pass over free text for that case: find
# anything shaped like an email, a phone number, or a Bookly order ID, and
# mask it the same way redact() would. It's deliberately simple regex, not a
# full PII/NER model -- see README "What I'd do differently" re: Presidio as
# the production-grade upgrade path (catches names, addresses, etc. that
# pattern matching structurally can't).

_FREE_TEXT_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_FREE_TEXT_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
)
_FREE_TEXT_ORDER_ID_RE = re.compile(r"\b[A-Z]{2,4}-\d{4,}\b")


def scrub_text(text: str | None) -> str | None:
    """Mask PII-shaped substrings inside free text before it's logged.
    Does NOT touch the actual value passed to tools/the model -- only what
    gets persisted to agent_traces."""
    if not text:
        return text
    text = _FREE_TEXT_EMAIL_RE.sub(lambda m: mask_email(m.group(0)), text)
    text = _FREE_TEXT_ORDER_ID_RE.sub(lambda m: mask_id(m.group(0)), text)
    text = _FREE_TEXT_PHONE_RE.sub(lambda m: mask_id(m.group(0), keep=2), text)
    return text
