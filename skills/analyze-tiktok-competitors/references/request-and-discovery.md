# Request, query plan and the discovery Gate

Read this whole file before writing the Request and the query plan. It covers the first two
judgments of a run. Commands carry the run directory and use `<skill directory>` exactly as
`SKILL.md` defines them.

## The frozen Request

`start` prints the Request and query-plan schemas. Freeze one `competitor-research-request.v3` with
`platforms: ["tiktok"]`, an exact UTC window, locale, the `tiktok_video` format, the subject
description, and either `auto` or `provided` competitor mode.

- **Supplied accounts.** When the user supplies competitor handles, keep those exact handles; they
  become the projected account universe while search rows stay visible evidence. Unresolved
  supplied handles are reported, never invented.
- **Latest N per account.** For a supplied-account request explicitly asking for each account's
  latest N videos instead of a date window, write `sampling: {"mode":"latest_per_account","count":N}`
  and omit `window`. Choose N from the user's request, never from popularity or a script's
  interpretation of the question. N must be an integer from 1 to 100, not a boolean. Sampling and a
  date window cannot be combined. Without sampling, the date window (default: last 30 days) applies
  unchanged.
- **`as_of`.** `discover` freezes one real UTC `as_of` when omitted and refuses any later Request
  change; an explicit timezone-bearing `as_of` is preserved.
- Leave `schema_version`, `request_id`, each intent's `intent_id`, and the `window` when it is the
  default last 30 days, out of the Request and the plan: `discover` fills them and keeps any value
  you write. It fills only those mechanical fields; it does not remove unknown fields or repair the
  meaning of a Request.

The printed JSON Schemas describe allowed data; they are not the data files. Do not copy
schema-definition keywords (`$schema`, `$id`, `title`, `type`, `properties`, `required`,
`additionalProperties`) into either instance. For automatic discovery, replace the bracketed values
in these minimal authoring shapes with the user's actual subject, target region and delivery
language:

```json
{"subject":{"name":"<subject name>","description":"<subject description>"},"platforms":["tiktok"],"locale":{"region":"<target region>","language":"<delivery language>"},"formats":["tiktok_video"],"discovery_mode":"auto","competitor_refs":[]}
```

```json
{"intents":[{"search_query":"<compact subject category or audience job>"}]}
```

These are shapes, not a ready Request or evidence. Keep the exact supplied handles, sampling or
explicit date span when the user requested them. A schema rejection is a failed step: correct the
named field, or stop when it is not an authored field.

## The query plan

Write one `tiktok-trend-query-plan.v1` (file `tiktok-query-plan.json`) with one or two compact
category or audience-job phrases.

- Build a category phrase from the subject's own words — the product-form noun and the channel from
  its bio, captions or website description, translated into the market's language — never from a
  broader term you generalised to. When one of its channels is TikTok itself, use another, because
  TikTok's own name matches every tool account here.
- Each `search_query` is two to five whitespace-separated words; a Chinese, Japanese or Korean
  phrase may instead be one 2–20-character phrase without spaces (do not insert dummy spaces just
  to pass the validator). A longer description fails `discover`.

## The search region

Choose `region` as an explicit ISO 3166-1 alpha-2 code from this request's content language or
target market. Prefer the frozen `locale.region` when it is a two-letter market code other than
`CN`; otherwise map the keyword script/language (Chinese → `TW`, with `SG` or `HK` also valid;
Japanese → `JP`; Korean → `KR`; else the known target market). Never send `CN` on this search
endpoint — the provider rejects it or returns empty pages for Chinese keywords. Never omit `region`
— a missing region defaults to `US`, which often mismatches non-English niches.

## What `discover` does

`discover` validates the plan (a rejection names the field to fix) and resolves `publish_time` from
the frozen Request: one of the enum days `0|1|7|30|90|180`; for `recent_30d` it is `30`; with
latest-N sampling it is `0`. It then hands over `tiktok_discovery_search_videos` once with
`sort_type` 0 and once with `sort_type` 1 for each intent, each call made on its own, never
batched: the frozen intent phrase as `keyword`, `count` 30, and the same `region` and
`publish_time` on every call. It places every response into `discovery-envelopes.json`, builds one
strict candidate set, and prints the discovery projection as a numbered account table (the subject
and locale on the first line, then one line per account with its handle, name and visible posts)
and the Gate line format. A failed route is kept as that route's evidence; do not repeat a search.

## The discovery Gate

Read only the discovery projection `discover` printed; do not open the file again. Classify every
numbered account exactly once as direct, adjacent, media or excluded and assign 0–100 scores for
category fit, audience-job fit, sustained focus and locale fit: one line per account in
`tiktok-competitor-discovery-gate.tsv`,
`<n> <relationship> <category_fit> <audience_job_fit> <sustained_focus> <locale_fit>`, every number
exactly once, nothing else on the line. `content` builds `tiktok-competitor-discovery-gate.v1` from
those lines and refuses a missing, duplicated or out-of-range line; fix the lines it names and run
it again.

- Product/category equivalence is required for direct; a single relevant post is insufficient.
- Do not use views, followers, rank or route in the semantic judgment. Relevance decides
  eligibility; public popularity and retrieval recurrence only rank already eligible accounts.
