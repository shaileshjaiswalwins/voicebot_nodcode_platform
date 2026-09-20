# Plan: Nocode voice-bot platform — demo readiness

## Context

Boss's demo ask (email), to be delivered on our own platform (`voicebot_nodcode_platform`),
using our own MongoDB tables/collections — explicitly not the ones in
`amitrajputfff/backend-Jd-Dashboard` / `amitrajputfff/JD-Dashboard`, which are a separate
teammate's parallel nocode-platform effort we have read-only reference access to (cloned
locally to `/Users/acmecorp/nocode_platform_compare/` for comparison only, not to depend on).

MIS APIs are shared/identical between both efforts and should stay that way.

## Comparative findings (own platform vs. amitrajputfff's repos)

| Capability (from boss's email) | Ours (`voicebot_nodcode_platform`) | Theirs (`backend-Jd-Dashboard`/`JD-Dashboard`) | Verdict |
|---|---|---|---|
| Multilingual bot config | Partial — DB-backed, PM-editable (`voicebot_platform/language_settings.py`), prompt-driven switching | Partial — hardcoded in `simran_prompt.txt`, prompt-driven | **Ours more mature** |
| Multi-agent flows (real handoff per stage) | **Full** — `workflow_engine.py` (just merged from our own `Own_TTS` branch): each conversation node is its own LiveKit `Agent`, real handoff via function-tool return | **Absent** — no execution engine at all in this repo; workflow config is inert JSON without a runtime | **Ours — and theirs literally cannot run without borrowing our engine** |
| Bot fine-tuning (temperature, eval loop) | Partial — temperature + working `/evals/run` LLM-vs-LLM eval endpoint | Stub — temperature knob only | **Ours** |
| STT provider toggle (Sarvam vs. alt) | **Full** — schema → UI → runtime (`stt_provider: sarvam\|deepgram`), tested | **Absent** — schema explicitly says "STT/LLM are fixed, not selectable" | **Ours** |
| Per-minute cost tracking | **Full** — `pricing.py`, `pricing_config.py`, `/api/pricing/*`, `estimated_cost` field, `BudgetCostWidget.tsx` | **Absent** — no pricing module at all | **Ours** |
| In-browser "try before deploy" sandbox | Full but LiveKit-room-mediated (`TestCallPanel.tsx` + `routers/testcall.py`) | Full, more literal raw-WebRTC (`webrtc-connection.ts`, `test-assistant-dialog.tsx`) | **Theirs, narrowly** — worth adapting their UX pattern, not their backend |
| A/B testing & versioning | Partial — real draft/publish version history + version-pinned test calls; no traffic-split | Absent — no version storage at all | **Ours** (neither has true A/B) |
| Smart escalation / human handoff | Partial — graceful fake-transfer (apologizes, ends call), honest docstring about the gap | Stub — schema field only, nothing executes it | **Ours, barely** — real SIP transfer missing in both |
| Zero-code webhooks (mid-call actions) | **Full** — CRUD, schema-build, LiveKit tool exec, tests (`custom_functions.py`, `custom_function_tools.py`) | Partial — config + "validate" UI only, no execution | **Ours** |
| Payload ingestion + analytics + export | **Full** — lead ingestion, `callback_worker` analysis+callback pipeline, working CSV export endpoints | Broken — "Export" button calls `/api/export/data`, which **doesn't exist** on their backend (404 trap) | **Ours** |
| Appointment-flow demo bot | **Absent** — no seed data, no appointment bot anywhere in our repo | Full seed script (`seed_acmecorp_appointment_workflow_bot.py`) — 12-stage vendor-outreach script, but calendar "booking" is just a recorded string variable, not a real calendar API call | **Theirs has the content; we have no equivalent yet** |

## What's missing, prioritized

1. **[Foundation, blocking everything else] Our own `workflow_bots` CRUD API + schema**, in our own
   `backend/` against our own Mongo collections — mirror the *shape* of `Workflow`/
   `WorkflowNode`/`WorkflowEdge` (proven-working schema design) but store it in our own
   collection, not theirs. `fetch_bot_config` needs to be able to return
   `bot_type == "workflow"` configs pulled from our DB so `bot_dev_param.py`'s new hook
   (already wired) has something to dispatch.
2. **Appointment-flow demo bot**, authored fresh into our own schema/DB — can reuse the
   *script content/stage structure* from their seed file as a reference (12 stages: identity
   confirm → decision-maker check → free-lead hook → competitor urgency → price/appointment
   → closing/hot-transfer with 3 retries → callback fallback), rewritten as our own
   `WorkflowNode` graph, seeded via our own script.
3. **A real calendar-booking webhook** for the appointment step — neither platform has this;
   worth building one real function-tool call (even against a mock calendar endpoint) so the
   demo shows something neither team has shipped yet.
4. **Frontend**: wire `FlowBuilderView.tsx` to actually save/load against the new
   `workflow_bots` API and drive a Test Call through `bot_dev_param.py` — closes the loop from
   your existing "Phase A" prompt-compiler to the new Phase-B interpreter.
5. **Demo script should foreground existing, already-working differentiators** rather than
   just matching their feature list: cost dashboard, STT provider toggle, eval-run endpoint,
   CSV export, version history — all real and already built, all either absent or broken on
   their side.
6. **Explicitly scope out, with an honest "roadmap" framing rather than faking them**: true SIP
   telephony transfer (both platforms fake this), true A/B traffic-splitting (neither has it),
   acoustic language auto-detection (both are prompt-driven only).

## Suggested order of work

1. Design + build our own `workflow_bots` schema/router/Mongo collection (own naming, own DB) —
   reusing the proven `Workflow`/node/edge JSON shape as a design reference only.
2. Confirm `bot_dev_param.py`'s dispatch hook (already added) resolves configs from this new
   API end-to-end with a trivial 2-node test workflow.
3. Author the appointment-flow bot into the new schema; seed it.
4. Add the calendar-booking function-tool webhook (can start against a mock endpoint).
5. Wire `FlowBuilderView.tsx` save/load + Test Call against the new API.
6. Rehearse demo hitting: appointment flow live walkthrough, cost dashboard, STT toggle,
   eval-run, CSV export, version history — in that order, saving the honest "not yet" list for
   direct questions only.

## Notes

- Keep all of this inside our own Mongo collections/tables per explicit instruction — no writes
  to `amitrajputfff`'s DB or repos at any point.
- MIS APIs stay shared/identical — no changes needed there.
- `/Users/acmecorp/nocode_platform_compare/` (local shallow clones of both their repos) is
  read-only reference material for this comparison — not a dependency, not to be deployed from.
