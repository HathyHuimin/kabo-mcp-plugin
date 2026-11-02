# Watching: media calls, persistence and outcomes

Read this whole before the first media call (workflow step 4). Commands use `<skill directory>`
(the folder holding `SKILL.md`) and run from this request's run directory.

## Make the planned calls

`media_job_plan.json` lists every planned request under `batches[].requests`, in order. Each
request names a `content_id`, its `url`, an `operation` and its `params`. Make **one individual
call per request**, in plan order, to the tool named for its operation, with the arguments that
`static_host.py job-call` prints for the planned `params` and the request's `attempt` (below) —
`{"parameters": <params>, "delivery": "download", "idempotency_key": …}` — sent verbatim:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/static_host.py' job-call --tool <tool> --parameters '<params JSON>' --attempt <attempt>
```

| Planned operation | Tool |
|---|---|
| `collect_public_metadata` | `public_video_collect_public_metadata` |
| `fetch_transcript` | `public_video_fetch_transcript` |
| `extract_keyframes` | `public_video_extract_keyframes` |

Never combine several requests into one batch call: a batch that fails as a whole erases the
evidence of every request in it. Independent requests go out in the same turn. Issue every planned
request; a planned video whose three calls were never issued is refused by the outcomes script.

## Keep the issued-job mapping

A healthy call answers with a deferred job id. Immediately after each call, record its job id with
the exact planned `content_id`, URL, operation and attempt, in original request order, including
failures and retries. Preserve this complete mapping before polling or issuing more, and check that
every planned request was issued. Never infer which job belongs to which video from the order in
which jobs finish, never copy an id from displayed text you cannot tie to its call, and never read
host conversation files to reconstruct one.

`attempt` starts at 1 and increases for each new request for the same content and operation,
retries included. A call that got no response at all (a timeout or lost connection) is not a new
request: send the same arguments again, and its key returns the original job instead of queueing
and charging a second one. After an error result, or a tool message naming `idempotency_key`,
the retry is a new request with the next `attempt`. A call that returns neither an envelope nor a job id has failed. Read its own
message: an invalid-input or validation error is never retried unchanged; a rate limit, server
error or empty answer gets one retry. Then move on. A call that answers synchronously with a terminal envelope has no job id: it needs no
mapping entry and no `--job` binding, and keeps the filename, video-ID and URL checks with the
numbered attempt filenames below.

## Poll every job to a terminal state

A job id is not a result. Read each with `get_research_data_job`, `wait_s` (up to 30) and
`"delivery": "download"` (call the media tools with it too), and keep
polling until it reaches a terminal state; never restart a queued job. A media round takes minutes,
not seconds: on a ten-video Instagram watch set, the thirty envelopes landed
over 178 seconds, the last just under three minutes after the first. A video still pending is a job
you have not waited for yet, not a video that cannot be watched.

## Fetch and persist each terminal result

Handle one terminal result at a time, as soon as it arrives. With `"delivery": "download"` the
result holds a download link instead of the data, valid for 10 minutes. Save the complete
`get_research_data_job` result (for a synchronous answer, the tool's own result, fetched under that
tool's name) to a file — the host's result export, or the small result unchanged — fetch it at
once, and persist it at once into the file named for the `content_id` its job belongs to in your
mapping (or, for a synchronous answer, the request that made it):

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/static_host.py' fetch --tool get_research_data_job --input 'host-results/<NN>-get_research_data_job.json'
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/persist_envelope.py' --dest snapshot/media-<content_id>-<dest_operation>.attempt-<attempt>.json --connector public-video-media --operation <envelope_operation> --any-status
```

Save each complete result in the run's `host-results/` folder, one file per result numbered in
call order (`host-results/01-<tool-name>.json`, `host-results/02-<tool-name>.json`, …). These
files are inputs to `fetch` and `stage`, not placed evidence, so never save them in `snapshot/`.

- `fetch` checks size and sha256, then stores the evidence exactly as `stage` would; a fetched
  result counts as staged. When it reports no ready download, save the complete result with the
  host's result export and run the same command with `stage` instead. When it says the link expired,
  call `get_research_data_job` again with `"delivery": "download"` and fetch right away; for a
  synchronous answer, call `get_research_data_download` with the `evidence_ref` that `fetch` names
  (nothing is re-run) and fetch it with `--tool get_research_data_download`. If that call itself
  fails, call the original tool again with the same parameters and `"delivery": "download"` (this
  re-runs it) and fetch that result right away.
