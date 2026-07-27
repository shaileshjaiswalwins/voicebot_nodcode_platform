# Plan: Flow builder redesign to match reference workflow-node UX

## Context
User shared screenshots + a "How workflow nodes work" guide dialog from a reference internal
app (Justdial Vendor Appointment Scheduler flow builder, also built on React Flow). Ask: make
our own flow builder (`frontend/src/views/FlowBuilderView.tsx`) look and work as close to it as
possible. Confirmed via AskUserQuestion: full redesign including the outcome-pill/transition
edge model, not just a visual reskin.

Reference node types + semantics (from the guide dialog, treated as the spec):
- **Start**: entry point, exactly one, has `first_message`.
- **Conversation**: a real LLM turn / its own narrow agent. Has `prompt`, optional `variables`
  (extracted structured data), and `transitions` — each transition is its own named connector
  dot; the model picks whichever transition's description best matches what the caller said.
- **Condition**: rule-based (not an LLM guess) — variable/operator/value, always falls through
  to a fixed Else branch.
- **Function (API Call)**: calls a webhook mid-call, stores response under `output_key` for a
  later Condition/Conversation node to reference.
- **End Call**: terminal, `closing_message`, multiple allowed.
- **Global**: no incoming connection, not part of the main flow, fires whenever
  `trigger_description` matches; action = end_call / transfer / continue (+ resume flag).
- Connecting: drag from a node's colored dot (one per transition/rule) to the next node.

We additionally keep our own pre-existing `transfer` node type (bot-to-bot handoff / squads-lite)
as a bonus 7th type — not part of the reference app but existing functionality we don't want to
delete.

**Explicit scope boundary**: this is the *visual flow editor* redesign only. The reference app
appears to actually execute the graph as a real multi-agent state machine at call time (each
Conversation node a distinct LLM turn, real rule evaluation, real webhook execution mid-call,
real Global interrupt handling). Our runtime still compiles the graph into prompt text
(`flow_compiler.py`, "Phase A" — model is instructed to follow it but can skip/reorder). Building
a true deterministic interpreter ("Phase B", already flagged in `flow_compiler.py`'s own
docstring) is a separate, much larger backend project and NOT part of this plan.

## Backend
- [x] `backend/models.py`: `FlowNode.type` Literal gains `"start"`, `"global"`.
  `FlowEdge` gains `source_handle: str = ""` (which named outcome/transition/rule an edge is
  attached to — purely an authoring/rendering detail, `flow_compiler.py` never reads it).
- [x] `flow_compiler.py`: `_node_content` updated per new node type field names, with fallback
  to legacy field names (`data.text`, `data.expression`, `data.tool_name`) so previously-saved
  flows still compile. Entry-point detection now prefers explicit `start`-type node(s) over the
  old "no incoming edges" heuristic.
- [ ] Run `USE_INMEMORY_DB=true uv run pytest backend/tests -q` — confirm `test_flow_preview.py`
  (legacy `data.text` shape) still passes unchanged, no other regressions.

## Frontend
- [x] `frontend/src/api.ts`: `FlowNodeType` adds `'start' | 'global'`; `FlowEdge` adds
  `source_handle?: string`.
- [x] `frontend/src/components/FlowGraphNode.tsx`: full rewrite.
  - `NODE_TYPE_META`: per-type color/icon/label (start=emerald/Play, message=blue/MessageSquare,
    condition=violet/Split, tool_call=amber/Zap "Function (API Call)", transfer=cyan/GitMerge,
    global=fuchsia/Radio, end=red/PhoneOff).
  - `hasTargetHandle(type)`: false for `start`/`global` (no incoming connection, per spec).
  - `nodeOutcomes(node)`: computes the named-outcome list per type (message → `data.transitions`
    or a default "Continue"; condition → `data.rules` + fixed trailing "Else"; tool_call →
    single "Continue"; global → single "Resume flow" only when `action==='continue'`; end →
    none). Single source of truth used by both the node card (rendering pills + one Handle per
    outcome, inline-positioned via `position:'relative'` style override) and
    `FlowBuilderView.toRfEdges` (label lookup).
  - Terminal (`end`) cards get red tint + "Terminal" tag; `global` cards get dashed border +
    "Global" tag.
- [x] `frontend/src/views/FlowBuilderView.tsx`: full rewrite.
  - New `.flow-toolbar` above the 3-column grid: agent name, Valid/N-issues badge
    (`validateFlow`: exactly one Start, at least one End Call), "Node guide" button (opens a
    `Dialog` reproducing the reference app's per-type explanations), "Test" button (calls new
    optional `onNavigateTest` prop), "Save" button (existing `onSave`/`saveState`).
  - Left palette (`FlowPalette`): draggable cards (grip-free, HTML5 DnD via
    `draggable`/`onDragStart` + `dataTransfer`) with icon/label/description per type, "click to
    add at centre, drag to position" hint; Start card disables once one exists.
  - Canvas drop target (`FlowCanvas` inner component, rendered inside `ReactFlowProvider` so it
    can call `useReactFlow().screenToFlowPosition`): `onDragOver`/`onDrop` computes flow-space
    drop position and calls `addNode(type, position)`.
  - `handleConnect` now threads `connection.sourceHandle` into the new edge's `source_handle`.
  - Right rail: "Flow overview" (per-type node counts + issues list / "No issues") when nothing
    selected; existing "Node inspector" pattern kept but extended with real per-type editors:
    Start (`first_message`), Conversation (`prompt` + add/remove/rename `transitions` list —
    removing one also strips any edge wired to that handle via `removeOutcome`), Condition
    (add/remove rules: variable/operator/value, value hidden when operator is `exists`),
    Function (`url`/`method`/`output_key`), Transfer (unchanged `target_bot_id`), Global
    (`trigger_description`/`action` select + conditional `transfer_number` or `resume`
    checkbox), End (`closing_message`).
- [x] `frontend/src/styles.css`: `.flow-toolbar`, `.flow-status-badge`, `.flow-palette-card`,
  `.flow-graph-node-outcomes`/`-outcome`, `.flow-graph-node-terminal`/`-global`/`-terminal-tag`.
- [x] `frontend/src/App.tsx`: wired `onNavigateTest={() => setView('test')}` into
  `<FlowBuilderView>` (prop already existed and is used this same way for `TranscriptsView`).
- [x] `frontend/src/views/FlowBuilderView.test.tsx`: existing 15 tests pass unchanged
  (click-to-add still works alongside drag-and-drop). No new type-specific tests added this
  pass — left as a follow-up if deeper coverage is wanted.

## Verification
- [x] Backend: `USE_INMEMORY_DB=true uv run pytest backend/tests -q` — 175 passed.
- [x] Frontend: `npx tsc --noEmit` clean; `npx vitest run` — 174/178 passed, the 4 failures are
  pre-existing/unrelated (`App.test.tsx` diagnostics-dismiss timing, `api.test.ts` fetch-timeout
  tests — neither file touched by this change).
- [x] Manual/browser: opened an agent's Flow tab, added Start/Conversation/End Call nodes via
  the palette, confirmed Valid/issues badge reacts correctly (1 issue while missing End Call →
  Valid once added), Start palette card disables after one exists, node inspector shows the
  right fields per type, outcome pill + connector dot renders on Start/Conversation, terminal
  red styling + "Terminal" tag renders on End Call, Node guide dialog renders reference-matched
  copy for all 7 types.

## Execution notes
- Durable progress file: this file — checkboxes updated as work lands.
- `transfer` node type kept as an additional 7th type (our own pre-existing bot-to-bot handoff
  feature), described separately in the Node guide dialog rather than folded into `global`.
