# Data calls, saved results and failures

Read this when a stage hands calls over and something does not go as printed: a fetch or stage
refuses a result, a link expires, a job is still queued, or a tool call fails. The normal path is
only what the hand-over prints: make the calls, save each result to the file named under it, run the
command under it, then run the printed resume command.

## Saved results

- The displayed result may be abridged, so never retype or paste data. Save each complete result
  unchanged (the host's result export, or the result as returned) to the file the hand-over names
  in the run's `host-results/` folder, then run the command printed under it. These files are inputs
  to `fetch` and `stage`, never placed evidence: do not save them in `snapshot/`.
- `fetch` checks size and sha256, then stores the evidence exactly as `stage` would: a fetched result
  counts as staged, and `fetch` refuses what `stage` refuses, with the same message. When it reports
  no ready download, run the same command with `stage` in place of `fetch`.
- Only for artifact items `fetch` lists as unavailable, or when scripts cannot reach the network,
  read those `object_refs` again without `delivery` and `stage` that result. Never rebuild a result
  from the host's own session or tool-result folders.
- Fetched images are files on disk: open each image's `path` from fetch's output with the host's own
  file or image viewer before describing it.
- Fetch or stage each result once, never a retyped or edited copy, and never rewrite its status. If
  the host cannot save a complete result, say that this step is not possible here and stop; never
  reconstruct data from what was displayed.

## Expired links

Do what `fetch` prints. For a data tool's result, call `get_research_data_download` with the
`evidence_ref` it names (no allowance cost; nothing is re-run) and fetch that result with
`--tool get_research_data_download`; the new link's sha256 and bytes can differ from the old one. If
that call itself fails, call the original tool again with the same parameters and
`"delivery": "download"` (this re-runs it) and fetch that result right away. For
`get_research_data_job` or `read_research_data_artifacts`, call it again with
`"delivery": "download"`.

## Deferred jobs

A tool that answers with a job id is not finished: read it with `get_research_data_job` and `wait_s`
(up to 30) until it is terminal, and fetch the terminal result under `get_research_data_job`, never
one still queued. Never restart a queued job. If a job names artifacts, read them with
`read_research_data_artifacts`; artifact reads are fetched or staged, never placed as Envelopes.

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
