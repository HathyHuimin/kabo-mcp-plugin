# Internal canonical traffic contract

This is the deterministic compiler target, not a second model-authored input. Do not author
these shapes: model input is [traffic-brief.md](traffic-brief.md) for plans and
[traffic-synthesis.md](traffic-synthesis.md) for the written analysis; field names here differ. Extend
`traffic-workflow-brief.v1` and its compiler when the public contract needs a new shape.

One `traffic-case.v1` JSON object selects only the requested questions. Keep original
exports/Envelopes; this projection does not replace them. Numerical normalization is
literal (percent → fraction); do not fill unknown fields from public proxies.

## Shared fields

- `platform`: `instagram`, `tiktok`, `youtube`; `account`: exact normalized identity.
  Supplied data without a handle may use the user's declared dataset identity, not
  an invented handle. An optional `--analysis` must match both fields exactly.
- `sources`: `{id, kind, locator}`; kind is `public`, `owner_export`, `user_supplied`
  or `synthetic`. Locator identifies the original file/row, URL or user turn.
- `coverage`: optional verbatim notes for supplementary evidence. AccountAnalysis coverage
  is copied into `imported_snapshot.coverage`; omit its notes here. Computed findings describe
  missing fields and comparability; do not add inferred claims or duplicate group descriptions.
- `questions`: ordered `{id, kind, ask, selection_reason, groups}`. `ask` is the
  user's specific question in the delivery language. `selection_reason` explains why
  those records answer the question; leave dates, counts and statistics to the computed
  group evidence instead of duplicating them here or in `coverage`. `groups` is an array of
  `{label, record_ids}`; records may be reused across independent questions, but
  not duplicated between groups of one comparison. An empty group is a missing
  input, not zero. Unknown kinds are rejected; clarify rather than mapping blindly.
- Optional question `intent` is `explain`, `diagnose` (default) or `act`, based on the
  user's ask. For `act`, a report without next steps says that none was formed.

Exact timestamps have timezones. Calendar intervals are half-open `[start,end)`;
date-only endpoints are allowed when both are dates, without inventing a timezone.
Counts are finite nonnegative numbers or null. Missing is not zero. Source and
record IDs are unique. Validators reject unknown fields and conflicting counts.

## Performance (`recent_drop`, `growth`)

`performance` rows:
`{id, source_ref, content_id, format, metrics, date_label?, post_age_hours?, published_at?, observed_at?, published_on?, observed_on?, url?}`.
Use `post_age_hours` for an explicitly supplied age ,
without making up dates. Use `date_label` to preserve a supplied date verbatim when
its role is unknown; it does not establish publication, observation or chronology.
Use `published_on` / `observed_on` only for dates explicitly identified as those
events. `_at` means an actual timestamp, `_on` a date, not both for one event.
Age is derived only from actual timestamp pairs or a supplied age, never date subtraction.
Metrics map names to numbers/null. Select the actual metric with question `metric`.
When every accepted record has an exact publication timestamp, the renderer derives
the group's date range from those records instead of trusting a model-written label.
No likes fallback. One observation per content per comparison; groups are earlier
to later. Known publication dates must be disjoint; otherwise the output identifies
that chronology follows the supplied groups. Recent drop has exactly two groups;
growth may have several user-relevant periods. Different formats are calculated
separately, never pooled to fill a missing baseline.

For an existing AccountAnalysis, use `--analysis` and omit performance rows: each ranking's `content_id`
becomes its record ID. Scope, rankings and the entire analysis are bound by digest.
This reuses normalization but recalculates prior-only cohorts instead of importing
baselines that may contain the recent group. Existing shared Analyzer files are unchanged.

Optional question `minimum_age_hours` requires `age_policy_reason` explaining the
user's chosen or explicitly proposed analysis convention. No default “mature” age
is applied. Unknown ages remain unknown; an explicit age filter excludes them.
Matching ages support
an age-matched descriptive comparison, not identical exposure or a causal conclusion.
An optional numeric `target` is the user's metric goal, not a detection threshold. It requires
`target_basis`: `each_record` when every selected record must meet the value, or `group_median`
when the user explicitly defines success by the cohort median. Target attainment is reported
separately from descriptive medians.

Per-group median, maximum, sum and sample size remain descriptive. Sample sums are
not calendar-period traffic; do not infer total account growth from them. Calendar
totals require actual period analytics. Partial history describes only that history.

For actual calendar traffic, set a growth question's `growth_basis` to `period_total`
and select `period_totals` records, one per group. These rows are
`{id, source_ref, metric, population, window, value, posts_published}`; window must
be a calendar interval, posts_published is a count or null. Windows are equal-length
and chronologically disjoint. The output shows total change and publishing counts
separately; dividing total traffic by newly published posts is not per-post performance
because the traffic can include older content. Default `growth_basis` is `per_post`.

## Traffic sources

`traffic_sources` rows:
`{id, source_ref, metric, population, source, window, content_id?, total?, count?, share?, share_tolerance?}`.
Use two groups. Each measurement must describe the same source, metric and
population; `window` is either `{kind:"post_age", hours}` (requires content_id) or
`{kind:"calendar", start, end}`. Compared windows must have equal duration; calendar
groups must be chronologically disjoint and each group internally aligned.

`share` and `share_tolerance` are fractions, not percentages. Tolerance is allowed
only for known source rounding, not to conceal conflicting numbers. Actual counts
take precedence; otherwise count = total × share is explicitly an estimate.
Aggregated share = sum(source counts) / sum(totals). Do not average percentages.
Post-age groups also report per-post source mean/median and total mean; especially
when group sizes differ, sample-sum changes are not per-post performance changes.
Only additive views/impressions can aggregate across posts; unique reach needs a
deduplicated total. No total or count means that part stays unknown.
Retain partially supplied records: known matching totals belong in these rows even
when source count/share is null. Do not discard a known measurement because another
field is absent. Reuse content IDs across modules for the same observations; do not
pair records whose population or window has not been established.

## Audience

`audience` rows:
`{id, source_ref, dimension, population, window, shares}`. `shares` maps reported
categories to fractions; partial distributions may sum below one. Do not treat
omitted categories as measured zero.
Use `window: null` when the measurement window was not supplied. Preserve the
distribution with that limitation; do not replace an unknown window with today's
date or a zero-hour post age. This does not establish a time-matched comparison.

Question `target`: `{dimension, population, categories, criterion, definition}`.
`criterion` is `{"kind":"largest_share"}`, `{"kind":"minimum_share","value": <fraction>}`
or `{"kind":"increase"}`. `definition` is
`{"status":"user_explicit","source_text": <user wording>}` only when the criterion was explicit,
otherwise `{"status":"provisional","confirmation": <short confirmation question>}`. Preserve the
user's success condition; do not silently turn qualitative wording into a confirmed threshold.
Compare only identical dimensions and populations. A follower breakdown is not a
reached-viewer breakdown; a language goal is not a country goal. Missing target
categories in partial distributions produce bounds, not a false exact percentage.
Do not infer an interest match from these demographic fractions.

## Validation

`validate_traffic.py` independently recomputes the assessment against the case and optional
AccountAnalysis. This validates the calculations and evidence, not the written analysis.
