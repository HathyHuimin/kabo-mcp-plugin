# Request modes

Choose exactly one request intent before building `content-calendar-request.v1`.

## `four_week_plan`

Use this mode for requests such as:

- “plan my content for the next four weeks”;
- “build a four-week plan at two posts a week”;
- “export the calendar as CSV or ICS”;
- “put the selected content, my production capacity and the publish times into one schedule”.

Set `intent=four_week_plan`, `single_slot_decision=null`, and provide at least one `cadence_rule`. The result must use `projection_mode=full_calendar`.

## `date_range_plan`

Use explicit inclusive `planning_window.start_date` and `end_date` for calendar-month or other bounded requests. Resolve “two months” into real calendar dates with the host's date context and Creator timezone; clarify whether it means named calendar months or the next two months when ambiguous. Never substitute eight weeks or 56 days. Set `weeks=ceil((end-start+1)/7)`, from 1 to 13 (at most 91 days), `intent=date_range_plan` and `single_slot_decision=null`. Longer plans need a separately scoped continuation, not silent truncation. Cadence applies to seven-day blocks anchored at start; the last block includes only dates through end. Capacity overrides refer to those same block indices. A final partial week retains the Creator's declared capacity; ask for an override if their actual availability differs.

The result is a full calendar with its exact date range; slots outside it are excluded. Four-week and single-slot requests retain the original 28-day behavior. Runtime must support the new intent before accepting its write handoff; local CSV/ICS/report generation does not authorize external writes.

## `choose_for_slot`: proposal stage

Use this mode when the Creator asks which already-selected item should occupy one exact slot, for example “which one do I post at 4pm today?” or “what should this account post tomorrow at four in the afternoon?”. Resolve the date and time in the Creator's IANA timezone, not the machine timezone.

On the first turn:

1. Set `intent=choose_for_slot` and `confirmation_status=pending`.
2. Create stable `decision_id` and `idempotency_key` values.
3. Put the exact offset datetime in `target_publish_at` and the exact account/platform/format in `target`.
4. List only already-selected request items in `candidate_item_ids`.
5. If the Creator named a preference, record it as `creator_preferred_item_id`; this is still a proposal, not confirmation.
6. Keep `confirmed_item_id=null`, `confirmed_at=null`, `pending_request_ref=null`, and `proposal_ref=null`.

The result must use `projection_mode=focus_slot_patch`. A proposal may identify one item, but its slot stays `proposed`, `requires_creator_confirmation=true`, and `action_scopes=[]`. An exact time in the initial question confirms the target time only; it does not confirm which content should occupy it.

## `choose_for_slot`: confirmation stage

Treat a follow-up such as “that one”, “confirmed, publish A”, or an equivalent explicit approval as content confirmation only when it refers to an existing pending decision. Preserve both immutable artifacts the Creator reviewed: the exact pending `content-calendar-request.v1` and its validated pending `content-calendar.v1` result. The confirmation Request must use a new `request_id` and a host-supplied confirmation `created_at`, then bind the two artifacts in `pending_request_ref` and `proposal_ref`. Apart from `request_id`, `created_at`, and the confirmation fields (`confirmation_status`, `confirmed_item_id`, `confirmed_at`, `pending_request_ref`, `proposal_ref`), every field and array order must remain identical to the pending Request. Reuse the same `decision_id` and `idempotency_key`. Fail if either artifact is missing, changed, unreadable, mutually inconsistent, or does not represent the same pending decision. Build it, in the confirmation's own run, with `cd '<run>' && python3 '<skill directory>/scripts/confirm_slot_request.py' --pending-request <pending Request> --proposal <pending result> --request-id <new id> --created-at <host time> --confirmed-at <host time> --item-id <item the Creator confirmed> --output <confirmation Request>`; it copies the pending Request and writes only the confirmation fields and both refs. When the Creator confirms an item other than the proposed one, it refuses: record that preference in a new pending Request instead.

The confirmed result locks only that slot and may request `upsert_calendar_slot`; it must never request `save_calendar` or replace a four-week calendar. Content-choice confirmation is not external-action authorization: `runtime_handoff.confirmation_scope.status` remains `not_granted`, and Runtime must separately confirm every scoped calendar, reminder or publish-preparation action.

If the Creator asks to upload, schedule or publish, finish the Calendar result and return the scoped handoff. Do not perform the side effect in this Skill.

## Clarify once, then fail closed

For an artifact-backed calendar, ask one consolidated question when a required target, account, date, Creator timezone, selected item, cadence, or source artifact is missing. First deliver any independent advice or fact-only review that the supplied information supports; the native input gate must not block those routes. Relative expressions such as “today”, “tomorrow” and “four in the afternoon” must become an offset datetime valid in the Creator's IANA timezone. If an exact instant needs an unknown timezone, ask; never read the machine timezone. A general weekly routine does not need a timezone. Use an explicit scenario date/time from the brief for a historical review instead of silently substituting today.
