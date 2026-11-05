---
name: develop-content-ideas
description: "Turn a creator's account review, trend or competitor research, a video analysis, a video's YouTube or TikTok comments, or an explicit topic into distinct content ideas the creator can compare and choose from. Use when a creator asks what to make next, wants a first batch of topics for a new account, wants ideas that fit a time or equipment budget, wants follow-up ideas from a video's comments, or wants new hooks for a weak topic. Not for diagnosing an account or planning a posting calendar."
license: MIT
compatibility: "Requires Python 3 and read and write access to a local workspace. Fetching a video's comments also requires the Kabo MCP server at https://kabo.sh/mcp-pub and network access from scripts to kabo.sh and to the signed storage.googleapis.com download links that Kabo tool results return (without it, a host that can save a tool's complete result to a file). Kabo permission, for comments only: data."
allowed-tools: get_research_capabilities get_research_data_job get_research_data_download youtube_list_comments tiktok_discovery_list_comments tiktok_discovery_list_comment_replies start_skill_run
---

# Develop Content Ideas

Turn validated upstream artifacts or an explicit topic into an IdeaSet of distinct, selectable
ideas. The schemas, builders, validators and renderers ship in this Skill's `references/` and
`scripts/`.

**Delivery.** The deliverable is `RESULT.md` at the run root, and only after core reports
`status="ok"` and the final check passes. Read it completely (every chunk when the host pages) and
copy it exactly to the reply, once: never summarize, rewrite, reorder or retype its numbers, and
never append a second paraphrased report. `IDEA_REPORT_FULL.md` is audit-only and never delivered.
A `needs_revision`, `not_met` or failed result is never presented as a validated report; when a
missing upstream artifact blocked the request, name the Skill that produces it as the next step.

When the host supports one, run this Skill in a foreground sub-agent, because a run has several
stages, and wait for its final result before replying; never run it in the background and tell the
user the report will come later. Without a foreground sub-agent, follow the same steps in the main
thread.

This Skill runs no research: it consumes validated research artifacts through `upstream_sources[]`
and deterministic `idea_context`. `user_brief.text` preserves the user's request; it is not by
itself proof that a usable topic exists. Never parse Creator-facing Markdown, search the web, use
competitor outputs as hidden criteria, or infer a business domain or topic from a handle,
account/display name, URL, platform or another Skill's description. Do not create a SelectedIdea
until the creator explicitly chooses or edits an idea.

## Running commands

`<skill directory>` is the folder holding this `SKILL.md`. Reserve exactly one run per request:

```sh
python3 '<skill directory>/scripts/static_host.py' init --workspace '<user-workspace>'
```

It prints the run directory (`<run directory>`, the `--run-dir .` the stage commands name). Every
command that needs the run starts with `cd '<run directory>' &&`, exactly as written below, because
hosts keep neither environment variables nor the working directory between commands. No step pauses
and resumes, so no command takes `--placed`. Every command here is complete: do not probe scripts
with `--help` or read their source. Quote paths and use `grep` rather than `rg`. Write `intent.json`
and `idea_set.json` at the run root with a single-quoted heredoc or the host's file tool, and make
model authorship and the one permitted revision between stage commands, never inside one.

A nonzero exit from a script ends the run (for core, see Core step 5): report it, and never reopen
the run, repeat completed prep, reset the generation budget or start a replacement run. Do not write
or claim a run-record status yourself. For ideas from a video's comments, read
`references/comment-evidence.md` whole before the first call: it holds the calls, the save, fetch
and persist commands, failure handling and the projection.

**Declare the run.** Right after the run directory is reserved, call the Kabo tool `start_skill_run` once with `skill_id` `develop-content-ideas` and `run_id` set to the last path segment of the run directory. It records the run once, so never call it again for the same run. If it fails with `credits_exhausted`, tell the user their credits are used up, give them the link in the error, and stop. If the tool is not available, continue without it.

## Prep

Read `references/prep.md` whole before writing `intent.json`: it says where each kind of evidence
comes from and its prep flag, how to choose `task_intent` and fill `generation_basis`, and how to
handle each prep decision. Also read the fields and definitions for this task intent in
`references/idea-request.schema.json` and `references/idea-set.schema.json`, and
`references/core-generation.md` whole. In short:

1. Copy each upstream JSON artifact into the run unchanged (`cp`); a trend or competitor report
   counts only with its `*_idea_context.json` projection. Never write a projection by hand.
2. Write `intent.json` (no `upstream_sources`, `idea_context`, `continuation_context` or
   `conversation_context`), then run
   prep once with the artifact flags this run has:

   ```sh
   cd '<run directory>' && python3 '<skill directory>/scripts/run_ideas_stages.py' prep --intent intent.json --run-dir .
   ```

3. Read prep's `status` even on exit `0`. `needs_correction`: fix every listed issue once and rerun
   the same prep. Then obey the decision exactly: `blocked_missing_required_source` (empty IdeaSet,
   validated by one core run), `needs_selection_context` (ask for a topic or evidence), or
   `ready_for_generation` (go to Core). Collection failure never authorizes brief fallback.

## Core

