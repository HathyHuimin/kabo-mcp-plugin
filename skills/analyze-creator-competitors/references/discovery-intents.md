# Discovery intents

Read this whole file before writing `competitor-discovery-intents.json`. Route from the frozen
input, not from an improvised search loop: when the user supplies competitor handles, skip
discovery and retrieve those accounts directly (`provided` mode); when competitors are unknown,
freeze category, workflow or audience-problem intents before retrieval.

## Which language is «local»

«Local» means the language the subject's own audience is served in — read it off
`subject.market` or `locale.region`, never off the language this conversation happens to be in.
The two are unrelated: `locale.language` also carries the language the Report is written in, and
a Creator asking in Chinese about English-language Instagram content is the ordinary case, not an
edge case. Resolving «local» from the chat language spends half the discovery budget searching a
language the target content is not published in — measured on one creator subject, a
`zh-CN` request produced two Chinese local lanes, the whole run returned four accounts and zero
labelled competitors, while the same subject with all four lanes in English had reached four to
five.

- When no market or region is stated, the subject is not thereby «Chinese» or «local» to the
  asker: write all four lanes in the language the category's practitioners actually publish in,
  which on Instagram is English unless the evidence says otherwise.
- **A country account of the same brand states a market too.** When the identity candidates show
  the brand running a country-specific account beside its global one — a handle or name carrying
  a country code or country word (`brand.xx`, `brand_xx`, `Brand <Country>`), a bio in that
  country's language, or prices in its currency — the brand actively sells there: whichever
  account you resolve as the subject, set `subject.market` and `locale.region` to that country
  and write the two local lanes in its language, while the two English lanes cover the global
  side. Measured on one product with a global English account and a country account in
  another language with local-currency pricing: runs that resolved the global account and left the
  market empty wrote four English lanes and delivered no local-market competitor at all in three
  of five runs; runs that kept the market delivered five or six.
- Where a market is stated the local lanes are load-bearing and must be used — a subject selling
  into a non-English market genuinely needs lanes in that market's language, and dropping them
  costs the same recall from the other direction.

## Instagram: four lanes

Instagram auto-discovery uses exactly four complementary intents with the canonical ids
`local_category`, `local_workflow`, `english_category` and `english_workflow`: one local-language
concrete product-category noun phrase, one local-language buyer workflow/problem phrase, one
canonical English product-category phrase, and one canonical English workflow phrase.

- Each is a compact two-to-five-term category or workflow phrase. Only the two category lanes may
  carry a `popular_topic`, of two to four terms (omit it to reuse a `search_query` of up to four
  terms); workflow lanes carry none — a buyer workflow phrase is not an Instagram topic, and
  Popular Search returns not found for it. A Chinese, Japanese or Korean lane may instead be one 2–20-character
  phrase without spaces; do not insert spaces just to pass the validator.
- Do not reuse the same `search_query` on two lanes. Do not write sentence-like queries that stack
  audience, product and market into one clause.
- Persist the plan as `competitor-discovery-intents.v1` with this exact top-level shape —
  `schema_version`, `"platform": "instagram"`, `content_language`, optional `market_tokens`, and
  the four-lane `intents` array. Do not omit `platform`.

### Caption phrases come first

Discovery matches literally against the captions other creators write, so an intent phrased as a
description of the subject retrieves the people who get described that way rather than the people
making the same content. Measured on one Creator: four intents derived from the description
(course- and education-shaped phrases) returned 52 accounts containing zero labelled competitors,
while that Creator's own caption wording plus one head-token variant of it reached 13.

**When `subject.caption_phrases` contains phrases of two or more terms that name a content form,
ground the category lanes in those verbatim phrases, not in the product description.**

