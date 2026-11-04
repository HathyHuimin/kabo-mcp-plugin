# Plan rules: request, verdicts and rates

Read this whole file before writing `analysis/request.json`. It holds how to author the request,
how the assessment reaches a verdict, and what a rate can and cannot say.

## Authoring the request

Build a `monetization-request.v1` document straight after retrieval. Save it as
`analysis/request.json` before anything is derived: none of its fields comes from the account
facts, as the first rule below says, and authoring it here lets the step 4 commands run back to
back. Its fields are in `references/monetization_request.schema.json`. The rules:
- Leave retrieved account facts out of `observed_metrics`: the assessment merges them from
  `analysis/observed-metrics.json`, and a count retyped there is replaced. Only a figure the
  creator told you goes in, with `basis: "user_supplied"`, and it takes precedence over the public
  reading of the same metric; a figure you reasoned toward does not go in at all.
- `region` with `region_source`: on YouTube leave both out - the derive step fills the channel's
  declared country as `account_declared`. Write a country only when the creator stated it for this
  account (`user_stated`); a country without a source is read as unknown.
- `assumptions` carries every rate to be modelled, each with a `role` and an `origin`.
  `user_supplied` is a figure the creator gave you; `model_estimate` is one you worked out to
  model a scenario. Record which it is - an estimate filed as `user_supplied` is read back as
  the creator's own rate. The rule library supplies a third kind: where a programme publishes
  or creators report a rate, the assessment adds it to the ledger as `rule_library` with the
  URL it was read from. Nothing else invents a rate.
- A range is one assumption with `value` and `value_high`, not two scenarios.
- `tax_rate`, a percent, is the only way a scenario becomes after-tax. Supply it only when the
  creator gave one, or as a `model_estimate` you name as such.
- `niche_question` carries a which-niche-pays-more question: the niches named, and whether the
  creator also asked how crowded they are.
- Set `income_breakdown` when the ask is what the creator's income adds up to (see
  `references/derivation-and-income-rows.md`).

## Decision rules

- A rule decides eligibility only when it is verified, in force on the assessment date, and
  applicable to the creator's country. An unverified rule still describes its program; it never
  produces a verdict.
- A program holds several routes at once - a current bar, a future bar, an existing-participant bar.
  Each carries its own source and effective dates and is judged separately. Show every live route
  rather than the nearest one: a watch-time bar and a Shorts-views bar ask for different work, and
  which to chase is the creator's call.
- A reading that can only bound the rule's true metric is used in the one direction it is valid.
  Public Shorts view counts overstate qualified Shorts views, so they can show a creator is short of
  the bar and can never show they have cleared it.
- A published count that is rounded cannot resolve a threshold it sits on. Report that, rather than
  picking a side.
- Where a known requirement went unchecked, the verdict is capped below `eligible`. This is common
  and correct: account standing, linked payments and country lists are real gates that no public
  data settles.
- Revenue scenarios exist only where a volume and a rate are both on the table, and the plan says
  where each came from: the creator, an estimate made to model the scenario, or the rule library's
  published or creator-reported rate carried with its source URL. A scenario resting on an estimate
  is a model, and the report says so next to the number. No rate is conjured outside those three.

## Rates by niche, and what a figure is net of

**No platform publishes a rate per content niche.** Checked on the platforms' own pages: YouTube's own RPM/CPM help
page defines the terms and gives generic examples, and its blog post on ad relevance says CPM can be
viewed by ad type and country - neither carries a finance, education or home-decor figure. The sites
that do publish such tables are not the platform, and this Skill only reads its versioned rule
library. So a per-niche rate has no source to read, and there is no default to fall back on: say
that the figure is not published rather than producing one.

**That answer covers the rate, not the rest of the question.** "Which niche pays better" usually
arrives attached to "and how crowded is it" - a question about what is being made and how it is
performing, which is content research, not a payout rule. Answer the rate half honestly, then say
the competition half belongs to trend or competitor research and hand it back. Letting "no rate
table exists" stand as the whole answer drops a question the creator did ask. The plan carries it as
`niche_comparison.competition`, naming the research step and the question to ask, so the next step
can be run rather than rediscovered. The rate conclusion reaches only as far as the pages checked for
that platform: where the library records none, the report says the rate was not checked there
rather than that it is unpublished.

**Say which side of the platform's cut a number is on.** A rate the rule library carries is used as
the source states it; never apply a revenue share to a figure that is already net of one, and never
present a pre-share figure as what the creator receives.

YouTube's RPM is the case that decides most of these questions. Per YouTube's own
page (https://support.google.com/youtube/answer/9314357): "RPM is your total revenue (after
YouTube's revenue share) per 1000 views", and "RPM is based on several revenue sources, including:
ads, channel memberships, YouTube Premium revenue, Super Chat, and Super Stickers." So the YouTube
figure here is channel views times the creator's own RPM: the channel's total YouTube income after
the share, not ad revenue alone, with no share taken off again. The same page says "For Shorts, it
includes all engaged views. For Videos, it includes all views." - a channel RPM already covers
Shorts. Take the views and the RPM from the same place and date range in Studio; never price a
channel total with a long-form-only RPM, and never add a Shorts figure on top of a channel one.

Tax is modelled only from a `tax_rate` the creator gave or one you model as an estimate. Without one
the scenario is pre-tax, and the report asks for the rate rather than leaving a number that reads as
take-home.
