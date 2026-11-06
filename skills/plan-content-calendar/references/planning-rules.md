# Deterministic planning rules

## Four-week planning

- Generate exactly four weeks from `planning_window.start_date` and the declared cadence.
- Within an initially authorized design task, the host may prepare the requested format and cadence choices before binding the selected artifacts. Once bound, do not rewrite their format, promise, title or Hook merely to spread posts across weeks and then regenerate refs. A new hash proves integrity, not approval. If an even distribution would change a Creator-confirmed selection or cadence constraint, explain that conflict and ask about that change; do not add a new approval step for choices already authorized.
- Assign all exact-time items before flexible items; accepted/scheduled and Creator-locked exact times are immutable. Then sort flexible items by trusted `priority`, then `item_id`.
- Preserve accepted/scheduled PublishSchedule time, ref and state. If it conflicts with cadence, blackout, capacity or spacing, keep the commitment visible and report the conflict; never silently move or erase it.
- Keep conflicted slots visible for review, but treat a collision symmetrically: every participant is blocked until the Creator resolves it. None of those slots may carry reminder records, ICS `VALARM`/post-receipt templates, or reminder/publish-preparation scopes. Unrelated safe slots in the same draft may continue independently.
- When multiple SelectedIdeas collapse to the same semantic content after enumeration suffixes are removed, keep the requested draft visible but clear reminder records and exclude every affected item from ICS reminder projections and reminder/publish-preparation handoffs until the Creator confirms an intentional series or replaces it.
- Keep blackout cadence slots visible in `unfilled_slots[]` and items that cannot fit in `unscheduled[]`.
- Keep unknown capacity as `unknown`; keep overload as `exceeded`. Do not claim feasibility.
- Treat only observed `production_progress.state=complete` as finished production. Structural Package/asset completeness is a separate publish-prep fact and must never manufacture progress. Continue lead-time and capacity checks from reported remaining work for non-complete progress.
- Detect numbered clones conservatively: normalize Unicode and whitespace, remove only an explicit trailing enumeration suffix in any language, such as `#2`, `(2)`, `Part 2`, `Pt. 2`, `Ep. 2`, `Episode 2`, `No. 2` or `Vol. 2`, then compare title, problem, promise, angle, Hook, outline and differentiation. Keep every Creator-selected item, but add `near_duplicate_selected_ideas`, return `partial_needs_user_input`, and ask the Creator to confirm an intentional series or replace the repeats. Do not treat a meaningful year or other terminal number as an enumeration suffix.
- When that gap is present, `summary.distinct_content_count` is lower than `summary.planned_slot_count`; report the difference plainly instead of presenting every numbered slot as a distinct direction.
- If `planning_window.timezone_source=assumed`, cadence dates may be drafted but cadence `preferred_local_times` must not become `publish_at`. Keep `publish_at=null`; propose at most `save_calendar`, with no pre/post reminder or publish-preparation handoff until the Host or Creator supplies a non-assumed timezone source.
- Return `projection_mode=full_calendar`. Action scopes may propose `save_calendar` and eligible reminder/publish-prep handoffs, but they remain unexecuted and require Runtime confirmation.

## Single-slot selection

Evaluate candidates in this order:

1. a confirmed item in the confirmation stage, unless it conflicts with an accepted/scheduled commitment;
2. the unique existing accepted/scheduled commitment at the exact target time;
3. the Creator's explicit preferred eligible item;
4. the unique operationally ready item;
5. the unique best trusted Creator/upstream priority among ready items;
6. otherwise require Creator choice; never break a tie silently.

An item is operationally ready for this selection only when it is target/time eligible, actual production progress is `complete`, and structural `publish_prep` is `complete`.

- Pending stage: `focus_slot_patch`, proposed slot, `action_scopes=[]`.
- Confirmed stage: a new immutable confirmation Request whose unchanged payload is proven by `pending_request_ref` and whose selected result is proven by `proposal_ref`; same decision/idempotency identity, locked slot, scoped `upsert_calendar_slot`; never `save_calendar`.
- Both stages: no side effect. Confirmed content selection still leaves action-level confirmation `not_granted`.

## Timing and reminders

