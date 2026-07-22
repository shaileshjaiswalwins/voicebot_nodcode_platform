# Plan: Outbound Voice AI Campaign Platform (CSV batch, INR cost engine, budget routing, pause/resume)

## Decisions locked in
- **Datastore: extend MongoDB** (no Postgres). New collections `contacts`, `call_logs`; extend existing `campaigns` collection — matches existing `campaigns.py`/`phone_numbers.py`/`audit_log` pattern, no new infra.
- Reuse existing: `backend/routers/campaigns.py` (CRUD/strategy/bot/status), `backend/audit.py` (`log_audit`), `frontend/src/components/CampaignLeadsPanel.tsx` + `CsvUploadDropzone.tsx` (already built, currently pointed at a stub endpoint).
- Net-new: contact/lead storage, CSV parse+validate endpoint, prompt variable injector, pricing/model-router engine, queue+pause/resume execution, call_log + latency/cost detail, phone/audit UI polish.

## Status legend: [ ] todo · [~] in progress · [x] done

## Day 1 — Schema & CSV ingestion
- [x] `backend/models.py`: added `CampaignLead` (id/campaign_id/phone_number/name/vars/status/call_id/estimated_cost — matches the frontend's existing `CampaignLead` type in `api.ts` exactly) and `CampaignLeadUploadResult`. (`CallLog` model deferred to Day 3 where it's actually consumed.)
- [x] `backend/routers/campaigns.py`: implemented `POST /api/campaigns/{key}/leads` (CSV upload via `UploadFile`, E.164 validation regex, blank name → "Customer", extra columns → `vars`) and `GET /api/campaigns/{key}/leads?status=` — matches `CampaignLeadsPanel.tsx`'s existing stub calls exactly, no frontend changes needed.
- [x] Mongo collection `tbl_ai_vb_campaign_leads` (`campaign_leads` in `db.py`) with index on `(campaign_id, phone_number)`.
- [x] Backend tests in `test_campaigns.py`: valid+invalid phone rows, missing phone_number column rejected (400), name fallback to "Customer", extra CSV columns land in `vars`, status filter, auth-required. Full backend suite: 107 passed.
- [x] Added `python-multipart` dependency (required by FastAPI for `UploadFile`) via `uv add`.

## Day 1.5 — Prompt variable injector + live preview
- [x] Backend: `PUT /api/campaigns/{key}/prompt` saves `prompt_template` on the campaign doc; `POST /api/campaigns/{key}/prompt/validate` returns unknown `{{var}}` tags not present in `name`/`phone_number`/any uploaded CSV column (`_known_vars_for_campaign` scans `campaign_leads.vars`).
- [x] Frontend: `PromptTemplateEditor.tsx` (new) mounted in `CampaignLeadsPanel.tsx` — textarea with `{{col}}` chip detection (unknown ones flagged red via debounced validate call), select-a-lead-row live preview using `interpolateTemplate`. Pure logic lives in `utils/promptTemplate.ts` (`extractTemplateVars`, `interpolateTemplate`), same pattern as `customFunctions.ts`.
- [x] Tests: `promptTemplate.test.ts` (dedup/ordering, missing var left untouched, name fallback to "Customer") + backend `test_campaigns.py` (unknown-var detection, all-known-vars empty result). `npx tsc --noEmit` clean; full frontend suite 152/156 passing (4 pre-existing unrelated failures in `App.test.tsx`/`api.test.ts` timeout tests, not touched by this work).

