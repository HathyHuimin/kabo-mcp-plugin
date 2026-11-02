# Short window: hottest and fastest-rising Reels

Read this file whole before the first data call of this path. It runs in the one run reserved for
this request; every command runs from that run directory with `KABO_STATIC_RUN_DIR` set, and
`<skill directory>` is the folder holding `SKILL.md`.

Use this path when the requested window is seven days or shorter (the last day, the last 48 hours,
this week — named in any language) and the user wants recent viral Reels to copy. Every longer
window, and every ask for repeated mechanisms or account-relative multiples, uses the Workflow with
its window policies (a seven-day-or-shorter ask for account-relative multiples with
`--policy user_named_span`). In this path do **not** stop because short-window Search is unsupported
or the pool is thin. Do **not** create a `TrendResearchRequest` or `TrendResearchReport`. Do **not**
call `collect_instagram_trend_candidates` or `enrich_instagram_shortlist`: a collect on a
short-window ask is a failed delivery, not a reason to stop.

## Placement

Keep each completed response at its own route/slot before building; missing collections or only
failed responses stop the builder rather than proving zero results. A completed empty collection
remains a valid empty result; any explicit failed or unavailable source, including one mixed with
completed sources, stops the builder because these layers do not project partial failures. A search
whose result `fetch` or `stage` refused (an MCP error without a connector envelope) cannot be
placed; after the one retry the failed-call rule allows, leave that intent unplaced and say in the
delivery that its search failed, with the tool's own message.

## Steps

1. Readiness (checked once): `instagram_baseline_search_reels` implemented and ready. Do not send
   `last-day` to Search.
2. Freeze the requested window in UTC: `until` is now unless the user named an end, and `since` is
   `until` minus the requested span (resolve a relative span once against the current time). At
   most two intents from the user's niche. Before any call, write `short-window-input.json` as
   `{"platform":"instagram","window":{"since","until"}}`.
3. Heat lane: for each intent, call `instagram_baseline_search_reels` once with
   `{"parameters": {"query": <the frozen intent phrase>}, "delivery": "download"}` (the capability
   has no date filter, so it returns recent popular Reels; the layer builder dates every row). Those
   rows already carry `taken_at` and plays.
4. After each call, fetch its complete result, then place its envelope:
   `cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_envelope.py' --into short-window-input.json --at runs --at <intent> --connector instagram-baseline-proxy --operation search_reels`.
   The layer builder reads `taken_at` from the rows itself.
5. Build and render two layers. Heat rows must show their publish time and must not be described
   as in-window. An empty window layer is a valid deliverable.

```bash
# once per completed search, before building
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_envelope.py' --into short-window-input.json --at runs --at <intent> --connector instagram-baseline-proxy --operation search_reels
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/build_short_window_layers.py' short-window-input.json --output short-window-layers.json
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/render_short_window_report.py' short-window-layers.json --language <the user's chat language> --print-presentation-contract --text-only --contract-output short-window-presentation.json
```

When the builder answers `status: needs_correction` (`no_placed_collection`), place every completed
response this run already fetched with the printed placement command, one per response at its own
slot, then repeat the build once. Do not fetch again, edit `short-window-input.json` by hand or
replace the run.

## Translate the presentation

The print command prints the full source contract and creates a smaller, text-only presentation
file. Translate the printed sources into the user's chat language by filling **only** every `text`
field in `short-window-presentation.json`, including labels, limitations and every displayed source
excerpt; keep proper names where appropriate. Read the whole contract without truncation.

- Preserve each complete source excerpt's meaning, numeric facts, URLs and handles. A row's
  `source_excerpt` is the first 80 characters of its caption and may stop mid-word: translate
  exactly that excerpt, not the full caption in `short-window-layers.json`, and never restore
  numbers, links, handles or words cut off after it.
- The original literal-number comparison remains first for compatibility in every language. Only
  when those numeric spellings differ, a complete English month-name calendar date may use the
  equivalent Chinese year/month/day notation (or vice versa); preserve the same valid date. This
  fallback does not verify translation semantics. Other numbers remain literal: do not add numbers
  from the surrounding task or window.
- Do not summarize the excerpt, add a headline, invent viewing, acceleration or broader coverage,
  or add advice. Empty source excerpts stay empty.
- Keep the generated layers hash, row slots and language unchanged. Do not copy source excerpts or
  item hashes into this file: the renderer binds those immutable fields from the exact layers
  bytes. Do not change the raw layers. The older full presentation contract remains accepted with
  all its binding checks.
- A script can check the binding and literal facts, not translation faithfulness; read the final
  complete report to verify its language and meaning.

## Render, correct once, verify

Render the authored presentation in the same open run with this non-final command:

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/render_short_window_report.py' short-window-layers.json creator-report.md --language <the user's chat language> --presentation short-window-presentation.json --allow-authoring-correction
```

The renderer checks the complete layers, presentation structure and immutable bindings before
classifying authored prose. Only named text errors return `status: needs_correction` with exit 0,
the exact canonical sources and the same-run `next_stage` arguments. This is unfinished authoring,
not an `ok` report: it writes no report, and an older report is not approved for delivery. Make
**one bounded correction** to only the named `text` fields against those sources, then repeat that
stage once in this same open run. If it still needs correction, state the unfinished translation
and stop; do not keep rewriting, refetch, change layers or hashes, reserve a replacement run,
reopen a terminal run or deliver the old English output. Source, structure, binding, file and
internal errors remain failures. The original command without
`--allow-authoring-correction` remains strict, and the no-presentation form remains legacy output
rather than a language-complete report. The print mode exclusively creates the presentation
skeleton and is non-final; keep the same run directory for every step and never resend
accumulated inputs or layers.

Only after render returns `status: ok`, run this path's final step, the strict read-only report
check:

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/render_short_window_report.py' short-window-layers.json creator-report.md --language <the user's chat language> --presentation short-window-presentation.json --verify-report
```

The final check recomputes the original strict validation and requires the existing report bytes
to match exactly; it never creates or rewrites a report. Its deliverable is `creator-report.md` in
the run directory. No tool call, new run or extra evidence is needed. Do not emit, return or claim
success from the rendered file until this final step succeeds; then deliver `creator-report.md`
after the whole-report language check in `SKILL.md`.
