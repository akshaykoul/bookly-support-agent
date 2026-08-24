# Bookly Support Agent — context for Claude Code

This file exists so a fresh Claude Code session in this repo has the same context that was built
up designing it in Cowork. Read this before making changes.

## What this is

Decagon Solutions Engineering take-home: a prototype chat+voice customer support agent for
Bookly (fictional online bookstore). Two deliverables: this repo (prototype/demo) and a separate
3-5 slide pitch deck (not built yet — see Status below).

Full architecture, setup, and rationale are in `README.md` — read that first for the "what" and
"why". This file is about status and things to know that aren't in the README.

## Current status (as of 2026-08-24)

- Scaffold is complete and committed (multiple commits on `main`, remote set to
  `https://github.com/akshaykoul/bookly-support-agent.git`). Not yet pushed.
- 18/18 unit tests pass (`pytest`). They test tool logic directly (verification,
  anti-enumeration, eligibility gating, the initiate/confirm return guardrail, password-reset
  uniformity, PII masking + free-text scrubbing) without calling the LLM.
- Added in v2: a `scrub_text()` free-text PII scrubber (closes a real gap — raw chat `content`
  logged to `agent_traces` wasn't being masked, only structured tool args/results were); a
  `BOOKLY_ACCESS_CODE` passcode gate on `/chat` and `/trace` (for when this is deployed
  publicly); a Decagon-brand-colored dark UI with a visible "independent interview prototype by
  Akshay Koul, not an official Decagon product" disclosure banner; a `render.yaml` Render
  Blueprint for one-click deploy.
- Added in v3: real Langfuse observability (`app/observability.py`, opt-in via env vars,
  additive to the local trace table, not a replacement); `evals/run_evals.py`, a 6-scenario
  Python-native behavioral eval harness against the real API (structural assertions on tool
  calls/guardrail flags/session state, not string-matching); ElevenLabs TTS for spoken replies
  (`app/voice.py`, `POST /speak`, opt-in, falls back to browser `speechSynthesis` on any
  failure) — deliberately output-only, speech input stays on the free browser API. Considered
  and explicitly declined a guardrails framework (Guardrails AI / NeMo) — see README "Key
  decisions" #6 for why.
- NOT yet done: a live end-to-end run with a real `ANTHROPIC_API_KEY` exercising the three
  required demo behaviors (multi-turn, real tool use, clarifying question) through the actual
  chat UI; the actual Render deploy (needs the user's Render account); `git push`.
- The pitch deck (3-5 slides: thesis, architecture, 2-3 key decisions with tradeoffs, what I'd
  do differently) has NOT been built. The content for it already exists across README.md's "Key
  decisions" and "Assumptions" sections — that's the source material, not from scratch.
- Stale git lock files (`.git/index.lock`, `.git/HEAD.lock`) can reappear after any git command
  run via the Cowork device sandbox, which lacks delete permission — they get `mv`'d into a
  `_to_delete/` folder in the repo as a workaround each time. That folder is safe to delete.

## Things worth knowing that aren't obvious from the code alone

- **Every guardrail here is structural, not just a prompt instruction.** Verification
  (`get_order_status` requiring both order_id + email match) gates the return flow via session
  state (`verified_order_id`), not via the model remembering to check. Returns require
  `initiate_return` (dry run) then a separate `confirm_return` — the DB write only happens in
  the second call. If you extend this agent with new actions, keep that pattern: propose, then a
  separate explicit confirm step for anything that writes data.
- **Anti-enumeration is deliberate in two places**: `get_order_status` returns an identical
  failure message whether the order doesn't exist or the email doesn't match; `reset_password`
  always returns the same "if an account exists..." message. Don't "improve" the error messages
  to be more specific — that would reintroduce an enumeration vector.
- **"Production-ready" was scoped deliberately, not skipped out of laziness.** The brief
  explicitly says code doesn't need to be production-ready and to avoid agentic frameworks. The
  choice here was: prod-grade *code patterns* (modular structure, real schema, structural
  guardrails, masked logging, tests, Dockerfile) without real *infrastructure* (no Postgres, no
  auth, no deployment, no external observability vendor). That tradeoff is intentional — see
  README "Assumptions" — don't add real infra unless the goal has changed.
- **No agentic framework on purpose** (LangChain/CrewAI/etc excluded) — the brief explicitly
  wants to see raw API calls and hand-rolled orchestration (`app/orchestrator.py`). Don't
  introduce one.
- Session state is in-memory (`app/orchestrator.py` `SESSIONS` dict) and per-process by design
  for this prototype scope — restarting the server clears it. Documented as a "what I'd do
  differently" item, not a bug.
- Mock data dates in `app/seed.py` are anchored to a fixed `SEED_TODAY` (2026-08-24), not real
  "today" — this keeps the eligible-vs-expired-return-window demo (`BK-10234` vs `BK-09876`)
  consistent no matter when the app is actually run.

## If continuing this work

Reasonable next steps in order: (1) live smoke test with a real API key through the actual chat
UI, hitting all three required demo behaviors; (2) `git push -u origin main`; (3) build the pitch
deck from README's "Key decisions"/"Assumptions" sections; (4) optionally record the 2-minute
demo video the brief allows as an alternative to a repo link.
