# Data calls, result ids and failures

Read this when a stage hands calls over and something does not go as printed: a fetch or stage
refuses a result, an id or link expires, a job is still queued, or a tool call fails. The normal path
is only what the hand-over prints: make the calls, then run the one printed resume command with each
call's result id in it (`--id NN=<id>`); the resume fetches every result itself.

## Result ids and saved results

- The displayed result may be abridged, so never retype or paste data. A result called with
  `"delivery": "download"` holds a download link instead of the data: never open that link yourself
  with a shell command or a browser. Its **result id** is the 22 characters between
  `research-export/v1/` and `.json` in that link. Do not save such a result: give its id to the resume
  command (`--id NN=<id>`, NN being the slot the hand-over printed under the call) or, where a step
  fetches by itself, to `static_host.py fetch --tool <tool> --id=<id>`. The script downloads the data
  by that id and checks its size and sha256. A result that lists `calls` has one id for the whole
  result: never call `get_research_data_download` or download anything per call. Ids exist only for
  whole-result links (`research-export/v1/`). A result whose links point to separate artifacts
  carries no id: save that small result in `host-results/` and run `static_host.py fetch --tool
  <tool> --input <file>` on it.
- Only a result with no download link (download unavailable, or a tool called without delivery) is
  saved: the result itself, whole and unchanged, to the absolute path the hand-over names for its
  slot in the run's `host-results/` folder, written with the host's file-write ability or a
  single-quoted heredoc, never by passing the result as a command-line argument; leave its `--id`
  out. These files are inputs to `fetch` and `stage`, never placed evidence: do not save them in
  `snapshot/`.
- `fetch` checks size and sha256, then stores the evidence exactly as `stage` would: a fetched result
  counts as staged, and `fetch` refuses what `stage` refuses, with the same message. When it reports
  no ready download, `stage` takes the file (the resume does this itself; elsewhere run the same
  command with `stage` in place of `fetch`).
- Only for artifact items `fetch` lists as unavailable, or when scripts cannot reach the network,
  read those `object_refs` again without `delivery` and `stage` that result. Never rebuild a result
  from the host's own session or tool-result folders.
- Fetched images are files on disk: open each image's `path` from fetch's output with the host's own
  file or image viewer before describing it.
- Fetch or stage each result once, never a retyped or edited copy, and never rewrite its status. If
  the host cannot save a complete result, say that this step is not possible here and stop; never
  reconstruct data from what was displayed.

## Expired ids and links

Ids and links last 10 minutes. Do what `fetch` (or the resume) prints. A result called again has a
new id: run the resume command again with that id (or replace the old result in its file). For a data tool's result, call `get_research_data_download` with the
`evidence_ref` it names (no allowance cost; nothing is re-run) and fetch that result with
`--tool get_research_data_download`; the new link's sha256 and bytes can differ from the old one. If
that call itself fails, call the original tool again with the same parameters and
`"delivery": "download"` (this re-runs it) and fetch that result right away. For a
job read, an artifact read or a `lookup_*` tool, call that same tool again with
`"delivery": "download"`. A `collect_*_trend_candidates` or `enrich_*` result is complete inline:
export it and run the same command with `stage` instead of calling the tool again. Any other result
that lists `calls` is not complete inline: call that tool again with the same parameters and
`"delivery": "download"`, then fetch it right away.

## Deferred jobs

A tool that answers with a job id is not finished: read it with `get_research_data_job` and `wait_s`
(up to 30) until it is terminal, and fetch the terminal result under `get_research_data_job`, never
one still queued. Never restart a queued job. If a job names artifacts, read them with
the artifact-read tool in `allowed-tools`; artifact reads are fetched or staged, never placed as
Envelopes.

## A failed tool call

A failed call is evidence, not the end of the run. A failed result that names its connector and
operation is staged like any other, and the stage keeps it as that route's or subject's evidence.
When `stage` refuses a result with "MCP error contains no terminal connector evidence: …" or
"Complete evidence object is required", that call failed; save nothing for it. Then:

1. Read the tool's own message, which `stage` prints after the colon.
2. **Invalid input** (a schema, pattern or validation error) is never retried unchanged and is never a
   network problem. When the bad value came from your own artifact or flag, correct it and run the
   stage again, if the stage accepts the change; otherwise report that step with the tool's message
   and stop.
3. **Rate limit, server error or empty answer:** make that one call once more (it counts toward the
   Skill's retrieval budget) before saving any later call of the same round, and save the retry if it
   succeeds. A failed connector envelope that `stage` accepted is not retried by you: run the resume
   command; the driver hands a briefly failed round over once more on its own, and an extra call of
   yours would land in the wrong slot.
4. **Still failed:** run the resume command. What the stage does without that response is in
   `SKILL.md`. Say the network or the data service was briefly interrupted (and to try again in a few
   minutes) only when the driver says so or the call returned nothing at all; never guess another
   cause.

## A failed script or validator step

It ends the run, except when its message names a field of an artifact you wrote: then correct only
that field and run the same stage again (add `--placed` if its calls were already made). If a
deterministic stage fails, do not call other analyzers or tools ad hoc. Never reopen a run, repeat
completed retrieval or start a replacement run.
