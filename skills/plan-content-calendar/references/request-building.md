# Building the request

Before writing the request, read the fields and referenced nested definitions needed for this request mode in `references/content-calendar-request.schema.json` and `references/upstream-selected-idea.v2.schema.json` together. Use the pipeline in `SKILL.md` for command syntax; inspect additional definitions only when needed to resolve a specific uncertainty.

Write all refs relative to one isolated run directory. Every ref must include `path`, `artifact_id`, exact `schema_version`, and SHA-256. Absolute paths and `..` escapes are invalid. Write each ref's `path` and `schema_version` (an asset ref's `asset_id`, `path` and `media_type`), then run `cd '<run>' && python3 '<skill directory>/scripts/calendar_ref.py' content-calendar-request.json`: it reads every `artifact_id` and `sha256` from the files. Never type an id or a hash.

For every `items[]` entry:

1. Require one `selected_idea_ref` using `selected-idea.v2`. A request to organize supplied topics does not confirm a proposed audience, CTA, format or production capacity. Preserve proposal/unknown provenance where supported; if the upstream contract requires explicit confirmation, ask for only those missing choices and keep any advisory draft separate. Never manufacture `confirmed` fields to satisfy a validator.
2. Bind `target.account_ref`, `platform`, `idea_format`, `production_format`, and `publish_format` to the SelectedIdea and downstream contracts. `target.idea_format` must exactly match a member of the bound `SelectedIdea.idea.formats`; it is not a normalized platform publish enum or a new paraphrase. Preserve the distinct downstream production/publish formats instead of substituting them for the Idea format.
3. Set `priority` only from explicit Creator priority or reviewed upstream order, and record `priority_source=creator|upstream_order`. Never create model preference.
4. Record actual progress in `production_progress={state,source,as_of,remaining_minutes,blockers}`. Only Creator-confirmed or workflow-system observations may use a non-`unknown` state. `as_of` must not be later than Request `created_at`. `complete` uses `remaining_minutes=null` and no blockers; `blocked` names at least one blocker; unavailable progress is exactly `unknown + unavailable + null as_of + null remaining_minutes + []`. Never infer progress or remaining work from a model, format heuristic, ScriptPackage approval, or declared asset shape.
5. Declare final `publication_assets` with hash-verified run-local refs. Its `status` describes the final `media_refs` set only; `cover_ref` independently represents a final platform cover/thumbnail. A Package cover brief is not a rendered cover, and a missing YouTube `cover_ref` must not make an already confirmed video appear missing.

Use these upstream refs and joins:

- `selected_idea_ref`: required `selected-idea.v2` per item;
- `candidate_idea_set_refs`: optional `idea-set.v3-candidate1` backlog, never a slot source;
- `script_package_request_ref` + `script_package_ref`: optional, but either both are present or both are `null`; use `script-package-request.v1` and `script-package.v1`;
- a `packaging_only` Package must point through its ScriptPackageRequest to a readable source ScriptPackage; that source must itself be `approved_for_production`, `approval_status=approved`, contain a script, and match the same SelectedIdea, platform, production format and language;
- `publish_request_ref`: required whenever PublishHistory, PublishRecommendation or PublishSchedule is present;
- `publish_history_ref`: required with `publish-recommendation.v2` and may also preserve a partial history-only chain;
- `publish_recommendation_ref`: required with `publish-schedule.v1`;
- `publish_schedule_ref`: optional; accepted or scheduled instants and state must be preserved exactly. In a Calendar-managed Timing call, pass the bound `SelectedIdea.selected_idea_id` as `PublishSchedule.draft_ref` even when a ScriptPackage already exists. Never use Calendar `item_id`, a temporary run ID, or the replaceable ScriptPackage ID as this cross-Skill content identity. Keep the exact ScriptPackage version separately in `PublishRequest.content.source_script_ref` when Timing actually consumed it;
- confirmed `choose_for_slot` additionally requires `single_slot_decision.pending_request_ref` to the exact pending `content-calendar-request.v1` and `proposal_ref` to its exact pending `content-calendar.v1` result;
- any nested `creator-profile.v3` ref in ScriptPackageRequest or PublishRequest must identify the same Creator account and, when both are present, the same profile artifact.

The host supplies `created_at` and, on confirmation, `confirmed_at`; do not read the machine clock. Interpret timezone provenance exactly: `creator_confirmed` means the Creator directly confirmed the IANA zone; `profile_confirmed` is an explicit Host assertion from a trusted account/profile setting; `assumed` is provisional. A `creator-profile.v3` artifact proves account identity and joins only—it contains no timezone field and cannot by itself justify `profile_confirmed`. A Creator cadence time is a preference, not platform evidence. If neither a Creator time nor a valid PublishSchedule supplies an exact instant, keep `publish_at=null`.

## Project structural publish preparation honestly

`publish_prep.status=complete` means the declared structure is complete for handoff, not that the platform accepted the media.

Require all of the following before returning `complete`:

- the current ScriptPackage is `approved_for_production` with `approval_status=approved`;
- for `packaging_only`, the source ScriptPackage also meets that approval pair and supplies the inherited script;
- the recommended headline, caption/description and cover/thumbnail brief exist;
- script asset placeholders, production-plan missing assets and placeholder/prohibited claims are resolved;
- `publication_assets.status=confirmed` and the referenced final media exist with matching hashes;
- the declared media matches the native publish family:
  - carousel/album/multi-image, or `idea_format=carousel`: at least two image/video refs;
  - reel/short/video/TikTok: at least one video ref;
  - image/photo/picture/pin: at least one image ref;
  - audio/podcast: at least one audio ref;
  - unknown native format: add `confirm_platform_media_requirements` and remain `partial`;
