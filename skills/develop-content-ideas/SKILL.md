---
name: develop-content-ideas
description: "Turn a creator's account review, trend or competitor research, a video analysis, a video's YouTube or TikTok comments, or an explicit topic into distinct content ideas the creator can compare and choose from. Use when a creator asks what to make next, wants a first batch of topics for a new account, wants ideas that fit a time or equipment budget, wants follow-up ideas from a video's comments, or wants new hooks for a weak topic. Not for diagnosing an account or planning a posting calendar."
license: MIT
compatibility: "Requires Python 3 and read and write access to a local workspace. Fetching a video's comments also requires the Kabo MCP server at https://kabo.sh/mcp-pub and network access from scripts to the signed storage.googleapis.com download links that Kabo tool results return (without it, a host that can save a tool's complete result to a file). Kabo permission, for comments only: data."
allowed-tools: get_research_capabilities get_research_data_job get_research_data_download youtube_list_comments tiktok_discovery_list_comments tiktok_discovery_list_comment_replies
---

# Develop Content Ideas

Turn validated upstream artifacts or an explicit topic into an IdeaSet of distinct, selectable
ideas. The schemas, builders, validators and renderers ship in this Skill's `references/` and
`scripts/`. When the host supports one, run this Skill in a foreground sub-agent, because a run has
several stages, and wait for its final result before replying; never run it in the background and
tell the user the report will come later. Without a foreground sub-agent, follow the same steps in
the main thread.

This Skill consumes validated research artifacts through `upstream_sources[]` and deterministic
semantic `idea_context`. `user_brief.text` preserves the user's request; it is not by itself proof
that a usable topic exists. Do not parse Creator-facing Markdown, use removed
`opportunities[]/opportunity_id`, use competitor outputs as hidden criteria, or search the web.
Never infer a business domain or content topic from a handle, account/display name, URL, platform,
or another Skill's description. Do not create a SelectedIdea until the creator explicitly chooses
or edits an idea.

Before writing artifacts, read the fields and referenced definitions for this task intent in
`references/idea-request.schema.json` and `references/idea-set.schema.json` together, and read
`references/core-generation.md` (stage timing and the two-call core budget) whole before prep.

## Set up one run

`<skill directory>` is the folder holding this `SKILL.md`. Reserve exactly one run per request:

```sh
python3 '<skill directory>/scripts/static_host.py' init --workspace '<user-workspace>'
```

It prints the run directory; below, `<run directory>` is that absolute path, and it is the
`--run-dir .` the stage commands name. Many hosts keep neither environment variables nor the working
directory between commands, so every command that needs the run starts with
`cd '<run directory>' &&`, and the ones that read the run's staging area also set
`KABO_STATIC_RUN_DIR='<run directory>'` inline, exactly as written below. `scripts/…` and
`references/…` live in `<skill directory>`; call scripts by that absolute path. No step here
pauses and resumes, so no command takes `--placed`.

- **Every command below is complete as written.** Do not run a script with `--help`, read its
  source, or search other Skills. Use `grep`, not `rg`. Quote filesystem paths in commands.
- Write `intent.json` and `idea_set.json` at the run root with a heredoc
  (`cat > <run directory>/intent.json <<'JSON' … JSON`) or the host's file tool. Keep the timing
  file and every upstream artifact in this run directory for the whole run; never invent another
  run path or reset prep. One run serves one request: never reserve a second run for the same
  request, and start a different request in a new run.
- Make the model authorship and the one permitted revision between stage commands, never inside
  one.
- A failed or unavailable **tool call** is evidence, not the end of the run: record it and continue
  as `references/comment-evidence.md` says. When `static_host.py fetch` or `stage` refuses a result
  because it carries no connector evidence, because its job is still queued, or because it holds no
  complete evidence object, that refusal means the tool call failed; it is not a script failure.
  Read the tool's own message it prints and retry by error kind as `references/comment-evidence.md`
  says. A nonzero exit from any other **script** ends the run (for core, see step 5): report it, and
  never reopen the run, repeat completed prep, reset the generation budget or start a replacement
  run. Do not write or claim a run-record status yourself; core succeeding does not make one.

## Where evidence comes from

This Skill runs no research. Upstream evidence is the JSON output another Skill already wrote in
this workspace:

| Evidence | Produced by | Prep flags |
|---|---|---|
| Account review and creator profile | `review-creator-account` (`account_review.json`, `creator_profile.json` in its report directory) | `--account-review`, `--creator-profile` |
| Trend report and its Idea projection | `research-youtube-trends`, `research-instagram-trends`, `research-tiktok-trends` | `--trend-report` with `--trend-context` |
| Competitor report and its Idea projection | `analyze-creator-competitors`, `analyze-tiktok-competitors` | `--competitor-report` with `--competitor-context` |
| VideoAnalysis | `analyze-content-video` | `--video-analysis` |
| A video's comments | fetched by this Skill (below) | `--comment-context` |

