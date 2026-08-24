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
from contextlib import contextmanager

_enabled = False


def _clean(value: str) -> str:
    """Strip whitespace and accidental wrapping quotes from a pasted env value.
    A trailing newline or a stray '"' from a copy-paste is invisible in a
    terminal but breaks Basic Auth's exact string match -- and shows up as
    the exact same generic 401 as a genuinely wrong key."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def init_langfuse() -> bool:
    global _enabled
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not (public_key and secret_key):
        return False

    public_key = _clean(public_key)
    secret_key = _clean(secret_key)
    base_url = _clean(os.environ.get("LANGFUSE_BASE_URL", "")) or "https://cloud.langfuse.com"
    base_url = base_url.rstrip("/")

    # Langfuse key prefixes are stable (pk-lf-... / sk-lf-...) so a swap is
    # detectable *before* ever calling the API -- and a swap is the single
    # most common cause of this exact "Invalid credentials" 401, more common
    # than the host being wrong (which we already guarded against in main.py).
    if public_key.startswith("sk-") and secret_key.startswith("pk-"):
        print(
            "WARNING: Langfuse init failed -- LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY look "
            "swapped in your .env (the public one starts with 'sk-', the secret one with 'pk-'). "
            "Swap the two values and restart."
        )
        return False
    if not public_key.startswith("pk-"):
        print(
            f"WARNING: Langfuse init failed -- LANGFUSE_PUBLIC_KEY doesn't look like a Langfuse "
            f"public key (expected it to start with 'pk-', got '{public_key[:6]}...'). Re-copy it "
            "from your Langfuse project's Setup -> API Keys page."
        )
        return False
    if not secret_key.startswith("sk-"):
        print(
            f"WARNING: Langfuse init failed -- LANGFUSE_SECRET_KEY doesn't look like a Langfuse "
            f"secret key (expected it to start with 'sk-', got '{secret_key[:6]}...'). Re-copy it "
            "from your Langfuse project's Setup -> API Keys page."
        )
        return False

    # Write the cleaned values back so get_client()'s own env-var read (and
    # anything else that reads os.environ later) sees the sanitized versions
    # too, not just this function.
    #
    # Set BOTH host env var names: the Langfuse Python SDK renamed its host
    # config from LANGFUSE_HOST (v2/v3) to LANGFUSE_BASE_URL (v4+) -- v3.7.0
    # (confirmed by reading that exact wheel's source) only ever reads
    # LANGFUSE_HOST and silently ignores LANGFUSE_BASE_URL, which is what
    # this project's own .env.example documents. Setting both means the
    # right one is picked up whichever major version ends up installed,
    # without us needing to know or pin it.
    os.environ["LANGFUSE_PUBLIC_KEY"] = public_key
    os.environ["LANGFUSE_SECRET_KEY"] = secret_key
    os.environ["LANGFUSE_BASE_URL"] = base_url
    os.environ["LANGFUSE_HOST"] = base_url

    try:
        from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor
        from langfuse import get_client

        AnthropicInstrumentor().instrument()
        # Deliberately NOT passing public_key/secret_key/base_url as explicit
        # constructor kwargs: the parameter names for this have changed
        # across Langfuse SDK major versions (e.g. base_url vs host), so a
        # hard-coded kwarg here can throw a TypeError on a different
        # installed version. get_client() takes no such kwargs -- it reads
        # os.environ itself, whatever that SDK version's internal param
        # names are, so writing the cleaned values back to os.environ above
        # is what actually makes this version-agnostic.
        client = get_client()
        client.auth_check()
        _enabled = True
        return True
    except Exception as e:  # pragma: no cover - depends on external service
        print(f"WARNING: Langfuse init failed, continuing without it ({e})")
        _enabled = False
        return False


@contextmanager
def trace_turn(session_id: str, voice: bool = False):
    """Wrap one /chat turn so every Anthropic call AnthropicInstrumentor
    auto-traces inside it (including every tool-loop iteration) nests under
    ONE Langfuse trace tagged with our own session_id, instead of each
    landing as its own disconnected trace. Langfuse's "Sessions" view only
    groups traces that share a session_id -- without this, nothing does,
    which is why traces could show up with auth working but Sessions still
    stayed empty. No-op (does nothing, costs nothing) when Langfuse isn't
    configured/authenticated -- callers don't need to check _enabled
    themselves."""
    if not _enabled:
        yield
        return

    # Set up the span (and only the span) inside try/except: a failure here
    # is a tracing problem, so fall back to a no-op turn. Once the span is
    # open, `yield` runs OUTSIDE any except that could catch it -- a
    # generator-based contextmanager can only yield once, so if the caller's
    # own code (the actual chat turn) raised and got caught here, we'd try
    # to yield a second time and Python would turn that into a confusing
    # RuntimeError that masks the real error. finally still guarantees the
    # span closes either way.
    try:
        from langfuse import get_client

        client = get_client()
        span_cm = client.start_as_current_span(name="chat_turn")
        span_cm.__enter__()
    except Exception as e:  # pragma: no cover - never let tracing break a chat turn
        print(f"WARNING: Langfuse trace_turn setup failed, continuing without it ({e})")
        yield
        return

    try:
        client.update_current_trace(
            session_id=session_id,
            tags=["voice"] if voice else ["chat"],
        )
    except Exception as e:  # pragma: no cover - never let tracing break a chat turn
        print(f"WARNING: Langfuse trace_turn metadata failed ({e})")

    try:
        yield
    finally:
        try:
            span_cm.__exit__(None, None, None)
            # This app is a low-traffic local-dev/demo prototype, not a
            # production service handling real throughput -- forcing a
            # flush after every turn means a trace is visible in the
            # Langfuse UI within a second or two of sending a chat message,
            # instead of waiting out the SDK's default batch interval. That
            # matters here specifically because you're often tabbing over to
            # Langfuse to check a trace right after typing a message.
            client.flush()
        except Exception:  # pragma: no cover - never let tracing break a chat turn
            pass
