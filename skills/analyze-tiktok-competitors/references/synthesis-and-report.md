# Account histories, synthesis and the final report

Read this whole file before writing `competitor-research-synthesis.json`. Commands carry the run
directory and use `<skill directory>` exactly as `SKILL.md` defines them.

## What `content` does

`content` deterministically selects up to five accounts and prints each included handle with its
relationship. It hands over `tiktok_list_account_posts` once with `limit` 100 for each included
exact handle and never replaces a failed account with a new search. It places each history under
its own handle, builds and analyzes one public Snapshot, projects retrieved titles into
`topic-coverage.json`, and prints `synthesis-evidence.json` with the synthesis schema.

**Latest-N sampling.** With explicit latest-N sampling, search uses `publish_time=0`; account
history is sorted by UTC publication time descending, then canonical content ID, with identical IDs
merged and conflicting duplicates rejected. Only the latest N distinct posts at or before the frozen
`as_of` enter the sample; views do not select them. An unknown publication time makes that
account's latest order unprovable and is reported, never sorted last or replaced. Missing titles in
selected posts remain gaps rather than drawing older replacements. The projection includes each
exact account's requested/selected/analyzed counts, actual publication dates and shortage reason.
Use these facts in the synthesis; do not label an account unavailable merely because the default
window would have excluded its posts. All baselines and outlier thresholds are calculated only
within the selected N, with the same same-account/same-format formulas.

**What it prints.** `synthesis-evidence.json`: each selected account with its relationship, its
analyzed items (title, views, a Creator-facing outlier multiple only where at least five comparables
support one, and the Analyzer's `evidence_score`, highest first), selected accounts without analyzed
items, `citation_allowlist` — the selected direct, adjacent or media accounts intersected with
in-window analysis evidence — and `hook_context`. Write the synthesis from that output and from
`audience-comments.json` when comments were fetched. Do not `cat` `public-content-analysis.json`,
`topic-coverage.json` or the Snapshot — they run to hundreds of kilobytes. Search
`topic-coverage.json` with `grep` only to check a specific missing-topic claim.

If a deterministic stage fails, do not call other analyzers or tools ad hoc: fix the input it names
and rerun that stage. If you deliver without the analyzer, label the result caption/title-level
analysis.

## Audience comments

Fetch comments only when the user asks what the selected accounts have not done, or what the
audience wants to see: run `content --comments` instead of `content`, or, if you decide only after
`content` has run, `drive.py comments` before writing the synthesis.

- The sample is bounded: at most three included-account posts already in `analysis.evidence`, one
  `tiktok_discovery_list_comments` call per post with that exact `aweme_id` (from the retrieved
  video URL) and `count` 20. Each envelope is paired with its post by `aweme_id`, and each post keeps
  its caption. Do not scrape tiktok.com, call a supplier, or use posts outside `analysis.evidence`.
- **Do not hand-write comment text**, and never create `comment-source.json` or
  `audience-comments.json` yourself. A post whose fetch was refused or never made is recorded as
  `not_fetched`.
- Read each `posts[].caption` with its rows. Use a leftover comment as audience-want only when it
  names a need or scene you can check against retrieved titles, and that post's caption does not
  already explain the comment as a reply the post asked for. Discard comments that fail that test.
  Do not keep a keyword list or a list of invalid comment kinds.
- Write `gaps[]` from the comments that pass: each audience-want gap names one scene those comments
  already say, plus a retrieved title that does not cover it. If none pass, only write title-absence
  gaps and do not claim audience-want. Do not invent a format or day-N series.
- If `texts[]` is empty — no targets, unsupported operation, or failed fetch — record that in
  `data_gaps` and still deliver "they have not done X" from titles.
- Comment counts, high views, search suggestions, magazine copy, Shop reviews and transcripts are
  not proof that the audience asked for a missing topic.

## Writing the synthesis

Write one immutable `competitor-research-synthesis.v3.1` as `competitor-research-synthesis.json` in
the run directory (`report` fills its `schema_version` and `request_id`):

- choose three to five representative accounts with analyzed content;
- cite only allowlisted exact `content_id` values;
- each pattern needs at least two accounts and two content references;
- distinguish direct competitors from adjacent or media references;
- describe mechanisms and differentiation in the user's language;
- for an opening mechanism or differentiation that needs a production choice, read
  `references/hook-core/research-review.md` whole and apply it in the existing fields. Keep
  selected-account citations; pattern details are not additional competitors or audience-comment
  needs;
- never copy metrics, formulas, sample sizes, routes or windows into the synthesis.

## Presentation and the final step

Compose and validate the synthesis, then prepare a source-bound presentation in the same run. This
applies to **every delivery language**, including English when a source title is in another
language. The synthesis's authored prose is already in the user's language; do not rewrite it in
the presentation.

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' report --prepare-presentation
```

This call is non-final. It runs Compose and Validate and exclusively creates
`report-presentation.json`; it does not write or deliver `creator-report.md`. The printed finite
slots cover only actual displayed labels, mechanical proofs, sample/shortage explanations, basis and
source-title excerpts. Read all of them and fill **only each `text`** in that same JSON.

- Keep every slot id exactly once and add none. Slots are matched by id and only `text` is read,
  so the simplest write-back is `{"id": …, "text": …}` per slot; the renderer recomputes
  source/ref/hash, templates and facts from the bound report and ignores copies of them. Leave the
  top-level `schema_version`, `language` and `report_sha256` as printed, or omit them; never change
  their values.
- Translate the entire source excerpt faithfully rather than rename or summarize its title. For
  identity text copy `source_template` exactly.
- Preserve each `{fact_N}` / `{quote}` placeholder exactly once; their numbers, dates, accounts,
  URLs and source quotations are inserted mechanically.
- A translated source title is explicitly labelled a translation and remains bound to the original;
  do not invent an observation, a new headline or a recommendation. Original authored conclusions,
  Chinese source text retained by identity, and the structured Report stay intact.

After filling the sidecar, run the final step from the same run directory:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' report --presentation report-presentation.json
```

Its deliverable is `creator-report.md` in the run directory; `report/creator-report.md` is not
created. Do not move or copy a report, or reopen a failed or closed run, to satisfy a different
path. Compose, Validate, hash or presentation binding failures (a changed header value, or a
missing, duplicate or unknown slot id) are fatal: preserve the first failure and stop. A slot whose
`text` drops or repeats a placeholder, or writes a digit, date, handle or URL outside its
placeholders (including a month name turned into a number), is not fatal: that slot is shown in its
source text and listed under `kept_source_text`, and the report is delivered as rendered. Neither call fetches again, and preparation is not task completion. A bare `drive.py report`
remains for old integrations but does not establish full user-language delivery when a displayed
source is foreign.

## Delivering the report

`report` prints the absolute path of `creator-report.md` on `creator_report:`. Deliver that file in
full, once, as your answer. Do not shorten translated quotes, retype metrics, synthesize a
replacement report or append a second paraphrased one. The presentation step validates
source/structure/fact binding, not semantic translation quality: inspect the whole final report and
state any remaining language or translation gap honestly.

The Renderer keeps supplier identity, query routes, quotas, coverage and audit mechanics out of the
report and closes it with one fixed basis sentence (analyzed posts, accounts, window dates, and the
baseline size only behind a shown multiple). Answer follow-up questions about measurement from the
structured Report.
