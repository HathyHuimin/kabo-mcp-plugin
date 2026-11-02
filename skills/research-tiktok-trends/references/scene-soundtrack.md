# Scene soundtrack

Read this whole file before the first call of this route. `<run directory>` and
`<skill directory>` are the absolute paths from *Running commands* in `SKILL.md`; run every command
from the run directory.

The user names video scenes, activities or genres (in any language) and wants sounds that fit
them, not a generic chart. Mood is a grouping label, not a search query. **Do not deliver the sound
board for this route**, and do not call its tool. Do not create a query plan, Snapshot, Analyzer
output or `TrendResearchReport` for this route.

1. Readiness must show `tiktok-discovery/search_videos` and `tiktok-discovery/search_sounds`
   implemented and ready.
2. Freeze one search keyword per requested scene or activity, derived from the request; do not add
   a default scene count or a default topic. Unknown scene assets allow conditional guidance; do
   not invent a default scene to start unrelated searches. Before any call, write
   `scene-sounds.json` with only your scene fields: `{"scenes":[{"id","label","keyword"}]}`. Write
   `label` in the delivery language; the renderer uses it as the scene heading and lists the scene
   labels in the report opening.
3. For each scene `i` (0-based), call `tiktok_discovery_search_videos` once (`sort_type: 1`,
   `count: 20`, `publish_time: 30` when the user named no dates, otherwise the smallest supported
   `publish_time` range covering the requested dates) and `tiktok_discovery_search_sounds` once
   (`sort_type: 1`, `count: 20`). Make each call on its own, never batched. After each call, save
   and fetch its result, then place it into that scene:

   ```bash
   cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_envelope.py' --into scene-sounds.json --at scenes --at [i] --at video --connector tiktok-discovery --operation search_videos
   cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_envelope.py' --into scene-sounds.json --at scenes --at [i] --at catalog --connector tiktok-discovery --operation search_sounds
   ```

   A call that still fails after its one retry (`references/data-calls.md`) is not placed:
   render the other scenes and say next to that scene that its search failed.
4. Render; this is the route's final step and its report is `creator-report.md`. The script drops
   original sounds, joins on `sound_id`, and does not invent `daily_growth`:

   ```bash
   cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/render_scene_sounds.py' scene-sounds.json creator-report.md
   ```

Treat `video_count` as lifetime usage. Do not invent a playlist, and do not infer niche fit,
audience demand, posting time or broader content trends from sound usage counts. Niche-fit
questions still need content-level evidence.
