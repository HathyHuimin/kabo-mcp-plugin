# Pricing rules

Read this whole before writing `analysis/request.json` the first time in a run. `SKILL.md` keeps the
short form of each rule; this file keeps the reasons and the edge cases.

## Decision rules

- **Two readings, never one.** Benchmark compilations disagree, sometimes several times over for the
  same account, because they draw tier boundaries in different places and survey different
  populations. Every band that matches is reported with its own boundaries. There is no combined
  figure anywhere in the output, and producing one by averaging would invent a precision that no
  source has.
- **Asking ranges and settled amounts are different populations.** Published rate cards run well
  above what money actually changes hands for comparable work. Both are shown, labelled for what
  they are. An all-tier average is never compared against one tier's band as though they measured
  the same thing.
- **Platform is priced once.** Bands are per platform already. There is no platform premium on top;
  applying one would charge for the same difference twice.
- **A term nobody prices is a term to settle, not a number to invent.** Revision rounds are the
  standing example: every guide says to cap them in writing, none of them prices an extra round.
- **No figure while the terms that decide it are open.** When usage rights or exclusivity are
  unspecified, the reply asks for them and states no number. This is the normal state of a first
  brief and it is not a degraded result.
- **The second route only when it disagrees.** Pricing from delivered views rather than follower
  count is shown only when the two routes diverge past the framework's threshold, and then the point
  is the direction of the gap: follower count flattering the reach, or reach outrunning followers.

## Income rows

Every quote carries `income_rows`: the brand-deal part of a "what does my income add up to" answer, in the shape `references/income-row.v1.schema.json` defines. That file and `scripts/income_rows.py` ship byte-identical in the platform-monetization Skill, so both Skills write the same row. There is one row per deliverable per published reading, because readings are never merged: the second reading of a line is the same money read another way, so it names the first in `already_counted_in` instead of being added to it. A reading from a published compilation carries the origin `rule_library`; a reading built from observed marketplace listings carries `marketplace_observed` and says the date it was seen, because what sellers asked on one day is not a published rate. While usage rights or exclusivity are open, a row carries no amount and its `missing` names the terms to settle - the same rule the quote follows. A line no published range covers gets a row with no rate and the reason.

Set `income_breakdown` in the request when the ask is what the creator's income adds up to; the report then prints the rows as a table.

Three rules hold here and whenever the main agent puts these rows next to the platform-monetization Skill's:

1. **An eligibility bar is not income.** Platform payouts and their thresholds belong to the other Skill; this one never writes a platform-payout row.
2. **Rows whose rates rest on different kinds of evidence are never added into one total.** `income_totals` only sums rows that share one origin, one period and one currency, and it is empty by default; the quote's own `package_totals` stay one per source.
3. **What a row lacks travels with it.** Combining rows with another Skill's keeps every open term, unpriced term and gap in the reply, and never adds a row to the one named in its `already_counted_in`.

## Comparables

The bands in the framework are compilations: somebody else's summary of what creators charge. A
stronger answer to "what am I worth" is a set of real accounts at the same size, in the same niche,
on the same platform, with their asking prices visible - the way a house is priced off recent sales
nearby rather than off a national average.

Creator marketplaces carry exactly that: public profiles listing a price per deliverable
("one short video, $500"), filterable by platform, niche, follower count and location. If you can
browse the public web, you may gather those observations and feed them in as
`marketplace_comparables`. There is no connector for this, deliberately - it is an optional
enrichment, and the run is complete without it.

Rules that make the result usable rather than decorative:

- **Respect the site.** If a marketplace refuses automated access, that refusal is the answer. Do
  not work around it, and do not switch to a different route to get the same page. Note that
  comparables were unavailable and continue; the sourced bands already cover the run.
- **Five observations minimum.** Below that the spread is noise, and a band built on two listings
  claims a precision it does not have. Fewer than five: report the count and skip the band.
- **Match on what actually matters.** Same platform, same deliverable type, and a follower range
  around the creator's own. A comparable with ten times the audience is not a comparable.
- **It is an asking price, not a settled one.** A marketplace profile shows what that creator wants.
  It never becomes evidence of what brands paid, and it never merges into the transacted figures.
- **Record what was observed, not what you concluded.** Each observation carries the follower count
  and the price. The distribution is computed downstream, so that the same observations always
  produce the same band.
- **Never fill a gap by estimating.** If a listing does not show a price, it is not an observation.

Given at least five observations, the pricing engine emits them as one more band, alongside the
compiled ones and equally unmerged, labelled as observed listings with the date and the sample size.
