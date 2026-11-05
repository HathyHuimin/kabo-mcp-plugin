---
name: plan-content-calendar
description: "Build a reviewable four-week or date-range content calendar (up to 91 days), or propose which already-selected item fits one exact publish slot, from selected ideas, script packages, production state and publish-timing results. Also CSV/ICS export and reminder or publish-preparation handoffs. Use for planning when to post what, or questions like which one to post at 4pm today. Not for research, choosing new ideas, rewriting content, inventing a best time, uploading or publishing."
license: MIT
compatibility: "Requires Python 3 and read and write access to a local workspace. Uses no Kabo data tools: every input is an artifact the creator or another Skill already produced in this workspace."
metadata:
  version: "0.1.25"
---

# Plan Content Calendar

Turn immutable upstream decisions into a deterministic, reviewable execution plan. `content-calendar.v1`
JSON is the product truth; Markdown, CSV and ICS are projections of it.

**Delivery.** For an artifact-backed run, the renderer is the final step and its deliverable is
`content-calendar.md`. Read it whole and reply with it as the calendar body, once, in the creator's
language, ending with `creator_report: content-calendar.md`; never regenerate a second schedule in
prose. Keep the planned and target counts, every unfilled slot and conflict, production conditions and
action boundaries. With it, give the validated `content-calendar.json` path and its `status`, the CSV
and ICS paths when exported, unresolved decisions and gaps, and a plain-language summary of each
proposed calendar, reminder and publish-preparation action with its exact slot scope. State plainly
that every proposed action needs the creator's separate confirmation and that no calendar, reminder,
upload, schedule or publication was created because an artifact or ICS file exists. Never print
snake_case action enums or internal field paths unless the user asks for the technical contract.

When the host supports one, run an artifact-backed calendar in a foreground sub-agent and wait for its
final result before replying; never run it in the background and tell the user the report will come
later. Without a foreground sub-agent, follow the same steps in the main thread.

## Route the request first

For both conversational proposals and artifact-backed calendars, reuse context already supplied
before asking for missing inputs. A request for fully filled topics needs a known content direction,
supplied topics or usable account context; if all are absent, ask for the direction instead of
inventing a niche. Generic advice and explicitly requested blank templates need no such clarification.
Resolve an ambiguous planning interval before assigning dates or converting it into weeks. Preserve
explicit dates and durations; while an anchor is missing, offer only a relative draft. Apply the
input boundaries in `references/advice-and-proposals.md`, including region-dependent holidays.

The request may arrive in any language. Decide which of these it is before anything else:

| The request | What to do |
|---|---|
| Strategy or workflow advice (sustainable filming/editing, restart guidance, content-role mix, a learning roadmap), a review of times or reminders the creator already stated, or a proposed plan for topics the creator supplied | Answer in the conversation, per `references/advice-and-proposals.md`. Reserve no run and call no script: without selected-idea artifacts the pipeline can only fail. |
| A four-week plan, an explicit date range, CSV/ICS export, or which selected item fits one exact slot | The artifact-backed pipeline below. Pick the mode in `references/request-modes.md`. |
| New ideas, holiday research or content selection a calendar would need | That is another Skill's job; materialize the slots only after selection. Missing selections stay explicit unfilled slots. |

For those conversation routes, read `references/advice-and-proposals.md` first, then answer now:

- **Topics the creator supplied, to be given angles, formats and posting dates**: that request
  authorizes a proposal. Deliver the complete proposed calendar in this reply, every supplied topic
  kept, with a sensible default cadence labelled as an assumption. Title it an unvalidated draft, not a
  native calendar, and name what would make it one (the selected items and the account). An unknown
  platform, start date or timezone does not hold it back: give the plan in relative order (week 1 day
  1, …) and ask only for the missing anchor; never invent an exact instant, and never reply with
  questions alone.
- **Advice**: answer the strategy now with stated assumptions, not a routing menu; missing account data
  blocks only an account-specific diagnosis.

A four-week horizon does not imply eight posts: keep the creator-selected content set, never expand or
clone it to fill weeks, and report unused slots honestly.

**Clarify once, then fail closed.** For an artifact-backed calendar, ask one consolidated question when
a required target, account, date, creator timezone, selected item, cadence or source artifact is
missing, after first delivering any advice or review the information already supports. Relative times
such as "today" or "4pm tomorrow" become an offset datetime in the creator's IANA timezone; never read
the machine timezone or clock, and ask when an exact instant needs an unknown timezone.

