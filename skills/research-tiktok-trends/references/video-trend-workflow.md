# TikTok video-trend workflow

Read this whole file before `drive.py start`. It holds what each stage of the video-trend
workflow does and every rule for the three judgments you make: the frozen Request with its query
plan (and the search region when the Request region is unknown), the relevance Gate, and the
synthesis. `<run directory>` and `<skill directory>` are the absolute paths from *Set up one run*
in `SKILL.md`.

If the requested window is seven days or shorter and the user wants recent viral content to copy,
stop this workflow and use the short-window route (`references/short-window.md`) only. A
seven-day-or-shorter ask for which videos beat their creator's own usual, or for any other
account-relative multiple, is not a short-window copy ask: it stays in this workflow with
`--policy user_named_span` and that span.

## How the driver works

- **`scripts/drive.py` runs every mechanical step; you make the judgments.** Each stage starts
  right after one judgment: it prints the data calls that judgment implies, places every Envelope
  and writes its request provenance from this run's staged copies, runs the deterministic scripts,
  and prints exactly what the next judgment reads, including that artifact's schema. Do not place
  Envelopes or write provenance yourself, do not re-run the scripts it ran, and do not open the
  files it wrote or the `references/*.schema.json` it printed: its printed output is the input to
  your next judgment.
- Leave `schema_version`, `request_id` and each intent's `intent_id` out of the Request and the
  query plan: `discover` fills them, and keeps any value you do write; `report` does the same for
  the synthesis's `schema_version` and `request_id`.
- Every stage runs from the run directory:
  `cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' <stage>`. Every later stage
  refuses to run anywhere else, this Skill's own directory included, and writes nothing there; its
  message names the directory to use. Run `start` once per request; when it is run again for the
  same request, repeat its exact arguments and add `--placed`. Run a command a stage prints
  exactly as printed.
- **Hand-over.** A stage that needs data stops with exit 0 after printing the calls, one `call:`
  line per call. That pause is unfinished work, not completion. Make exactly the printed calls with
  the printed arguments verbatim, each as its own individual call (never combined into one batch
  call), all in one response; save and fetch each complete result as `SKILL.md` says; then run the
  same `drive.py` command again with its exact arguments and `--placed` added, in the same run.
  Rerun only the `drive.py`
  command, not the heredoc before it: the artifact is already written. A stage can hand calls over
  more than once (`discover` hands over its first round, then its second); each time, make the
  calls it prints and run it again with `--placed`. Do not make data calls a stage has not printed.
- Failed calls: see *Failed tool calls* in `SKILL.md`.
- If the data service briefly failed (`rate_limited`, `service_unavailable`) on calls you made, the
  stage sets that round aside and hands the whole round over once more; make it again and resume
  with `--placed`. A second failure is placed as the answer.
- A stage can still be running when the command first returns output; when your shell tool lets
  you choose how long to wait, give each `drive.py` command 120 s. Wait for that same command to
  finish; never start a stage a second time while it runs.
- If a stage stops with an error, the message names what to fix, usually a field or line of the
  artifact you just wrote. Fix it and run the same stage again in the same run; add `--placed` when
  the stage says its data calls had already gone out, so they are not made twice. When `discover`
  stops because a search or ranked-board response is missing after your retry, the trend scan
  cannot be completed: say so and stop. Never reopen a failed run, repeat completed retrieval or
  start a replacement run.

## 1. Start

