# Answer rules

Read this whole file before writing `analysis/creator-task-result.json`. It says how to read
the engine's outcome, what the answer leads with, how to report the signals and which rules hold
in every outcome.

## Three answers, only two refusals

| outcome | what happened | what the creator sees |
|---|---|---|
| `recommended` | an hour, or failing that a block, reads better than the rest | the time, what supports it, and how much it is worth |
| `no_clear_window` | the day was read and nothing prefers any part of it | **publish now** — waiting costs a slot and buys nothing measurable |
| `abstain_no_history` | no posts at all | which input is missing |
| `abstain_no_publish_time` | the source reports no publish hour | change source, not cadence |

Read the outcome from the engine. The explanation lives in `finding` for the middle row and in
`abstain_reason` for the bottom two — never both. A finding read out of the refusal field becomes a
refusal.

## Lead with the time

The first line is the time, or "publish now". Not the method, not the caveats. Then, in this order:

1. **What it rests on** — how far the hour stands above the rest of the day, how many posts sit
   inside it, and which of the other signals agree.
2. **What it is worth** — the size of the gap against the creator's own spread. A 30% timing difference worth 45 views on a range of 61–241
   tells the creator to stop optimising this and go work on the hook. Never drop it because the
   answer looks better without it.
3. **What to discount** — every entry in `confidence_penalties`, one per line, in the creator's
   language. The engine writes them in English as an audit record; carrying that text through
   untranslated hands a creator a report in a language they did not ask for.

## The signals, and which one may decide

`evidence_base.signals` lists every source this task can draw on and the status of each. Report the
ones that spoke; name the ones that did not rather than letting the answer look more complete than
it is. Only `own_history_hour` may resolve the time. The rest corroborate it, narrow the day, or
declare themselves unavailable — because the question is about this creator's account, not about
accounts in general.

`peer_accounts_hour` is the one signal built from other people's posts, so it carries the tightest
rule: **whenever it contributed, name accounts.** A creator can open a named account and check
it; strip the handles and what is left is the generic chart this Skill refuses. Name about three and close with "and others", not the
whole cohort: twelve handles in one sentence are correct and unreadable. The full list is in the
structured object.

**Agreement is the confidence, and say the number.** "Three metrics, one chose this hour" is
something a creator can weigh. Never translate it into a statistical term. If one metric decided
it, say that there was nothing to corroborate it with. `medium` is this creator's own history
resolving a clean hour on its own, `high` adds an independent source landing on the same one, and
`low` is either a caveat that could make the reading wrong or an hour the peers broke a tie on. A `high` is worth saying plainly.
A penalty marked `costs_confidence: false` — windows never tried, an unresolvable zone — still gets
told to the creator; it changes how to read the answer, not whether the reading holds.

**The day is read separately from the hour.** Seven days times twenty-four hours is 168 cells and
no creator's history fills them. A named day — "Wednesday" — is offered only when most of the week
has actually been used and the metrics agree on one day. Otherwise the weekday/weekend split, and
otherwise nothing. Report whichever came back and do not upgrade it in the wording.

## Rules that hold in every outcome

- Never a generic best-time chart, a platform-wide peak, or a default from another tool. What was
  declined is recorded in `refused_fallbacks`.
- Never fill an empty hour with the account average. An hour with no posts means the creator has
  never tried it — say so, it is useful.
- Never claim causation. This is the hour that performed best in their history, never an hour that
  will make the next post perform better. Say the second half out loud: topic and hook move these
  numbers far more than publish hour.
- Never report a time without the zone travelling with it in the structured object.
- On `no_clear_window` give no threshold and no posting quota. The comparison ran.
- If the creator publishes elsewhere, record it as their choice with no confidence attached.
- Ask once, and only for what changes the answer: which metric they care about when the history
  could be keyed on more than one. Do not substitute a default for an unanswered question.

Only two things block an answer outright: **per-post publish times to the minute** (a date is not
enough; if the source reports every post at midnight, change source — more posts from it add rows
and no information) and **something to compare against** (a single readable slice of the day has
nothing to rank against). Everything else shapes the answer without blocking it: platform and
format split the cohort, post age becomes a stated penalty when the winning slice also holds the
older posts, and a history too thin to resolve an hour falls back to a block instead of refusing.
