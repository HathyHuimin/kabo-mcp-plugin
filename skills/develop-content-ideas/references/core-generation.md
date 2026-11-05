# Core generation budget (prep / core)

## Stages and timing

Split every run into **prep** and **core**. Do not move idea authorship or revision into prep to evade the core budget. If stages overlap in wall time, record each once; do not sum overlapping intervals.

| Stage | Includes | Records |
| --- | --- | --- |
| Prep | Using upstream artifacts and comment evidence, intent authoring, request build, request validation, generation decision | `stage_timing.json` prep duration; host may also count prep model/tool calls |
| Core | From `ready_for_generation` through idea authorship, at most one validation-triggered revision, final validate + render | `stage_timing.json` core duration and `generation_attempts` |
| Full task | User send → final delivery | Total wall clock; approval waits listed separately |

Network / 5xx / host truncation retries are allowed and must be recorded with `cd '<run directory>' && python3 '<skill directory>/scripts/run_ideas_stages.py' core --run-dir . --record-network-retry "<note>"` (does not consume a generation attempt). Unlimited creative retries after two generation attempts are not allowed.

## Budget

This skill cannot hard-cap the host's model-call count. The **acceptance metric** for latency work is: after prep reaches `ready_for_generation`, the host should need **at most two** model passes that author or revise `idea_set.json`.

## Counting rules

| Counted as one generation attempt | Not a generation attempt |
| --- | --- |
| Writing the first complete `idea_set.json` | Prep evidence, tool calls, approvals |
| One revision of `idea_set.json` after `revision_brief.json` | `run_ideas_stages.py prep` / `core` themselves |
| | Host truncation continuation of the **same** response |
| | Network / 5xx retries recorded via `--record-network-retry` |

`stage_timing.json` → `core.generation_attempts` is the skill-side attempt counter. Live harnesses should also count host model calls attributed to this skill's core window and report both.

## Required sequence

1. Prep: evidence + `intent.json` → `cd '<run directory>' && python3 '<skill directory>/scripts/run_ideas_stages.py' prep --intent intent.json --run-dir .` (+ artifact flags).
2. Only when decision status is `ready_for_generation`: one model pass writes a complete IdeaSet.
3. `cd '<run directory>' && python3 '<skill directory>/scripts/run_ideas_stages.py' core --run-dir .`
4. Read structured script stdout: `status="needs_revision"` and `revision_remaining=1` (exit `0`, still unvalidated) requires one model pass revising from the returned errors / `revision_brief.json`, then core again in the same run directory. Keep the attempt counter; do not restart prep or deliver unvalidated prose instead of this runnable step.
5. `status="ok"` with report paths (exit `0`) makes the reports ready; `status="not_met"` (exit `3`) stops creative retries. A failed/missing status is not permission to claim completion or reset the budget. If execution is unavailable, report that the draft remains unvalidated.

Run these commands from the run directory `SKILL.md` sets up. Every core call is non-final,
including the first attempt. Only after `status="ok"`, run SKILL.md's separate final validation
step, without calling core again; its deliverable is `RESULT.md` at the run root, not under
`report/`. Exit `0` for `needs_revision` keeps the run open; it does not mean validation passed.
Invalid request/upstream inputs remain hard failures, distinct from a repairable
authored IdeaSet; validation still rejects an incorrect source mode, unsupported confirmed fit,
or omitted evidence gaps until the single allowed revision fixes them. Every host uses the same
prep/core driver and the same retained run directory. A failed step never falls back to another
execution route. Nothing resets the generation counter or permits another creative attempt.

Passing validation on step 3 means a single generation attempt is enough.

## Timing files

`stage_timing.json` schema_version `ideas-stage-timing.v1` records prep/core wall intervals inside the run directory. Hosts may attach separate model/tool counters. Parallel prep work must not be double-counted into core.

## Continuations

For a follow-up such as “10 more in the same style”, preserve the current request verbatim in
`user_brief` and leave `direct_topic=null` unless this turn also names a literal topic. Resolve the
reference against the visible conversation: which list, version, topic or style does the user mean?
If history is paged, inspect its range/count metadata or latest available page before treating
an unread tail as absent. Read the relevant exchange through the current request's boundary.
Resolve a number against the nearest relevant list and later user corrections; for an unnumbered
style reference, use the nearest concrete proposal and its clarifications, not the older list of
possible directions. Carry forward its mechanism, completion goal and restrictions unless the user
changes them; use the current request's output count. Ask only about
ambiguity that remains after this check, not context already supplied.
Copy the current continuation phrase into `generation_basis.continuation_quote` verbatim and choose
one source route:

