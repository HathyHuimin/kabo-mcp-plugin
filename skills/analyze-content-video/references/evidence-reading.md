# Reading the evidence

Shared by both modes. Read the section a step points you to; read *When to stop waiting* before
the first data call of a run.

## Frame sampling

The request asks for at most 60 frames with `"sampling": "duration_scaled"`: twice a second through
the opening three seconds, then one a second, spread out evenly once a longer video would exceed the
cap (`job-call` adds `max_frames`). That plan already samples the opening densely, so never pass
`fps`: it applies only to `evenly_spaced`, is capped at 2, and a higher value is rejected as
invalid input.
`sampling_plan` records requested sampling, not verified decoded timestamps or viewing coverage; even `t_seconds` may be a nominal requested point. Without verified actual decode
records, never state a planned interval as actual spacing in judgment or creator cards. Describe it
only as a planned/requested sampling limitation. Judge the hook over `frames.sampling_plan.hook_window_s`
rather than a fixed three seconds — a Short lives or dies in about three, long-form has fifteen to
thirty. When `body_degraded` is true, name the planned `body_interval_s` as a coverage limitation:
shorter cuts may be missing. Before a change depends on an element being absent, inspect the
available evidence for the relevant span. Sampled frames are not full-video or viewing coverage;
an unseen span remains unconfirmed. Denser requests add potential evidence, not a walkthrough.

On some videos no frame can be retrieved at all, and the evidence instead carries timestamped
descriptions of what a model saw. `frames.origin` says which you have. When it reads
`model_observation`, the pixels were read by a model on the supplier's side rather than fetched
here — **and on YouTube that is the ordinary path, not a rare fallback**, so a rule written as if
it were rare would fire on most videos.

What that origin costs is narrow. `on_screen_text` on such an observation is text that was *read
off the screen*, so **quote it**. What you cannot do is take timing or rhythm out of prose:
descriptions arrive per observation, not per cut, so counting cuts or measuring a shot's length
from them is inventing a measurement. Attribute the observation as reported rather than as
something this run looked at, and say so once — not on every line. They are still evidence — they
carry a second and they can be cited — but a claim about typography, framing or a specific frame
needs a frame.

**Reading the frame bytes.** A completed keyframe job lists `frames[]` / `artifacts[]` refs. Select
the exact refs you need and read them with `read_research_data_artifacts` (`job_id` plus those
`object_refs`, with `"delivery": "download"`), save that small result and run `static_host.py fetch`
on it (it is fetched, not persisted). Then open each image's `path` from fetch's output with the
host's own file or image viewer: a file on disk is not yet seen. Only for items fetch lists as
unavailable, or when scripts cannot reach the network, read those refs again without `delivery`
(the images then arrive inline) and stage that result from the host's result export; never rebuild
it from the host's own session folders. A frame list or a queued receipt is not viewing. Reuse that job,
never a new extraction; bind each image's meaning to its `frame_id`, object ref, SHA and nominal
`t_seconds`, never to arrival order. If the bytes cannot be read, keep honest partial coverage;
never invent a path or refetch.

## Transcript

`transcript.cues[]` carries the spoken track with a start second on every cue; on providers that
return no timing it arrives as `transcript.plain_text` instead. Read it.

The retrieved envelope carries each cue's start in **milliseconds**; the evidence object carries
`start_s` in **seconds**. `scripts/build_content_evidence.py` makes that conversion, sets
`granularity: timestamped` and `timestamps_available: true` whenever any cue carries a start, and
uses `plain_text` only for a path that returned no timing at all. Do not fill or correct these
fields by hand: a start pasted across unchanged is off by a factor of a thousand. A video can carry
a full argument in speech and show none of it on screen — the frames of a video with no burned-in
captions are silent. Quote a cue as the evidence for a hook, a promise or a claim the way a frame is
quoted for what was visible, and cite it as `transcript.cue[i]`.

