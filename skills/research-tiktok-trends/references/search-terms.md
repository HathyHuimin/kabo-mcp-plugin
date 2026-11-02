# Rising search terms

Read this whole file before the first call of this route. `<run directory>` and
`<skill directory>` are the absolute paths from *Running commands* in `SKILL.md`; run every command
from the run directory.

Use this route when the user asks what TikTok users are searching, which search terms are rising,
or which search terms to follow now. Current search demand needs no public account handle. Do not
run the video-trend workflow, and do not create a query plan, Snapshot, Analyzer output or
`TrendResearchReport`. Do not substitute hot videos, hashtags or the music chart for search terms.
General video-trend requests use the workflow, never search-term lists as a substitute.

## Fetch

1. Check readiness once and require `tiktok-discovery/list_search_insights` to be implemented and
   ready. Video-discovery readiness and the music chart are not required.
2. Call `tiktok_discovery_list_search_insights`. Use `tab: all` by default; add a second call with
   `tab: content_gap` only when the user asks about gaps or underserved searches.
   - Set `language_filters` from the request language when it is a two-letter code, otherwise
     `en`.
   - The tool's `category_filters` values are `Gaming`, `Fashion`, `Tourism`, `Science`, `Food` and
     `Sports`. Set one only when the user's niche is that category or sits under it in the
     returned `category_path` taxonomy. When the niche is a subcategory of a broader filter value,
     call with that parent and then keep only rows whose returned `category_path` contains the
     subcategory: for example, Fitness lives under `Sports`, so a gym or workout niche calls
     `Sports` and keeps only `Fitness` rows. A parent-wide dump that includes unrelated
     subcategories is a failed answer for the narrower niche. When no supported value matches,
     omit `category_filters`; do not invent an alias or broaden the final result to fill a count.
   - `limit` is 1–50 (default 20).

   Save and fetch each result, then persist it:

   ```bash
   cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_envelope.py' --dest search-insights-envelope.json --connector tiktok-discovery --operation list_search_insights
   ```

   (`--dest search-insights-content-gap-envelope.json` for the `content_gap` call). A failed or
   unavailable operation is not an empty list: report the missing search-term capability and stop.
3. If `tiktok-discovery/get_search_insight_trend` is also implemented and ready, call
   `tiktok_discovery_get_search_insight_trend` for at most five niche-matching `query_id` values
   from the **kept** list. After each call, save, fetch and place it:

   ```bash
   cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_envelope.py' --into search-insight-trends.json --at runs --at <query_id> --connector tiktok-discovery --operation get_search_insight_trend --any-status
   ```

   These calls are optional: the renderer reads only the list. A trend call that failed is placed
   as it is (`--any-status`) and skipped; when nothing could be staged for it, skip its placement
   too. That term keeps the list's own `trending_seq`. List rows already include `trending_seq`;
   do not invent a window when the sequence is missing.

## Judge

4. Every reported term must show its window (`trending_seq` first → last) and `rise_ratio`.
   `rise_ratio` is last/first of that window when the first point is > 0. A null or ≤1 ratio is
   related, stable or a gap, not rising. Do not call a term rising because it is popular.
5. Review the returned terms against the requested niche and write a separate selection JSON,
   `search-insights-selection.json`, with `keep_query_ids` and `recommend_query_ids`, both arrays
   of exact retrieved IDs. Keep only relevant terms; recommendation IDs must be a subset of kept
   IDs and name reusable mechanisms. A matching brand or product name may remain visible evidence
   without becoming a tutorial recommendation. Empty arrays are valid; never widen the niche to
   fill a requested count. "Worth making now" also requires `rise_ratio` > 1 or a `content_gap`
   label. Do not recommend one-off person, place or news names even if rising. Do not hardcode any
   niche list or edit the source envelope. Use a separate selection for each fetched list.
6. A popularity index is not absolute search volume; returned video counts and a content-gap label
   do not prove quality-adjusted supply. Undated sequence points do not establish a recent calendar
   window: never assign calendar dates to sequence positions. Preserve these distinctions when
   ranking opportunities, and state the shortfall when the requested number lacks support. Once
   detail enrichment fails to supply a date, report that precise field gap rather than asking the
   user to copy publicly retrievable lists or fetching the same series again.

## Render

This is the route's final step; its report is `creator-report.md`. Render and deliver the result
without rewriting the numbers:

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/render_search_insights.py' search-insights-envelope.json creator-report.md --selection search-insights-selection.json
```

When the niche is a subcategory of the filter you called (for example Fitness under Sports), add
`--keep-category-substr <subcategory>` with the exact value seen in the returned `category_path`.
Do not render the unfiltered parent list. If the `content_gap` list was also fetched, write its own
selection, `search-insights-content-gap-selection.json`, and render it to a second file:

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/render_search_insights.py' search-insights-content-gap-envelope.json creator-report-content-gap.md --selection search-insights-content-gap-selection.json
```

Deliver both reports, `creator-report.md` first; do not merge the two lists by hand.

## Demand and supply together

For a composite demand-and-supply opportunity request, deliver the search-term result first.
Content supply is a separate, bounded study, not part of this run: name it as the next step (this
Skill's video-trend workflow on the kept terms, as a new request). A search-term-only request must
not trigger that extra collection. When that study runs, it uses the same topic, market and
declared period, compares relevant content against the actual need, and preserves sample URLs and
missing content dimensions. Content search is supply evidence, never a substitute for search
demand. Do not rank an entire market as underserved from a small sample or inflate one supported
term into the requested number of independent gaps.
