# Bookly Support Agent

A prototype customer support agent for Bookly (fictional online bookstore), built for the
Decagon Solutions Engineering take-home. Chat + voice UI, real Claude tool-calling against a
mocked SQLite backend, no agentic framework. Optional real-vendor integrations (Langfuse
observability, ElevenLabs voice output) are wired in but stay fully inert until their env vars
are set — see "Optional integrations" below.

## Quick start

Requires Python 3.9+ (the codebase deliberately uses `typing.Optional[...]` rather than the
newer `X | None` shorthand, which only works on 3.10+, so it runs on an older system Python
without needing a specific interpreter version).

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY

uvicorn app.main:app --reload
```

Open http://localhost:8000. The database (`bookly.db`) is created and seeded with mock
customers/orders automatically on first run — delete the file to reseed from scratch.

Run the tests: `pytest`

Run the behavioral evals (hits the real Claude API, needs `ANTHROPIC_API_KEY` — see "Evals"
below): `python evals/run_evals.py`

## Optional integrations

All three are inert (silently skipped, app runs fine without them) until their env vars are set
in `.env`:

- **Langfuse** (`LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY`) — real observability dashboard.
  Auto-instruments the existing Anthropic SDK calls via OpenTelemetry (`app/observability.py`);
  no changes to the orchestrator itself. Free Cloud tier at [langfuse.com](https://langfuse.com)
  or self-host. This is additive to, not a replacement for, the local masked `agent_traces`
  table — redaction has to happen inside this app before anything leaves it, so that table stays
  the source of truth for what a security reviewer would check.
- **ElevenLabs** (`ELEVENLABS_API_KEY`, optionally `ELEVENLABS_VOICE_ID` / `ELEVENLABS_MODEL_ID`)
  — real voice synthesis for spoken replies (`app/voice.py`, `POST /speak`), called server-side
  so the key never reaches the browser. Defaults to the `eleven_turbo_v2_5` model with voice
  settings tuned for a conversational tone (lower stability for natural pitch/pace variation, a
  touch of style, speaker boost for clarity) rather than the flatter default preset. Emoji and
  other pictographic characters are stripped from the text before it's sent to either ElevenLabs
  or the browser fallback — otherwise both will narrate an emoji's name out loud. Falls back to
  the browser's free `speechSynthesis` on any failure (not configured, quota exceeded, network
  error) — voice trouble should never break the chat. Speech *input* stays on the browser's free
  `SpeechRecognition`; only output quality was worth spending API budget on.

## Deploying (for sharing a live link)

A `render.yaml` blueprint is included. On [Render](https://render.com): **New +** &rarr;
**Blueprint** &rarr; point it at this GitHub repo. Render reads `render.yaml` and asks you to fill
in two secrets (never committed): `ANTHROPIC_API_KEY` and `BOOKLY_ACCESS_CODE`. Once deployed you
get a public HTTPS URL.

`BOOKLY_ACCESS_CODE` gates `/chat` and `/trace` behind a shared passcode (see "Key decisions"
below) so a public link doesn't let random bots burn your API quota. Leave it unset for local
dev — the gate is automatically disabled when the env var isn't present.

Free tier note: the service spins down after ~15 min idle and cold-starts on the next request —
fine for a demo link, not for real production traffic.

### Try it with the seeded mock data

- Verified order, eligible return: order `BK-10234`, email `priya.sharma@example.com`
- Verified order, return window expired: order `BK-09876`, email `daniel.osei@example.com`
- Order not yet delivered: order `BK-10198`, email `priya.sharma@example.com`

Try the same order in both modes to see the difference deliberately built into Voice mode: in
Chat mode, ask about order `BK-10234` and you'll get the full written detail (status, items,
return-eligibility date). Switch to Voice mode (top-right pill toggle) and ask the same
question by speaking — the reply comes back as one or two short spoken sentences ("delivered
last week, and yes, it's still eligible for return"), not the line-item detail read aloud. Same
data, same tools, deliberately different reply shape — see "Key decisions" below for why.

## Architecture

```
Browser — Chat mode (text) or Voice mode (mic), toggled top-right
   │  POST /chat {message, session_id, voice}
   ▼
FastAPI (app/main.py)
   │
   ▼
Orchestrator (app/orchestrator.py) ── hand-rolled tool-use loop, no framework
   │  voice=True:  SYSTEM_PROMPT + VOICE_ADDENDUM, max_tokens capped ~220
   │  voice=False: SYSTEM_PROMPT only,              max_tokens 1024
   │  call Claude with that system prompt + tool schemas + history
   │  while stop_reason == "tool_use": run tool, feed tool_result back
   │  (capped at 5 iterations)
   ▼
Tools (app/tools.py) ── get_order_status, check_return_eligibility,
   │                     initiate_return, confirm_return, lookup_policy,
   │                     reset_password
   ▼
SQLite (app/db.py + app/seed.py) ── customers, orders, order_items,
                                     returns, policies, agent_traces
