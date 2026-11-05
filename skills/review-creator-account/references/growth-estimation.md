# Conditional growth calculations

Use `<skill directory>/scripts/estimate_growth.py` for a growth estimate, including a follow-up to a complete review. It has no network dependencies. Read actual persisted evidence or a user export; never manufacture observations. JSON retains the evidence rows; a Markdown handoff is saved beside it and printed. `computed` means arithmetic succeeded, not that future performance is known.

## Decide whether the requested forecast is identifiable

First resolve the target from the immediately preceding request and reply. For an existing post,
a single lifetime view/like observation alone does not define its next-month or next-year trajectory.
If that is all the evidence and the user supplied no explicit future rate, correct earlier invented odds, ask for dated observations of that
post, and stop the numeric forecast. Do not substitute a channel-content scenario to fill the
requested monthly table. If the user switches targets, assess the new target separately.

A historical per-post median times a posting cadence estimates a batch at the sampled posts'
observed ages. It is not a monthly deadline series, and adding it repeatedly to today's account
stock does not make it one. Keep this distinction even under a "conditional" label. Engagement
combining likes and comments is not a like rate; never use it to compute future likes.

For the missing-input branch, the reply has two parts only: a brief correction of the unsupported
earlier estimate and a concrete request for the dated records or explicit assumptions needed.
End there; do not append a table of unknowns, a different-target forecast, or an offer to produce
percentage odds later. Once inputs arrive, the supported calculation path below is available.

## Require source rows, not reconstructed summaries

A prior report's median, quartiles or posting pace is useful context, but does not provide the
raw dated observations required by the calculation contracts below. If those rows or the saved
snapshot are missing, request them. Do not reconstruct a fictional sample from aggregates, or
replace `estimate_growth.py` with mental arithmetic or a short custom calculator to bypass its
input requirements. A summary is not a validated calculation result either.

Resolve the continuation's referent before selecting a calculation. If "the same" could mean an
account, new posts or a named existing post, ask which one instead of filling all three with
numbers. A content batch at historical observation ages answers a different question from a
calendar-month trajectory; only offer that alternative with its changed measurement explicit,
and do not produce its table until the user chooses that alternative.

## Before answering a forecast or extending one

Resolve the account versus a particular post, the platform, metric definition (net additions,
cumulative stock, period total or new-content performance), baseline date and requested horizon.
Use clear prior context; clarify only unresolved scope. Do not silently switch between a top
post and the whole account when a follow-up asks for the same calculation over more months.

Audit each input's source. A previous assistant's outcome range, multiplier, conversion rate or
"most likely" number is not an observation or a user-supplied assumption, even if the user asks
to extend it. Correct that earlier overstatement explicitly before reusing the genuine baseline.
A single public snapshot cannot supply a post's growth trajectory, subscriber conversion or
monthly account-wide totals. Keep missing subscriber history separate from available content data.

This Skill computes conditional scenarios, not calibrated probabilities. Neither subjective
labels nor a disclaimer make invented percentages reliable. When asked for odds without an
appropriate validated predictive model, explain that no defensible percentage is available here;
offer supported conditional calculations or ask for the minimum missing dated records instead.
Do not label a scenario "most likely", assign scenario weights or create confidence intervals
from a median, quartiles, a single snapshot or intuition. Do not promise that a few more snapshots will make percentage odds available: two dated points
can enable unchanged-pace arithmetic, not a calibrated predictive model. When requesting data,
say what conditional calculation it enables and what will remain unknown.
Calibration would require a defined
outcome and horizon plus evaluation of predicted probabilities against held-out comparable
outcomes; these calculations do not provide that evidence.

For multi-month requests, keep every month on the same explicit measurement and assumptions.
Show monthly values only when the supported calculation can produce them; never invent a decay,
acceleration or breakout curve to fill a table. State the observed window alongside the requested
horizon, explain which assumptions become less dependable farther out, and set a review point
when new dated observations arrive. Do not multiply a new-content scenario into an account total
or the future cumulative total of one existing post. If inputs are insufficient, identify the
missing series and provide the collection/calculation plan rather than a numeric forecast.

When the evidence and assumptions are sufficient, still answer the supported numeric part with
the script: do not turn this boundary into a blanket refusal. Put the formula, changed assumption
and uncertainty next to each conditional result. Controllable goals and experiments are plans,
not promises about what the platform will distribute.