When `transcript.status` is `available` but no cue text and no plain text reached the evidence
object, treat the transcript as unavailable and say so beside the claims it weakens. A segment
count is not a transcript.

When the platform published no captions, `public_video_fetch_transcript` falls back to machine
transcription inside the same call. There is no second tool to reach for: one call is the whole
path. **A file on this machine has no platform captions to fetch**; where its words come from is
under *Getting the words* in `references/local-file.md`.

`transcript.origin` says which arrived — `platform_captions` for the file published with the
video, `provider_transcript` for one the retrieval path supplied, `speech_recognition` for words a
machine heard after the fact. Machine-heard words carry a word error rate: quote them as what the
analysis heard, never as the creator's exact wording, and confirm any proper noun, price or figure
against a frame before repeating it. A video with no burned-in captions whose transcript never
arrived is a video whose argument was never read — say that.

**Exporting the transcript file.** `scripts/export_transcript.py` takes two positional arguments,
the evidence file and the output directory, plus optional `--stem`, `--formats` and `--summary`. It
writes a `.txt` from whichever track arrived, and a `.srt` only where the evidence carries cue
timing. Ends come from the following cue; only the final cue's end is derived, and the summary says
from what. Where the path returned no timing it writes no `.srt` and says so: a subtitle file with
invented timings desynchronises silently. Confirm its actual `written` / `refused` summary, name the
written files in the delivery, and report a refused format as a missing piece. A short caption
excerpt is not a complete export.

## Where the metadata comes from

**On YouTube, take `content_metadata` from `youtube_get_video`, not from
`public_video_collect_public_metadata`.** The media tool reads a video page from a datacentre
address, and YouTube refuses those: it answers `service_unavailable` on every YouTube URL, and a run
that used it lost title, tags, cover and publish date. `youtube_get_video` is the platform answering
about its own video; it is not a deferred job and carries the same facts plus public counts. Call it
with `{"parameters": {"video_id": "<resolved-video-id>"}, "delivery": "download"}`. The media tools
`public_video_fetch_transcript`, `public_video_extract_keyframes` and
`public_video_measure_media_signals` take `{"parameters": {"url": "<original-video-url>"}}`
(keyframes add `"sampling": "duration_scaled"` and no `fps`), built into full arguments (with
`delivery` and `idempotency_key`, and `max_frames` for keyframes) by
`static_host.py job-call` as `SKILL.md` says. Readiness alone does not
supply parameter names; use each tool's input schema.

On **Instagram and TikTok** `public_video_collect_public_metadata` stays the metadata call.

Persist the YouTube response like any other, naming its own connector and operation:
`--connector youtube-public --operation get_video`. The builder reads its fields into the same shape.

⚠ **Do not issue both.** Two metadata envelopes for one video are two readings of the same facts,
and the builder picks one — which one is then invisible in the report.

## Cover and tags

`content_metadata` carries what the platform itself declares: `tags` in the creator's own order,
and `thumbnails` by tier, largest first. This is the only part that decides whether the click
happens at all, so a diagnosis of "why did this get no views" that never mentions the cover has
skipped the top of the funnel.

Read `status` before reading the arrays. `available` with `tags: []` is a finding — this creator
published none. `unavailable` means the fetch did not happen, and the two arrive looking
identical. Never report the second as the first.

`thumbnails[0]` is the highest tier. The ordering is the connector's: do not re-sort and do not
re-derive "highest" from the dimensions.

When the fetch did not happen, say so in a limitation that names `content_metadata` by that name.
The gate looks for the field, not for the words "cover" or "tags".

A thumbnail URL is a reference, not an image. No tool here returns cover pixels, so this run has
not seen it: cite that a cover exists, hand the creator the file, and say what the tags declare —
but never describe what the cover *looks like*, and never judge whether it works.

## When to stop waiting

