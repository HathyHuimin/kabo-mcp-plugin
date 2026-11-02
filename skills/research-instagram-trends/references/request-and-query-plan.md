# Workflow judgment 1: the Request and the query plan

Read this file whole before writing `trend-research-request.json` and `trend-query-plan.json`.
`drive.py start` has printed the source Reel (when one was supplied) and both schemas; write from
those, and do not open the schema files again.

## Source Reel

With `--post`, `start` reads the source Reel with `lookup_instagram_post` (with `include` for the
comment preview, tagged users, location and collaboration fields) and `instagram_get_transcript`,
and prints what the Request is written from — the author (`owner.username`), the caption, the
transcript, the public numbers, the top comments, tagged users, coauthors, location and the paid
partnership flag; fields the lookup could not return are left out and listed under `unavailable`. Do not
call those tools yourself unless the stage hands the calls over; the source Reel's own words are the
only place the source-wording intent can come from, so read them before you write the plan. A
failed call whose envelope was staged is printed as "not resolved" or "none" and the scan goes on;
a call whose result could not be staged at all stops `start --placed`, and the run cannot continue
(report that the source Reel could not be read, with the tool's own message).

## The Request

Build one `TrendResearchRequest`. Freeze niche, locale, Reel format and exact UTC window. Leave `locale.region` and `locale.language` null unless the user names a market or content language ("Spanish videos", "in Brazil"); then quote those words verbatim in `locale.named_in_ask`; `discover` checks it against the ask from `--question-file -`. The ask's own language sets only the reply language. Resolve
relative durations from the current UTC clock, not from a run-directory name. Leave
`schema_version` and `request_id` out (`discover` fills them, and keeps any value you do write).

**The window.** Write the rest of the Request first; `discover` reads it and writes only the
window. Never type the window: pass the policy to
`drive.py discover --policy <default_1y|recent_90d|user_named_span> [--since <ISO> --until <ISO>]`,
which freezes it once per run with `scripts/freeze_request_window.py`. Classify the ask once and let
the script own the dates:

- `user_named_span` when the user named a span or dates (pass it as `--since`/`--until`, kept
  exactly);
- `recent_90d` when the ask says recent / lately / now, in any language, without a number (the last
  90 days);
- `default_1y` when the ask names no time at all — a niche scan, a reach question, or "find hot
  videos like this one" (the last year, so the scan reaches as many strong posts as the channel
  can).

The same three policies run on every trend channel; a short-window copy ask (seven days or less)
keeps its own path. A seven-day-or-shorter ask for which Reels beat their creator's own usual, or
for any other account-relative multiple, is not a copy ask: it stays in the Workflow with
`--policy user_named_span` and that span. A month of a niche rarely contains its own biggest posts,
so a recency window answers a reach question with the wrong content rather than with less content.
`discover` prints the script's result, including `instagram_lanes`: the discovery lanes this window
calls for and each lane's collector bucket. When it prints `reaches_since: false`, the span starts
before Instagram discovery can reach; keep the coverage incomplete rather than widening anything. A
named span that ended more than a year ago fails there, before any collection.

The window is the most consequential field in the Request: **three separate rules downstream switch
on whether it spans more than one month** — how many intents may carry a `popular_topic`, whether
discovery runs one lane or two, and what the report window becomes. A window frozen at thirty days
silently turns all three off at once, and it does not fail: it produces a normal-looking
single-lane report over recent content, with no signal anywhere in the artifacts that the reach half
of the question was never asked. (Observed: a reach question asked in plain words —
which posts went furthest — was frozen as a thirty-day window, and the run went out with one lane,
zero Popular intents and no reach content, looking healthy throughout.)

**The niche.** Write `niche` as one narrow subject line, never a list of adjacent topics: a niche
that bundles several subjects makes every adjacent tutorial read as relevant and destroys the Gate's
ability to separate same-format from merely same-subject content. Record the Creator's recurring
moves in `creator_profile.observed_content_mechanisms` and their handle in `creator_profile.handle`
instead of widening the niche to cover them; both are projected into the Gate input and are the only
format evidence the scorer receives.