```

The `voice` flag on `/chat` is the one place the UI mode deliberately reaches into agent
behavior, not just I/O. Everything below it stays identical either way — the tools, the
database, the verification and confirm-return guardrails don't know or care whether a message
came from typing or speech. What changes in Voice mode is narrower and intentional: a different
system-prompt addendum and a lower `max_tokens` cap, so replies come back conversational and
phone-call-shaped instead of the fuller written detail Chat mode gives for the exact same
question (see "Key decisions").

Speech *input* is a thin layer on top of the same text pipeline in both modes: the browser's
built-in `SpeechRecognition` transcribes speech into the same string a typed message would be.
Speech *output* calls ElevenLabs server-side for real synthesized speech when configured
(`ELEVENLABS_API_KEY` set), tuned for a conversational tone (turbo model, lower stability for
natural pitch variation) with emoji/pictographic characters stripped before anything is sent to
either TTS engine — otherwise both ElevenLabs and the browser's `speechSynthesis` will narrate
an emoji's name out loud instead of skipping it. Falls back to the browser's free
`speechSynthesis` on any ElevenLabs failure — see "Optional integrations" and "Key decisions."

The UI itself (`static/`) is a single-page app with two screens: an access-code gate (see "Key
decisions" #4) that hands off to the chat panel with a brief fade rather than an instant swap,
and the chat panel, styled in a dark, gradient-accented palette inspired by Decagon's own
product design — colors and general visual language only, see "UI branding note."

## The three required demo behaviors

- **Multi-turn**: ask "where's my order" with no details → the agent asks for the order number
  and email before it can look anything up (this is also the verification step — see below).
- **Real/mocked tool use**: `get_order_status`, `check_return_eligibility`, `initiate_return`,
  `confirm_return`, `lookup_policy`, and `reset_password` are all real function calls against the
  SQLite database, not the model narrating an action.
- **Clarifying question**: ask to return "something" with no order info, or ask about an
  ambiguous policy topic ("what about international shipping?") — the agent asks rather than
  guessing. This also happens naturally any time verification info is incomplete.

## Evals

`evals/run_evals.py` is a small, Python-native scripted eval harness — not pytest (it calls the
real API and costs real tokens, so it's deliberately not auto-collected), and not a separate
framework like promptfoo (a Node CLI, which would add a second toolchain for not much benefit at
this scale). Six scenarios run real conversational turns through the actual orchestrator and
assert on *structured* outcomes — which tools got called, guardrail flags, session state — read
from the same `agent_traces` table `/trace` uses, rather than fragile string-matching on the
model's natural-language reply. Covers: verification-gated multi-turn, clarifying questions on
vague requests, the expired-return-window block, the confirm-before-action guardrail,
prompt-injection flagging, and password-reset anti-enumeration. These are behavioral evals
against a live LLM, not deterministic unit tests — an occasional failure is itself a signal
(regression, or a prompt worth tightening), not necessarily a bug the way a `tests/` failure
would be.

## Key decisions (see the pitch deck for the full defense)

1. **Verification is one shared, structural gate, not a prompt suggestion.**
   `get_order_status(order_id, email)` is the only way to verify an order — both fields must
   match, and a wrong email vs. a nonexistent order return the *identical* generic failure
   message (no account/order enumeration). Once verified, the order_id is stored in session
   state, and `check_return_eligibility` / `initiate_return` / `confirm_return` all refuse to
   act on an order_id that wasn't verified this way. The model can't route around it by simply
   not asking — the tools themselves enforce it.

2. **Returns are a two-step commit.** `initiate_return` is a dry run: it checks eligibility and
   returns a plain-language summary (refund amount, timeline) but writes nothing. Only
   `confirm_return` — callable only after `initiate_return` and only for a matching
   order/item — actually writes to the database. "No irreversible action without explicit
   confirmation" is enforced by the tool design, not just a prompt instruction.

3. **Observability + masking, mocked but real-shaped.** Every turn and tool call is logged to a
   SQLite `agent_traces` table (latency, token counts, tool args/results, guardrail flags), with
   a redaction step applied before anything is persisted — the tools use real customer data to
   do their job, but nothing sensitive is written to logs in cleartext. Two layers: `redact()`
   masks known PII fields (email, order_id, ...) in structured tool args/results, and
   `scrub_text()` pattern-matches PII shapes (emails, phone numbers, order IDs) inside raw
   free-text chat messages before they're logged — the latter exists because field-name-based
   masking alone misses PII a customer just types in a sentence. This is deliberately simple
   regex, not Microsoft Presidio or another NER-based PII tool — see "What I'd do differently."
   `GET /trace/{session_id}` serves the raw (masked) timeline; when `LANGFUSE_PUBLIC_KEY` /
   `LANGFUSE_SECRET_KEY` are set, the same Anthropic SDK calls are also traced to a real Langfuse
   dashboard (see "Optional integrations") — chosen over building this out further because it's
   a genuinely small addition (OpenTelemetry auto-instrumentation, no orchestrator changes) that
   trades a homemade view for a real one, rather than a "would build in production" line item.

4. **A shared passcode gates the public deploy, not real auth.** Once this has a live URL to
   share with reviewers, anyone with the link could otherwise trigger real, billed Claude API
   calls. `BOOKLY_ACCESS_CODE` (unset by default, only set on the public deploy) gates `/chat`
   and `/trace` behind a shared code checked server-side — cheap to build, sufficient to keep
   the demo link from being scraped, and explicitly not pretending to be customer
   authentication.

5. **Real voice quality (ElevenLabs) traded in deliberately, not by default.** The original
   scope decision was voice as a free browser-only layer (zero cost, zero extra API). Swapping in
   ElevenLabs for spoken *output* is a conscious step away from that — it's a better demo but a
   real dependency: a paid-tier-shaped API, a key to manage, another point of failure. Kept
   input on the free browser API (no reason to spend budget on something already free), and
   built the output swap with an automatic fallback to browser TTS on any failure, so a demo
   never breaks because of a voice quota. This is the kind of trade a solutions engineer makes
   with a customer all the time — better experience vs. more moving parts — worth naming as a
   deliberate choice rather than papering over.

6. **Voice mode gets a structurally shorter reply, not just a prompt asking for brevity.** A
   support answer that reads well in a chat bubble (order status, every line item, a policy
   paragraph) is a wall of talking when a TTS engine reads it aloud verbatim — the same content
   needs a different *shape* depending on the channel, not just a "be concise" nudge. Voice mode
   sends a `voice` flag on `/chat`; the orchestrator swaps in a short addendum to the system
   prompt (one or two sentences, no lists, ask one thing at a time) *and* caps `max_tokens` to
   ~220 versus 1024 for text — so even if the model tried to ramble, the response is capped
   before it can turn into a paragraph read aloud. Same tools, same database, same guardrails;
   only the reply-generation step branches on channel, and it's enforced the same way the rest of
   this project treats guardrails — as something the code guarantees, not something the prompt
   merely requests.

7. **Considered and declined a guardrails framework** (e.g. Guardrails AI, NeMo Guardrails).
   This agent's actual failure modes are about *actions* — refunding without confirmation,
   disclosing without verification — which are already solved structurally in the tool layer
   (decisions 1–2), a stronger guarantee than a prompt/content-filter framework provides for
   this shape of risk. Adopting one would also mean wrapping the LLM call in someone else's
   abstraction, which is exactly what the brief says to avoid. Right call for this agent's risk
   profile, not a blanket "guardrail frameworks aren't worth it."

## Assumptions / documented scope decisions

- **"Production-ready" here means code patterns, not infrastructure**: modular code, a real
  (if embedded) database with a schema, structural guardrails, logging, a Dockerfile, tests —
  but no Postgres server, no real auth, no deployment, no external observability vendor. Each of
  those is called out explicitly as a "what I'd do differently" item rather than built, to keep
  the time budget on agent design rather than infra plumbing.
- Verification uses order number + email (matches typical e-commerce "track my order" UX).
  Easy to swap for a different factor pair.
- Session state (conversation history, verification, pending returns) is in-memory and per
  process — restarting the server clears all sessions. A real deployment needs a persistent,
  multi-instance-safe session store (Redis, or a DB-backed one).
- No RAG/vector DB: the policy set is small enough that a flat `policies` table + exact-topic
  lookup is sufficient and keeps every FAQ answer tool-grounded. At real scale this would become
  proper retrieval.
- The prompt-injection/scope guardrail is a lightweight keyword flag for observability, not a
  hard block — the system prompt itself is responsible for actually declining out-of-scope or
  override attempts. A production system would add a dedicated classifier or moderation step.
- LLM-provider-side data handling (what Anthropic itself retains from API calls) is a real
  constraint outside this app's control and isn't solved here — noted rather than ignored.

## What I'd do differently in production

Real secrets management instead of `.env`; a persistent multi-instance session store; real
authentication instead of email-based verification (and instead of the shared-passcode demo
gate); a larger, CI-integrated eval suite beyond the 6 scripted scenarios in `evals/` (regression
gating on every prompt/tool change, not just a manual run); a human-handoff path for anything
outside the three flows; NER-based PII detection (e.g. Microsoft Presidio) instead of regex
pattern-matching for free-text scrubbing, which would catch names/addresses/etc. that pattern
matching structurally can't; and RAG over a larger policy corpus if the FAQ surface grew beyond a
handful of topics. Also a basic CI matrix (even just a GitHub Actions job running `pytest` on a
couple of Python versions) — this repo actually hit a real Python 3.9-vs-3.10 syntax
incompatibility (`X | None` unions, fixed by switching to `typing.Optional[...]`) when run
locally on an older interpreter than it was built against; a one-line CI check would have caught
that before it ever reached a live demo.

## UI branding note

The interface uses Decagon's public brand colors and general product design language (dark
background, gradient accents, generous whitespace, rounded surfaces) — sourced from their
published brand assets and public site, not their logo/wordmark or any copied markup. A visible
banner on every page states this is an independent prototype built by Akshay Koul for a Decagon
interview, not an official Decagon product. The Chat/Voice mode toggle follows the same pattern
used in the Claude and ChatGPT apps — a segmented switch rather than a buried settings checkbox
— since that's a UI convention users evaluating an AI support agent will already recognize.
