# Metric derivation and income rows

## Deriving the metrics

`scripts/derive_monetization_metrics.py` applies these rules. They are written out so the numbers can
be checked, not so they can be worked out by hand.

- `youtube.subscribers` is the channel's published subscriber count.
- The channel's declared country arrives on the same `youtube_list_channel_uploads` response; use it
  as the region rather than asking, and mark it `account_declared`.
- `youtube.public_uploads_90d` counts uploads published within 90 days of the assessment date.
- `youtube.shorts_views_90d` sums public view counts for Shorts published within 90 days. Supply it
  only when the retrieved rows actually distinguish Shorts from long-form. When format cannot be
  determined, omit the metric: a total that silently counts long-form views as Shorts views would
  report a creator as approaching a bar they are nowhere near.
- `instagram.followers` is the profile's follower count.
- `tiktok.followers` is `follower_count` on the account-post response. It is nullable: when the
  upstream omits account stats it arrives as null, and a null must be left out of the request rather
  than entered as zero - zero would report a creator as needing the entire threshold.
- `tiktok.public_posts_30d` counts posts published within 30 days of the assessment date.
- `tiktok.views_30d` sums views for posts published within 30 days, and is a lower bound whenever the
  retrieved window does not reach back a full 30 days. Set `coverage_complete` honestly; the
  assessor uses it to decide which direction the number is allowed to argue in.
- Watch time has no public source on any platform. Never derive it from view counts by assuming an
  average viewed duration, and never enter such a figure as an observed metric.

Retrieved facts come from this script, not from a general public-snapshot assembler, which drops
the follower and subscriber counts these gates need.

## Income rows

Every plan carries `income_rows`: the platform-payout part of a "what does my income add up to"
answer, one row per programme, in the shape `references/income-row.v1.schema.json` defines. Kabo's
brand-deal pricing writes the same row shape, so the two can sit side by side in one answer. Each
row states the income stream, its unit, the volume and where it came from, the rate and the kind of
evidence behind it (`retrieved` from the creator's own account, `user_supplied`, `model_estimate`,
`rule_library` for a rate the platform publishes, or `creator_reported` for a rate other creators
report and nobody publishes), the period, what it is net of (platform share, tax), which other row
already contains it, and what it still lacks. A row with no rate carries no amount, only the path to
one. A priced row is always one of this plan's revenue scenarios, at the same range. An unpriced row
still opens `missing` with where the creator stands against the programme's bar. Brand-deal rows add
one more origin, `marketplace_observed`, for ranges read off observed marketplace listings; like
every origin it is never added to another.

When the ask is what the creator's income adds up to, set `income_breakdown` in the request, and list
`digital_products` under `streams_without_owner` when the creator sells them: nothing here prices
that income, so it becomes a row that says so instead of disappearing. The report then prints the
rows as a table.

Three rules hold here and whenever these rows are put next to brand-deal rows:

1. **An eligibility bar is not income.** A threshold says whether a programme is open to the
   creator; it is never an amount. A programme the creator has not qualified for keeps that in
   `missing`.
2. **Rows whose rates rest on different kinds of evidence are never added into one total.** A
   creator-reported rate, a modelled estimate and a published rate are not one measurement.
   `income_totals` only ever sums rows that share one origin, one period and one currency, and it is
   empty by default.
3. **What a row lacks travels with it.** Combining rows keeps every `missing` entry, every
   assumption and every "not yet eligible" in the reply, and never adds a row to the one named in its
   `already_counted_in`.

Never price a brand deal, a sponsorship or a digital product here.

## The rule library

`references/rules/platform-monetization-rules.v1/` holds `library.json` (metrics, benchmarks, niche
rates and the list of programme files) and one `program-*.json` file per programme. The scripts load
them back into the single object `references/monetization_rules.schema.json` describes.

Every rule carries its source, region, effective period, the date it was last checked against that
source, and the set of pages that must be re-read to know whether it still holds. The last two are
separate from the first three on purpose: an official page has been observed still stating a
threshold that had already been superseded, so "it cites the official page" and "it is currently
true" are two different claims.

And the page a rule is written on is not always the page its change appears on. YouTube's 2027
thresholds were announced on the platform's blog and stated on a separate changes page while the
eligibility page this library cites said nothing about them - and still says nothing. A re-check
that reads only `source_url` would have come back clean on a rule that had already moved, which is
why `watch_urls` lists every page to read rather than the one page to cite.
