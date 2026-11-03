# Watching: one backend call, the outcomes and the frames

Read this when the watch step (workflow step 2) does not go as printed, and before writing the cards
(step 3). Commands use `<skill directory>` (the folder holding `SKILL.md`) and run from this request's
run directory.

## The media call

`watch_media.py` prints one `collect_video_media` call. Its `requests` are `media_job_plan.json`
(every selected video × `collect_public_metadata`, `fetch_transcript`, `extract_keyframes`) and its
`run_key` belongs to this run. The backend queues every job itself, keyed so that the same arguments
never queue or charge a job twice, and waits up to about 45 seconds per call. A media round takes
minutes, not seconds (on a ten-video Instagram watch set the thirty jobs landed over about three
minutes), so:

- while the result still reports running jobs (`coverage.pending` above 0), make the same call again
  right away with the same arguments and save the newest result over the same file;
- then run the printed resume command. It fetches the saved result, and while jobs are still running
  it hands the same call over again with a new file to save; do as it says.
- Never call the `public_video_*` tools, `get_research_data_job` or `get_research_data_download`
  yourself for the watch set, and never change the call's arguments: a changed request under the
  same `run_key` is refused for that item and queues nothing.
- A failed call (an error result with no composite result) is evidence of an outage, not of the
  videos: make the same call once more; if it fails again, say which deliverable is unavailable.

The resume writes each terminal envelope as `snapshot/media-<content_id>-<operation>.attempt-<n>.json`
(the backend's attempt number; a job that failed with a rate limit or server error was queued once
more by the backend) and binds every job id to its planned video, so no envelope or job id is ever
handled by hand. Never write or edit these files.

## Classify what was watched

The resume then runs `record_watch_outcomes.py` and `build_watch_evidence.py` itself:

- Watched means frames arrived or a transcript arrived. Metadata alone is not watched. Never narrate
  hooks, cuts or spoken lines for an unwatched `content_id`.
- A job still running after the resume has handed the call over six times becomes a genuine
  `pending`: that video is recorded as `jobs_pending` and the videos that arrived are delivered. Say
  how many videos were not watched; never describe them.
- Public metadata may be partial. Use a retrieved title without inventing a duration or engagement
  metrics, and preserve the metadata origin, connector status and limitations in the synthesis. A
  title from public embed metadata does not mean the full metadata fetch succeeded. A duration
  measured from frames stays attributed to frame extraction.

## View the frames and write the cards

Display the actual frame images in the model context before making any
visual claim; an artifact path, frame count or returned image-type list is not viewing. Make every
printed `read:` call (`read_research_data_artifacts`) in one response. When the result holds a
download link, save it in the run's `host-results/` folder and run
`static_host.py fetch --tool read_research_data_artifacts --input '<saved file>'`, then open each
image's `path` from fetch's output with the host's own file or image viewer; when the images arrive
inline they are already displayed. Keep their content IDs, timestamps and original `object_ref`s.
Only for items fetch lists as unavailable, read those refs again without `delivery`. Never persist an
artifact read as an envelope. If images cannot be displayed, keep the missing-visual-evidence limit and use the
transcript only. `watched_evidence.json` reuses the single-video frame and cue contract. A title,
caption or view multiple is not proof of a hook, a cut or on-screen text. Write one short card
per watched item from its frames and transcript:

- spoken hook, visual hook, text overlay, first-frame promise;
- five shot aspects: subject, subject motion, scene, spatial framing, camera;
- short-form checkpoints: 1–2 s hook, a visual change every 1–3 s, payoff second.

The checkpoints are analysis prompts, not observed timings. Use the viewed frame timestamps and
spoken cue times: when a sentence spans a checkpoint, do not say its full proposition was
established before it. A cue with no end time proves its start and text, not when the sentence
finished. Sparse frames support only the viewed moments, never continuous motion, an unchanged
shot or absence across an unseen interval. Describe spoken and visual hooks separately, never
substituting the title for speech. Keep observed objects and case-study setups in low-performing
examples too; performance rank does not make an opening evidence-free. Do not emit the
single-video Skill's diagnosis cards or 17-dimension funnel, and do not run that Skill. Watching
can keep a Leo class or rename it — that rename is the value of watching; unwatched rows keep
their provisional catalog labels.
