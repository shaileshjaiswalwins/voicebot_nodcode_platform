# Inbound number → bot routing, and the persona template

Status: **proposed, not implemented.** Nothing in this document is built yet.

## The goal

A PM creates a voice agent in the dashboard (persona name "Tarun", an organization, an
opening line), assigns a phone number to it in the UI, dials that number, and hears Tarun
answer with that bot's configuration. Reassigning the number to a different bot takes
effect on the next call, with no deploy and no LiveKit change.

None of that works today. This document explains why and proposes the fix.

---

## The problem

### 1. Creating a bot has no runtime effect whatsoever

`create_bot` (`backend/routers/bots.py:55`) inserts documents into `tbl_ai_vb_bots` and
`tbl_ai_vb_bot_versions`. No worker ever reads them.

The chain breaks in three independent places, any one of which is sufficient on its own:

- **`fetch_bot_config` (`bot.py:618`) ignores its argument** and returns
  `_HARDCODED_BOT_CONFIG` unconditionally.
- **The room-metadata key doesn't match.** `testcall.py:34` writes `bot_id`;
  `bot_dev_param.py:335` reads `assistant_id`. So `_assistant_id` is always `""`, the
  `if _assistant_id` guard fails, and line 337 falls through to the hardcoded config.
  `test_bot_version_id` is threaded all the way into room metadata and then read by nobody.
- **On an inbound SIP call there is no metadata at all.** The dispatch rules pass
  `Attributes: map[]`.

### 2. Assigning a number to a bot is a database write and nothing else

`reassign_phone_number` (`backend/routers/phone_numbers.py:98`) sets `assigned_bot_id` and
`assigned_bot_version_id` in Mongo. Its own docstring states the gap:

> "Wiring the actual dispatch-time read (deciding what bot_id goes into LiveKit
> room-dispatch metadata) is future work, out of scope here."

Nothing reads those fields at call time.

### 3. Nothing in the repo manages SIP dispatch rules

Grepping for `create_sip_dispatch`, `SIPDispatchRule`, `SIPInboundTrunk` across every
Python file returns zero hits. The existing dispatch rules were created by hand with the
`lk` CLI. Creating a bot in the UI never creates a rule, a trunk, or a worker.

### 4. Two unrelated things are both called `agent_name`

This is the most important confusion to clear up, because it invalidates the obvious design.

- **`WorkerOptions(agent_name="voice-bot-justdial-fallback")`** (`bot_dev.py:2365`,
  `bot_dev_param.py:2207`, `bot_pipeline.py:2378`, `bot.py:3845`) is a LiveKit *routing
  label for an OS process*. It is read once at process start, inside
  `if __name__ == "__main__"`, alongside a hardcoded port. It never reaches the LLM.
- **`BotConfig.agent_name`** (`backend/models.py:74`) is the *persona name* — "Tarun" — the
  thing that produces "hi, my name is Tarun" via the `{agent_name}` placeholder in
  `initial_message`.

Setting the WorkerOptions name to "Tarun" yields a worker registered as "Tarun" that still
introduces itself as Simran.

**Why "one agent name per bot" cannot work:** that value is baked into a running process
with its own port. One agent name per bot means one always-on OS process per bot, each on
its own port, plus a dispatch rule per number. Creating a bot in the UI is a Mongo insert —
it cannot spawn a process or claim a port. Every new bot would need an ops deploy before
its number worked.

Note also that dispatch rules are scoped to a **trunk**, not a number. Both existing rules
cover an entire trunk each.

### 5. The "Tarun" visible on a test call is a client-side preview

`TestCallPanel.tsx:293` renders `<PromptPreview>`, which (`BuilderView.tsx:47-53`) performs
the substitution **in React**, reading `version.config` from the API:

```js
const opening = rawOpening
  .replace('{product}', srchterm || '<product>')
  .replace('{agent_name}', cfg.agent_name || '<agent_name>')
  .replace('{organization_name}', cfg.organization_name || '<org_name>');
```

It never leaves the frontend. On a test call you read Tarun on screen while Simran talks in
your ear. That same component already warns about it (`BuilderView.tsx:60`):
`'agent_name not set — bot may use hardcoded persona'`.

### 6. The New voice agent form has no concept of the system prompt — and this is the trap

`App.tsx:523-529` builds the config as `{...defaultConfig, agent_name, organization_name,
language, initial_message}`. Four fields from the form; everything else from `defaultConfig`.

