# J · Jev second opinion

_Component note for `callback_worker/jev_judge.py`. Written in the same shape as the atlas deep dives: what it does, how it is built, what it costs, and what is still unanswered._

**Status:** shipped behind a flag, default off. Live-verified on one Hindi transcript. Not yet calibrated.

---

## In one line

A second, cheaper judge that re-reads the call after Gemini has, returns a probability instead of a sentence, and turns the Approved/Enriched line into arithmetic.

## Why it exists

The call outcome is the single commercially meaningful field this system produces — it decides whether a lead is worth money. Three things were true about it before this change:

1. **It had no confidence attached.** One Gemini call returned a string. Nothing downstream could tell a call it was sure about from one it guessed at.
2. **The same rule was written four times.** Post-processing rules 2a, 2b, 2c and 2d in `analysis.py` each recount how many schema questions were genuinely answered, and each re-derives the outcome from that count — because the model miscounts it. Four hand-written repairs for one rule.
3. **Drift auditing cost N× and ran by hand.** `audit_outcome_drift.py` re-runs the whole analysis several times per call and takes a majority vote, because a majority vote was the only way to get a confidence signal out of a model that does not emit one.

A model that returns a probability makes (1) free, makes (2) a `len()`, and makes (3) a single call.

## What it does

Gemini keeps everything only a generative model can do — the call summary, the business fields, and pulling each answer out of the transcript. Jev answers two things Gemini is bad at:

- **Which of the 19 dispositions was this call?** A `Choice` over `DISPOSITION_MAP`, returning the outcome, a probability for every outcome, and a confidence.
- **For each schema question, did the buyer actually answer it?** One `Noul` each. Python counts the ones over the threshold, and that count is the Approved/Enriched line.

## How it's built

`callback_worker/jev_judge.py`, called from the last four lines of `generate_call_analysis` — after every existing deterministic repair, so the Gemini path is byte-identical when the flag is off.

One `system_one` request carries the disposition `Choice`, one `Noul` per schema question, and one B2B `Noul`. The transcript and the muted-window transcript go in as state. The 19 outcome descriptions are passed in as the `Choice` criteria, so the answer space *is* `DISPOSITION_MAP` — an invalid outcome is not representable, which removes the `_VALID_OUTCOMES` fallback path entirely for this judge.

`DISPOSITION_MAP` is passed in as an argument rather than imported, because `analysis.py` imports `jev_judge` and the reverse import would be a cycle.

### Modes

| `JEV_JUDGE_MODE` | Behaviour |
|---|---|
| `off` (default) | Never called. Zero change to the existing pipeline. |
| `shadow` | Called. Writes a `jev` block onto the analysis. Changes no outcome, ever. |
| `enforce` | May correct the outcome — but only the count-derived tier, and only inside `QUALIFICATION_FAMILY`. |

`enforce` cannot touch Abusive Lead, DNC, Voicemail, Wrong Number, Seller Intent, Job Seeker or Technical Issue. Those are either set by a deterministic guard before any model runs, or carry a consequence too heavy to hand to a probability. The 19-way `Choice` itself stays **advisory in every mode** until there is calibration data — only the arithmetic tier is enforced.

### Steps in execution

1. **Gemini first** — the existing analysis runs unchanged, including all post-processing repairs.
2. **Ask** — one `system_one` call: 1 `Choice` + 1 `Noul` per question + 1 B2B `Noul`.
3. **Count** — questions whose `Noul` clears `JEV_ANSWERED_GATE` (default 0.6) are answered.
4. **Derive** — all answered → Approved; some → Enriched; none → no opinion, because Interested vs Not Interested vs Could Not Confirm turns on intent, not on a count.
5. **Record** — a small `jev` block goes onto the document: outcome, confidence, top 3 probabilities, per-question values, agreement with Gemini, and why a human might want to look.
6. **Correct, or not** — only in `enforce`, only the tier, only inside the family.

A failed Jev call returns `None` and the worker continues on Gemini's result. A judge outage must never stop lead callbacks.

## Decisions locked

