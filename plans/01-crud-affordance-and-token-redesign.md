# Plan: CRUD affordance consolidation + design-token enforcement

## Context

A Dieter Rams design audit (`DESIGN-IS-2026-07-13/`) scored the Voice AI Platform frontend 15/30 — a REDESIGN verdict, not REFINE. The problem isn't any single ugly screen; it's that four views (Bots, Phone Numbers, Campaigns, Library) each independently hand-built their own Edit and Delete affordances (8 Edit implementations, 5 Delete-confirm implementations total), and a real design-token system exists in `styles.css` but most component rules bypass it with one-off values. This plan consolidates that duplication and enforces the tokens, while explicitly preserving what already scored well: honest copy (3/3), the `Dialog`/`ConfirmDialog` primitives, and the "no uninvited modals" behavior.

**Full audit reference** (read once, not needed again mid-execution): `DESIGN-IS-2026-07-13/01-evidence.md`, `02-scorecard.md`, `03-verdict.md`.

## Allowed APIs / patterns (from Phase 0 discovery — do not invent alternatives)

- `Dialog` — `components/Dialog.tsx:3-23`, props `{ title, icon?, children, footer?, tone?, maxWidth?, closeOnBackdrop?, closeOnEscape?, onClose }`.
- `ConfirmDialog` — `components/ConfirmDialog.tsx:5-23`, props `{ title, description, confirmLabel, busyLabel?, busy?, tone?, onCancel, onConfirm }`. **Note: none of the 5 current call sites pass `busy`/`busyLabel`** — the new shared component must wire these through, since it's a real, already-built gap.
- `ErrorState` — `components/ErrorState.tsx`, props `{ heading, description, onRetry? }`. Already built, used only in `AnalyticsView.tsx:96`. Do not build a new error-state component — adopt this one.
- `Tooltip` — `components/Tooltip.tsx`, props `{ label, children }`. Already built, used only in `AnalyticsView.tsx:109-111`. Do not build a new tooltip — adopt this one for jargon explanations.
- `EmptyState` — `components/EmptyState.tsx`, reference for component shape/API style to match.
- Token block — `styles.css:1-75` (`--jd-blue-*`, `--jd-gray-*`, `--space-*` 4pt scale at `styles.css:60-65`, semantic aliases). Values are correct; only enforcement is missing.
- `BotsView.tsx`'s `DeleteAgentDialog` (`BotsView.tsx:209-223`, rendered `App.tsx:1333-1341`) is an intentionally different, stronger pattern (type-to-confirm) for a higher-stakes delete. **Do not fold this into the shared component** — it stays as-is.

## Phase 1 — Shared `RowActions` component

**What to implement**: A new `components/RowActions.tsx` that replaces the 5 duplicate Edit+Delete-confirm implementations found at:
- `PhoneNumbersView.tsx` — Edit button `:206`, Delete button + `deleteTarget` state (`:71`) + `ConfirmDialog` (`:377-386`) + `confirmDelete` handler (`:137-138`)
- `CampaignsView.tsx` — Edit button `:685`, Delete button + `deleteTarget` state + `ConfirmDialog` (`:797-806`) + `confirmDelete` handler
- `LibraryView.tsx` (languages) — Delete button + `pendingDelete` state (`:251`) + `ConfirmDialog` (`:418-427`) + `performPendingDelete` handler (`:299-301`)
- `LibraryView.tsx` (phrases) — Edit button `:126`, Delete button `:127` (`onConfirmDelete(row)`) + `pendingDelete` state (`:461`) + `ConfirmDialog` (`:576-585`) + `confirmDelete` handler (`:509-510`)

Proposed API (copy the shape from the existing 5 call sites above — every field below is already present in at least one of them, just scattered):

```tsx
// components/RowActions.tsx
type RowActionsProps<T> = {
  item: T;
  onEdit?: (item: T) => void;
  editTitle?: string; // defaults to "Edit"
  onDelete?: (item: T) => Promise<void> | void;
  deleteTitle: string; // e.g. "Delete phone number?"
  deleteDescription: (item: T) => React.ReactNode; // matches existing per-caller description closures
  deleteConfirmLabel?: string; // defaults to "Delete"
};
```