The form never collects a system prompt, but `defaultConfig` supplies one
(`frontend/src/constants/ui.tsx:27`):

> "You are a warm and professional call center agent. Greet the caller, understand their
> requirement, and collect key details."

25 words. Meanwhile `_HARDCODED_BOT_CONFIG["system_prompt"]` (`bot.py:299`+) is ~300 lines
of real prompt engineering: the gender rule, the five fixed business rules (never name
brands, one question per response, max 2 asks per question), price-question handling,
"are you a bot" handling, language switching, filler rules, number and decimal reading.

And `build_system_prompt` (`bot.py:985`) does:

```python
if _bc.get("system_prompt"):
    base_prompt = _bc["system_prompt"]
```

The one-liner is truthy, so **it wins**. The moment `fetch_bot_config` starts returning a
form-created bot's config, 300 lines of guardrails are replaced by 25 words. Tarun would
greet perfectly and then quote prices, name brands, ask three questions at once, and admit
to being an AI.

**Fixing the routing without fixing this makes the bot dramatically worse, and it will look
like the routing broke it.** This is the reason the phases below are ordered as they are.

### 7. Persona fields are dead, and the default opening line is gendered

| Form field | Reality today |
|---|---|
| Display name | UI only — never reaches the worker |
| Description | UI only |
| Agent persona name | Stored; read by nobody |
| Organization name | Stored; read by nobody |
| Language | Decorative — `bot_dev_param.py:359` hardcodes `HINDI_LANG_CONFIG` |
| Opening line | Stored; `initial_message` is read by no worker (defined at `bot.py:541`, never consumed) |

`build_system_prompt` substitutes only `{script_rule}` and `{language_name}`, so
`{agent_name}` would reach the LLM as literal text — exactly what `backend/evals.py:15`
guards against with `must_not_contain=["{agent_name}", "{organization_name}"]`.

**The gender bug:** the form's placeholder suggests *"e.g. Tarun, Priya, Aman"* — two of
three are male names. But the default opening line is `मैं {agent_name} बोल रही हूँ`
(**feminine**), and the base prompt hard-rules *"Simran is female. Every first-person verb
and adjective MUST use feminine forms."* There is no gender field. "Tarun" introduces
himself as a woman and stays feminine for the entire call.

---

## The proposed solution

**Principle: LiveKit routes a call to a *pool*. Mongo decides *which bot* answers.**

One agent name per **environment**, not per bot. The two existing dispatch rules become two
pools (`voice-bot-justdial-live-1`, `voice-bot-justdial-fallback`). No rule, trunk, or
process is ever created when a PM makes a bot. The per-bot decision happens in Mongo, at
call time, keyed on the dialed number.

The data layer for this already exists and needs no schema change: `tbl_ai_vb_phone_numbers`
has `number` (the DNI — exactly what LiveKit hands the worker as `sip.trunkPhoneNumber`),
`environment` (`dev`/`preprod`/`prod`), and `assigned_bot_id`. The UI to assign a number is
already built (`PUT /api/phone-numbers/{id}/reassign`).

### Target call flow

1. Call hits a number on trunk `ST_LDzA29BwCipF`.
2. The dispatch rule fires; LiveKit hands the job to any free `voice-bot-justdial-live-1`
   worker.
3. The worker reads the dialed number from the participant's `sip.trunkPhoneNumber`.
4. The worker looks that number up in `phone_numbers` → resolves the bot → loads that
   version's config from `bot_versions`.
5. The worker renders its prompt from that config and greets as Tarun.

Steps 3 and 4 are the entire feature.

---

### Phase 1 — Persona template (verifiable without placing a phone call)

1. **Extract the prompt into a template.** Move `_HARDCODED_BOT_CONFIG["system_prompt"]`
   (`bot.py:299`) into a root-level `persona_template.py`, importable by both `bot.py` and
   the backend via the defensive-import pattern already used at
   `backend/routers/bots.py:23` for `flow_compiler`. Every hardcoded identity becomes a slot:
   - `"You are Simran, ... calling from Justdial"` → `{agent_name}`, `{organization_name}`
   - the `GENDER — HARD RULE` block → `{gender_rule}`
   - `"जी, मैं Simran बोल रही हूँ Justdial से"` → needs a `{speaking_verb}` slot
     (`बोल रही हूँ` / `बोल रहा हूँ`); the feminine conjugation is baked inline into the
     Hindi example lines, not confined to the gender section

   This is the fiddliest step — it is prompt surgery, and the gendered verbs are scattered.