- What qualifies: a phrase that names a *content form* — the shape `<qualifier> <form noun>`.
  Single-term entries do not qualify and are skipped: a one-word entry is a topic hashtag that
  anyone in the market can attach; pinning it drags the query toward a generic term and retrieves
  the people who *talk about* the category instead of the people supplying it. Measured on one
  product subject whose four caption phrases were all single-token hashtags (a country name plus
  generic topic words such as `technology` and `automation`): pinning them produced generic
  two-word queries, and the delivered six contained shoutout and coaching accounts while two
  genuine competing products sat unselected in the pool.
- Calls to action and deictic leftovers from a single caption also do not qualify: a phrase whose
  first word is an imperative such as comment / type / reply / send, or a leftover such as `all of
  these styles` or `+100 MORE`. Those retrieve remix pages and resource-funnel accounts. A series
  title that still names the form remains eligible.
- Count terms rather than maintaining a stop-word list.
- **Take the first three qualifying phrases and reuse each one verbatim in a `search_query` of its
  own; the fourth must keep one of those phrases' head token while changing its qualifier.** Each
  pinned phrase needs its own lane — phrases often nest (`<qualifier> <form noun>` inside
  `<qualifier> <form noun> remix`), and letting one query stand in for two silently frees a lane
  to search something else. Which lane a phrase lands in does not matter: discovery runs all four
  intents together.
- Why pinned: the list is produced deterministically upstream, and pinning it removes a free
  choice that dominates recall. Two models handed the identical eight-phrase list picked almost
  disjoint sets of four, and across three repeated discovery runs each the labelled-account recall
  was 7 / 8 / 4 versus 5 / 9 / 9; the pinned first-three-plus-variant rule scored 9 / 8 / 11. The
  variant lane is load-bearing: the first four phrases verbatim with no variant scored 5 / 5 / 5,
  because anchoring every lane on the Creator's own wording only reaches the sub-community that
  already talks like them.
- After the filter, if exactly one phrase remains and it has three or more words, also pin its
  first two words as their own lane: a long series title nests a shorter form, and both need a
  `search_query`.
- When fewer than three phrases qualify, use all of them verbatim and fill the remaining category
  lanes with head-token qualifier variants of those phrases, not with a description-derived brand
  or course phrase, and not by padding with the phrases that did not qualify. Measured:
  two pinned form phrases plus a description-derived inspiration phrase recalled
  adjacent-category accounts and only one labelled same-form account. Then fill any remaining
  lanes from the description rule.
- Caption phrases serve only the form noun: never lift a style, aesthetic or lifestyle qualifier
  from the captions (such as `minimalist`) into any lane. Measured on one creator: a
  lane carrying a lifestyle qualifier lifted from the captions retrieved bloggers with almost no
  Reels in the window and both Reports came back with no patterns, while the plain
  profession-noun lane had delivered in every earlier run.

### Description rule (no qualifying caption phrases)

Ground both category lanes in the resolved subject's public product description and choose the
narrowest visible product noun plus delivery surface, not a broad benefit. A phrase that only
concatenates a channel with broad benefits such as automation and sales does not satisfy a
category lane: name what the product is on that surface (shape `<product noun> <channel>`, such as
a chatbot for a named messaging app), and keep a broad role or benefit phrase for the workflow lane
only.

**Local lanes keep the subject's own words.** Take the product-form noun (what the product is) and
the channel it runs on from how the subject describes itself — its profile bio, its own captions,
its website description as the user gave it — and translate them into the market's language;
never replace them with a broader term you generalised to. Every local-language `search_query`
keeps at least one of the two: the subject's channel word or its product-form noun.

- When the subject runs on several channels and one of them is the platform being searched, take
  another one for the local lanes: the searched platform's own name appears on every tool account
  there, so it matches the global English sellers instead of the local ones.
- Example: a fictional appointment tool described as a booking bot over Telegram and Instagram
  messages, selling in Germany, gets a local category lane like `Terminbuchung Bot Telegram
  Deutschland` — not `Automatisierung Tool Deutschland` (a generalised benefit) and not `Instagram
  DM Termine Deutschland` (the searched platform).
