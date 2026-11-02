# Short window: hottest and fastest-rising

Read this whole file before the first call of this route. `<run directory>` and
`<skill directory>` are the absolute paths from *Running commands* in `SKILL.md`; run every command
from the run directory.

Use this route when the requested window is seven days or shorter (the last day, the last 48
hours, this week — named in any language) and the user wants recent hot videos to copy. Every
longer window, and every ask for repeated mechanisms or account-relative multiples, uses the
video-trend workflow with its window policies. Do **not** stop because the short-window pool is
thin. Do **not** create a query plan, Snapshot, Analyzer output or `TrendResearchReport`. Do
**not** call `collect_youtube_trend_candidates` or `enrich_youtube_shortlist`, not even to fill
plays: a collection on a short-window ask is a failed delivery, not a reason to stop.

## Collect

1. Readiness: `youtube-public/search_videos` and `youtube-public/get_video` implemented and ready.
2. Freeze the requested window in UTC: `until` is now unless the user named an end, and `since` is
   `until` minus the requested span (resolve a relative span once against the current time). At
   most two intents from the user's niche.
3. Window lane: for each intent, `youtube_search_videos` once with that exact `published_after` /
   `published_before` window. Copy the frozen intent phrase into the tool's required search-term
   field (`query`).
4. Heat lane: for each intent, `youtube_search_videos` once over the last 30 days, ordered by view
   count (`order: viewCount`), with the same search-term field.

Make each call on its own, never batched. After every search call of steps 3 and 4, window lane
and heat lane alike, completed or failed, save and fetch its result and persist it, numbering the
files `01`, `02`, … in call order. The placement refuses a failed response; a search that still
fails after its one retry (`references/data-calls.md`) means the short-window report cannot
be completed: say so and stop, and never build from the remaining lanes.

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_short_window.py' --dest envelope-NN.json --connector youtube-public --operation search_videos
```

Calls made together may also be persisted afterwards, one command per call: each takes this run's
earliest unplaced response of that operation. Search rows have no `statistics`; do not treat null
plays as zero and do not rank that union as heat. Missing collections, or only failed responses,
stop the builder rather than proving zero results. A completed empty collection remains a valid
empty result; any explicit failed or unavailable source, including one mixed with completed
sources, stops the builder because these layers do not project partial failures.

5. Extract the unique video ids from the persisted search Envelopes, listing every
   `envelope-NN.json` that steps 3 and 4 placed by file name, as positional arguments in number
   order. Chunks hold at most 50 ids, the `get_video` maximum:

   ```bash
   cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/complete_youtube_short_window.py' ids envelope-01.json envelope-02.json ... --output video-id-chunks.json
   ```

6. For each chunk, call `youtube_get_video` with `video_ids` (not one-by-one `video_id` unless the
   chunk has one id). After each call, save, fetch and persist it:

   ```bash
   cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_short_window.py' --dest envelope-detail-NN.json --connector youtube-public --operation get_video
   ```

   If any `get_video` is missing, failed, or not `completed` (the script refuses those), stop.

## Build and author

7. Merge search and `get_video` results into `short-window-input.json`, passing every
   `envelope-NN.json` and then every `envelope-detail-NN.json` in number order, and the chunk file:
   merge refuses a chunk no persisted `get_video` answered. If merge reports `views_filled` 0, stop:
   do not write a heat layer ordered as plays. Then build and print the presentation contract:

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/complete_youtube_short_window.py' merge envelope-01.json ... envelope-detail-01.json ... --chunks video-id-chunks.json --since <since> --until <until> --output short-window-input.json
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/build_short_window_layers.py' short-window-input.json --output short-window-layers.json
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/render_short_window_report.py' short-window-layers.json --language <the user's chat language> --print-presentation-contract --text-only --contract-output short-window-presentation.json
```

The build makes two layers: the heat layer (hottest dated videos of the last 30 days) and the
window layer (the exact window). Heat rows must show numeric public plays, author names and their
publish time, and must not be described as in-window. An empty window layer is a valid
deliverable.

The print command prints the full source contract and creates a smaller, text-only presentation
file. Read the whole contract without truncation. Translate the printed sources into the user's
chat language by filling **only** every `text` field in `short-window-presentation.json`, including
labels, limitations and every displayed source excerpt; keep proper names where appropriate.

- Preserve each complete source excerpt's meaning, numeric facts, URLs and handles. A row's
  `source_excerpt` is the first 80 characters of its caption and may stop mid-word: translate
  exactly that excerpt, not the full caption in `short-window-layers.json`, and never restore
  numbers, links, handles or words cut off after it. Empty source excerpts stay empty.
- The original literal-number comparison remains first in every language. Only when those numeric
  spellings differ, a complete English month-name calendar date may use the equivalent Chinese
  year/month/day notation (or vice versa); preserve the same valid date. This fallback does not
  verify translation semantics. Other numbers remain literal: do not add numbers from the
  surrounding task or window.
- Do not summarize the excerpt, add a headline, invent viewing, acceleration or broader coverage,
  or add advice.
- Keep the generated layers hash, row slots and language unchanged. Do not copy source excerpts or
  item hashes into this file: the renderer binds those immutable fields from the exact layers
  bytes. Do not change the raw layers. The older full presentation contract remains accepted with
  all its binding checks.
- A script can check the binding and literal facts, not translation faithfulness; read the final
  complete report to verify its language and meaning.

The print mode only creates the presentation skeleton and is not a final step. Keep the same run
directory for every step and never resend accumulated inputs or layers.

## Render

Render the authored presentation in the same run:

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/render_short_window_report.py' short-window-layers.json creator-report.md --language <the user's chat language> --presentation short-window-presentation.json --allow-authoring-correction
```

The renderer checks the complete layers, presentation structure and immutable bindings before
classifying authored prose. Only named text errors return `status: needs_correction` with exit 0,
the exact canonical sources and the same-run `next_stage` arguments. This is unfinished authoring,
not an `ok` report: it writes no report, and an older report is not approved for delivery. Make
**one bounded correction** to only the named `text` fields against those sources, then repeat that
stage once in this same run. If it still needs correction, state the unfinished translation and
stop; do not keep rewriting, refetch, change layers or hashes, reserve a replacement run or deliver
the old English output. Source, structure, binding, file and internal errors remain failures. The
command without `--allow-authoring-correction` stays strict, and the form without a presentation
is legacy output, not a language-complete report.

## Final check

Only after render returns `status: ok`, run this route's final step, the strict read-only report
check:

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/render_short_window_report.py' short-window-layers.json creator-report.md --language <the user's chat language> --presentation short-window-presentation.json --verify-report
```

It recomputes the original strict validation and requires the existing report bytes to match
exactly; it never creates or rewrites a report. No connector call, new run or extra evidence is
needed. Do not emit, return or claim success from the rendered file until this final step
succeeds; then deliver `creator-report.md` after the complete-report language check in `SKILL.md`
(*Language at delivery*).
