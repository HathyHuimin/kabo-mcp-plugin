# Analysis plan fields

One `traffic-workflow-brief.v1` object with `questions`. `drive.py analyze` inherits platform,
account, source and original request, so its plan needs nothing else; a plan for `fetch
--brief-file` or `prepare` also gives `user_request` verbatim, and `prepare` the `platform` and
`account`. Optional top-level `coverage`, beside `questions`, is a text array of needs the selected
modules cannot resolve. Supplied data may declare
`source:{kind,locator}`; kinds are `user_supplied`, `owner_export`, `account_analysis`.

Each question needs `kind` and `intent` (`explain`, `diagnose`, or `act` for requested next steps).
Optional `id`, `ask`, `selection_reason` describe the user's need and evidence selection.
Set `platform_status_check` only for a requested restriction or eligibility check.

## Prepare location

Prepare into this request's run directory, the one `static_host.py init` reserved, with
`--output-dir '<run directory>'` (or `.` when already working in it). Do not reserve a second run
for the same request or choose an unrelated writable directory.

## Performance

`kind`: `recent_drop` or `growth`; fields: `metric`, `format`, ordered `groups`.
Each supplied group has `label` and `values`. Optional aligned arrays: `content_ids`, `date_labels`,
`published_at`, `observed_at`, `published_on`, `observed_on`, `post_age_hours`.
Question-level `post_age_hours` applies only when all records have that stated age.
Group-level `post_age_hours` may also be one stated age applying to every row in that group.

AccountAnalysis groups use `record_ids` or `select`, never both. A selector filters by optional
`format`, `published_from` (inclusive), `published_before` (exclusive; a bare date is UTC midnight), then sorts oldest first
and applies zero-based `start` (default zero) and `stop` (exclusive; omitted means all).
Unknown dates cannot be selected chronologically. Decide ranges from the ask or inspect records first.
Different content formats remain separate in the calculated evidence.

Only user-stated numeric goals use question-level `target:{value,basis}` and sibling
`target_source_text` quoting the goal exactly. Basis is `each_record` or `group_median`.

Calendar totals use `growth_basis:period_total`, `metric`, `population`, and groups with
`label`, `window`, `value`, `posts_published`. Account-period traffic is not per-post performance.

## Traffic sources

`kind:traffic_sources`; fields: `total_metric`, `population`, `source_name`, two ordered `groups`.
Use one common `window` or `post_age_hours`. Each group has `label` and aligned arrays of
`totals`, `counts`, `shares`, `share_tolerances`, `content_ids` as available. Shares are fractions;
preserve nulls and the original measurement population. Window is `kind:post_age` with `hours`,
or `kind:calendar` with `start` and `end`.
Additive count metrics are `views`, `plays`, and `impressions`; their names retain the supplied
definition. `reach` and `unique_viewers` require deduplicated totals, not sums across posts.
Redundant group `post_age_hours` must match the common window for every measurement.

## Audience

`kind:audience`; fields: `dimension`, `population`, `groups` and optional `target`.
Each group has `label`, `shares` mapping categories to fractions, and a known `window` if supplied.
Target has `desired_categories`, `criterion`, `source_text`; criterion is `largest_share`,
`increase`, or an object with `kind:minimum_share` and the user's `value`.
Do not substitute followers for reached viewers or geography for language or interests.