For a public review, `drive.py growth` replaces the two-step request below: run it in the review's run directory, choose one listed choice, save it as `review_growth_input.json` with the user's number and wording filled in, and run it again before writing the synthesis. `--metric` and `--horizon-days` follow the user's question (views, 30 days by default). Only `content_scenario` belongs in that file. `drive.py report` binds it to `snapshot.json`, `account_analysis.json` and the retained `question.txt`, recomputes the estimate, and includes the number, formula, observation ages, deadline comparison and sensitivity meaning in the validated `ACCOUNT_REVIEW.md`. The normal creator-report handoff carries the complete answer; no separate model-written growth summary is needed. Outside a review run, dated stock or standalone calculations keep the direct interface below.

## Choose the supported output path

A growth-only request or follow-up uses the standalone `estimate_growth.py` interface and its
printed Markdown handoff. It can calculate `observed_pace` or `assumed_pace` from their dated
inputs, including one dated baseline plus an explicit user-supplied future rate. The latter is a
conditional assumption, not a measured trajectory or probability.

Inside a complete public review, `drive.py growth` and the composed `ACCOUNT_REVIEW.md` currently
support only `content_scenario`. Do not pass stock-pace inputs into that report contract or claim
they are included. Use the separate standalone calculation for a growth-only follow-up instead.
This clarification does not extend the public-review schema or hide unsupported coverage.

## Evidence → choice → answer

For a public snapshot assessment, use top-level `schema_version`, `scope`, positive `horizon_days`, and `metrics` drawn from subscribers/followers/views/likes/comments. Only `platform` and `account` belong in `scope`; replace the example account with the snapshot's identity:

```json
{"schema_version":"growth-review.v1","scope":{"platform":"youtube","account":"creator"},"horizon_days":30,"metrics":["views"]}
```

Run:

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/estimate_growth.py' request.json --assess --snapshot snapshot.json --output growth_evidence.json
```

The script binds actual snapshot rows, groups formats, retains missingness/coverage and sample ages, and calculates the conditional median-performance scenario at captured publication pace for each supported content metric. It marks stock-growth inputs separately. These continuation assumptions come from the evidence; the user need not invent a publishing count. Select the relevant format and explain why its observed performance is a useful reference. All captured items are a transparent default, not proof of a representative or age-matched sample. If a narrower comparison is justified, use the direct method below and explain the selection.

In the answer, keep the number's measurement and deadline comparison together, so later turns can reuse the result without changing its meaning. For content scenarios, `estimate.time_basis` separates the publication window from the actual sampled content ages. Start with the supported comparison: the planned batch matching historical per-item performance at those ages. The deadline total itself is not identified: staggered publication dates give different ages on the deadline, and one cumulative snapshot supplies no age-performance trajectory. Its direction is `lower` when the samples' median observed age exceeds the publication window, since no item published inside the window can be older than the window at its end; say it is usually lower, without a number, probability or size of the gap. Otherwise the direction is `not_identified`; say it may be higher or lower. A follow-up about the deadline uses this field, not a new judgement. Dated trajectories and a publication schedule would support a separate deadline calculation.

If subscriber history is absent, ask for dated net gains; other requested metrics can still be answered. Correct unsupported earlier assistant numbers rather than adopting them as evidence. Targets may use historical performance with an explicit rationale, but neither a target nor user pressure supplies evidence for attainability or likelihood.
For dated stock records or an explicit user rate/count, calculate directly. `needs_input` identifies the minimum missing inputs. Do not translate a scenario into odds, manufacture a conversion rate, or add an uncomputed outcome range.

For a rough range, use the calculated sensitivity choices: the same captured cadence, with each new item's performance set to the observed lower quartile, median, or upper quartile. Explain the changed per-item assumption; these are neither the batch's predicted quantiles nor bounds covering a known probability. A prior assistant's uncalculated range is not another scenario. Keep subscriber/follower gaps separate rather than assigning conversion numbers to complete a table.

Direct calculation inputs use top-level `schema_version: growth-input.v1`, positive `horizon_days`, and `scope: {platform, account, metric, semantics}`. Each row carries the same scope and a `source` pointing to its persisted evidence or the supplied record. Timestamps include a timezone. Missing values are null, observed zero remains zero. Never mix accounts or metric definitions; window totals are not cumulative stock observations. Public totals do not measure private conversion.

Choose one method:

- `observed_pace`: for dated follower/subscriber or other cumulative stock observations. `scope.semantics` is `cumulative_stock`; `observations` contain `observed_at`, `value`, `source`, `scope`. The script uses first-to-last net change divided by actual elapsed days and exposes intermediate interval rates. Explain reversals or short/old windows before interpreting the unchanged-pace scenario; missing dates are not zero. Mock records are not real observations.
- `assumed_pace`: same observation fields, at least one dated baseline, plus `assumption: {amount, period: "day" | "week", origin: "user", quote}`. Use only an assumption explicitly supplied by the user. A cadence or scenario name is not a rate. Negative net change is allowed; linear values crossing zero are flagged.
- `content_scenario`: for views/likes/comments, with semantics `per_content_cumulative`. `samples` contain `content_id`, `published_at`, `observed_at`, `format`, `value`, `source`, `scope`. Supply `cohort: {format, age_min_days, age_max_days}` appropriate to the user's comparison; use the same account and keep the chosen age band visible. The script filters format and observation age, preserves missingness and multiplies the available sample median by a publication plan: `plan: {posts, origin: "user", quote, binding}` for an explicit total count; add `period: "day" | "week"` when `posts` is the user's count per period. Interpret the user's language into the source-bound contract below: the script derives the equivalent item count as `posts × horizon_days / period_days`. Keep a fractional equivalent count as a rate scenario, not a rounded calendar schedule. Or use `plan: {origin: "observed_cadence"}` with `--snapshot` to continue the captured pace. The latter derives `(captured same-format posts - 1) / elapsed publication days` from at least two dated posts; do not supply `_snapshot_cadence`. Report the actual observed ages returned by the script, not the broader selection band. This is a conditional total for new content at that observation age, not account-wide growth, a confidence interval or a guaranteed total by the plan's end. Do not use it for subscribers. A tiny or selective sample can be computed but needs that limitation in the interpretation.

Example dated input:

```json
{"schema_version":"growth-input.v1","mode":"observed_pace","scope":{"platform":"youtube","account":"creator","metric":"subscribers","semantics":"cumulative_stock"},"horizon_days":14,"observations":[{"scope":{"platform":"youtube","account":"creator","metric":"subscribers","semantics":"cumulative_stock"},"observed_at":"2026-10-01T00:00:00Z","value":37,"source":"user export row 1"},{"scope":{"platform":"youtube","account":"creator","metric":"subscribers","semantics":"cumulative_stock"},"observed_at":"2026-10-08T00:00:00Z","value":51,"source":"user export row 2"}]}
```

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/estimate_growth.py' growth_input.json --output growth_estimate.json
```