- Treat `PublishSchedule.draft_ref` as the immutable SelectedIdea identity for this Calendar flow. It must equal both the loaded `SelectedIdea.selected_idea_id` and `selected_idea_ref.artifact_id`. Adding or replacing a ScriptPackage later must not invalidate an existing Schedule that was built from that SelectedIdea; when a PublishRequest names `source_script_ref`, validate that version independently.
- Copy `target.publish_format` and the matching cadence rule's publish format from the bound PublishRequest, using the same exact word as its Recommendation cohort and History rows. Public YouTube history uses `youtube_long` or `youtube_short`; do not replace these with a generic video label. Keep the SelectedIdea's content language in the timing Request even when this Calendar report is in another language. A Schedule rebuilt for a newly selected item also needs a normally rebuilt, selection-bound PublishRequest and matching Recommendation; an old unbound Request cannot be repaired by changing only the Schedule's draft reference.
- Keep the full PublishSchedule behind its hash-pinned `publish_schedule_ref`. Project only the fields Calendar needs for display and decisions; do not duplicate `state_history` or manufacture a second mutable Schedule object inside ContentCalendar.
- `engine_recommended` is legal only when the bound recommendation says `recommended` and the instant falls inside its window.
- A Creator time inside a recommendation is `creator_choice_within_recommendation`; outside it is `creator_override_of_recommendation`; without one it is `creator_choice_not_recommended`. Creator timing confidence stays `null`.
- Never replace `no_clear_window`, `abstain_no_history` or `abstain_no_publish_time` with a generic best time.
- An assumed timezone cannot anchor an exact cadence publish time or any time-dependent handoff. A draft full calendar may still request confirmation to `save_calendar` with untimed slots.
- Pre-publish reminders anchor to `planned_publish_at` and may become ICS `VALARM` entries or the scoped Runtime action `create_pre_publish_reminders`.
- Compare every computed pre-publish trigger to the immutable Request `created_at`. Omit a trigger when it is at or before `created_at`; do not create a stale Runtime reminder or stale ICS `VALARM`. A later configured trigger for the same slot may remain eligible.
- Post-publish attention reminders anchor to real `connector_published_at`; keep `at=null` until a Connector receipt exists. ICS stores only `X-KABO-POST-PUBLISH-REMINDER-TEMPLATE`, and Runtime may request `activate_post_publish_reminders_after_receipt` only as a receipt-gated action.
- Reminder and publish-preparation scopes require an exact future `publish_at`, non-assumed timezone, and no unresolved calendar or semantic-duplicate conflict for that slot. ICS may project reminders only for the same scoped slots. Publish preparation additionally requires observed production `complete`, complete structural prep, capacity within, no reported/planning blockers, and a slot that is not already `scheduled`.

## Fail closed (full list)

Stop with explicit errors for a bad hash, unsafe path, wrong Schema/ID, reused confirmation `request_id`, changed pending payload, missing or changed `pending_request_ref` / `proposal_ref`, invalid or downgraded timezone provenance, a timezone inferred from `creator-profile.v3`, a non-`unknown` production state without `creator_confirmed|workflow_system` provenance, an internally inconsistent `production_progress` tuple, an exact duplicate SelectedIdea artifact or ID, target mismatch, PackageRequest/Package mismatch, unapproved `packaging_only` source Package, Publish Timing lineage mismatch, accepted/scheduled movement, conflicting commitments, or an engine-recommended instant outside its evidence window.

Do not use web search, another Skill, model memory, generic platform advice, a different artifact, or generated replacement content to make an invalid request pass. Do not silently select a candidate Idea, infer missing production effort, rewrite upstream content, treat a cover brief as a final asset, treat structural readiness as platform preflight, grant standing authorization, create an external reminder, upload, schedule, publish, or write back.

Preserve these hard product contracts verbatim:

- `never choose on the creator's behalf`
- `No generic platform best-time fallback`
- `No upload, schedule, publish or external write side effect`

An upstream artifact may truthfully say a schedule was already accepted or scheduled; preserve that state as evidence. Never claim this Skill caused the state. The highest Skill-owned plan state remains `ready_for_review`.
