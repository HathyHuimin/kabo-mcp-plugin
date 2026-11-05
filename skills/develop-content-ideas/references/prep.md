# Evidence and prep

Read this whole file before writing `intent.json`. Commands use `<run directory>` and
`<skill directory>` exactly as `SKILL.md` defines them.

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

## Prep steps

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
   - `direct_topic` is an exact content-topic span from the current `user_brief.text`, not a
     paraphrase (“sourdough shaping techniques”, not “bread tutorials”). A handle, account/display
     name, URL, platform, format, or generic request for ideas is not a topic. Never derive it from
     another Skill's description, the environment, or model knowledge.
   - An explicit request for reusable patterns or fill-in templates is itself a concrete creative
     subject. Use the current request's exact template-topic phrase; keep the requested slots and
     pair each pattern with its requested example. Label filled examples as illustrative, not as
     the user's industry or verified results. Do not require an industry just to deliver a general
     template; ask only when tailoring to an unspecified industry is part of the request.
   - For a follow-up such as “10 more in the same style”, read *Continuations* in
     `references/core-generation.md` whole before writing the intent.
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
4. Write `intent.json` without `upstream_sources`, `idea_context`, `continuation_context` or
   `conversation_context`, not
   even empty: prep derives them from the flags and rejects an intent that carries them. Include
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
   or `--comment-context`. A continuation also takes either `--previous-ideas` or
   `--conversation-context`, as *Continuations* in `references/core-generation.md` defines.
   Account evidence reaches prep as `--account-review <copied
   account_review.json>` alone: prep projects that review into `account_idea_context.json` in this
   run itself. Never write or edit an account context by hand; an `--account-context` that differs
   from the review's projection is rejected. Prep writes `request.json`, `decision.json`, and the
   prep section of `stage_timing.json`. Do not synthesize before reading that decision.

   Read prep's `status` even on exit `0`: `needs_correction` (a non-JSON or missing artifact, a
   nonliteral topic, a malformed locale) is not generation permission. Fix every listed `issues[]`
   entry once and rerun the same prep in the same run; never invent identifiers, switch the report
   language, drop required sources or reword the user's brief to pass. A second invalid input ends
   prep as `not_met`.
   `invocation_error` (exit `1`) for an unreadable `--intent` file is separate: correct that path
   or its permissions and repeat the same prep command in the same run. No authored input was
   read, so this neither consumes nor resets the input budget. Other I/O failures remain terminal.
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