Copy each upstream JSON file into this run directory unchanged (`cp`) and pass the copy. A trend
or competitor report counts only together with its `*_idea_context.json` projection; when the
projection is missing, treat that source as missing and name the producing Skill and its Idea
projection step as the next step. Never write a projection by hand. Every
artifact flag takes the JSON artifact, never its Markdown report (`account_review.json`, not
`ACCOUNT_REVIEW.md`). A report summary, a description of an earlier reply or model-retyped JSON is
not a replacement for the original file; if the original is no longer available, follow the
missing-source decision rather than inventing account context. Restored inputs remain reused evidence with their original
limits; they do not imply a new account fetch or deep-viewing projection. The deep account review
writes no Idea projection; use the standard review it extended. When a required artifact is not in
the workspace, read `references/evidence-acquisition.md` whole: it says which claims need which
evidence and how to name the Skill that produces it. Skip it when supplied evidence or the explicit
brief already satisfies the request. Never ask the creator to transcribe public
data or copy results; follow the prep decision below.

**Comments.** For ideas based on a video's comments, this Skill fetches them itself with the
tools in `allowed-tools`. Read `references/comment-evidence.md` whole before the first call: it
holds the calls, the commands that save, fetch and persist each complete result, and the
projection.

## Prep

Split every run into **prep** and **core**; do not move idea authorship or revision into prep to
evade the core budget. `build_idea_request.py` derives `source_mode` from the actual supplied
artifacts and projections; leave it out of the intent. Trend/competitor context alone becomes
`context_only`, and missing evidence never becomes `evidence_rich`. Required-source gates and
lineage validation still apply. Collection failure does not authorize brief fallback.

1. Determine the request's `task_intent` and preserve the exact requested Idea/Hook count and
   original user wording in `user_brief` when present. If the user asks for ideas but does not
   specify a Hook count, set `hook_option_count` to `1` (one opening per idea); never emit `0` for
   a generative shortlist. Choose between `open_shortlist`, `capacity_plan`, `rework_content` and
   `brief_only` as *Choosing task_intent* in `references/idea-set-rules.md` says.
2. Fill `generation_basis` before synthesis:
   - `direct_topic` is a literal content-topic phrase the user supplied. A handle, account/display
     name, URL, platform, format, or generic request for ideas is not a topic. Never derive it from
     another Skill's description, the environment, or model knowledge.
   - `required_sources` lists evidence the user explicitly requires: `creator`, `account`,
     `trend`, `competitor`, `video`, or `comments`. A request about comments on a video requires
     `comments`; it does not also require `video` unless the Creator asks to analyze the clip
     itself.
   - `allow_brief_fallback` defaults to `false`; set it to `true` only when the user explicitly
     permits using `direct_topic` if required evidence is missing. When you set it, copy the part
     of `user_brief.text` that gave the permission into `brief_fallback_quote`, word for word and
     in the user's own language: the check is that the span is really in the brief, not that it is
     worded any particular way. Do not paraphrase it, and do not set the flag on a brief that does
     not say this.
3. Validate each native upstream artifact before synthesis: sparse `creator-profile.v3`,
   `account-idea-context.v1`, `trend-research-report.v3`, `competitor-research-report.v3`, and
   optional VideoAnalysis. Consume only the deterministic `idea_context.creator/trend/competitor/account`
   projection the upstream Skills write.
4. Write `intent.json` with all required request fields except `upstream_sources` and
   `idea_context`. Never put either field in `intent.json`, even empty: prep builds both from the
   artifact flags and rejects an intent that carries them. Include
   `schema_version="idea-request.v3-candidate2"`, a run-local unique `request_id`, ISO-8601
   `created_at`, `task_intent`, counts, goal, audience, desired action, platforms, languages,
   `report_language`, `user_brief`, `generation_basis`, production constraints and exclusions; the
   builder does not invent missing metadata. Then run one prep command that builds, validates, and
   decides without further model branching:

   ```sh
   cd '<run directory>' && python3 '<skill directory>/scripts/run_ideas_stages.py' prep --intent intent.json --run-dir .
   ```

   Add the artifact flags this run has: `--creator-profile`, `--account-review`, `--trend-report`
   with `--trend-context`, `--competitor-report` with `--competitor-context`, `--video-analysis`,
   or `--comment-context`. Account evidence reaches prep as `--account-review <copied
   account_review.json>` alone: prep projects that review into `account_idea_context.json` in this
   run itself. Never write or edit an account context by hand; an `--account-context` that differs
   from the review's projection is rejected. If a flag points at a non-JSON file, prep exits `0`
   with `status="needs_correction"`, lists the flag, path and JSON files beside it, and changes
   nothing in the run: rerun the same prep in the same run with the JSON path. Prep writes
   `request.json`, `decision.json`, and the prep section of `stage_timing.json`. Do not synthesize
   before reading that decision.
