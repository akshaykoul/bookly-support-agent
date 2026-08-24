# Bookly Support Agent

A prototype customer support agent for Bookly (fictional online bookstore), built for the
Decagon Solutions Engineering take-home. Chat + voice UI, real Claude tool-calling against a
mocked SQLite backend, no agentic framework.

## Quick start

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

## Architecture

```
Browser (chat + mic)
   │  POST /chat {message, session_id}
   ▼
FastAPI (app/main.py)
   │
   ▼
Orchestrator (app/orchestrator.py) ── hand-rolled tool-use loop, no framework
   │  call Claude with system prompt + tool schemas + history
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

Voice is a thin I/O layer on top of the same text pipeline: the browser's built-in
`SpeechRecognition` transcribes speech into the same input the text box uses, and
`speechSynthesis` reads the reply back out loud. The agent core never knows whether a message
came from typing or speech — no separate voice backend, no extra API keys.

## The three required demo behaviors

- **Multi-turn**: ask "where's my order" with no details → the agent asks for the order number
  and email before it can look anything up (this is also the verification step — see below).
- **Real/mocked tool use**: `get_order_status`, `check_return_eligibility`, `initiate_return`,
  `confirm_return`, `lookup_policy`, and `reset_password` are all real function calls against the
  SQLite database, not the model narrating an action.
- **Clarifying question**: ask to return "something" with no order info, or ask about an
  ambiguous policy topic ("what about international shipping?") — the agent asks rather than
  guessing. This also happens naturally any time verification info is incomplete.

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
   See `GET /trace/{session_id}` for the raw (masked) timeline; a production deployment would
   ship this same shape to a real tracing vendor (Langfuse/Datadog/OTel).

4. **A shared passcode gates the public deploy, not real auth.** Once this has a live URL to
   share with reviewers, anyone with the link could otherwise trigger real, billed Claude API
   calls. `BOOKLY_ACCESS_CODE` (unset by default, only set on the public deploy) gates `/chat`
   and `/trace` behind a shared code checked server-side — cheap to build, sufficient to keep
   the demo link from being scraped, and explicitly not pretending to be customer
   authentication.

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
gate); a dedicated eval harness/regression suite for the agent's behavior; a human-handoff path
for anything outside the three flows; a real observability vendor instead of the homemade trace
table; NER-based PII detection (e.g. Microsoft Presidio) instead of regex pattern-matching for
free-text scrubbing, which would catch names/addresses/etc. that pattern matching structurally
can't; and RAG over a larger policy corpus if the FAQ surface grew beyond a handful of topics.

## UI branding note

The interface uses Decagon's public brand colors (sourced from their published brand assets) as
a color scheme, not their logo/wordmark. A visible banner on every page states this is an
independent prototype built by Akshay Koul for a Decagon interview, not an official Decagon
product.