2. **Add `persona_gender`** to `BotConfig` (`backend/models.py`) as
   `Literal["female", "male"] = "female"`. Not optional: the docstring at `models.py:63`
   warns that Pydantic silently drops any field not declared there, which has bitten this
   codebase before. Mirror in `frontend/src/types.ts` and add the control to the form.

3. **Seed the template server-side.** In `create_bot`, when `payload.config.system_prompt`
   is empty, fill it from the template. Set `defaultConfig.system_prompt`
   (`constants/ui.tsx:27`) to `''` so the one-liner stops clobbering. Server-side seeding
   avoids a 300-line prompt duplicated across TS and Python and drifting.

4. **Render the slots at runtime.** Extend `build_system_prompt` — which already replaces
   `{script_rule}` and `{language_name}` — to also handle `{agent_name}`,
   `{organization_name}`, `{gender_rule}`, `{speaking_verb}`.

5. **Make `initial_message` live.** Wire the opening line into the greeting path with
   `{product}` / `{agent_name}` / `{organization_name}` substituted, and flip the default
   line's verb on `persona_gender`.

**Verify:** create a male bot named Tarun, run a test call. He greets as Tarun in masculine
Hindi *and* still refuses to quote prices or name brands. The second half is the real check
— it proves the guardrails survived the extraction.

### Phase 2 — Route the number to the bot

6. **Give the worker a platform-DB handle.** Its Mongo points at `ai_lead_qualify` (live
   prod; `backend/db.py:10` says do not read/write it from this codebase). The bots live in
   `ai_voice_bot_management`. Add a second read-only client behind a `VOICEBOT_PLATFORM_DB`
   env var.

7. **Move the SIP read earlier.** `bot_dev_param.py` reads the dialed number at step 11
   (~line 2056) but chooses its config at line 335 — the data arrives after the decision
   that needs it. Move that block, and the `sip_info` dict, to just after
   `wait_for_participant()` (line 332). Pure reordering, no new logic.

8. **Make `fetch_bot_config` a real resolver** (`bot.py:618`), with precedence:
   1. version pinned in room metadata (test call) → use it
   2. dialed number → `phone_numbers` → the bot's config
   3. `_HARDCODED_BOT_CONFIG`

   Keep fallback 3. Unassigned numbers then behave exactly as they do today, so this ships
   without a flag day.

**Verify:** assign a number to Tarun in the UI, dial it, hear Tarun. Reassign to Priya, dial
again, hear Priya — no restart, no LiveKit change.

### Phase 3 — Parity and cleanup

9. **Fix the test-call key mismatch** (`bot_id` vs `assistant_id`) so the test panel
   exercises the same resolution path as a real call, and what you hear matches the preview.

10. **Decide on `bot_dev.py`** — the `fallback` pool on trunk `ST_9yF7oqF4WQHU` is a separate
    file and inherits none of this. Either point both dispatch rules at
    `voice-bot-justdial-live-1`, or port the resolution across.

11. **The Language dropdown** is decorative (`bot_dev_param.py:359`). Either wire it or
    remove it; the form's own helper text already admits it does nothing.

---

## Open decisions

**Version resolution.** `reassign` stores both `assigned_bot_id` and
`assigned_bot_version_id`. At call time, resolve the bot's current `active_version_id`
fresh (publish → live on the next call), or use the pinned version (frozen until someone
reassigns)? *Recommendation: fresh.* It makes "publish" mean publish, and matches what
`reassign_phone_number`'s docstring asks for — read at dispatch time, never cached.

**Template seeding vs. server-side base.** Seeding snapshots the prompt per bot, so later
template improvements do not reach existing bots (re-seed or hand-edit). The alternative
keeps the template server-side as a base and treats `config.system_prompt` as an override:
bots inherit improvements for free, but the PM cannot see what actually runs.
*Recommendation: seeding.* A PM editing Tarun should not silently change Priya, and
per-bot versioning is precisely what `bot_versions` exists for.

---

## Why the phase order is not negotiable

Phase 2 without Phase 1 is a regression. The moment the worker starts honouring a
form-created bot's config, that bot's 25-word `defaultConfig.system_prompt` replaces the
300-line prompt, and every guardrail disappears on live calls.