## Running commands

`<skill directory>` is the folder holding this `SKILL.md`. Reserve exactly one run per
artifact-backed request:

```sh
python3 '<skill directory>/scripts/static_host.py' init --workspace '<user-workspace>'
```

It prints the run directory, `<run>` below. Copy every upstream artifact the calendar binds
(SelectedIdeas, ScriptPackage requests and packages, publish-timing request, history, recommendation
and schedule, final media and covers) into `<run>` unchanged with `cp`, because every ref is a path
relative to the run. Every command starts with `cd '<run>' && `. Write authored JSON with a
single-quoted heredoc or the host's file tool. Every command here is complete: do not probe scripts
with `--help` or read their source, and never edit the scripts, schemas or validators. Quote paths and
use `grep` rather than `rg`. A failed step stops the run: report the error it printed; never fabricate
artifacts or confirmations to pass it.

## Pipeline

1. Build `content-calendar-request.json` per `references/request-building.md` (refs, items, joins,
   timezone provenance, production progress and publication assets), then fill every ref's id and hash
   from the files:

   ```sh
   cd '<run>' && python3 '<skill directory>/scripts/calendar_ref.py' content-calendar-request.json
   ```

2. Validate the request, then build the raw calendar:

   ```sh
   cd '<run>' && python3 '<skill directory>/scripts/validate_content_calendar.py' request content-calendar-request.json --run-root .
   cd '<run>' && python3 '<skill directory>/scripts/build_content_calendar.py' content-calendar-request.json --run-root . --output content-calendar.raw.json
   ```

3. Export CSV and ICS from that same JSON (this only writes local files and their hashes; it creates no
   reminder and saves no calendar). For a reviewable draft without export, copy
   `content-calendar.raw.json` to `content-calendar.json` instead and continue:

   ```sh
   cd '<run>' && python3 '<skill directory>/scripts/export_content_calendar.py' content-calendar.raw.json content-calendar-request.json --run-root . --csv content-calendar.csv --ics content-calendar.ics --output content-calendar.json
   ```

4. Validate the final calendar and its projections, then render:

   ```sh
   cd '<run>' && python3 '<skill directory>/scripts/validate_content_calendar.py' calendar content-calendar.json content-calendar-request.json --run-root .
   cd '<run>' && python3 '<skill directory>/scripts/render_content_calendar.py' content-calendar.json content-calendar-request.json --run-root . --output content-calendar.md
   ```

Never skip the request validator, the deterministic replay check, the final validator or the
projection hash checks. The planning rules the builder applies, and that the report must keep, are in
`references/planning-rules.md`.

## Ownership boundaries

- Ideas own what to make: only `selected-idea.v2` can occupy a slot; `idea-set.v3-candidate1` is
  backlog and stays `candidate_requires_selection`.
- Script & Packaging owns how to make it: copy approved package fields; never draft missing headlines,
  captions, scripts, covers or assets here.
- Publish Timing owns evidence about when: preserve its lineage; never recompute a recommendation or
  turn `no_clear_window` / `abstain_*` into a generic best time.
- Runtime owns confirmation and durable actions: calendar writes and reminders happen only after the
  creator confirms each scoped action; platform media preflight, upload, scheduling and publishing
  belong to an authorized connector, never to this Skill.
- Trial Reels, variants, frequency experiments and post-performance analysis are `LATER`: add no
  experiment fields and never present a normal slot as an experiment.
- Every scheduled slot shows its complete title, audience problem, promise, angle and opening Hook,
  never shortened to `#2`; observed production state and structural publish preparation are shown
  separately, and neither stands in for the other.
- The highest state this Skill owns is `ready_for_review`. An upstream artifact may truthfully say a
  slot was already accepted or scheduled; preserve that state, never claim this Skill caused it.

Hard product contracts, kept verbatim:

- `never choose on the creator's behalf`
- `No generic platform best-time fallback`
- `No upload, schedule, publish or external write side effect`

Do not use web search, another Skill, model memory, generic platform advice, a different artifact, or
generated replacement content to make an invalid request pass. The full fail-closed list is at the end
of `references/planning-rules.md`.
