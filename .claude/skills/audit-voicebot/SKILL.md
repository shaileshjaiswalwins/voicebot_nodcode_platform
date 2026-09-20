---
name: audit-voicebot
description: |
  Full senior-engineer audit of the voicebot_nodcode_platform branch. Reads the
  branch diff AND a fixed list of historically under-reviewed files (callback
  analyzer, ops, docs, build scripts), then produces a severity-tagged report
  plus a prioritized task list. Use when the user asks to "audit", "review the
  platform", or "look at the latest updates as a senior engineer".

  Distinct from /ultrareview (which caps at 4 hunk-level findings). This skill
  is the broad audit, not the pre-landing quick check.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - TaskCreate
  - TaskUpdate
  - AskUserQuestion
triggers:
  - audit my voicebot
  - audit the voicebot-nodcode-platform
  - senior engineer audit
  - review the latest updates as a senior engineer
  - conduct an audit
---

# Audit voicebot_nodcode_platform

## When to use this skill

The user opens a new session and asks for a full audit of the branch — typically with phrasing like *"checkout the latest updates… conduct an audit from a senior engineer perspective"*. They want a comprehensive review that produces a written report and an actionable task list, not a 4-finding hunk check.

If the user just wants a pre-landing quick review, use `/ultrareview` or the `review` skill instead.

## Defaults

- Effort: **high** (the user explicitly asks for Opus 4.6/4.7 high effort both times in history)
- Comparison: **branch vs `main`** unless they specify a different base
- Coverage tracking: **explicit list of what was NOT read** at the end of the report

## Phase 1 — discover the diff

```bash
cd /Users/acmecorp/voicebot_nodcode_platform
git status -uno
git rev-parse --abbrev-ref HEAD
BASE=${AUDIT_BASE:-main}
git diff --name-status "${BASE}...HEAD" 2>/dev/null
git log --oneline "${BASE}..HEAD" | head -30
```

Capture the changed-file list. This is the **primary read set**.

## Phase 2 — read the historically under-reviewed siblings

Past audits missed these on the first pass. Always include them in the read set even if not in the diff:

| Path | Why it gets missed |
|---|---|
| `callback_worker/analysis.py` | 566-line LLM rubric; not edited often, but critical to call-outcome correctness |
| `ops/docker-compose.observability.yml` | Image pins, env defaults, healthchecks |
| `start.sh`, `start_api.sh` | PID handling and dep sync — has bitten the user before |
| `pyproject.toml` | Dep upper bounds and `pymongo>=3.12,<4` lockdown |
| `docs/*.md` | Drift between docs and code (especially `platform_v1.md`, `runtime_config_schema.md`) |
| `.env.example` | What env vars are documented vs read by the code |
| `bot.py` near `_HARDCODED_BOT_CONFIG` and `entrypoint` | Config-fetch and save-call paths |

Read these in full. They're cheap and they're where the bugs hide.

## Phase 3 — analyze, with this severity rubric

Group findings into exactly four buckets. Keep each finding to one line: `path:line — what's wrong → concrete failure mode`.

1. **Critical (production blockers)** — async/sync I/O on the LiveKit event loop, missing auth, CORS misconfig, secret-in-URL, save-path crashes, data loss
2. **High** — track leaks, race conditions, broken cleanup, hard-coded production IPs, validation gaps that let bad data into Mongo
3. **Medium** — architecture smells, deprecated APIs, missing pagination, oversized files, schema drift
4. **Quick wins** — 5–30 minute fixes (CORS scope, build deps in wrong section, hardcoded UUID in defaults, cache versioning)

For each bucket, also note **what was done well** in 1–3 bullets. Past audits did this and the user found it useful for calibration.

## Phase 4 — produce a written report

The report must include:

1. Executive summary (3–4 sentences: what was built, the 3 most critical things to fix)
2. Findings by severity (the four buckets)
3. Architecture / maintainability notes (1 short section)
4. Security notes (1 short section)
5. **Prioritized fix list** as a table with: priority • file • effort estimate
6. **Coverage report** — explicit list of `<path>: reviewed | skipped because <reason>` for every directory and file you considered. The user asked "is there any part you have not reviewed?" both times — preempt this.

## Phase 5 — seed an actionable task list

Use `TaskCreate` to add one task per finding ranked P0–P3. Title in imperative form, description with the file and line. This lets the next session pick up via `TaskList` without re-reading the full report.

Skip task creation only if the user explicitly says "just the report, no tasks".

## Stopping conditions

- All changed files in the diff have been read in full (not just hunks).
- All Phase 2 sibling files have been read in full.
- The report is written with all six sections present.
- The coverage report names every directory at least once.

## Anti-patterns to avoid

- Do **not** invoke `/ultrareview` first hoping it'll be enough. History shows it isn't for this user's audit need.
- Do **not** ask the user to pick effort — default to high effort. They will downgrade if they want.
- Do **not** create new files in the repo as part of the audit. Audit is read-only unless the user says "fix it".
- Do **not** skim test or doc files because they're "boring" — `docs/*.md` and `tests/` are part of the contract.
