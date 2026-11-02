# Audio board and scene soundtrack

Read this file whole before the first data call of either path. Both paths run in the one run
reserved for this request (see *Set up one run* in `SKILL.md`); every command below runs from that
run directory with `KABO_STATIC_RUN_DIR` set, and `<skill directory>` is the folder holding
`SKILL.md`. Neither path creates a `TrendResearchRequest`, `TrendResearchReport`, Snapshot or
Analyzer output, and neither uses `drive.py`.

## Instagram Reels audio board

Which audio, sounds, songs or music are trending, hot or rising on Instagram Reels right now - the
generic board and its fastest risers - is one board the platform already published, not something
to search for. Kabo builds that board once a day and serves it through one tool; this path reads
that snapshot and shows it unchanged. Never answer it from Reel search results, Popular recall,
account histories or prior knowledge - none of those is the board, and a board question answered
from Reel evidence is a wrong answer, not an approximation.

1. Check readiness once (`get_research_capabilities`) and require only
   `creator_trends_get_instagram_audio_board` (operation
   `creator-trending-board/get_instagram_audio_board`) to be implemented and ready. Reel-discovery
   readiness is not required here. When that operation is missing, unimplemented or not ready, say
   the audio board is unavailable right now and stop.
2. Call `creator_trends_get_instagram_audio_board` exactly once with
   `{"parameters": {}, "delivery": "download"}` - no parameters at all. The defaults are the whole
   published board: the ranked rows plus today's and yesterday's fastest risers. Pass `limit`
   (integer 1-50) only when the user asked for fewer rows than the board holds; it truncates the
   ranked rows and leaves the risers as they are, and never returns more rows than the published
   board. When the user asks for more rows than the board returned (say 100-300 songs from a 20-row
   board), say in one sentence how many rows the published board holds; do not refetch, page, or
   fill the gap from other sources. Pass `include_rising: false` only when the user said they
   do not want the risers. Do not repeat a completed call, do not batch it with other calls, and
   never substitute another tool or another operation for this one - not a music catalog search, not
   Reel search. A failed or unavailable operation is not an empty board: report the missing board
   capability and stop.
3. Save the complete result, fetch it (`static_host.py fetch --tool
   creator_trends_get_instagram_audio_board`, or `stage` when fetch reports no ready download),
   then persist and render it. The renderer keeps the
   returned order, numbers the rows by that order, copies every number as given and adds nothing:

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_envelope.py' --dest trending-board-envelope.json \
  --connector creator-trending-board --operation get_instagram_audio_board \
&& python3 '<skill directory>/scripts/render_trending_board.py' trending-board-envelope.json creator-report.md \
&& cat creator-report.md
```

This is the path's final step; its deliverable is `creator-report.md`. Deliver the printed report
without another model rewrite. Never
re-rank, filter, dedupe or merge the ranked rows and the riser lists, never recompute growth, and
never add a row, count or growth figure the board does not carry.

When the user also asks for an idea for each audio, give the ideas after the delivered board,
labelled as suggestions rather than board data, one per distinct audio in the board's order: every
ranked row, then each riser that is not already a ranked row (a riser that is also ranked gets no
second idea). Never fold them into one shared plan for every row.

Always say which day the board is from. The day is `fetchedAt` in the response and the renderer
prints it. When `stale` is true, say plainly that today's board has not been built yet and this is
the last published one: a fresh fetch of a stale board is still the earlier day's board, and
presenting it as today's is the one failure this path can produce silently.

Treat the usage count (`clipsCount`) as the lifetime number of reels using the audio, not search
volume. A null `dailyGrowth` means no comparable prior count was available, not zero growth. A
board shows usage and its day-over-day change; it never shows niche fit, audience demand, posting
time or broader content demand, and niche-fit questions still need content-level evidence. An
empty successful board means no audio was returned.

Music for named video scenes is the path below, not this board.

## Scene soundtrack

When the user asks for music that fits named video scenes, activities or genres, including dated
soundtrack requests, do **not** run the Reel trend workflow, deliver the current audio board, or
substitute a TikTok sound list. A generic current Reels audio chart uses the board path above; a
historical month request cannot be answered by today's board.

1. Readiness (checked once) must show `instagram_baseline_search_reels` and
   `instagram_baseline_search_music` implemented and ready.
2. Freeze keywords from the requested scenes, activity, genre and context; derive every scene from
   the request; do not invent fallback scenes. Write `label` in the delivery language. Mood alone
   is not proof of genre fit. Preserve `requested_count`, `requested_month` and `retrieved_at` when
   supplied. Before any call, write `scene-sounds.json` with those supplied request fields at the
   top level alongside `scenes`. Initially restrict each scene object to `id`, `label` and
   `keyword`; this restriction does not remove top-level request metadata.
3. For each scene `i` (0-based), start with `instagram_baseline_search_reels` and
   `instagram_baseline_search_music`
   (`{"parameters": {"query": <keyword>}, "delivery": "download"}`). Music search has no cursor or
   monthly filter: do not invent pagination or claim a complete monthly chart. Reels search supports
   its returned cursor (`cursor` in `parameters`). Continue only while more results and a new cursor
   exist, within the agreed call budget; stop on enough relevant evidence, exhaustion, repeated
   cursor, no new sound IDs, failure or budget. Record each scene's reason in
   `scenes[i].stop_reason`; deduplicate by sound_id and keep dated post evidence.
4. Store every Reels page at a new zero-based index `p` under `scenes[i].video`, beginning at
   `[0]`; increment `p` after each call. Reusing a path replaces that page, so never write a second
   page at the same index. Keep `catalog` as one non-paginated music envelope; never hand-copy
   envelope fields. After each call, fetch its complete result, then place its envelope into that
   scene:

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_envelope.py' --into scene-sounds.json --at scenes --at [i] --at video --at [p] --connector instagram-baseline-proxy --operation search_reels
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_envelope.py' --into scene-sounds.json --at scenes --at [i] --at catalog --connector instagram-baseline-proxy --operation search_music
```

   The music call is placed once per scene, without a page index. The reels envelope already
   carries `attached_sounds`.
5. Render. Known original audio is dropped. Join is `sound_id` (`audio_cluster_id` for catalog
   music). Unknown original status is not licensing evidence. Review actual genre/scene relevance;
   unrelated tracks cannot fill a requested count. Distinguish candidates, dated in-month usage and
   popularity evidence. Lifetime counts or retrieval dates establish neither in-month usage nor
   monthly popularity; an incomplete month cannot establish full-month popularity. Keep source
   links and post dates when returned; report missing ones explicitly. Do not invent usage or
   rights.

```bash
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/render_scene_sounds.py' scene-sounds.json creator-report.md --language <en-or-zh-CN>
```

This render is the path's final step; its deliverable is `creator-report.md`.