**A call that cannot succeed is not a call to make.** The `public_video_*` tools run server-side;
**a file on the creator's machine is not reachable by any of them** — not as a `file://` URI, not
by copying it into the run directory. When `source.acquisition` is `local_file`, call no
`public_video_*` tool at all and follow `references/local-file.md`. `build_content_evidence.py`
refuses the mixture too.

A retrieval path that has not answered is not evidence that arrives later. Give each tool one
retry (for a `public_video_*` tool, a retry after an error result is a new request: build it with
the next `job-call --attempt`; any other tool is simply called again), and treat a call that comes back with neither an envelope nor a job id as failed at once.
Such a result is also what `static_host.py fetch` and `stage` refuse ("MCP error contains no terminal
connector evidence"): record it with `--limitation` on the evidence-building command and carry on.
The `public_video_*` tools are deferred: a healthy call answers with a job id, which you read with
`get_research_data_job`. Pass `wait_s` (an integer from 1 to 30; 0 is rejected) so the server
waits for you instead of a polling turn; it replaces a turn, not the job's own runtime, and a job can still come back unfinished.
Poll again until it is terminal, a retry has failed, or the approved budget for this request is
spent; never restart a queued job. On a real run two calls sat idle for 936 and 681 seconds after
the same tools had already failed five times; that run spent 27 of its 48 minutes on a dependency
that was already dead. Record each attempt and its failure verbatim, then continue under
`failure_policy`.

Make the run's independent calls in the same turn — they take no input from each other.

A job that answered with a job id is healthy, the opposite case from the dead calls above: keep
polling it inside this same turn with `get_research_data_job` and `wait_s`, until about three minutes
after it was submitted or until the turn has only enough time left to build and deliver. Where the
host scopes a job id to the turn that submitted it, ending the turn to wait throws the job away and
the next turn pays for the same analysis again; where the host keeps jobs across turns, a later turn
polls the same id instead of submitting again.

When a valid job id exists but the waiting window ends before a terminal result, pass it to
`build_content_evidence.py` as `--pending <operation>=<job id>` (recorded `pending`), or as
`--deferred-incomplete <operation>=<job id>` when this run is closing (recorded
`deferred_incomplete`), rather than recording `failed`. These carry no media evidence and keep the
affected stages unassessed.

When the window closes with frames still outstanding, deliver; a reply that only says the analysis
is still processing is not a delivery. With no keyframe envelope the builder records `frames.status` as `not_attempted`; list the visual dimensions in
`unavailable_dimensions[]`, and say in one sentence beside the affected claim that this reading
rests on speech and metadata alone. A report that arrives without the frame track beats a report
that never arrives.

## When part of the media is out of reach

- **No measurement.** When `public_video_measure_media_signals` is missing or says the media could
  not be retrieved, and no keyframe result carries `signals`, say the cuts were not measured because
  the platform refused the download, never that the video cannot be analysed. Place each visible
  change between the two frames that bracket it ("between 1:03 and 1:05"), read the on-screen text
  of every frame with its second, and mark the spans between frames unconfirmed.
- **No words on a long video.** For a published video, a quick transcript lookup that returns none
  is not the final answer: call `public_video_fetch_transcript`, which falls back to machine
  transcription inside the same call, before saying the words were not read. A local file never
  goes to that tool; its words come from the tiers in `references/local-file.md`. When a
  whole-video reading is capped by length but accepts segments, read the opening window and the
  spans the question names, deliver, and only then ask for timestamps.
- **Photo and carousel posts.** Metadata naming a photo or slideshow post, or a video reading that
  answers `source_not_video`, makes it a still-image post: make no further video call. Read its
  slides where the post images can be fetched, quote the caption as the platform's text, and ask the
  creator to paste text only when both came back empty. After a share link resolves, use the
  canonical URL it returned for every later call.
- **Edit requests.** This Skill diagnoses; it does not edit, mix or render video. Asked to edit, add
  sound or export, say so in one sentence and deliver the timed edit plan in its place.