**Whose handle.** When the ask supplies a video instead of naming an account, the Creator is that
video's author: take the handle from what the source tool returned for it (`owner.username`, which
`start` prints), never from an account this machine has on record. (Observed: a run wrote
the machine's own registered account into `creator_profile.handle` while the tool had already
returned the real author in the same run; the handle is projected as `anchor_handle`, so the Gate
scored `creator_transferability` against the wrong person.)

**Caption phrases.** When an account review supplied a creator profile, attach the Creator's
caption phrases with
`cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/attach_caption_phrases.py' --creator-profile <the account review's creator-profile.v3> --request trend-research-request.json`,
chained after the Request heredoc and before `discover`: it copies `content_profile.caption_phrases`
into `creator_profile.observed_caption_phrases` verbatim, in order and in the original content
language, and leaves the field out when the review supplied none. Never type them into the Request
yourself. Mechanisms and caption phrases are not interchangeable: mechanisms are prose written for
the Gate to reason over; caption phrases are the Creator's literal wording and exist to be typed
into a search box. Never paraphrase, translate or tidy the caption phrases, and never invent them
when the review did not supply any.

## The query plan

Build one `trend-query-plan.v1` with at most six intents, each carrying one compact
two-to-five-term Search phrase. Leave `schema_version`, `request_id` and each `intent_id` out
(`discover` fills them; each intent's id must be unique when you do write one).

- Every intent must stay on the asked mechanism. Do not add adjacent-industry phrases
  (`B2B SaaS founder`, generic `conversational AI` research talk) that pull podcasts and LinkedIn
  commentary into a messaging-sales ask.
- Prefer the short caption words the format is searched with — the shape `<channel> sales`,
  `<channel> automation` or `<channel> funnel`, in the language the format is posted in — over
  longer analyst sentences (`<channel> sales workflow`). Use all six slots on those mechanism
  phrases when the ask names several product surfaces; do not spend a slot on a neighbouring market.
- When the plan is built from one supplied video rather than an account review, spend at least one
  slot on the format's common name — the words another community captions the same format with,
  carrying none of the source video's own wording. (Measured across three channels: plans
  that took every intent from the source's own words produced no such intent at all, and only the
  plans that reserved a slot for the common name reached outside the source's own vocabulary; the
  Creator's own wording retrieves a strict subset of their own format.)
- When `creator_profile.observed_caption_phrases` is present, at least two intents must reuse one of
  those phrases **exactly** as their `search_query`, and at least two more must be variants that
  keep the head noun of a caption phrase and change only its qualifier. The Creator's own wording
  names the format correctly but is only one community's name for it: a Creator who captions
  `<qualifier> <form noun>` makes the same content that other communities caption with a different
  qualifier on the same form noun. (In the measured run one such community variant surfaced nine
  confirmed same-format accounts against three for the Creator's own phrase.) Holding the head noun
  fixed keeps a variant on the format; changing the qualifier reaches the rest of the community.
- Discovery matches literally against the captions other creators write, so the Creator's own
  caption wording is the only query source known to retrieve their format; a mechanism restated in
  analyst vocabulary is no substitute, however accurately it describes the format. (Observed: for a
  Creator whose captions repeat one form phrase, that phrase retrieved the format's biggest post at
  search rank 1, while two faithful analyst rewordings returned pools with no same-format content.)
- Derive any remaining intents from `creator_profile.observed_content_mechanisms`, still preferring
  the plain words the format is captioned with over an industry term of art that names a different
  artifact. A subject phrase retrieves same-subject content; only a format phrase retrieves
  same-format content, and same-format is what this report exists to find.
- For a non-English market whose niche uses English product terms, include local-language and
  English intents. Locale owns market/language fit; do not inflate every query into a sentence.

### Popular topics

Attach a `popular_topic` to **every** intent when the Request window spans more than one month, and
to at most one when it does not. Unused `popular_topic` fields must be omitted, never null. Popular
returns a topic's all-time top posts, so how much of it lands inside the window is a function of the
window, not a property of the route: against a one-month window every row resolved months to years
early (the origin of an older one-intent cap); against a one-year window seventeen of twenty-three
resolved inside it. Size the route to the window in front of you.

Write each `popular_topic` as its intent's Search phrase with only the plural collapsed —
`<qualifier> <form nouns>` becomes `<qualifier> <form noun>` — and **never strip the qualifier down
to the bare head noun**. A topic is matched against the whole platform rather than a niche, so a
word like `design` returns that word's all-time champions instead of the format's (measured:
`design styles` shortened to `design` pulled in a door manufacturer at 123M plays and a metal
fabricator, and dragged the route's median to 548K; with the qualifier kept it returned
graphic-design accounts at a 97K median). Judge a topic by whether its top rows are the format's
practitioners, not by how large its play counts are; on this route a rising median is the symptom of
a topic that has gone too broad.

### Why the collection budget is fixed

Search still returns dates without plays, and Popular still returns plays without a provider-side
window. The server also runs one in-window Reels-search challenger page per intent: those rows come
back with both plays and dates, then get locally filtered to the requested window. That challenger
is a supplement, not a replacement (measured: one benchmark niche gained 6 windowed-hot
items in the Top 12, 3 of them Y+; another gained 12). The validator writes the collector budget
`search_pages_per_intent=2`, `popular_pages_per_intent=1`, `challenger_pages_per_intent=1`,
`max_provider_calls=24`, `max_candidates=160`, `max_detail_calls=0`: six intents need 12 Search + 1
Popular + 6 challenger pages, and 20 calls would starve the challenger that actually returns
in-window plays. Never turn the challenger off, omit it, lower the call budget, or spend the
challenger budget on extra Popular pages; never rebuild the collector input by hand.

## Writing both files and starting discovery

Write the Request and the plan with heredocs and start discovery in the same command (chain them
with `&&`, with the caption-phrase command between the Request and the plan when it applies):

```bash
cd '<run directory>' && cat > trend-research-request.json <<'JSON'
…
JSON
cat > trend-query-plan.json <<'JSON'
…
JSON
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' discover --policy <default_1y|recent_90d|user_named_span> [--since <ISO> --until <ISO>] --question-file - <<'ASK'
<the ask, verbatim>
ASK
```

`discover` freezes the window (once per run: running it again keeps the window it froze), then
validates the plan before collection, once per lane, with `scripts/validate_query_plan.py` against
the `discovery_window` that `freeze_request_window.py` printed for that lane — never a bucket picked
by hand. The validator also projects nullable Request locale fields into legal optional tool fields.
For two lanes it emits a separate input file for each lane from the same frozen Request and plan:
`trend-collection-input.json` for the relevance lane and `trend-collection-input-reach.json` (with
the `last-year` window) for the reach lane. A rejected plan names the field to fix: fix the plan and
run the same command again.

Before discovery fetches, the driver checks only the local authored Request and plan with the
original validators. A `needs_correction` response is non-final: correct the named authored input
once in this same run and repeat `discover` with its required window arguments. It records the
original bad bytes and error under `analysis/discovery-input-errors/`; a second invalid attempt, any
data already begun, a frozen-window conflict, an I/O or internal failure stays fatal. Never prune
Search intents, widen the window, recollect or replace a failed run.

Once collection starts, keep `trend-research-request.json` immutable; `discover` keeps the window it
froze even when the Request is written out again. The ranking builder intentionally intersects the
frozen window with the discovery window; this is not clock drift and does not require editing the
Request. Do not move its dates to make a later command pass.