- Measured on one local-market product: once nothing held the local category lane to the
  subject's own words, the planner wrote `<automation> <searched platform> <market>`; profile
  search returned only global English tools, the delivered six held no local account and 17
  analysable posts, where the earlier `<product-form noun> <other channel> <market>` lanes had
  delivered three local products and 52 posts.

### The local workflow lane also steers profile search

Besides Search/Popular, `discover` sends `instagram_search_profiles` for the two category lanes
with compact market tokens stripped, plus one compact local variant: the first content token of the
local workflow plus the last token of the local category. Profile search matches account names and
bios, not captions, so the workflow lane's first word decides which sellers that variant finds.

- Measured on one local-market product: the variant shaped `<function noun> <channel>`
  returned about seven comparable local products in ten rows and barely overlapped the raw
  category query.
- A raw multi-word workflow phrase was mixed; a gerund-headed workflow phrase (`replying to
  messages` in the local language) returned greeting and faith accounts, and its verb-head variant
  returned zero, so the planner skips a variant whose workflow head ends in `-ndo` or `-ing`.
- Measured on the same subject: workflow lanes headed by a broad role or buyer-outcome
  word (`agent …`, `reply to customers …`) found adjacent agent and auto-reply services with no
  Reels in the window, and both Reports came back with no usable evidence, while runs whose
  workflow lane began with the subject's own function noun delivered.
- Start the local workflow lane with the noun the subject and its sellers use for what the product
  does — read it off the subject's recent captions and bio printed by `subject` — not with a verb
  or a buyer outcome.

### Market tokens

Locale and the later eligibility Gate own market and language as result filters — do not treat
market or language as a hard eligibility gate. When the Request names a market or region, each
local-language `search_query` must still carry one compact market token — the market's own name or
demonym as local captions spell it — so retrieval can match local captions; English lanes stay
global. Declare the token(s) you used in the plan's top-level `market_tokens` so profile search
strips exactly those words; when caption phrases pin the lanes, keep those phrases verbatim and
omit `market_tokens`. Measured on one local-market product: four market-free intents
never put a single local product account into the pool.

## YouTube: one or two intents

Freeze at least one and at most two intents; `search_query` may be a natural search phrase. Do not
copy Instagram's four-lane plan into a YouTube request. On the plan also write
`content_language`: the language the accounts you are looking for publish in, decided by the
«local» rule above (an ISO 639-1 code) — never the report's `locale.language`. The same rule sets
the language of each `search_query`: when no market is stated, write the queries and
`content_language` in the language the niche's leading channels publish in, which on YouTube is
English unless the evidence says otherwise — not the language this conversation is in. The
collector searches in it.

## Validation and correction

`discover` validates the Request window and the intents plan before retrieval. If intents
validation returns `status: needs_correction`, read its named issue and correct the authored input
while preserving the subject, market and intended meaning; never truncate a query mechanically.
Discovery retrieval has not started. Run `discover` again in the same run directory as the same
non-final stage. This is a pause for input correction, not a completed discovery stage or
permission to replace the run. Other execution or data failures still stop the run.

## What discovery returns

Once valid, `discover` hands over the candidate collector (`collect_instagram_trend_candidates` or
`collect_youtube_trend_candidates`) and, on Instagram, the planned complementary profile searches
together, places and merges them, and prints the Gate table. Each numbered line carries everything
discovery returned for that account — the public follower and post counts when the route gave
them, and for every one of its rows the published date, the play/like/comment counts, the lane and
rank it came back on, its content id and its caption in full.

The collector's recall window is bounded at sixty accounts. If the result reaches the ceiling, any
planned route is incomplete, or the returned status is partial, keep the persisted Candidate Set
but mark discovery coverage incomplete; never call that bounded window the complete market. Never
retype, reconstruct, trim or relabel the Candidate Set, and never change its status. Do not call
any shortlist-enrichment tool: those rank Trend content and skip this skill's author Gate.