## Day 2 — Model configurator + real-time INR cost estimator
- [x] `backend/pricing.py` (pure, no I/O): `STT_RATES`/`LLM_RATES`/`TTS_RATES`/`TELEPHONY_RATES` tables (₹/min, Sarvam entries match `provider_params.py` hardcoded defaults exactly), `ProviderStack` dataclass, `estimate_cost_per_min`, `pricing_matrix()`, `select_stack_for_budget`.
- [x] `backend/routers/pricing.py`: `GET /api/pricing/matrix`, `GET /api/pricing/budget-route?max_inr_per_min=` — registered in `main.py`.
- [x] Budget auto-router: 4 named tiers (Budget/current-default/Performance/Ultra) sorted by cost; picks highest-cost tier still under cap, falls back to cheapest tier if cap is unreachably low (never raises/500s).
- [x] Backend tests (`test_pricing.py`, 8 passing): cost sum correctness, unknown-provider-key raises, budget selecting current default at ₹2.72, upgrade path at ₹15, fallback-to-cheapest at unreachably low cap, both endpoints incl. auth-required.
- [x] Frontend: `BudgetCostWidget.tsx` (new) — fetches `GET /api/pricing/matrix`, live total cost as dropdowns change, ₹2–₹15/min budget slider calling `GET /api/pricing/budget-route` to auto-select a stack. Mounted in `CampaignLeadsPanel.tsx` (not `CampaignsView.tsx`'s edit-strategy dialog, to avoid touching that component's existing test coverage/complexity for this slice).
- [x] Tests: `BudgetCostWidget.test.tsx` (default-stack cost render, budget-slider triggers route call and updates displayed cost). Full frontend suite 158/162 (net +2 vs. prior checkpoint; same 4 pre-existing unrelated failures). `tsc --noEmit` clean.

