# Write the delivery instance, not another analysis

The semantic pass writes `analysis/creator-task-result.json` from this run's complete validated
PublishRecommendation. Keep its existing envelope and outcome-specific block sequence. This note
clarifies the existing shape; it supplies no card prose or new result-producing script.

Inside `creator_view`, explicitly write `language` in the user's delivery language, alongside
`headline`, `summary`, `cards`, `next_actions` and `data_note`. A language under the request's
`content` does not supply `creator_view.language`.

Each card's `why_for_you` is a **string containing two to four nonempty lines**. In JSON, encode
the line breaks as `\n` inside that string, for example `"why_for_you": "<grounded fact>\n<another grounded fact>"`.
Write actual grounded sentences in place of the placeholders, at the existing display-width limits.
`action` is a separate field: it does not count as a second line of `why_for_you`. Do not use an
array, a literal backslash followed by `n`, one paragraph, or filler lines to satisfy the count.
Keep each card's evidence references resolvable in this recommendation.

Keep the exact timezone source and analysis zone in the structured Request/Recommendation. When
the host supplied no creator timezone, preserve `not_supplied` and the engine's UTC analysis
fallback; never substitute the operator's zone or claim that a local clock was established.
Disclose the missing zone and fallback in the user's language, for example "The creator's timezone was not provided, so the analysis uses a fallback clock; do not read these hours as local time."
The Creator-facing fields still omit zone identifiers/names as the existing gate requires. Do not
translate or rename one to evade that rule, and do not present a fallback-clock interval as a
verified local posting time. The exact fallback remains available in the structured evidence.

State the actual collected scope. If a history response still has `more_available` and a cursor,
or collection otherwise stopped before exhausting the history, describe mature counts as this
run's retrieved sample, never the account's entire history. Preserve that partial-coverage boundary
in the concise `data_note` or grounded card lines in the user's language. A thin retrieved sample
does not prove the account has no more history. Keep the existing pagination contract and retain
every original envelope; this note adds no page budget, replacement fetch, threshold or formula.
