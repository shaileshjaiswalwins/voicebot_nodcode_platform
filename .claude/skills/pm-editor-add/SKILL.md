---
name: pm-editor-add
description: |
  Add a new PM-editable thing to the dashboard following the established
  pattern: Mongo collection + cached reader + CRUD endpoints + Pydantic
  schema + frontend type + Library tab + runtime read path. Use when the
  user says "let PMs edit X from the dashboard" or "make X editable
  without a code deploy".
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Grep
  - Glob
  - AskUserQuestion
triggers:
  - let pms edit
  - pm-editable
  - editable from the dashboard
  - editable without a code deploy
  - add a library editor
  - new editable thing
---

# Add a PM-editable thing to the dashboard

## When to use

The user wants to expose a piece of currently-hardcoded data so PMs can edit it from the dashboard. Examples that have already shipped (use these as references):

- `voicebot_platform/phrase_library.py` (voicemail / hold-music / DNC phrases)
- `voicebot_platform/outcome_catalog.py` (the 18 call-outcome descriptions)
- `voicebot_platform/option_catalogs.py` (voices and languages)
- `voicebot_platform/language_settings.py` (per-language timeout / style notes)

All four follow the same skeleton. This skill is that skeleton applied to a new field.

## Step 0 — clarify the data

Before writing code, lock these down with one AskUserQuestion if not obvious:

1. **Scope** — one shared list across all bots, or per-bot? Look at the existing modules:
   - Global → `phrase_library`, `outcome_catalog`, `option_catalogs`
   - Per-bot → store inside `tbl_ai_vb_bot_versions.config` instead and expose in the Builder

2. **Keys immutable?** — if the runtime references string keys (like `outcome_catalog.key` does in `callback.py`), lock the key from editing.

3. **Where the runtime reads it** — which file consumes the data today? That's where you'll swap the hardcoded list for a `get_X()` cached read.

## Step 1 — backend module

Create `voicebot_platform/<feature>.py` mirroring `phrase_library.py`. It must contain:

- `<FEATURE>_COLLECTION = "tbl_ai_vb_<feature>"` constant
- `DEFAULT_<FEATURE>: list[dict]` — the current hardcoded values, used to seed
- `seed_default_<feature>(user: str)` — idempotent: insert only if missing
- `list_<feature>()`, `create_<feature>(payload, user)`, `update_<feature>(id, payload, user)`, `delete_<feature>(id)` as needed
- `get_<feature>_for_runtime()` — **cached** reader with 60s TTL using `threading.Lock` (see `phrase_library.get_phrase_texts` for the pattern). Must fall back to `DEFAULT_<FEATURE>` if Mongo is unreachable.
- Validation in setters: length caps, allowed values

Validation rule of thumb: text fields ≤ 500 chars, descriptions ≤ 2000, prompts ≤ 8000, ids ≤ 80.

## Step 2 — Mongo index

In `voicebot_platform/mongo.py:ensure_indexes`, add:

```python
db["tbl_ai_vb_<feature>"].create_index([("<unique_key>", ASCENDING)], unique=True)
```

## Step 3 — Pydantic schema

In `voicebot_platform/schemas.py`, add:

```python
class <Feature>Payload(BaseModel):
    model_config = ConfigDict(extra="allow")
    # ... fields with Field(default=None, max_length=...)
```

Use `assert_object_id` for id params from URL path.

## Step 4 — API endpoints

In `voicebot_platform/api.py`:

1. Import `seed_default_<feature>`, `list_<feature>`, `create_<feature>`, `update_<feature>`, `delete_<feature>` from your new module.
2. Hook seed into the lifespan handler (alongside `seed_default_phrases`).
3. Add CRUD routes under `/api/library/<feature>` mirroring `phrases_create` / `phrases_update` / `phrases_delete` precisely.
4. Convert `KeyError` → 404 and `ValueError` → 400 with `HTTPException`.

## Step 5 — runtime read path

Swap the consumer. For analyzer data:

```python
# Before
_SIGNALS = ["a", "b", "c"]
# After
from voicebot_platform.<feature> import get_<feature>_for_runtime
_SIGNALS = get_<feature>_for_runtime()
```

The cached reader makes this safe to call per-request. **Don't** call it once at module load — that defeats the cache and freezes the data.

## Step 6 — frontend types

In `frontend/src/api.ts`:

```ts
export type <Feature>Entry = { _id?: string; ... };

export const api = {
  ...,
  <feature>: () => request<<Feature>Entry[]>('/api/library/<feature>'),
  create<Feature>: (p: Partial<<Feature>Entry>) =>
    request<<Feature>Entry>('/api/library/<feature>', { method: 'POST', body: JSON.stringify(p) }),
  // update / delete as needed
};
```

## Step 7 — frontend view

In `frontend/src/main.tsx`:

1. Add state: `const [<feature>, set<Feature>] = useState<<Feature>Entry[]>([]);`
2. Extend `refresh()`'s `Promise.allSettled` array to include `api.<feature>()`. Add a matching scope name and result handler. Cache via `cacheSet('<feature>', ...)`.
3. Add to the initial `cacheGet` block in the mount effect.
4. Add an action handler (`createX` / `updateX` / `deleteX`) that calls the API and reloads.
5. Add a tab to the `Library` view (the section selector inside `LibraryView`). Render a new component `<Feature>Catalog` following the shape of `OutcomeCatalog` or `LanguageSettingsCatalog`.
6. Wire props through `LibraryView`.

## Step 8 — tests

Add `tests/test_<feature>.py` mirroring `tests/test_phrase_library.py`:

- Seed is idempotent
- CRUD round-trip works
- Validation rejects bad inputs (over-length, missing required)
- Cached reader falls back to defaults when Mongo is empty

Run `uv run pytest tests/ -q` — must pass.

## Step 9 — verify end-to-end

```bash
# Backend imports cleanly
uv run python -c "from voicebot_platform import api, <feature>; print('OK')"

# Frontend TypeScript clean
cd frontend && npx tsc --noEmit

# Tests pass
uv run pytest tests/ -q
```

## Reference: lines of code per feature

For sizing expectations based on the four shipped examples:

| Feature | Backend (lines) | Frontend additions (lines) | Tests |
|---|---|---|---|
| phrase_library | ~200 | ~250 (incl. PhraseEditor) | ~70 |
| outcome_catalog | ~130 | ~120 (OutcomeCatalog) | ~50 |
| option_catalogs | ~200 (voices+languages) | API only so far | none yet |
| language_settings | ~170 | ~180 (LanguageSettingsCatalog) | none yet |

A new editor should land in roughly 2–3 hours including tests.

## Anti-patterns

- Don't put the reader call at module load — use the cached function in the request path so edits propagate within 60s.
- Don't allow editing the **key** field when downstream code uses string-keyed lookups (see how `outcome_catalog` exposes `key` as read-only and only lets PMs edit `description` / `display_label`).
- Don't skip the hardcoded fallback in `get_X_for_runtime`. If Mongo blips, the analyzer must keep working.
- Don't add the new collection to `mongo.ensure_indexes` *after* the seed has already run — index creation on a populated collection is more expensive.
- Don't add a new top-level dashboard view if it fits under Library. Library is the established home for PM-editable catalogs.