Internally, `RowActions` owns its own `pendingDelete: T | null` state and `busy: boolean` state, renders the Edit icon-button, the Delete icon-button, and a single `ConfirmDialog` wired with `busy`/`busyLabel` (the gap noted above). This is the copy-from-docs task — assemble it from the 5 existing call sites' exact prop values, do not invent new copy or new confirm flows.

**Verification**:
- `npx tsc --noEmit` clean.
- New `components/RowActions.test.tsx` covering: renders Edit+Delete buttons, clicking Delete opens confirm, confirming calls `onDelete`, cancel does not call `onDelete`, busy state disables both dialog buttons during an in-flight delete.
- `npx vitest run` clean (109 existing + new tests).

## Phase 2 — Migrate the 4 call sites to `RowActions`

**What to implement**: Replace the local Edit button + Delete button + local `ConfirmDialog` block in each of `PhoneNumbersView.tsx`, `CampaignsView.tsx` (row-level delete only, not the separate `pendingStatusChange` ConfirmDialog — that one is a different action and stays untouched), and both `LibraryView.tsx` sections with `<RowActions item={...} onEdit={...} onDelete={...} deleteTitle="..." deleteDescription={...} />`, copying the exact existing title/description/confirmLabel text verbatim from Phase 0's discovery findings above (Honesty scored 3/3 — the copy does not change, only where it lives).