| Axis | Decision | Reason |
|---|---|---|
| Scope | Additive, not a replacement | Jev returns no text. The summary, business name/city, and answer extraction still need a generative model — there is nothing to replace them with |
| Default | `off` | This field decides lead value. A new judge earns its way in through shadow data, not through a deploy |
| Enforcement | Tier only, family only | The count is arithmetic over independent yes/no answers. The 19-way choice is a model opinion, and is not enforced until measured |
| Failure | Fail open | A callback that does not go out costs a real lead. A missing second opinion costs nothing |
| Placement | After all existing post-processing | Makes the flag-off path provably identical, and makes the shadow data a fair comparison against what production actually ships today |
| Coupling | Disposition map passed in, not imported | `analysis.py` imports `jev_judge`; importing back would be a cycle |
| Thresholds | Named constants, env-overridable, documented as guesses | 0.6 and 0.55 are starting points, not findings |

## Measured

One live run, against a transcript where the buyer gives a quantity, answers "मुझे ठीक से पता नहीं" to the material question, then changes the subject instead of naming a city.

| Signal | Value | Correct? |
|---|---|---|
| Outcome | `Enriched`, confidence 0.82 | Yes — two of three answered, so not Approved |
| Runner-up outcomes | Interested 0.08, Could Not Confirm 0.07 | Plausible neighbours, which is what a usable distribution looks like |
| q1 quantity ("दो हज़ार पीस") | 0.95 answered | Yes |
| q2 material (honest "not sure") | 0.88 answered | Yes — see the finding below |
| q3 city (subject changed) | 0.05 answered | Yes |
| B2B | 0.71 | Plausible; unverified |
| Latency | 1,328 ms | One call, 21 questions |

**Sample size is one transcript plus 20 unit tests.** That is enough to say the mechanism works and not enough to say the judgement is good. Shadow mode exists to close that gap.

## Finding: the rubric and the code disagree about "not sure"

The disposition rubric states that for the Approved / Enriched / Interested distinction, **an honest "not sure" counts as answered**. Post-processing rules 2a–2d test the opposite:

```python
(e.get("answ") or "").strip().lower() not in ("not sure", "")
```

So a buyer who honestly says they do not know the material is counted as not having answered, and a lead that the rubric grades Enriched can be graded lower. Jev returned 0.88 on exactly that question, because the instruction says so once, in one place, instead of four times in four repair blocks.

This is a pre-existing production behaviour, not something the Jev work introduced. It is written up here rather than silently fixed, because which side is right is a product decision, not an engineering one — see **Q-J3**.

## Tradeoffs

Stated plainly, including the ones that do not flatter the change.

- **It adds a call, it does not remove one.** Gemini still runs. Input is $0.042/MTok and output is free, so a ~1.5k-token transcript costs roughly $0.00006 — but the honest framing is "a second opinion for a rounding error", not "a cost saving". The saving is elsewhere: drift auditing stops needing N re-runs per call.
- **It adds latency to the worker tick.** Q-W1 already asks whether 50 documents × 2 sequential LLM calls fits in a 60-second poll. This makes it 3 calls. ~1.3s × 50 is ~65s of added wall clock in the worst case, which is over the interval on its own. See **Q-J2**.
- **The thresholds are guesses.** 0.6 for answered and 0.55 for confidence are starting points chosen by hand. Until they are fitted against labelled calls, `enforce` is not justified by anything.
- **A closed answer space cuts both ways.** A `Choice` cannot return an invalid outcome, which removes a real failure mode. It also means a genuinely novel call gets pushed into the nearest of 19 buckets rather than flagged as unmapped — the confidence value is the only signal that happened.
- **Agreement is not correctness.** A high agreement rate between Jev and Gemini means the two are consistent, not that either is right. Only human-labelled calls settle that.
- **One more vendor in the path of a revenue-critical field.** Mitigated by fail-open and by `off` being the default, but it is a real dependency.
- **Hindi is verified, not benchmarked.** One code-mixed Devanagari transcript worked well. That is not a measurement across dialects, Latin-script Hindi, or the regional greetings the `_BARE_CALL_SIGNAL_TOKENS` set exists to catch.

## Questions

Open, in the convention used by the rest of this repo's docs.

