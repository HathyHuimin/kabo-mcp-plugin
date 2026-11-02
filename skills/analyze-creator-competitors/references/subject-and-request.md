# Subject, account scope and the frozen Request

Read this whole file before running `start`. It covers the first two judgments of a run: which
public identity is the subject, and the frozen `CompetitorResearchRequest`
(`references/competitor-research-request.schema.json`). Commands carry the run directory and use
`<skill directory>` exactly as `SKILL.md` defines them.

## Account-selection input

Freeze `discovery_mode` and `competitor_refs` from the user's scope before retrieval.

- `auto` discovers accounts with an empty reference list; references written under `auto` are
  kept and the run proceeds as `hybrid`.
- `provided` compares only the supplied public account handles or profile URLs (up to twenty). A
  profile tab URL (`/@name/shorts`, `/@name/videos`, `/name/reels/`) names that one account, not a
  content format: keep the Request's `formats`, the subject description and the findings to what
  the user asked in words, never narrowed to the tab's format.
- `hybrid` preserves those accounts and may discover additional peers.

Do not reinterpret a requested comparison as discovery, or require a separate subject-account
lookup when the user supplied a comparison topic and accounts. Start without `--query` when no
subject identity needs resolving.

For `provided`, write the Request and run `drive.py discover`: it skips discovery intents,
collection and the discovery Gate, resolves the requested identities, fetches their profiles and
prints the eligibility input. For `hybrid`, use the normal discovery stages; `select` pins the
requested identities before adding automatic candidates.

- YouTube handles must resolve to a channel ID through `youtube_get_channel` with matching `custom_url`;
  a search title or bio mention is not identity proof.
- A missing or ineligible requested account remains a reported limitation and is never silently
  replaced.
- Automatic six-account and media quotas apply to discovered additions, not to the explicit
  roster. Preserve all requested accounts in the audit even when only some support content
  findings.
- When the user names how many competitors they want ("find 10 similar channels"), write that
  number as `requested_competitor_count`. The run still compares at most six discovered accounts,
  and the reply states that limit; do not promise or imply more accounts than were compared.

## Resolving the subject

Resolve the subject before competitor discovery. If the user explicitly provides a platform
handle, resolve that handle once.

**From a public Instagram post.** When the user supplies a public Instagram post URL instead of a
competitor list, run this and nothing else first:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' start --platform instagram --post '<that URL>'
```

It hands over `lookup_instagram_post` (`{"post": "<that URL>", "delivery": "download"}`), places
the post as `seed-envelope.json` — whichever route answered — and runs the `subject` stage for the
post's author with that seed, so skip the `subject` command below. Do not call
`lookup_instagram_post` yourself unless the stage hands the call over, and never pass it a username
or profile link. Do not invent `resolve_post`; that operation does not exist. Set `subject.account_ref` from the printed author,
and set `subject.seed_content_ref` to the resolved item's canonical `content_id`, not the raw URL
or unparsed shortcode. Keep the visible mechanism in `subject.description`.

**From a product or creator name.** Run:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' start --platform <instagram|youtube> --query '<the name as the user wrote it>'
```

The Instagram identity search accepts only letters, digits, `.` and `_`, at most 30 characters: when
the name has spaces or other characters, pass a handle-like form of it (for example `AcmeBot` for
"Acme Bot") or ask the user for the handle, rather than a query the tool will reject.

It runs the identity search once (`instagram_search_accounts` on Instagram,
`youtube_search_channels` on YouTube) and prints each candidate's handle, display name,
followers, official external URL, category and bio — everything the subject choice is made on.
Compare the printed candidates using public bio, display name and official external URL, then
resolve only the selected canonical handle when necessary. Never invent a handle by lowercasing or
stripping punctuation from a product name.

**From a niche only.** When the user names no account, product or creator at all — only their
niche, such as «I make <niche> videos on YouTube» — there is no identity to search: run `start
--platform <instagram|youtube>` without `--query`, skip the `subject` stage, and freeze the
Request from the user's own words with no `account_ref` and no market they did not state. Never
search the niche and adopt one of its channels as the user: in testing, doing so turned
an English-language niche into a Chinese one — the adopted channel's market — and discovery
missed every labelled competitor.

