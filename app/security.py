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
