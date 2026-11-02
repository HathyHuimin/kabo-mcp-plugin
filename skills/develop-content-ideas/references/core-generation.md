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
`user_brief` and leave `direct_topic=null` unless it also names a literal topic. Pass the earlier
run's original `idea_set.json` (it stays in that run directory in the workspace) to prep with
`--previous-ideas '<path>'`, and copy the current continuation request word for word into
`generation_basis.continuation_quote`. Prep keeps the exact original as `previous_ideas.json` in
this run and binds its IDs, file checksum and creative directions as `continuation_context`; never
author that field or paste earlier assistant text into `user_brief`. History is unverified creative
context, not account or trend evidence. Without the original file, ask for the missing topic or
context; never recreate an earlier IdeaSet from memory or a Markdown summary.

When generating, compare against every previous title and making direction in
`continuation_context`, keep the requested style, and propose new premises or outcomes rather than
renaming the same ideas. Apply the current request's exclusions and count. A repeated previous
title is rejected; semantic novelty still needs this comparison. A brief-only continuation needs
either a literal `direct_topic` or this bound continuation context.
