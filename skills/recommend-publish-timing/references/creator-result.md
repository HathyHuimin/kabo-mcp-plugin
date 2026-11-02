# Writing the Creator-facing result

Read this whole before writing `analysis/creator-task-result.json`. It holds the delivered object's
contract, the block shape, the length caps, the citable references and what `details` may hold.
The commands that check and render it are in `SKILL.md`.

## Engine sentences are an audit record

`signals[].detail`, `confidence_penalties[].statement`, `finding.statement`,
`abstain_reason.statement`, `ranking.granularity_reason` and `weekday_note.statement` are the
audit record of what the engine decided; the report says the same thing in the creator's language.

**Write that report from `params`, not by translating the sentence.** Every one of those sites
carries a `code` naming what happened and a `params` object holding the values the sentence was
built from — the hour, the window, the counts, the metric names, the thresholds. `code` tells you
which sentence to write; `params` gives you the figures to put in it. Translating the English
prose instead means re-deriving numbers that are already sitting next to it, and a figure the
creator reads should be the figure the engine computed. `ranking.granularity` carries the same
pair for `granularity_reason`.

Machine values are English too — `weekday_note.stronger`, `per_metric`, `days_compared` and
`recommendation.windows[].weekday` carry `monday` … `sunday`, or `weekday` / `weekend` / `all`.
They are identifiers, not words to show: a report naming a day names it the way the creator
would say it.

## The envelope is part of the delivery

The delivered object is a `CreatorTaskResult`: declare `schema_version: creator-task-result.v2-final`
and include `result_id`, `task`, `status`, `details`, `evidence_index`, `interaction` and `creator_view`.
The view explicitly carries `language`, `headline`, `summary`, `cards`, `next_actions` and `data_note`.
Every card carries `title`, `why_for_you`, `action` and `evidence_refs`; diagnosis without action is observation.
Read `references/creator-view-authoring.md` before writing it: line encoding, missing-zone disclosure
and partial-history scope clarify this existing contract; all final gates remain unchanged.

Every figure in `creator_view` traces to the analysis or the evidence — the headline and the
summary included, not just the line the creator reads aloud. A rounded figure counts as grounded
(`3.1` from `3.1043` is correct writing, not invention), a magnitude word is a scale on the source
(`2.6M` from 2,605,682; the CJK ten-thousand and hundred-million units are read the same way), and
a clock is not a figure at all. A real run opened with a view count
that appeared in no structured object, and nothing asked where it came from.

## Where the objects land

`analysis/creator-task-result.json` is the delivered object, under that name. Two runs of this skill
wrote it as `creator-result.json` and `creator-task-result.json`, which leaves anything archiving or
reading the run guessing. The name is part of the contract, not a per-run choice.

The structured objects are written straight to disk as JSON. Do not author a throwaway Python file
to emit them: an early stability batch was discarded because six agents each wrote their own
`build.py` into one shared directory and overwrote each other's, and a script that only interpolates
this run's own prose pays for every sentence twice.

Rank tests are still computed and reported in `evidence_base.significance` as an audit layer that
decides nothing. An earlier version used them as the gate and refused on all five real accounts,
because publishers do not post into their own bad hours and observational history has no control
group.

## Land the answer on something

A `recommended` outcome carries a proposed `PublishSchedule` on `interaction` — the next occurrence
of the named hour, in state `proposed`, for the creator to confirm. This is the one thing every
competitor does that we did not: they end on a countdown, an upload page or a push, and we ended on
a paragraph.

It schedules nothing. Reminders, queueing and publishing are outside this Skill. And it never launders a
refusal into advice: `publish now` and both `abstain_*` outcomes propose no slot at all, because
there is no future instant to hold and inventing one would turn "waiting buys you nothing" into a
reason to wait.

## Output boundary

No file paths, validator status, Connector calls, credits, cost, provider names, or a separate
limitations section. Every outcome here is a product result — a time, "publish now", or a named
missing input — so **keep the time, its support, what it is worth and the caveats visible**, and
leave the full causal and audit ledger in the structured object unless asked.

The Creator-facing result is four blocks in a fixed order, so a creator who has read one of these
knows where the answer lives without hunting for it:

| block | what it answers | holds | lines |
|---|---|---|---|
| 1 | *(no heading — headline and summary)* | the time, and the day when there is one | 1–2 |
| 2 | how the comparison was made | one line per signal that spoke | 2–4 |
| 3 | what the difference is worth | `practical_stakes` | 2 |
| 4 | how far to trust it | `confidence_penalties`, one per line | 2–4 |

