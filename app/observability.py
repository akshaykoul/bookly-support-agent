"""
Optional Langfuse tracing.

Auto-instruments the Anthropic SDK calls already made in orchestrator.py via
OpenTelemetry -- no changes to the orchestrator loop itself, no manual
span/trace creation. This is additive to (not a replacement for) the masked
`agent_traces` SQLite table: redaction has to happen inside this app before
anything leaves it, so the homemade trace stays the source of truth for what
a security reviewer would check, while Langfuse gives a real
dashboard/vendor-shaped view for everything else (latency, cost, traces
across sessions).

Entirely opt-in: only activates when LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY
are set. Absent them (local dev default), this is a no-op -- consistent with
how ANTHROPIC_API_KEY / BOOKLY_ACCESS_CODE / ELEVENLABS_API_KEY are all
optional-until-configured elsewhere in this app.
"""

import os


def init_langfuse() -> bool:
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return False
    try:
        from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor
        from langfuse import get_client

        AnthropicInstrumentor().instrument()
        client = get_client()
        client.auth_check()
        return True
    except Exception as e:  # pragma: no cover - depends on external service
        print(f"WARNING: Langfuse init failed, continuing without it ({e})")
        return False
