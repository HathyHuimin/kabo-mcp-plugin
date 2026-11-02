# YouTube video-trend workflow (longer windows)

Read this whole file before `drive.py start`. It holds what each stage of the longer-window scan
does and every rule for your judgments: the frozen Request with its query plan, the relevance Gate,
and the synthesis (and, only when the first collection fails coverage, a replacement plan).
`<run directory>` and `<skill directory>` are the absolute paths from *Set up one run* in
`SKILL.md`.

If the requested window is seven days or shorter and the user wants recent hot videos to copy,
stop this workflow and use the short-window route (`references/short-window.md`) only. Never call
`collect_youtube_trend_candidates` or `enrich_youtube_shortlist` there.

## How the driver works

- **`scripts/drive.py` runs every mechanical step; you make the judgments.** Each stage starts
  right after one judgment: it prints the data call that judgment implies, persists the staged
  result, runs the deterministic scripts, and prints exactly what the next judgment reads,
  including that artifact's schema. Do not persist results yourself, do not re-run the scripts it
  ran, and do not open the files it wrote or the `references/*.schema.json` it printed: its printed
  output is the input to your next judgment.
- Leave `schema_version`, `request_id` and each intent's `intent_id` out of the Request and the
  query plan: `discover` fills them, and keeps any value you do write; `report` does the same for
  the synthesis's `schema_version` and `request_id`.
- Every stage runs from the run directory:
  `cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' <stage>`. Every later stage
  refuses to run anywhere else, this Skill's own directory included, and writes nothing there; its
  message names the directory to use. Run `start` once per request; when it is run again for the
  same request, repeat its exact arguments and add `--placed`. Run a command a stage prints
  exactly as printed.
- **Hand-over.** A stage that needs data stops with exit 0 after printing the call, one `call:`
  line per call. That pause is unfinished work, not completion. Make exactly the printed calls with
  the printed arguments verbatim, each as its own individual call (never combined into one batch
  call), all in one response; save and fetch each complete result as `SKILL.md` says; then run the
  same stage again with its exact arguments and `--placed` added, in the same run. A call that
  failed is handled as *Failed tool calls* in `SKILL.md` says. Do not make data calls a stage has not
  printed. `collect_youtube_trend_candidates` and `enrich_youtube_shortlist` take their printed
  arguments directly, not inside `parameters`.
- A stage can still be running when the command first returns output; when your shell tool lets
  you choose how long to wait, give each `drive.py` command 120 s. Wait for that same command to
  finish; never start a stage a second time while it runs.
- If a stage stops with an error, the message names what to fix, usually a field or line of the
  artifact you just wrote. Fix it and run the same stage again in the same run; add `--placed` when
  the stage says its data calls had already gone out, so they are not made twice. A stage that says
  to stop and report is final: tell the user what it names and do not run it again. Never reopen a
  failed run, repeat completed retrieval or start a replacement run.

## 1. Start

