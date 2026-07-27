# Goal: Admin-configurable pricing (INR) + per-bot "Agent details" cost card

Full plan/context: see the approved plan (this file tracks execution progress; update
checkboxes as work lands so progress survives context compaction).

## Status legend: [ ] todo · [~] in progress · [x] done

## Backend
- [x] `backend/models.py`: `PricingModelEntry`, `PricingConfig` (stt/llm/tts/telephony lists + `voice_infra_cost_inr_per_min`)
- [x] `backend/pricing.py`: widened `estimate_cost_per_min`/`named_tiers`/`select_stack_for_budget` to accept optional rate-dict params (default = existing module constants); removed now-dead `_TIERS_BY_COST` module constant (recomputed locally in `select_stack_for_budget` instead)
- [x] `backend/pricing_config.py` (new): `get_config()` (in-memory default, no persist-on-read — matches `settings.py`'s exact pattern), `update_config()`, `get_effective_rates()`, `_DEFAULT_LLM_LATENCY_TOKENS` seed table
- [x] `backend/routers/pricing.py`: `/matrix`, `/tiers`, `/budget-route` now read from `pricing_config.get_effective_rates()`; new `GET`/`PUT /api/pricing/admin-config`
- [x] `backend/tests/conftest.py`: added `tbl_ai_vb_pricing_config` to `_clean_db` wipe list
- [x] `backend/tests/test_pricing_config.py` (10 tests): seed defaults match hardcoded rates, default LLM latency/token estimates present, update persists, get_effective_rates reflects updates, admin-config GET/PUT round-trip + auth required, matrix/tiers/budget-route all reflect admin config changes
- [x] Confirmed `backend/tests/test_pricing.py` passes unchanged (11/11) — signature widening is backward-compatible. Full backend suite: 160 passed.

## Frontend
- [x] `frontend/src/api.ts`: `PricingModelEntry`/`PricingConfig` types, `getPricingAdminConfig()`, `updatePricingAdminConfig()`
- [x] `frontend/src/views/AdminView.tsx` (new): `PricingCategoryEditor` sub-component reused for STT/LLM/TTS/Telephony (LLM gets extra latency/token fields via `withEstimates`), flat Voice Infra rate input, single Save button with idle/running/saved/failed states matching `SettingsView.tsx`'s pattern
- [x] Nav wiring: `types.ts` View union (+`'admin'`), `constants/ui.tsx` CMD_VIEWS (+IndianRupee icon entry), `App.tsx` NAV_ICONS + render branch + `pricingConfig` state loaded eagerly alongside other data on auth (same pattern as `loadSettings`/`loadLibrary`), `handleUpdatePricingConfig` handler. Also had to fix `utils/routes.ts` (`VIEW_TO_SEGMENT`/`SEGMENT_TO_VIEW`/`RouteState` missing the new view — caught by `tsc`) and `TestCallPanel.tsx`'s `titleFor`/`subtitleFor` (also caught by `tsc`).
- [x] `frontend/src/utils/agentCost.ts` (new, pure): `estimateAgentCost(botConfig, pricingConfig)`, `normalizeModelKey`
- [x] `frontend/src/components/CostBreakdownPopover.tsx` (new): rich hover popover, total + LLM/TTS/Voice Infra rows
- [x] `frontend/src/views/BuilderView.tsx`: new "Agent details" panel in right-rail (ID copyable via `CopyableId`, Cost/Latency/Tokens, Cost wrapped in `CostBreakdownPopover` with `.dotted-underline`)
- [x] `frontend/src/styles.css`: `.dotted-underline`, `.panel-header-inline`, `.agent-details-popover*`, `.cost-breakdown-wrapper`, `.pricing-row*`
- [x] `BudgetCostWidget.tsx`: no code change needed — already reads `/matrix`/`/tiers`/`/budget-route`, which now source from `pricing_config.py`. Confirmed via `test_pricing_config.py`'s `test_matrix_endpoint_reflects_admin_config_changes`/`test_tiers_endpoint_reflects_admin_config_changes`/`test_budget_route_endpoint_reflects_admin_config_changes` that editing admin config changes what those endpoints return, which is exactly what `BudgetCostWidget` consumes.

## Tests
- [x] `agentCost.test.ts` (7 tests: normalization, exact match, fallback-to-first-entry, blank model, latency/tokens, voice infra always included)
- [x] `CostBreakdownPopover.test.tsx` (3 tests: hidden until hover, shows breakdown rows + total on hover, hides on mouse leave)
- [x] `AdminView.test.tsx` (7 tests: loading state, renders existing entries, adding an LLM row shows latency/token fields, deleting a row removes it, editing label auto-slugifies key + save wiring, editing voice infra rate, failed-save retry state)
- [x] `BuilderView.test.tsx` extended: new test asserts Cost/Latency/Tokens render in ₹ once pricing config loads (12/12 passing)
- [x] Fixed a real regression caught during this work: `App.test.tsx`'s hand-built `api` mock didn't include `getPricingAdminConfig`, so `BuilderView`'s new `useEffect` call threw synchronously against the mocked object and broke 6 unrelated builder/evals tests. Added `getPricingAdminConfig`/`updatePricingAdminConfig`/`pricingMatrix`/`pricingTiers`/`pricingBudgetRoute` to that mock.
- [x] Final state: backend 160/160 passed. Frontend 175/179 passed (same 4 pre-existing unrelated `App.test.tsx`/`api.test.ts` timeout failures, no new regressions). `tsc --noEmit` clean.

## Verification
- [x] `USE_INMEMORY_DB=true uv run pytest backend/tests -q` — 160 passed
- [x] `npx tsc --noEmit` (frontend/) — clean
- [x] `npx vitest run` (frontend/) — 175/179 passed (same pre-existing 4 unrelated failures, no new regressions)
- [x] Manual-sanity equivalent covered by automated tests: `test_matrix_endpoint_reflects_admin_config_changes` (BudgetCostWidget's data source) + `BuilderView.test.tsx`'s new Agent-details test (per-bot card) both prove admin-config edits flow through to their respective totals in ₹.

## ALL CHECKLIST ITEMS COMPLETE.