Rollout order (touch one view, verify, move to next — this is the "migration path" for a single-role internal tool, per the audit's REDESIGN scope):
1. `PhoneNumbersView.tsx`
2. `CampaignsView.tsx`
3. `LibraryView.tsx` (languages section)
4. `LibraryView.tsx` (phrases section)

**Verification per view**: `npx tsc --noEmit` clean after each file; `npx vitest run` clean after each file (don't batch all 4 before checking — catch regressions per-view); manually confirm in `npm run dev` that Edit still opens the same modal and Delete still shows the same confirm text as before the swap.

## Phase 3 — Token enforcement sweep

**What to implement**: Grep `styles.css` for every `font-size` and `gap`/`padding`/`margin` value NOT already a `var(--...)` reference, and remap each onto the existing scale (`styles.css:60-65` for spacing) or a newly-defined, deliberately small type scale (e.g. `--text-xs/sm/base/md/lg`, collapsing the 18 near-duplicate values found in the audit — `0.72/0.74/0.75/0.76/0.78rem` etc. down to one shared step). Also fold the 7 stray hardcoded hex literals (badge greens/reds/yellows) into the existing `--success`/`--danger`/`--warning` tokens they duplicate.

This is a mechanical sweep, not a restyle — every remapped value should render pixel-identical or near-identical to before; the goal is *traceability to a token*, not a new visual language.

**Verification**:
- `grep -oE "[0-9.]+(rem|px)" frontend/src/styles.css` before/after — after, every match should be inside the `:root` token block (lines 1-75) or a `calc()`/`var()` expression, not a bare literal in a component rule. (A handful of legitimate exceptions are fine — e.g. `1px` borders, `100%` — note them explicitly rather than silently leaving stragglers.)
- `grep -oE "#[0-9a-fA-F]{3,8}" frontend/src/styles.css` outside lines 1-75 should return zero matches (or only the 4 `rgba()` shadow/backdrop literals already noted as acceptable in the audit).
- `npx vitest run` clean (pure CSS change, no component logic touched — should not break any test).
- Manual: `npm run dev`, spot-check Bots/Phone Numbers/Campaigns/Library render visually unchanged (no accidental size/color drift from the remap).

## Phase 4 — Jargon + focus + error-state fixes

**What to implement**, three independent small fixes:
1. Wrap "AOD ports" (`PhoneNumbersView.tsx:285,354`) and "DNI" in "Number (DNI)" (`PhoneNumbersView.tsx:265`) and "Lead API" column header (`CampaignsView.tsx:620`) in the existing `Tooltip` component, copying the usage pattern from `AnalyticsView.tsx:109-111` exactly. Tooltip text: "AOD ports" → "Number of simultaneous audio-on-demand lines this number supports." / "DNI" → "Direct Number Identifier — the actual number that gets dialed." / "Lead API" → "The endpoint this campaign pulls leads from."
2. Restore visible focus indication at the 3 sites that removed it with no replacement: `styles.css:262` (`.search-box input:focus`), `styles.css:334` (`.cmdk-input-row input:focus`), `styles.css:548` (`.agent-chat-input input:focus`) — each currently sets `outline: none` with nothing in its place. Add back a visible (but visually appropriate for that context, e.g. a border-color change instead of a hard outline if the outline visually clashes with the search-box chrome) focus indicator at each site.
3. Add a real error state to `PhoneNumbersView.tsx` and `CampaignsView.tsx` list rendering, adopting `ErrorState` (copy the exact usage pattern from `AnalyticsView.tsx:96`) — requires checking how these two views currently receive/propagate a load error from `App.tsx` (their parent) and passing it through as a new optional `error?: string` + `onRetry?: () => void` prop, gated the same way `loading` already is.

**Verification**:
- `npx tsc --noEmit` clean.
- New/updated tests: `PhoneNumbersView.test.tsx`/`CampaignsView.test.tsx` (or wherever their existing tests live) — assert `ErrorState` renders when an `error` prop is passed, assert `Tooltip` renders on the 3 jargon terms.
- Manual: tab through the search box / command palette / agent chat input in `npm run dev` and confirm a visible focus indicator appears at each of the 3 fixed sites.

**Status: Phases 1, 2, and part of 4 implemented and verified.**
- Phase 1: `components/RowActions.tsx` built (Edit + Delete + `ConfirmDialog` with `busy`/`busyLabel` wired), `components/RowActions.test.tsx` (5 tests) added. `tsc --noEmit`, `vitest run` clean.
- Phase 2: all 4 call sites migrated — `PhoneNumbersView.tsx`, `CampaignsView.tsx` (row delete only; `pendingStatusChange` dialog untouched by design), `LibraryView.tsx` languages (delete-only, no edit button — edit is via the existing inline form) and phrases (edit triggers existing inline-row-edit state, delete goes through `RowActions`). Verified per-view with `tsc --noEmit` + `vitest run` (114/114 passing) after each file.
- Phase 3 (token sweep): **partial** — added `--danger-border` token and folded the 3 duplicate `#fecaca` literals into it. Did not do the full spacing/font-size sweep (30+ values, 18 near-duplicate font sizes) — that's a larger, purely mechanical follow-up that's safe to defer; noted here so it isn't lost.
- Phase 4: done — Tooltip added to "Number (DNI)", both "AOD ports" labels, and "Lead API" column header (copying the exact `AnalyticsView.tsx:109-111` pattern); focus indication restored on `.search-box`, `.cmdk-input-row`, `.agent-chat-input` via `:focus-within` border-color instead of a raw outline (avoids clashing with the surrounding chrome). `ErrorState` wiring into PhoneNumbersView/CampaignsView **not done** — requires tracing how load errors currently propagate from `App.tsx`, deferred as a separate follow-up.
- `npx tsc --noEmit`, `npm run build`, `npx vitest run` (114/114) all clean as of this pass.

**Remaining for a future pass:** full Phase 3 spacing/type-scale sweep; Phase 4 item 3 (`ErrorState` + `onRetry` on PhoneNumbers/Campaigns); manual in-browser check (no browser automation available in this environment).

## Final Phase — Verification

1. Re-run the audit's own evidence checks to confirm the fixes landed:
   - `grep -rn "ConfirmDialog" frontend/src/views/*.tsx` should show `RowActions.tsx` as the sole owner of delete-confirm wiring in the 4 migrated views (Campaigns' separate `pendingStatusChange` dialog is the one legitimate exception, and BotsView's `DeleteAgentDialog` is the one deliberate exception).
   - Re-run the spacing/color greps from Phase 3.
2. Full suite: `npx tsc --noEmit`, `npm run build`, `npx vitest run` (109+ tests), `uv run pytest backend/tests/` (unaffected by this frontend-only plan, but run to confirm no accidental backend regression) — all clean.
3. Manual: `npm run dev`, walk through Bots/Phone Numbers/Campaigns/Library — Edit and Delete should look and behave identically to before (same copy, same confirm flow) but now come from one component; hover the 3 jargon terms for tooltips; force a load error (e.g. stop the backend) and confirm `ErrorState` + Retry appears instead of a blank/broken list.
4. Cutover criterion (from the audit handoff): all 4 CRUD-heavy views use `RowActions`, and the two grep checks in step 1 return zero unexpected results — at that point this redesign is complete, no flag/dual-path needed since this is an internal single-role tool with no external migration to stage.