- YouTube additionally has a confirmed image `cover_ref`.

Do not infer the media family from `production_format=short_form|long_form`; use `publish_format` and the explicit carousel exception above. Even `complete` does not prove codec, dimensions, aspect ratio, duration, platform policy, account authorization, upload or scheduling. Runtime/Connector media preflight remains mandatory.

Project actual production progress separately from structural publish preparation:

- Copy `production_progress.state/source/as_of/remaining_minutes/blockers` without rewriting it. The report must label this as the observed production state and label `publish_prep` separately; neither may substitute for the other.
- If `state=complete`, set `production.due_at=null`, `due_status=not_applicable`, and charge zero remaining weekly capacity. Do not show an invented historical estimate.
- If `state=not_started|in_progress|blocked`, apply `production_lead_hours` when an exact `publish_at` exists and use only the supplied `remaining_minutes` in capacity arithmetic. A blocked state preserves its reported blockers.
- If progress or remaining work is unknown, keep due/capacity unknown and add `production_progress_unavailable`; do not infer a number from the Idea, format, Package, or model.
- Only a Creator/workflow-reported `complete` state **and** `publish_prep.status=complete` can enter publish-preparation handoff.

## Preserve ownership boundaries

- **Ideas owns what to make.** Only `selected-idea.v2` can occupy a slot. `idea-set.v3-candidate1` is backlog only and must remain `candidate_requires_selection` until the Creator selects or edits it.
- **Script & Packaging owns how to make it.** Copy approved Package fields; never draft missing headlines, captions, scripts, covers or assets here.
- **Publish Timing owns evidence about when.** Preserve its request, history, recommendation and schedule lineage. Never recompute a recommendation or turn `no_clear_window` / `abstain_*` into a generic best time.
- **Calendar owns planning.** It combines immutable content/timing decisions with cadence, capacity, blackout and spacing constraints and projects structural publish preparation.
- **Calendar exposes content identity without rewriting it.** Every slot copies a compact `content_preview` (audience problem, promise, angle and Hook) from its bound SelectedIdea so a Creator can distinguish the planned items. It never drafts replacement copy.
- **Calendar reports semantic diversity.** `summary.distinct_content_count` counts conservative semantic keys among the SelectedIdeas assigned to `slots[]`. It is not the target slot count, planned slot count, input item count or artifact-ID count. The key removes only explicit enumeration suffixes and otherwise uses title, audience problem, promise, angle, Hook, outline and differentiation.
- **Runtime owns confirmation and durable actions.** It performs idempotent calendar writes and creates or activates reminders only after scoped user confirmation.
- **An OAuth-authorized Connector owns platform execution.** It performs media preflight, upload, scheduling, publishing and receipt writeback.
- **Trial Reels, variants, frequency experiments and post-performance analysis are `LATER`.** Do not add experiment fields or present a normal slot as an experiment. First-two-hour attention reminders are notification templates, not an R02 analysis loop.

## Project structural publish preparation honestly

`publish_prep.status=complete` means the declared structure is complete for handoff, not that the platform accepted the media.

Require all of the following before returning `complete`:

- the current ScriptPackage is `approved_for_production` with `approval_status=approved`;
- for `packaging_only`, the source ScriptPackage also meets that approval pair and supplies the inherited script;
- the recommended headline, caption/description and cover/thumbnail brief exist;
- script asset placeholders, production-plan missing assets and placeholder/prohibited claims are resolved;
- `publication_assets.status=confirmed` and the referenced final media exist with matching hashes;
- the declared media matches the native publish family:
  - carousel/album/multi-image, or `idea_format=carousel`: at least two image/video refs;
  - reel/short/video/TikTok: at least one video ref;
  - image/photo/picture/pin: at least one image ref;
  - audio/podcast: at least one audio ref;
  - unknown native format: add `confirm_platform_media_requirements` and remain `partial`;
- YouTube additionally has a confirmed image `cover_ref`.

Do not infer the media family from `production_format=short_form|long_form`; use `publish_format` and the explicit carousel exception above. Even `complete` does not prove codec, dimensions, aspect ratio, duration, platform policy, account authorization, upload or scheduling. Runtime/Connector media preflight remains mandatory.

Project actual production progress separately from structural publish preparation:

- Copy `production_progress.state/source/as_of/remaining_minutes/blockers` without rewriting it. The report must label this as the observed production state and label `publish_prep` separately; neither may substitute for the other.
- If `state=complete`, set `production.due_at=null`, `due_status=not_applicable`, and charge zero remaining weekly capacity. Do not show an invented historical estimate.
- If `state=not_started|in_progress|blocked`, apply `production_lead_hours` when an exact `publish_at` exists and use only the supplied `remaining_minutes` in capacity arithmetic. A blocked state preserves its reported blockers.
- If progress or remaining work is unknown, keep due/capacity unknown and add `production_progress_unavailable`; do not infer a number from the Idea, format, Package, or model.
- Only a Creator/workflow-reported `complete` state **and** `publish_prep.status=complete` can enter publish-preparation handoff.