Use this skill's absolute script path from the run directory. A narrow estimate requires no synthesis or public handle. When public samples are needed, use the existing public review fetch contract; keep fetched facts in persisted artifacts and include their references in the calculation input. Do not claim that daily follower history or owner analytics are connected just because storage exists elsewhere in the platform. No tool of this Skill provides them: request a dated export rather than inventing a tool or substituting another creator's pace.

For `content_scenario` from the review's `public-content-snapshot.v1`, omit `samples` and add `--snapshot snapshot.json` to the command. The script binds the matching creator and metric directly, preserves unavailable values, and retains snapshot item references; do not retype those facts into samples. Keep the snapshot's coverage and source limitations in the interpretation. The native result is described by `references/growth_estimate.schema.json`.

For user inputs, pass `--question-file` pointing to the original request as plain text. Public review fetch retains it as `question.txt` in the printed run directory; use `--snapshot snapshot.json --question-file question.txt` there. For a publication plan, interpret the request's meaning first, then supply `quote` as the complete original request (excluding surrounding whitespace) and `binding`:

- Explicit total: `{"posts": 4, "origin": "user", "quote": "If I post 4 long videos in total next month, how many views will I get?", "binding": {"quantity_kind": "total", "quantity_quote": "4"}}`.
- Frequency: `{"posts": 3, "period": "week", "origin": "user", "quote": "3 videos per week", "binding": {"quantity_kind": "cadence", "quantity_quote": "3", "period": "week", "period_quote": "per week"}}`.

The model interprets total versus frequency and maps the user's language to `day`/`week`; the script does not claim to understand arbitrary prose. It checks that the whole request is retained, the numeric source matches `posts`, the period evidence occurs separately in the source, and the normalized fields agree. It then calculates the equivalent publication count without rounding. Do not invent evidence or turn a frequency into a model-calculated monthly total. Missing interpretation, truncated quotes, absent evidence or unsupported periods return `needs_input`; clarify when the request cannot be interpreted confidently. Structural consistency cannot prove that the model's semantic interpretation is correct. Read compatibility remains only for a complete old explicit-total short form such as `6 videos` (or `publish 6 videos`), not a number excerpted from arbitrary prose.

The new `binding` contract applies only to publication plans. `assumed_pace` retains its pre-existing stock-rate input reader for compatibility; it recognizes the existing Chinese/English forms rather than providing a new multilingual rate interface: `assumption.quote` is an exact source excerpt containing the signed net amount and day/week period, such as “20 subscribers per week” (a Chinese excerpt with a per-day or per-week net amount is read the same way). Never relabel an assistant suggestion as a user assumption. Relay computed values, formula, assumptions and missing inputs together; preserve the same limits under follow-up pressure.