When the ask supplies a video instead of naming an account, pass it; otherwise run `start` alone:

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' start --video "<the video URL or id as the user gave it>"
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' start
```

With `--video` it hands over one `youtube_get_video` call. Make it, fetch the result and run
`start --video … --placed`: it prints the source video's fields (title, description,
`channel_title`, `channel_id`, duration and public metrics), then the Request and query-plan
schemas.

## 2. The Request, the query plan and discovery

Build one `TrendResearchRequest`. Freeze niche, locale, formats and exact UTC window. Leave `locale.region` and `locale.language` null unless the user names a market or content language ("Spanish videos", "in Brazil"); then quote those words verbatim in `locale.named_in_ask`; `discover` checks it against the ask from `--question-file -`. The ask's own language sets only the reply language.

- **Creator.** When the ask supplies a video instead of naming an account, the Creator is that
  video's author: take `creator_profile.handle` from what the source call returned for it
  (`channel_title`, `channel_id`), never from an account you remember or one recorded elsewhere.
  The handle is projected into the Gate input as `anchor_handle`, so the wrong one makes the Gate
  score `creator_transferability` against the wrong person.
- **Window.** Never type the dates: write the rest of the Request, and `discover` freezes its
  `window` and `window_policy` in place (once per run: running it again keeps the window it froze).
  Classify the ask once and let the script own the dates: `user_named_span` when the user named a
  span or dates; `recent_90d` when the ask says recent / lately / now, in any language, without a
  number (the last 90 days); `default_1y` when the ask names no time at all — a niche scan, a reach
  question, or "find hot videos like this one" (the last year, so the scan reaches as many strong
  posts as the channel can). The same three policies run on every trend channel; a short-window
  copy ask (seven days or less) keeps its own route. For an explicitly named relative day span, use
  `--policy user_named_span --last-days <the positive day count the user named>` instead of
  calculating UTC endpoints by hand: it freezes the clock once, has no default, and cannot combine
  with `--since`/`--until` or replace an established window. Explicit dates use
  `--since <ISO> --until <ISO>`, kept exactly.
- **Query plan.** Build one minimal `trend-query-plan.v1` with at most two stable natural-language
  search intents. Each intent contains only `intent_id` and the downstream-consumed
  `search_query`; YouTube does not consume `popular_topic`. Keep each query to 2–9
  whitespace-delimited terms and at most 50 characters; a query in a script written without spaces
  (Chinese, Japanese, Thai) is one natural phrase of 2–20 characters instead, with no spaces
  inserted to pass. A 6–9-term query must include a grammatical connector from the validator's
  fixed multilingual set, and no query may contain three or more acronym or code tokens. Express
  one natural category or workflow phrase, not a sentence or a synonym-stuffed keyword list; locale
  already carries language and region. With only two slots, spend one on the source's own wording
  and one on the format's common name — a phrase another community would search for the same
  thing, carrying none of the source's words. Measured: every plan that spent both slots
  on the source's own words returned nothing outside it, while the plans that reserved the second
  slot for the common name were the only ones that reached beyond it.

Write both artifacts and start discovery in one command:

```bash
cd '<run directory>' && cat > trend-research-request.json <<'JSON'
…
JSON
cat > trend-query-plan.json <<'JSON'
…
JSON
KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' discover --policy <default_1y|recent_90d|user_named_span> [--since <ISO> --until <ISO> | --last-days <N>] --question-file - <<'ASK'
<the ask, verbatim>
ASK
```

(chain the three with `&&`). What `discover` does:

- Before any data call it checks only the local authored Request and plan with the original
  validators. A `needs_correction` answer is unfinished: correct the named authored input once in
  this same run and repeat `discover` with its required window arguments. It records the original
  bad bytes and error under `analysis/discovery-input-errors/`; a second invalid attempt, any data
  already begun, a frozen-window conflict, an I/O or internal failure stays fatal. Never prune
  search intents, widen the window, recollect or replace a failed run.
- It freezes the window, fails closed on the Request window and the query plan (a rejection names
  the field to fix), then hands over `collect_youtube_trend_candidates` once per validated plan
  with the frozen intents, the exact window, the Request's language and region, and the budget
  `results_per_route=50`, `max_provider_calls=4`, `max_candidates=160`. The backend uses only the
  official YouTube public-data capability for relevance and view-count ordering, then
  canonical-deduplicates the union.
- It persists the returned `youtube-trend-candidate-set.v1` from this run's staged copy, validates
  coverage and prints the numbered Gate table. When it reports that the collection result was not
  recorded, stop and report that: never write, hand-truncate or reconstruct `items` or a shortlist
  yourself, and never call collection again to repair a local file.

**Coverage.** Every intent must return at least one usable row from both `relevance` and
`view_count`, with neither route degraded; `discover` checks this before any semantic work. If the
first collection fails, it names the failed intents and keeps the first result as
`youtube-trend-candidate-set-first.json`. Shorten only the failed query to its 2–5 core topic
terms, write the replacement plan (same `intent_id`s; do not rewrite the Request), and run
`discover` again without `--placed`: it validates the replacement plan and recollects once. Never
produce a Creator report from a second failed plan; `discover` stops the run if the recollection
fails too. Never call or silently fall back to the standby outlier capability, expose page tokens
or hand-allocate route quotas.

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
  rules, or any script: a Gate computed that way is a failed Gate even when it validates. Do not
  output direct or adjacent labels, reasons, popularity judgments, metrics or route labels.

The scores mean:

- `topic_relevance`: whether the video itself demonstrates the requested topic or mechanism.
- `creator_transferability`: whether its visible move can credibly be adapted by this Creator.
  **Omit this field entirely when the Gate input carries neither `subject.anchor_handle` nor
  `subject.anchor_mechanisms`.** With no anchor the score would be invented, yet carry a quarter of
  the ranking weight. Omitting it is a supported state, not an error: the contract accepts the
  field's absence and the ranking redistributes its weight proportionally across `topic_relevance`
  and `locale_fit`. Never write a placeholder such as 0 or 50 in its place, and never omit it when
  an anchor is present.
- `locale_fit`: fit to the requested language, culture and target market; it is not geographic
  popularity. When the user named no market or content
  language, the language they write in sets only the reply language: do not score an item down for
  being in another language.

```bash
cd '<run directory>' && cat > trend-relevance-gate.tsv <<'TSV'
…
TSV
KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' rank
```

`rank` first builds `trend-relevance-gate.json` from your lines with
`scripts/build_relevance_gate.py`. It refuses a missing, duplicated or
out-of-range line; fix the lines it names and run `rank` again. Before any Gate write or new fetch,
it may return `status: needs_correction` with exit 0 for invalid authored score lines in an open,
unfrozen run. This is unfinished work, not success: correct only those lines and follow the
returned exact command in the same run. It neither changes scores nor fetches data.
**Freeze the Gate before exact metrics:** once the enrichment has run, `rank` refuses a changed
Gate. Frozen Gates, missing or corrupt source files, data faults and internal errors remain fatal;
never reopen or replace a failed run or use correction to change a frozen Gate.

## 4. Rank and enrichment

`scripts/build_youtube_ranking_input.py` is the only shortlist selector. It requires topic at least
60 and locale at least 50, reserves 12 `view_count` slots, fills the rest by semantic score, and
caps the official batch at 50. It emits whichever enrichment form the collection supports: the
handle form with `candidate_set_id` and `selected` when the collection returned an id, otherwise
the inline `items` form.

When the shortlist is non-empty, `rank` hands over `enrich_youtube_shortlist` exactly once with
that file's contents verbatim as the tool arguments, and persists the result as
`trend-enrichment-01.json` from this run's staged copy, with the same stop rule as the candidate
set. Make the call with the printed arguments verbatim. Never rebuild `selected`, re-derive it from
the Gate, add or drop entries, or reorder them, even when a tool description recommends the handle
form. Do not use legacy 6+6, split into three-item chunks, hand-pick or reorder.

The backend performs one official exact-detail batch, filters the window, and computes:

- semantic = 50% topic + 25% transferability + 25% locale;
- pre-score = 45% semantic + 35% heat + 20% velocity;
- final = 45% semantic + 45% primary evidence + 10% secondary evidence.

Heat is a tie-aware percentile of exact public views. Velocity is exact public views divided by
video age, then converted to a tie-aware percentile in this run. They and all weighted scores are
deterministic backend calculations, not model output. The backend selects the top 12, retrieves
bounded official channel history, computes leave-one-out same-channel, same-format mature
baselines, and returns the final evidence order.

`rank` then builds the Snapshot and analyzes it exactly once, and prints
`public-content-analysis.json`, the opening-method catalog and the synthesis schema. If no item
reaches both floors, it never calls enrichment with an empty array: it builds the empty Snapshot
and enrichment artifact itself and analyzes that.

An enrichment result with a partial status or an explicit empty `items` array still proceeds
through Snapshot, Analyze, Compose, Validate and Render. Its original status and limitations stay in
the audit artifacts: no eligible results, incomplete evidence and actual execution failure are
distinct, and a partial or failed enrichment is not a reason to omit the report or discard evidence
the workflow already verified. Such a report must not claim views or multiples the enrichment did
not return.

Duration above 180 seconds can establish long-form. Duration at or below 180 seconds is only a
provisional Short candidate, because the official API exposes neither aspect ratio nor a reliable
Shorts flag; never build a Shorts outlier cohort from it.

## 5. The synthesis

Write one `trend-research-synthesis.v3.3` into `trend-research-synthesis.json` from the printed
Analysis and schema, using only allowlisted ranked evidence.

- Copy `content_id` into every `featured_content_refs` and `content_refs` field. The composer also
  accepts an exact, unambiguous `evidence_id` from this same Analysis; unknown, excluded or
  other-run references are rejected.
- Write natural headlines, `why_now`, up to three mechanisms and Creator-fit directions. Begin
  `overview.summary` directly with the content conclusion; never mention research dates or window,
  capture time, snapshot status, candidate or analyzed counts, coverage or limitations there. Never
  copy formulas, routes, quotas, shortlist mechanics, experiments, window boilerplate or audit
  inventory. Write such notes into `data_gaps`, which stays in the structured Report and is not
  rendered. One strong reference is valid; do not pad.
- Keep every artifact dependency-driven: every field must have a named downstream consumer; do not
  add completeness-only, usually empty or unused fields.

**Openings.** For a Short candidate's mechanism or Creator-fit direction that needs an opening
choice, read `references/opening-method.md` whole and apply it in the existing mechanism or
direction fields.

## 6. Report and delivery

Compose, validate and render deterministically in the same command:

```bash
cd '<run directory>' && cat > trend-research-synthesis.json <<'JSON'
…
JSON
KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/drive.py' report --language <the user's chat language, such as en, zh-CN or pt-BR>
```

`report` is the workflow's final step. It runs `scripts/compose_report.py`,
`scripts/validate_output.py` and `scripts/render_creator_report.py`, and prints
`creator_report: <absolute path>` followed by `creator-report.md`. Do not run those scripts by
hand: `report` runs them. Keep all their validators. If Compose, Validate or Render rejects the
synthesis, correct it and run `report` again in the same run. Do not emit, return or claim success
from a rendered file until `report` succeeds.

The renderer closes the report with one fixed basis sentence (analyzed public posts, the window
dates and, only when a multiple is shown, the baseline size behind it) in the `--language` you
pass: the language the user writes in, not the Request's `locale.language`. Unsupported languages
fall back to English, which the language check then translates. It rejects internal method
identifiers in model prose. When the Creator asks how the result was measured, answer from the
structured Report (window, collected and eligible counts, baselines, `data_gaps`) instead of
re-running anything.

Then deliver it after the complete-report language check in `SKILL.md` (*Deliver once*). When this
Skill runs in a sub-agent, return the original absolute report path on its own `creator_report:`
line, so the main thread can read the complete validated artifact and apply the same check before
delivering it.