## Day 2.5 — Execution engine + pause/resume
- [x] Queue: `backend/campaign_execution.py` (new) — `tbl_ai_vb_call_jobs` Mongo collection (`call_jobs` in `db.py`, indexed on `(campaign_id, status)`), `enqueue_pending_leads` (idempotent), `claim_next_job` (atomic `find_one_and_update` + `LEASE_TIMEOUT=10min`, same pattern as `callback_worker/worker.py`'s `_claim_one`/`ReturnDocument`), `complete_job`, `progress_counts`. No Redis/Celery/BullMQ introduced.
- [x] `campaign.status` gains real pause semantics: `claim_next_job` checks `campaigns` doc status and returns `None` when `"paused"` — reuses the existing `PUT /{key}/status` endpoint, no new status-setting endpoint needed.
- [x] New endpoints in `backend/routers/campaigns.py`: `POST /{key}/start` (enqueue + set active, idempotent), `GET /{key}/progress`, `POST /{key}/claim`, `POST /{key}/jobs/{job_id}/complete`.
- [x] Frontend: `CampaignLeadsPanel.tsx` — Start/Pause/Resume buttons wired to `startCampaign`/`setCampaignStatus`, progress bar + summary cards now read live `CampaignProgress` (queued/in_progress/completed/failed/total) via polling `getCampaignProgress`, falling back to lead-status counts if the progress call fails.
- [x] Tests (`test_campaign_execution.py`, 12 passing): two claims never return the same job, stale in_progress job reclaimed after lease timeout, paused campaign blocks claims, resume unblocks, idempotent enqueue, non-terminal complete_job status rejected, full start→claim→complete→pause→resume flow via HTTP endpoints. Full backend suite: 129 passed. Frontend: `tsc --noEmit` clean, full suite 154/158 passing (same 4 pre-existing unrelated failures).
- [~] Known gap carried forward unchanged: this is job **state** management only — no actual LiveKit room is spawned by `claim_next_job`. Wiring real dispatch (stamping `bot_id` + resolved provider stack into room metadata) is out of scope for this session; flagged in the module's docstring so it isn't silently assumed done.

## Day 3 — Phone inventory polish, audit trail, call logs, e2e
- [x] Phone inventory: confirmed `PhoneNumbersView.tsx` already renders the assigned bot's name per number (`botById.get(phone.assigned_bot_id)?.name`) — no new work needed, matches the plan's expectation.
- [x] Audit log: audit calls added at every new mutation point — `upload_leads`, `update_prompt_template`, `start_campaign`, `complete_call_job` (all in `backend/routers/campaigns.py`, using the existing `log_audit()`). Pause/resume reuses the pre-existing `set_status` audit call. `claim` (a poll, called every dialer tick) deliberately NOT audited — would spam the log with a non-mutating, high-frequency action.
- [x] Call log + detail: `backend/campaign_execution.py` — `complete_job` now also (a) syncs `status`/`call_id` back onto the `campaign_leads` doc (fixes a real gap: without this, the leads table would show "pending" forever even after a call completed, since it reads status off the lead doc, not `call_jobs`), and (b) upserts a `call_logs` doc with `cost_inr`/`latency_ms` breakdowns when a `call_id` is given. `get_call_detail(call_id)` merges `call_logs` with the pre-existing `transcripts` collection (transcript/duration/recording_url/analysis) — reused rather than duplicated. `GET /api/campaigns/{key}/calls/{call_id}` exposes it (404 if neither collection has anything for that call_id).
- [x] E2E test (`test_campaign_e2e.py`): 10-contact CSV → upload → start → complete 5 → pause (verify claim blocked) → resume → complete remaining 5 → progress shows all 10 completed → audit log contains `upload_leads`/`start_campaign`/`set_status`/`complete_call_job` entries for the campaign.
- [x] Full backend suite: 134 passed (was 107 at Day 1 checkpoint).
- [x] Frontend call-detail drawer UI: `CallDetailDrawer.tsx` (new) — clicking a lead row with a `call_id` opens a dialog fetching `GET /api/campaigns/{key}/calls/{call_id}`, showing cost breakdown (with total), latency breakdown, and transcript. Mounted in `CampaignLeadsPanel.tsx`. `tsc --noEmit` clean, full suite 154/158 (same 4 pre-existing unrelated failures, no regressions).

## Post-Day-3 UI pass
- [x] Fixed a real functional gap in `CampaignsView.tsx`'s list-level "Activate" button: it only called `setCampaignStatus`, never `enqueue_pending_leads` — so activating a campaign from the main list (as opposed to the Start button inside `CampaignLeadsPanel.tsx`) silently never enqueued any call_jobs. `App.tsx`'s `handleSetCampaignStatus` now calls `api.startCampaign` on activate (idempotent, matches what the leads panel's own button does).
- [x] `backend/pricing.py`: added `named_tiers()` (Budget/Current default/Performance/Ultra, sorted cheapest-first) + `GET /api/pricing/tiers` endpoint, so the frontend has a single source of truth for a real tier comparison table instead of re-deriving tier compositions client-side.
- [x] Redesigned `BudgetCostWidget.tsx`: added a 4-card tier comparison row (name, ₹/min, STT/LLM/TTS stack) matching the original PRD's Option A/B/C table — click a card to apply that stack. Per-provider dropdowns moved behind a "Show advanced" toggle since most users pick a tier, not individual providers. Visual polish: accent-colored top border per tier, active-tier checkmark badge, hover lift, bigger/bolder total-cost display.
- [x] Tests: `test_pricing.py` +3 (named tiers sorted, endpoint auth+shape), `BudgetCostWidget.test.tsx` rewritten for the new markup +3 net new (tier cards render, clicking a tier applies its stack, advanced dropdowns hidden until toggled). Backend 137 passed, frontend 161 total (157 passing, same 4 pre-existing unrelated failures), `tsc --noEmit` clean.
- Note: no dedicated frontend/design skill was available in this environment (checked `DesignSync`, which needs a claude.ai design-system project + auth — out of scope for a quick pass) — this polish was done directly with CSS/component work rather than through a design tool.

## Known cross-cutting gap (do not silently reproduce)
`backend/routers/phone_numbers.py` does not yet stamp `bot_id`/version into LiveKit room metadata at real dispatch time (flagged in existing code comment, and in PRD-AI-156 section 10). The queue/dialer built here (Day 2.5) must write `bot_id` + resolved provider stack into room metadata itself when placing a call — otherwise campaign calls will run on hardcoded bot config same as today, defeating the budget router. This is the one place Day 2/2.5 work must NOT assume "someone else already wired this."

## Execution notes for continuation across context compaction
- This file is the durable source of truth for progress — update checkboxes as work lands, do not rely on conversation memory.
- Working in small commits per checklist item where possible.
- No Postgres, no Redis/Celery/BullMQ — deliberately staying inside the existing Mongo + FastAPI + React stack per the locked decision above.