Core starts only after prep reports `ready_for_generation` (prep opens `stage_timing.json`
core.started_at at that moment). The target is **at most two** model generation passes that write
or revise `idea_set.json` (generate, then revise only if programmatic validation fails). Do not
invent extra critique/rewrite loops. Record network, 5xx or truncation retries with
`--record-network-retry` as `references/core-generation.md` says; they use no attempt.

1. Apply explicit production constraints. Record only assets the user supplied or evidence shows
   are available. For a continuation, differ from every previous idea as *Continuations* says.
2. In **one** generation pass, write the complete `idea_set.json`: distinct titles the Creator could publish, hooks, making direction, evidence basis, ranking, gates, and limitations. Remove near-duplicates and rank by Creator fit, evidence strength, differentiation, timeliness, and realistic production cost. A plain question or statement is sufficient for a title; keep teaching method detail in `making_direction`. Never invent loss amounts, customer outcomes, efficiency gains, market facts, policy facts, numbers, or URLs. Preserve artifact-level lineage in `upstream_sources`, context-level refs in `source_context_refs`, `must_avoid` as `must_differ`, and risk language. `required_assets` must be a subset of the request's declared `available_assets`; when the user supplied no assets, leave both `required_assets` and `assets_used` empty and put possible future props in `assumptions` or `production_missing`. Do not edit, patch, or replace the skill scripts, schemas, or validators; fix a rejected IdeaSet by revising only `idea_set.json` once.
3. Put missing information into `gates.selection_missing` only when it prevents a valid choice.
   Put filming, editing, CTA, cadence, or other later details into `gates.production_missing`;
   these may coexist with `ready_for_selection`. If `production_missing` is nonempty, include
   `answer_production_question` in `interaction.next_actions` alongside the selection action; do
   not make later production details block topic selection.
4. Run the core stage (validate → render, or emit a revision brief):

   ```sh
   cd '<run directory>' && python3 '<skill directory>/scripts/run_ideas_stages.py' core --run-dir .
   ```
5. Read the script's structured stdout (`stage`, `status`, `errors`, `revision_remaining`) as well
   as its exit code. Both `ok` and recoverable `needs_revision` exit `0`; the latter keeps the run
   open but does not validate or render a deliverable.
   - `status="ok"` with report paths (exit `0`): run step 7, then deliver. The `--concise` output is the sole Creator-facing artifact: `RESULT.md`. The `--full` output is an audit-only artifact; keep `IDEA_REPORT_FULL.md` in the run directory and never deliver it or copy it to `RESULT.md`.
   - `status="needs_revision"` with `revision_remaining=1` (exit `0`, unvalidated): use the returned errors and `revision_brief.json` to revise `idea_set.json` **once**, then run the same core command in the same run directory. Do not rerun prep, reset the attempt counter, or replace this runnable revision with an unvalidated prose answer. If the working file cannot be read directly, write the corrected file into the same run directory from the retained IdeaSet content and returned errors.
   - `status="not_met"` (exit `3`): stop; do not start another creative pass or loosen validation.
   - `status="failed"`, absent/unreadable script status, or an unavailable execution tool: diagnose
     the specific failure. Do not infer successful validation or permission for another creative
     pass. If recovery cannot complete, say the draft is unvalidated rather than presenting it as
     the completed skill result.
6. If machine-readable JSON is requested, emit only the declared JSON object. Creator-facing
   Markdown is a renderer projection and must not be appended to strict JSON.
7. Only after core reports `status="ok"`, run the final step from the run directory, without
   running core again or consuming another generation attempt:

   ```sh
   cd '<run directory>' && python3 '<skill directory>/scripts/validate_ideas.py' idea-set idea_set.json request.json && test -s RESULT.md
   ```

   This is the final step; its deliverable is `RESULT.md` at the run root, not under `report/`.
   There is no final step for `needs_revision`, `not_met` or a failed/unreadable stage result. A
   zero exit with `needs_revision` never permits emitting the draft as a validated report.

## Judgment rules

Read `references/idea-set-rules.md` whole before writing `idea_set.json`; it holds the per-intent
contract and ranking rules. In short:

- Never invent numbers, URLs or metrics; exact numbers trace to evidence.
- `source_backed` only when upstream/context refs and evidence refs are present; otherwise
  `brief_based` or `hypothesis`. `why_now` is evidence-backed only when a dated source supports it.
- A request for account performance, trends, competitors, Creator context or a specific video never
  silently becomes brief-only: without the evidence and without explicit fallback, return the
  missing-source block without ideas.
- `selection_status` remains `candidate` until the user selects an idea.

## Report language and conversation context

Set `report_language` on both IdeaRequest and IdeaSet to the language of the final report (BCP 47),
separate from `language_targets`; read `references/report-language.md` whole before writing
`idea_set.json`. Before asking for a topic, audience, goal or evidence, reuse the conversation up to
the current turn. Follow *Continuations* in `references/core-generation.md` to pass an earlier
IdeaSet or exact visible chat through prep, while keeping the current request unchanged.
When no explicit topic or usable evidence exists, ask only for the topic/niche (or
an account/evidence source); a platform name and an idea count do not supply a niche. Clarification
is not a completed idea-generation task.