**The order is fixed; the headings are yours to write.** Give each block a heading in the
creator's language that says what it answers, and do not give two blocks the same one. The
sequence is what the reader navigates by, and it is checked by position and by what each block
cites — not against a list of phrases, because a list of phrases can only ever be in one language.

**Every block carries at least two lines.** One line is a sentence with a heading on it, and two
isolated runs collapsed the third block to a single line by merging the effect size and the caveat
that qualifies it into one sentence — which is the paragraph this shape exists to prevent, arriving
as a list of one. The stakes block holds two facts: what the difference is worth, and what
outranks it. It is the block that cites `stakes`, and dropping it is rejected wherever it is
recognised from.

`creator_view.next_actions` carries **one to three** of them, and one is the floor rather than a
suggestion. The schema marks the key required but permits an empty array, so `[]` is schema-valid
and rejected on delivery; two isolated runs shipped exactly that. A timing answer with nothing to
do next is a reading, not advice.

That is the `recommended` shape. The other two outcomes answer different questions and get their
own, shorter, sequences — a task that always returns the same headings has stopped answering and
started filling a template:

| outcome | blocks after the headline |
|---|---|
| `recommended` | how it was compared · what it is worth · how far to trust it |
| `no_clear_window` | how it was compared · what would change this |
| `abstain_no_history` / `abstain_no_publish_time` | what would change this |

## How long each part gets

An answer the creator has to read twice has not saved them any time, and length pools: the block
table above caps lines per block, which does nothing about one field absorbing a paragraph. Three
isolated runs put 130, 178 and 96 characters into `data_note` against a cap that nothing told them
about.

The caps are in **display columns**, not characters: a full-width character counts as two and
everything else as one. A cap counted in characters is a different cap in every script — the
figures below were set on Chinese reports, and read as characters they gave a Latin-script report
about a third of the room for the same content.

| Field | Columns |
|---|---:|
| `summary` | 400 |
| `data_note` | 240 |
| `cards[].action` | 320 |

Every line in a block carries at least 24 columns, and `summary` at least 40. A line shorter than
that meets the line count without carrying a fact.

`data_note` is the tightest because it is the one field with no job in the answer — it says what
the reading rests on, and a reader who wanted the full basis has the `PublishRecommendation`. If it
will not fit, the sentence is carrying audit detail that belongs in the structured object.

## Every block cites what it stands on

Each block carries a non-empty `evidence_refs`, and each reference must resolve to something that
is actually in the `PublishRecommendation` you were handed. The point is not that a field gets
named; it is that a reader can open the object at that reference and find the thing the sentence
is about. A reference that resolves to nothing is an opinion wearing evidence's clothes, and the
shape gate rejects it.

Six sections are citable. Where a section is a list, the second segment names one entry by its own
identifier rather than by position, so a reference stays valid when the order changes:

| reference | resolves to |
|---|---|
| `signals.own_history_hour` | the entry in `evidence_base.signals` whose `signal` is that name |
| `hour_curve.14` | the hour under `evidence_base.hour_curve.hours` |
| `penalties.peers_disagree` | the entry in `recommendation.confidence_penalties` with that code |
| `ranking.abstaining_metrics` | that key under `evidence_base.ranking` |
| `stakes` / `stakes.metric_range` | `evidence_base.practical_stakes`, whole or one key of it |
| `cohort` / `cohort.mature_n` | `evidence_base.cohort`, whole or one key of it |

`penalties` and `stakes` are the short names for `confidence_penalties` and `practical_stakes`;
both spellings resolve. Nothing else is citable — in particular the `evidence_id` values carried by
peer accounts (`ev-content-…`) are the upstream snapshot's identifiers, not references into this
recommendation, and citing one tells a reader to open something they were never given.

## What `details` may hold

`details` is the delivery half of the result, not the audit ledger. It must not carry
`calculations`, `reproducibility`, `limitations` or `evidence`: those belong to the
`PublishRecommendation`, which travels alongside and is where anyone checking the work will look.
Copying them across does not hide them from the creator — `creator_view` already does that — it
duplicates the audit layer into the object shown to the creator, and the two copies then drift.

A refusal gets one block because there is one thing to say. The explanation belongs in a single
field, and writing it in two turns a finding into a refusal.

Lines, not paragraphs. Every line carries one fact and one evidence reference; `confidence_penalties`
already arrives as separate entries and must not be merged back into prose.

Localize every generated heading, label, unit and conclusion to the user's language. Timezone
identifiers stay canonical — and stay out of the Creator-facing text.