When the ask supplies a video instead of naming an account, pass it as the user gave it; otherwise
run `start` alone:

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' start --video "<the video URL as the user gave it>"
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' start
```

It prints the Request and query-plan schemas. With `--video` it hands over one
`lookup_tiktok_video` call (`video` exactly as printed). Make it, fetch (or stage) its result, and
run the same `start --video` command again with `--placed`: it places the response and prints the
source video (caption, `author.handle`, public numbers, `unavailable` fields) to write the Request
from. A private, removed or photo post prints as not resolved; go on without it.

## 2. The Request, the query plan and discovery

Freeze one `trend-research-request.v3` with `platforms: ["tiktok"]`, a narrow niche, locale,
`tiktok_video` format and exact UTC window. Leave `locale.region` and `locale.language` null unless the user names a market or content language ("Spanish videos"); then quote those words verbatim in `locale.named_in_ask`; `discover` checks it against the ask from `--question-file -`. The ask's own language sets only the reply language.

- **Creator.** When the ask supplies a video instead of naming an account, the Creator is that
  video's author: take `creator_profile.handle` from what `start` printed for it
  (`author.handle`), never from an account you remember or one recorded elsewhere. The handle is
  projected into the Gate input as `anchor_handle`, so the wrong one makes the Gate score
  `creator_transferability` against the wrong person.
- **Window.** Never type the window: write the rest of the Request, and `discover` freezes its
  `window` and `window_policy` in place with `scripts/freeze_request_window.py` (once per run:
  running it again keeps the window it froze). Classify the ask once and let the script own the
  dates: `user_named_span` when the user named a span or dates (pass it as `--since`/`--until`,
  kept exactly); `recent_90d` when the ask says recent / lately / now, in any language, without a
  number (the last 90 days); `default_1y` when the ask names no time at all — a niche scan, a reach
  question, or "find hot videos like this one" (the last year, so the scan reaches as many strong
  posts as the channel can). The same three policies run on every trend channel; a short-window
  copy ask (seven days or less) keeps its own route. TikTok search reaches back at most 180 days
  with a date filter, so `default_1y` freezes 180 days here; do not widen it by hand.
- **Query plan.** Write one `tiktok-trend-query-plan.v1` with at most two two-to-five-term natural
  search intents; a query in a script written without spaces (Chinese, Japanese, Thai) is one
  natural phrase of 2–20 characters instead, with no spaces inserted to pass. With only two slots,
  spend one on the source's own wording and one on the format's common name — a phrase another
  community would search for the same thing, carrying none of the source's words. In tests,
  only the plans that reserved the second slot for the common name reached beyond the source's own
  words.
- **Region.** The search region is the Request region: every search carries it as `region` and the
  ranked board as `country_code`, one and the same value on every call. When the Request region is
  unknown, leave it `null` and either name one region with `--region <code>` (it is then used on
  every call) or pass none (no call carries a region). `discover` copies the region it is given; it
  never infers one.

Write both artifacts and start discovery in one command:

```bash
cd '<run directory>' && cat > trend-research-request.json <<'JSON'
…
JSON
cat > tiktok-trend-query-plan.json <<'JSON'
…
JSON
KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' discover --policy <default_1y|recent_90d|user_named_span> [--since <ISO> --until <ISO>] [--region <code>] --question-file - <<'ASK'
<the ask, verbatim>
ASK
```

(chain the three with `&&`). What `discover` does:

- freezes the window and validates the plan with `scripts/validate_tiktok_query_plan.py`; a
  rejection names the field to fix;
- takes `publish_time` from `scripts/resolve_search_publish_time.py`: the narrowest catalog value
  that reaches back from now to the window's start. When it prints `reaches_since: false`, keep the
  coverage incomplete (in `data_gaps`);
- for each intent, `tiktok_discovery_search_videos` once with `sort_type: 0` and once with
  `sort_type: 1`, `count: 30`, that `publish_time` and the region, with the frozen intent phrase as
  `keyword` (never `query`, `search_query` or `q`). Every route is its own call; do not batch them,
  because a batch failure erases the per-route terminal evidence this report must preserve;
- the regional ranked board (`tiktok_discovery_list_top_contents`, `period_dimension: 3`,
  `limit: 100`) for UTC D-1, dated 00:00 UTC of that day; when that completed board is empty, it
  tries D-2 once and stops;
- two rounds of calls — the `sort_type: 0` searches with the D-1 board, then the `sort_type: 1`
  searches with the D-2 board when it is needed — so that no round sends the same keyword twice;
- places every Envelope into `discovery-envelopes.json` and writes each request's provenance into
  `discovery-requests.json` (`connector_id`, `operation` and `request_id` from the staged response,
  and the params that were sent). Missing or conflicting request provenance is a failed
  collection, not a reason to guess a route or query. Do not switch providers after partial or
  failed results;
- canonical-deduplicates and applies the exact timestamp window locally with
  `scripts/build_tiktok_candidate_set.py`, and prints the numbered Gate table
  (`scripts/project_relevance_gate_input.py … --output trend-relevance-gate-input.json --table`).

## 3. The relevance Gate

Score from the numbered table only.

- A small table arrives in the established numbered format and is complete. A large table arrives
  as a JSON page with `table_chunk`, `table_page`, `total_pages` and `next`. Read every page before
  scoring: run `next.script` (relative to `<skill directory>`) from the run directory with exactly `next.arguments` (the read-only
  command is `cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/project_relevance_gate_input.py' --input <this run
  projection JSON> --table --page N`). Concatenate all `table_chunk` values in page order without
  separators; a long candidate can continue across pages, including its sponsorship disclosures.
  Only `next: null` marks the last page; it does not prove earlier pages were complete.
- If any shell or tool output reports `truncated` or inserts a truncation marker, that page is
  unread: read the same read-only `--input` page completely in this same run. Do not recollect or
  score until every page is complete. Do not re-run discovery, recollect, inspect other files, or
  score or rank an incomplete table. Page reads never change the projection JSON.
- After the table, the driver prints `gate_scoring` and `next_stage` for this run. Follow their
  exact `scores_file`, ordered `columns`, `header: false` and next-stage command after reading all
  pages. The column names describe the format; never put a header in the TSV.
- Write one line per numbered item into `trend-relevance-gate.tsv` with a shell heredoc —
  `<n> <topic_relevance> <locale_fit> <creator_transferability>`, or `<n> <topic_relevance>
  <locale_fit>` when the table's first line carries neither `subject.anchor_handle` nor
  `subject.anchor_mechanisms` — every number exactly once, integers 0–100, nothing else on the
  line — and chain `rank`.
- **Read each line and judge it.** Never produce scores with code, keyword or regular-expression
  rules, or any script: a Gate computed that way is a failed Gate even when it validates. Judge the
  visible content, not route, rank, followers or metrics.
- Score `topic_relevance` and `locale_fit` for every item, and `creator_transferability` whenever
  the table's first line carries `subject.anchor_handle` or `subject.anchor_mechanisms`. **Omit
  `creator_transferability` entirely when it carries neither.** With no anchor the score would be
  invented, yet carry a quarter of the ranking weight. Omitting it is a supported state, not an
  error: the contract accepts the field's absence and the ranking redistributes its weight
  proportionally across the other two. Never write a placeholder such as 0 or 50 in its place, and
  never omit it when an anchor is present.
- `locale_fit` is fit to the market and content language the user named, not geographic
  popularity. When they named none, the language they write in sets only the reply language: do
  not score an item down for being in another language.

```bash
cd '<run directory>' && cat > trend-relevance-gate.tsv <<'TSV'
…
TSV
KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' rank
```

`rank` first builds `trend-relevance-gate.json` from your lines with
`scripts/build_relevance_gate.py`. It refuses a missing, duplicated or
out-of-range line; fix the lines it names and run `rank` again.

Before any Gate write or new fetch, `rank` may return `status: needs_correction` with exit 0 for
invalid authored score lines in an open, unfrozen run. This is unfinished work, not success:
correct only those lines and follow the returned exact command in the same run. It neither changes
scores nor fetches data. **Freeze the Gate before account-history retrieval:** once `rank` has
asked for the histories, it refuses a changed Gate. Frozen Gates, missing or corrupt source files,
data faults and internal errors remain fatal; never reopen or replace a failed run or use
correction to change a frozen Gate.

## 4. Rank and account histories

`rank` builds the deterministic shortlist with `scripts/build_tiktok_ranking_input.py` and hands
over `tiktok_list_account_posts` once with `limit: 30` for every exact handle in its
`history_requests`, in the listed order (highest-ranked accounts first), each as its own call. It
places each history under its own handle (lowercase, without `@`) in
`account-history-envelopes.json`. A history that did not come back (a call that failed or was
refused) leaves that account with absolute plays only, and nothing retries it later in the request.
Never replace a failed account with a new search or hand-picked account. It then builds account
baselines (`scripts/build_tiktok_enrichment.py`), the Snapshot and deterministic analysis once
(`scripts/build_trend_workflow_snapshot.py`, `scripts/analyze_public_snapshot.py --mode trend`),
and prints `public-content-analysis.json`, the opening-method catalog and the synthesis schema.

## 5. The synthesis

Write one `trend-research-synthesis.v3.3` from allowlisted evidence only.

- Read `analysis.claim_boundary.snapshot_count` before choosing any pattern stage or
  emerging-creator status. When it is below 2, every pattern `stage` MUST be
  `current_high_performance` or `recurring_pattern`: `current_high_performance` only when the
  pattern cites an item at 1.5× or more of its own account's usual or in the top quarter by public
  metrics, otherwise `recurring_pattern` (when no cited item meets either, say in the overview that
  nothing beat its account's usual). Every emerging creator MUST avoid `breakout_confirmed`, and
  `pattern`, `mechanism`, `why_now`, `summary` and `overview` MUST NOT claim or imply momentum
  (including rising, falling, growth, acceleration, breakout, "starting to", "shifting toward", or
  equivalents in the delivery language).
- Keep natural headlines, `why_now`, up to three mechanisms and Creator-fit directions. Do not copy
  formulas, routes, windows, sample sizes, coverage or audit gaps into Creator-facing prose; write
  any such note into `data_gaps`, which is never rendered.
- Use exact `content_id` or `evidence_id` values from `analysis.ranked_content` in
  `featured_content_refs` and every `content_refs` array. These are data references, not links:
  keep the public `url` out of those fields; the Composer resolves eligible IDs and supplies public
  proof links. Do not derive IDs from a URL.

**Openings.** When a video mechanism or Creator-fit direction needs an opening choice, read
`references/synthesis-openings.md` whole before writing it. It adds no steps to the sound-board,
scene-sound or search-term routes.

## 6. Report and delivery

Compose, validate and render deterministically in the same command:

```bash
cd '<run directory>' && cat > trend-research-synthesis.json <<'JSON'
…
JSON
KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' report --language <the user's chat language, such as en, zh-CN or pt-BR>
```

`report` is the workflow's final step. It runs `scripts/compose_report.py`,
`scripts/validate_output.py` against the composed report, the Request and the Analysis, and
`scripts/render_creator_report.py`, then prints `creator_report: <absolute path>` followed by
`creator-report.md`. Do not run those scripts by hand: `report` runs them. Keep all their
validators. If Compose or Validate rejects the synthesis, correct it and run `report` again in the
same run. Do not emit, return or claim success from a rendered file until `report` succeeds.

The renderer closes the report with one fixed basis sentence (analyzed posts, window dates, and the
baseline size only behind a shown multiple) in the `--language` you pass: the language the user
writes in, not the Request's `locale.language`. Unsupported languages fall back to English, which
the language check then translates. Answer follow-up questions about measurement from the structured
Report.

Then deliver it after the complete-report language check in `SKILL.md` (*Deliver once*). When
this Skill runs in a sub-agent, return the original absolute report path on its own
`creator_report:` line, so the main thread can read the complete validated artifact and apply the
same check before delivering it.