- If `static_host.py fetch` or `stage` refuses the result because it is a tool error with no
  connector evidence, it is a failed attempt of that request, not a script failure: persist nothing,
  retry the request by its error kind as above, then move on. If it refuses a job that is still
  queued or running, keep polling that job.
- `persist_envelope.py` has no job or content-ID flag: each call takes the earliest staged response
  of its `--operation` that this run has not placed yet. Persisting each result right after staging
  it is what binds it to the right file. The outcomes script checks every file's content, URL and
  job against its name.
- `envelope_operation` is the `operation` field on the result. A completed
  `extract_keyframes_hosted_media` or `extract_keyframes_hosted_stream` envelope is the same seat as
  `extract_keyframes`: persist it with `--operation` set to the name on the envelope and
  `dest_operation` `extract_keyframes`. For the other two operations `dest_operation` is the planned
  operation.
- Persist only a terminal envelope, and preserve every failed terminal envelope. Keep every prior
  attempt's file; never overwrite one. Legacy filenames without an attempt suffix remain readable.
- Never retype a tool result into a file or write an envelope yourself, and do not edit
  `persist_envelope.py`. If the host cannot save a complete result, say that this step is not
  possible here.
- Read frame bytes with `read_research_data_artifacts` with `"delivery": "download"`, save that
  small result in `host-results/` and run `static_host.py fetch` on it; then open each image's
  `path` from fetch's output with the host's own file or image viewer — a file on disk is not yet
  viewed. Never persist an artifact read as an envelope. Only for items fetch lists as unavailable,
  or when scripts cannot reach the network, read those refs again without `delivery` (the images
  then arrive inline) and stage that result from the host's result export. Never rebuild a result
  from the host's own session or tool-result folders.

Public metadata may be partial. Use a retrieved title without inventing a duration or engagement
metrics, and preserve the metadata origin, connector status and limitations in the synthesis. A
title from public embed metadata does not mean the full metadata fetch succeeded. A duration
measured from frames stays attributed to frame extraction.

## Classify what was watched

After every planned job has a terminal envelope or an outstanding job id, pass every recorded
attempt that returned a job id as `--job <content_id>:<operation>=<job_id>`, one per issued
attempt (synchronous answers need none):

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/record_watch_outcomes.py' media_job_plan.json --envelope-dir snapshot --job <content_id>:<operation>=<job_id> [--job …] --output watch_outcomes.json
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/build_watch_evidence.py' watch_outcomes.json --envelope-dir snapshot --output watched_evidence.json
```

- A bound issued job without a terminal envelope stays pending with its real job id, even without
  an explicit `--pending` flag. `watch_outcomes.json` keeps the selected terminal job id, and
  `build_watch_evidence.py` checks it again before reading that video's frames or cues. The full
  outcomes keep job ids for audit; compact evidence carries pending status without them.
- `record_watch_outcomes.py` refuses, writing no outcomes, while a planned video has had none of its
  three calls issued, and while a video not yet watched still has a pending job (it prints the job
  ids to poll). That refusal is not a reason to deliver: issue the missing calls or poll those jobs,
  persist each terminal envelope, then run it again. Every planned video gets its round — a review
  that skips them is a catalog review wearing this Skill's name.
- Only when a job has been polled across the whole window above and still has no terminal state does
  it become a genuine `pending`: record it with `--pending <content_id>:<operation>=<job id>` (or
  `--deferred-incomplete` when this run is closing) and `--allow-pending`, and deliver the videos
  that were watched. The job id is the one the call returned; a placeholder such as `not-started` is
  refused, because pending means the call went out and has not come back. Reaching for that flag
  early is the failure it exists to make visible — a run once closed with seven of
  ten videos at `jobs_pending` and reported shot-level findings from the three that had arrived.
- Watched means frames arrived or a transcript arrived. Metadata alone is not watched. Never narrate
  hooks, cuts or spoken lines for an unwatched `content_id`.