- **Q-J1** The answered gate (0.6) and confidence gate (0.55) are hand-picked. What labelled set do we fit them against, and who owns re-fitting them when the prompt or the model version changes?
- **Q-J2** Adding a third model call per document makes the worst-case tick longer, against an already-open Q-W1. Do we parallelise the Jev call with the Gemini call, move it out of the tick entirely, or raise the poll interval?
- **Q-J3** The rubric says an honest "not sure" counts as answered; rules 2a–2d say it does not. Which is the intended product behaviour, and does correcting it change historical outcome mix enough to matter to reporting?
- **Q-J4** `needs_review` is computed and stored, but nothing reads it. Does it become an alert rule, a dashboard filter, or a queue someone works?
- **Q-J5** The 19-way disposition `Choice` is advisory in every mode. What agreement rate, over how many calls, justifies promoting it to enforced?
- **Q-J6** Does the B2B `Noul` agree with `generate_b2b_score`'s existing `b2b_user` field often enough to replace it, or is it a second signal to keep alongside?
- **Q-J7** `audit_outcome_drift.py` still majority-votes across N re-runs. Once shadow data exists, is the stored confidence a good enough drift signal to retire that script?

## Questions an interviewer would reasonably ask

Written down because the honest answers are the interesting part of this work.

**"Why not just replace Gemini with Jev and save the call?"**
Jev returns typed judgements and probabilities. It does not generate text. The call summary, the business name and city, and the extraction of each answer out of a Hindi transcript are all generative work. Replacing Gemini would mean dropping fields the callback payload depends on.

**"Why is the default off? Isn't that just shipping dead code?"**
This field decides what a lead is worth to the business. The shadow mode is not caution theatre — it produces the agreement data that is the only argument for turning enforcement on. Shipping it on by default would mean enforcing thresholds nobody has validated.

**"You enforce the tier but not the outcome. Why the asymmetry?"**
The tier is arithmetic: count the questions whose independent yes/no answer cleared a threshold, compare to the total. I can explain any tier decision by pointing at the three numbers that produced it. The 19-way disposition choice is a single model judgement over a large answer space, and I have no calibration data for it. Enforcing the part I can audit and not the part I cannot is the whole design.

**"What happens when the Jev API goes down at 3am?"**
`judge()` catches everything, logs a warning, and returns `None`. `reconcile()` returns the result untouched. The worker tags the document and sends the callback exactly as it did before. The test for this is `test_a_failed_judge_call_changes_nothing`.

**"How do you know it works? One transcript is not evidence."**
It is not. One transcript plus 20 unit tests shows the mechanism is correct — the right questions get asked, the count is right, the mode gates hold, terminal outcomes are untouchable. Whether the judgement is *good* is unanswered, and shadow mode is the instrument for answering it. I would rather say that than quote a number I did not measure.

**"What did you find that you weren't looking for?"**
The "not sure" contradiction. I only saw it because a probability of 0.88 on a question the existing code scores as zero is a visible disagreement, where two strings that both say "Enriched" are not. That is the argument for confidence-bearing outputs in one sentence.

**"What would you do next?"**
Shadow for a week, fit the two thresholds against hand-labelled calls, answer Q-J3 with the product owner, then decide on enforcement. And measure Q-J2 before any of it, because a 60-second poll interval with three model calls per document is an arithmetic problem, not an opinion.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `JEV_JUDGE_MODE` | `off` | `off`, `shadow`, or `enforce` |
| `TYPESAFE_API_KEY` | — | Required for any mode but `off`; absent means `off`, with a warning |
| `JEV_ANSWERED_GATE` | `0.6` | A question counts as answered at or above this probability |
| `JEV_OUTCOME_CONFIDENCE_GATE` | `0.55` | Below this, the call is flagged for review |

## Tests

`callback_worker/test_jev_judge.py`, 20 tests, no network. They pin the mode gate, the question construction, the count that replaces rules 2a–2d, the shadow/enforce boundary, the terminal-outcome protection, and the fail-open path.

```bash
pytest callback_worker/test_jev_judge.py -q
```

## How this file is maintained

Update it when a threshold moves, a mode default changes, or a question above gets answered — and strike through answered questions rather than deleting them, so the reasoning stays legible.