- **Earlier native IdeaSet:** pass its unchanged `idea_set.json` to prep with
  `--previous-ideas '<path>'`. Prep retains `previous_ideas.json` and derives
  `continuation_context` with its IDs, checksum and creative directions.
- **Earlier visible chat without an IdeaSet:** write `conversation-source.json` containing only
  `schema_version: "idea-conversation.v1"`, `messages` (the relevant earlier `user`/`assistant`
  messages with exact `text`, in order), and `reference` (`message_index`, zero-based within this
  array, plus `quote`, an exact nonblank span in that message). Include the context needed to retain
  constraints, exclusions and corrections; a summary is not a substitute for the original text.
  Copy text mechanically from the available message fields or file ranges. Preserve message
  boundaries; join streamed fragments only when they belong to the same message, in order and
  without crossing a user turn. Check the selected quote and zero-based index before prep.
  Pass `--conversation-context conversation-source.json`. Prep retains the original bytes and
  derives `conversation_context`; never write that field into `intent.json`.
  For a chat-only follow-up with no new literal topic or required research, use
  `prepare_conversation_inputs.py` from `references/prep.md` to construct these inputs from the
  visible message export instead of manually retyping the source and request layout.

Do not combine both routes, paste history into `user_brief`, invent a previous IdeaSet, or read host
session files. Reuse context already visible to the Agent; only request missing source material
when it is unavailable. Chat is unverified creative context, not account, trend, comment or video
evidence, and never satisfies a missing research requirement. The program checks the literal
quote, index and retained bytes; the Agent must judge whether the reference and style are correct.
The conversation reference is already bound by `conversation_context`. Do not copy it into an
IdeaSet's `source_context_refs` or invent IDs for it. Those arrays reference only the validated
research contexts in the request's `idea_context`; without those contexts, keep both the top-level
and each idea's `source_context_refs` empty. Chat binding and research lineage are different fields.

For same-style variants, build the batch by **substitution into the selected example**, not by
brainstorming other ideas in its genre. Derive a shared title, opening and making-direction
scaffold from the selected example, leaving only the changeable slots open. Choose all requested
concrete slot values before expanding any item, then use the same scaffold for each. A shared
sentence pattern and mechanism are the requested similarity, not duplicates to remove. Rank
`differentiation` only by the slot substitutions and their consequences within that mechanism.
Keep the selected subject/domain unless this turn names a literal `direct_topic`;
keep the format, causal mechanism, completion goal, restrictions and title/opening construction
unless the user changes them. Later corrections override earlier proposals. These defaults never
override the current instruction.

In the existing `overview.portfolio_logic`, state the selected example and its relationship in
plain language: the concrete starting input, how it produces progress, what controls each stage,
and the end condition. Name the slots this batch changes. Preserve the **kind and number of inputs**
as well as their causal roles. If one specific resource funds successive unlocks, another specific
resource can replace it; a location, an activity, a collection of unrelated income sources or a
new prohibition cannot replace that resource. Merely adding an unlock board to a different
challenge does not preserve the mechanism. Do not broaden the slots to create variety.

For each item, instantiate those slots first, then write its title, hook and `making_direction`
from that mapping. The making direction must name the actual starting input, its earning or
transformation loop, the staged cost/condition and the unchanged goal. Put any necessary proposed
rules in the idea itself; distinguish them from facts about how a platform or game works. Prices
and production tuning may remain to be tested, but the input and its progression loop cannot be
left as "choose a resource", "define a currency" or another task for the creator. A requested
fill-in template is the exception: retain its blanks and supply the requested filled examples
under the template rules.

During this same generation pass, compare each completed item with the selected relationship,
not with the broad genre or the batch introduction. A matching title or a claim that all items
share the mechanism cannot compensate for a missing or changed link in an individual item.
Replace such an item with a concrete substitution before writing the complete IdeaSet. Distinct
substitutions count as useful variety; do not force different mechanisms to satisfy novelty.
This is the Agent's semantic check within the existing generation budget, not a new script
validator or extra revision loop. Programmatic validation cannot establish that the referent
or mechanism is right.

Repeated native IdeaSet titles are rejected; semantic novelty and
style consistency still need the Agent's comparison. A brief-only continuation needs a literal
`direct_topic` or one of these bound context routes.