**Subject stage.** Once the subject identity is known — chosen from those candidates, given by the
user, or taken from the seed post's author — run:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' subject --handle <instagram handle>
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' subject --channel-id <UC… channel id>
```

It retrieves the subject profile and one bounded page of the subject's own recent public items in
one `collect_account_history` call, prints the profile, writes and prints `caption_phrases`, then prints the Request and intents
schemas.

- If that history is failed, unsupported or empty, write one data gap and continue competitor
  discovery; do not abort the run because the subject has no uploads playlist.
- The script reads the seed post's caption (`lookup_instagram_post`) as well as Reel `caption` fields, and
  independently reads canonical YouTube video titles. Both fields are used when present: titles
  supplement captions, never replace them. Each source keeps up to eight qualifying
  two-to-four-term first-line phrases, drops calls to action, and strips a trailing episode
  number. A lone usable text retains its form hint; adding titles does not disable an existing
  single-caption hint. The merged list preserves all original caption phrases and their order,
  then adds unique title phrases. One video supporting a phrase in both fields counts once.
  Descriptions and transcripts are not caption observations.
- That list is the find-competitors coordinate: other people who write the same form words. If it
  comes back empty, leave `caption_phrases` empty and freeze intents from the description rule.
  `discover` writes those phrases onto the Request's `subject` itself; never retype them.

If no returned candidate supports the requested identity, stop as unresolved instead of assigning
an unrelated market. When only a YouTube @handle is known, run `subject --platform youtube --handle
<@handle>`: the same call resolves it to its channel before reading the uploads (a bare
`--handle` without `--platform youtube` means Instagram). For an explicit YouTube @handle, the
driver preserves that handle and requires the fetched profile's canonical public handle to match it; a bio mention or promotion of
the product does not establish that identity. A mismatch or missing handle proof stops before
phrase extraction and discovery.

## Freezing the Request

Complete `subject.account_ref`, market and audience when the selected public identity supports
them, then freeze the final Request. Before the single synthesis write the Request must still
carry a valid subject account reference whenever identity resolution succeeded. Do not write a
placeholder Request with a null account reference and repair it after synthesis.

**Window.** The primary competitor window is the last 30 days UTC when the user asks about recent
or latest activity in any language (for example "recent" or "lately"), or does not name a longer
span: `until` is now, `since` is `until` minus 30 days, and `window_policy` is `recent_30d` (or
omitted, which means the same). Do not freeze a ~90-day window just because competitor research
often looks at a quarter. A longer look is only allowed when the user named that span; then set
`window_policy` to `user_named_span` and keep that span. The research window is not an
account-selection constraint. Write window timestamps in UTC with a trailing `Z`
(`2026-10-20T00:00:00Z`), never with `+00:00`: the YouTube collector rejects any other form.

Persist the Request and fail closed before discovery. The Request must include a non-empty
`request_id`; `discover` fills it (and `schema_version`, and the default window) when omitted and
keeps any value you write. Do not invent a slug.

**`subject.kind`.** Set it from the same public evidence: `product` when the resolved identity
markets an offering a buyer can sign up for or purchase — pricing, plans, a signup or install
link, product features — and `creator` when the subject's own output is the content. This one
field decides what the word *competitor* means downstream, so do not leave it to be inferred
later: for a product, competitors are the other things a buyer would evaluate in the same
purchase; for a creator, they are the other people making the same kind of content. The accounts
that answer one question are frequently wrong answers to the other.

- A missing, empty or failed subject profile — `playlistNotFound`, blank channel description,
  empty uploads page — is a data gap, not evidence of `creator`. Do not freeze `kind` from that
  emptiness, and do not infer `kind` from the English word sense of the name.
- Keep resolving the same name on other public profiles of that identity (other-platform bios,
  the link text those profiles show) before choosing `kind`.
- The subject's own website is not a retrievable source; never try to fetch a creator's or
  company's own site. Use what the user wrote about that site, and say the site itself was not
  read.
- Default to `creator` only after those surfaces still show the subject's own output is the
  content and no purchase offer is visible. Measured on a YouTube name query whose
  channel had no uploads: defaulting `creator` from the empty profile retrieved deal and
  real-estate channels instead of purchase alternatives.

On the Request write `"platforms": ["instagram"]` or `["youtube"]`; never a singular `"platform"`
field. Then write the discovery intents as `references/discovery-intents.md` says.