5. Obey the decision exactly:
   - `blocked_missing_required_source`: write an empty IdeaSet, add every canonical `missing_required_source:<source>` reason, and set `interaction.next_actions` to include all of `blocked`, `refresh_evidence` and `provide_own_direction` (never `choose`). Then run core once to validate/render the empty block (this is not a creative generation success).
   - `needs_selection_context`: write an empty IdeaSet with
     `explicit_content_topic_or_upstream_evidence` in `selection_missing`; ask for a topic or
     evidence. Clarification is not a completed idea-generation task.
   - `ready_for_generation`: continue into Core. When `brief_fallback_used=true`, carry every
     missing-source token into `evidence_summary.data_gaps` and do not claim the missing evidence
     informed the ideas.
   - An explicit niche plus a concrete deliverable is sufficient `direct_topic` for brief-only
     generation when the user did not require external evidence. Do not ask the creator to choose
     between a quick and broad research path after the deliverable is clear; choose the least
     expensive valid path and deliver the requested candidates. If the user names an account,
     trend, competitor, video, or comments as an input, preserve that requirement and use the
     evidence path above or the missing-source block rather than asking them to select a research
     mode.

## Core

Core starts only after prep reports `ready_for_generation` (prep opens `stage_timing.json`
core.started_at at that moment). The target is **at most two** model generation passes that write
or revise `idea_set.json` (generate, then revise only if programmatic validation fails). Do not
invent extra critique/rewrite loops. Network, 5xx or host truncation retries are allowed and are
recorded with `cd '<run directory>' && python3 '<skill directory>/scripts/run_ideas_stages.py' core
--run-dir . --record-network-retry "<note>"` (this does not consume a generation attempt).

1. Apply explicit production constraints. Record only assets the user supplied or evidence shows
   are available.
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

   Every core call is non-final, so its one permitted revision runs in the same run.
5. Read the script's structured stdout (`stage`, `status`, `errors`, `revision_remaining`) as well
   as its exit code. Both `ok` and recoverable `needs_revision` exit `0`; the latter keeps the run
   open but does not validate or render a deliverable.
   - `status="ok"` with report paths (exit `0`): run step 7, then deliver. The `--concise` output is the sole Creator-facing artifact: `RESULT.md`. The `--full` output is an audit-only artifact; keep `IDEA_REPORT_FULL.md` in the run directory and never deliver it or copy it to `RESULT.md`.
   - `status="needs_revision"` with `revision_remaining=1` (exit `0`, unvalidated): use the returned errors and `revision_brief.json` to revise `idea_set.json` **once**, then run the same core command in the same run directory. Do not rerun prep, reset the attempt counter, or replace this runnable revision with an unvalidated prose answer. If the working file cannot be read directly, write the corrected file into the same run directory from the retained IdeaSet content and returned errors.
   - `status="not_met"` (exit `3`): record not_met from `stage_timing.json`; do not start another
     creative pass or loosen validation.
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

`build_idea_request.py`, `validate_ideas.py` and `render_idea_report.py` exist for the stage
runner; the stage runner is the required execution path so fixed steps are not each re-decided.

## What may not be claimed

Read `references/idea-set-rules.md` whole before writing `idea_set.json`; it holds the per-intent
contract and ranking rules. In short:

- Never invent loss amounts, customer outcomes, efficiency gains, market facts, policy facts,
  numbers, URLs or metrics to satisfy a schema. Exact numbers trace to evidence.
- `source_backed` only when upstream/context refs and evidence refs are present; otherwise
  `brief_based` or `hypothesis`. `why_now` is an evidence-backed claim only when a dated source
  supports it; otherwise say timeliness is unverified.
- A request for account performance, trends, competitors, Creator context, or a specific video
  cannot silently become a brief-only request; without the evidence and without explicit fallback,
  return the missing-source block without ideas.
- `selection_status` remains `candidate` until the user selects an idea.

## Report language and conversation context

Set `report_language` on both IdeaRequest and IdeaSet to the language requested for the final
report and interaction (BCP 47 tag), separate from `language_targets`. Infer it from the current
user request unless explicitly specified earlier; preserve quotations, titles and supplied creative
text verbatim, and generate all explanatory fields, questions and rationale in that language. Every
explicit `report_language` also needs `evidence_gap_translations` entries. Read
`references/report-language.md` whole before writing `idea_set.json`.

Before asking for a topic, audience, goal or evidence, reuse the relevant conversation up to the
current turn. Do not import subsequent replies. When no explicit topic or usable evidence exists,
keep the existing generation gate and ask only for the topic/niche (or an account/evidence
source); a platform name and a requested idea count do not supply a niche. Normal clarification is
not a completed idea-generation task.

## Delivery

Before delivery, read the complete `RESULT.md` from the run directory, continuing through every
chunk when the host's file reading pages. `status="ok"`, the final check and the original rendered
`RESULT.md` are the delivery gate. Copy `RESULT.md` exactly to the Creator-facing surface, as
natural prose: the structured objects remain the source of truth. Deliver it once, keep the audit
files in the run directory, and do not append a second paraphrased report. An invalid or partial
result keeps its actual status, evidence gaps and source limitations. When a missing upstream
artifact blocked the request, name the Skill that produces it as the next step.
