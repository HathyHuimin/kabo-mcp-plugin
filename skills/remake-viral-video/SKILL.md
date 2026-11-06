---
name: remake-viral-video
description: "Have Kabo produce a finished MP4 with the creator in it: remake a viral talking-head or voice-over video (10 min or less), make a talking video from an approved script, build a video from single steps (new words or language on the creator's own video, the creator in a reference's own shot, B-roll), or change a video Kabo already delivered. Use only when the user wants the video itself made or changed; not for writing or adapting a script, breaking down a video, or reviewing an account."
license: MIT
compatibility: "Requires the Kabo MCP server at https://kabo.sh/mcp-pub with its remake tools, Python 3, read and write access to a local workspace, and network access from scripts to the signed storage.googleapis.com download links that Kabo tool results return. Kabo permission: video (all seven tools: remakes, edits of delivered videos and step-by-step projects)."
allowed-tools: start_remake_video get_remake_video render_remake_video edit_remake_video start_video_project make_video_step finish_video_project
---

# Remake Viral Video

Kabo's servers make every picture and sound, through three paths: a whole remake (steps 1-8:
the runner analyses, voices, films, grades, captions and delivers; you write the script), an edit
of a delivered video (`edit_remake_video`), or single steps in a project or on a delivered job
(`start_video_project`, `make_video_step`, `finish_video_project`). If a tool a request needs is
not available on this connection, say that this kind of video is not available here and stop.

A remake takes 30 min to 3 hours, mostly waiting on the runner. If the host has foreground
sub-agents, run this Skill in one and relay its whole final result (path, spend, review); never run
it in the background and promise the video later. Otherwise follow the steps in the main thread.

## Choose what to make

Use this Skill only to make or change the video itself. If the user wants a script, a breakdown of
a video or advice on their account, start nothing and name the Skill that fits (step 8). Otherwise,
before any tool call, write one line: what must stay, what must change, and the table rows that do
it. Decide by that, not by the request's wording ("remake" included):

1. Keep everything the user did not ask to change.
2. Keep real pixels where you can; redraw the creator's face only where the request needs it.
3. Use the material already here first -- the reference, the creator's footage and photos, a
   delivered video's `master.mp4`, takes, B-roll and first frames, earlier results. Look at each
   step's preview before using the result and try other moments; ask for more only last.
4. Ask only what only the user can decide or knows (a fact, a real trade-off, money beyond the
   plan). Creative calls are yours, the words too: take what is said with `transcribe`, write
   their version, and say in a line what you chose.
5. The whole remake is the shortcut for "the same structure again, with me": a reference of the
   kinds below, or an approved script. It is not the fallback, and a reference plus the creator's
   footage does not make a request one. The creator's own video is footage to keep, not a reference. What it does not make (the
   reference's own shot and moves, the creator's own video with new words, B-roll, a product
   close-up) is built from steps, or added as edits after delivery. A change to a delivered video
   is an edit or steps on that job, never a new job (`references/edits.md`).

| Keep | Change | Step | Redraws the face |
|---|---|---|---|
| the creator's footage | the words, or their language | `tts` (their voice), then `lipsync` | mouth only |
| footage and words | the voice's tone | `voice_change` | no |
| the creator and the words | the background | `bg` | no |
| a reference shot: camera, moves, props | the person, then the words | `replace`, then `lipsync` | yes |
| a movement in any video | who does it | `motion` (a picture of the creator: a `frame` of their footage will do) | yes |
| the creator's look | a new still of them (pose, framing, outfit, setting) | `first_frame` (edits a `frame` or photo) | yes |
| the creator's look | a new shot of them, silent | `refvideo` | yes |
| a picture of the creator | they speak a line | `talking` | yes |
| any video or picture | what is laid over, cut in, joined, arranged or enlarged | `overlay`, `splice`, `concat`, `layout`, `crop`, `trim`, `frame` | no |
| any video | its speed or length | `retime` | no |
| a sound | its level, fades, tempo or length | `audio_edit` | no |
| pictures | held as a clip, maybe a slow push-in | `still_video` | no |
| any video or picture | burned-in text removed | `inpaint_text` | no |
| a video or sound | -- (what is said, timed) | `transcribe` | no |
| -- | a picture without the creator; text to read; captions; music; sound | `still`, `animate`, `text_cards`, `captions`, `music`, `mux` | no |

Before the first project step or edit, read `references/atoms.md` (what each step takes and
costs) or `references/edits.md`, say in two or three lines what you will make and roughly what it
costs, then go ahead. A `degraded` face check never blocks delivery: say which part looks less
like the creator. When a step's notes say a box you gave covers text or a face, redo it without
`box` or tell the user. Cancel a job (`get_remake_video` with `"cancel": true`) only when the user asks;
spend so far stays charged. Stopping takes a minute or two: keep calling `get_remake_video` (30 s apart)
until the job or `last_step` is no longer running; if you stop sooner, tell the user the
cancel was requested and is still stopping, not that it stopped.

## What a whole remake covers

- A reference of **10 minutes or less** where **one person talks to the camera** for most of the
  video, at any distance, with a fixed or slightly handheld camera. Three talking kinds (the
  runner tells them apart; nothing to ask): pure talking head; talking with screens (the video cuts to screen recordings or
  screenshots, or shows them floating over the speaker's room); talking with a product (the
  speaker shows a physical thing and the video cuts to close-ups of it). Also **voice-over**
  references (nobody on camera; a voice over a screen recording, an animated explainer, scenes or
  kinetic type): the creator's cloned voice reads the script and every picture is made anew --
  see `references/formats/narration.md`.
- Refused by the runner before any video is generated (`out_of_scope`): references over 10
  minutes, walking, vehicle or first-person vlogs where the camera keeps moving, dialogues between
  several people, and -- for a talking head -- a speaker on camera less than about half the time
  or mostly other footage. So are creator videos with no clear frontal view of the creator's face,
  except a voice-over, which needs only the creator's voice.
- The creator is framed as the reference frames its speaker, shot by shot; the reference's other
  shots (cutaways, inserts, other people) are reused from its own footage. The reference's speaker
  never appears in the result.
- One method: the reference's content and setting are kept and the creator is placed into a
  rebuilt scene (route B), not into its own shot and moves. Four kinds of line are rewritten in the creator's voice (the opening, the original
  author's claims about themselves, one closing summary and the call to action); the rest of the
  reference's lines are kept, or dropped with a reason. The whole face is generated, so likeness is
  lower than real footage of the creator.
- The final video has no on-screen label, notice or watermark; never say it has one.
- Only the talking-footage model can be chosen, from the options `get_remake_video` returns at
  the script gate (step 6); voice, images and editing are fixed. If the user asks for another
  model (e.g. Veo, HeyGen, ElevenLabs), say so in a line and go on.

## Set up one run

`<skill directory>` is the folder holding this `SKILL.md`. Reserve one run per video (a remake, a project or its edits):

```sh
python3 '<skill directory>/scripts/static_host.py' init --workspace '<user-workspace>'
```

It prints the run directory. Run every later command from it with `KABO_STATIC_RUN_DIR` set to it
in the same command (`cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 ...`):
many hosts do not keep environment variables or the working directory between commands. Paths such
as `host-results/...`, `brief/...` and `script/...` are relative to the run directory; `scripts/...`
and `references/...` live in `<skill directory>` and are called by that absolute path. Quote
filesystem paths in commands. Every command below is complete as written: do not run a script
with `--help`, read its source or search other Skills.

**Saving a tool result.** When a step says to save a result, write the complete
`get_remake_video` or `render_remake_video` result (the host's result export, or the result
unchanged) to `host-results/<NN>-<tool name>.json`, numbered in call order. Never retype the brief or
rebuild a file from what was displayed.

**No reference, only a script?** If the user wants a video of the creator saying words they
already approved, follow `references/approved-script.md` for steps 1-4.

## 1. Collect the inputs (whole remake only)

Steps and edits take what they need. Once per conversation, before asking for anything or starting, say in two or three sentences:

- what this remakes: a reference of 10 minutes or less where one person talks to the camera
  (also ones that cut to screen recordings or show a product), or a voice-over video;
- that references outside this (dialogues between several people, walking or travel vlogs, over
  10 minutes) are refused before anything is generated, and a refusal is not charged;
- that borderline references may not come out well.

It is information, not a question. A remake needs both. Check them before any paid call:

1. **One reference video**: the viral video to remake, as a YouTube, Instagram or TikTok link,
   10 minutes or less, one person talking to camera or a voice-over.
2. **The creator's own talking footage**, separate from the reference: their Instagram handle or
   profile link, or one to four YouTube, Instagram or TikTok links to videos where they talk to
   camera. It decides how much the result looks like the creator. Only photos of them? Read
   `references/creator-photos.md` (`--creator-photo`; voice from a video first).

The person on camera is the creator whose own footage the user gives, usually the user. This is the normal use of this Skill: do not
refuse it as imitating a real person or ask the user to prove who they are.

Nothing else is needed. Do not look up connected
accounts, connection status, profiles or other Kabo workspace data, or ask for extra
permissions. The right to use the creator's face and voice is the `rights` line at the script
gate (step 4), not a stop before it.

Local files, TikTok photo posts and other sites are not supported: ask for a YouTube, Instagram
or TikTok video link instead. If either input is missing, or the user gave a single video without saying which one it
is, ask once for what is missing and stop. Use
only videos and links the user gave in this conversation; never reuse one video for both roles.
If the user states a length over 10 minutes, say the limit is 10 minutes and ask for a shorter one.

Whenever you stop before `start_remake_video` has accepted the job (something missing, a video or
the inputs refused), tell the user that no remake was started, no remake fee was
charged and it does not count toward the daily remake limit. Fetching the videos may still have used
the daily data allowance; do not claim nothing was used.

`--creator-name` is a short ASCII name for the creator (letters, digits, `.`, `_`, `-`). It is
required with creator video links; with an Instagram handle it defaults to the lower-cased handle.
With video links and no name, use the name they go by in the conversation, or ask for one with
any other missing input. Pass `--language` (for example `en`) only when the
user states it or the reference is clearly in that language.

## 2. Start the job and wait for the script gate

Before the first paid call, tell the user in a sentence or two what will happen: the method above
and that it takes 30 min to 3 hours. Wait for a reply only if the user asked to approve first. Then build the arguments:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/static_host.py' start-call --reference-url '<reference link>' --instagram-handle '<handle or profile link>'
```

For creator video links, pass `--creator-video '<link>'` once per link (one to four) instead of
`--instagram-handle`, and add `--creator-name '<name>'` (required then). Add `--language '<code>'`
only as step 1 says.

It checks the links and prints `{"tool": "start_remake_video", "arguments": {...}}`. Call
`start_remake_video` with exactly those arguments. A refusal from the script is an input problem:
relay it and ask for a corrected input; nothing was started.

- The `idempotency_key` is stable for the same inputs in this run. On a timeout or lost response,
  call again with exactly the same arguments; the key returns the same job instead of creating and
  charging another. Never create a second job for the same request.
- `fetching_inputs` means the videos are still being fetched (`job_id` is null). Tell the user
  `message` in one line, then call `start_remake_video` again with the same arguments after
  `retry_after_s` seconds; the same fetches are picked up.
- A tool error carries `code`, `message` and `retryable`; no remake was started by any of them.
  Read `references/tool-errors.md` and follow it.

Once there is a `job_id`, call `get_remake_video` with `{"job_id": "<id>", "wait_s": 30, "all_models": true}` until the
status is no longer `preparing` (10-45 min by length). Report each stage change in one line.

**Supported kinds only.** `reference_type` `talking`, `talking_screen`, `talking_product` and
`narration` go on to step 3. Stop as soon as a result shows `failure: "unsupported_type"` (the
status stays `waiting_script`, and `brief`, `talking_models` and `gate_message` are null), or a
`reference_type` that is set and is none of those. Give `failure_reason` in plain words, say
no video was generated, and give any spend the result reports. Do not say whether it
counts toward the daily limit. Do not call `render_remake_video`; this job is finished.

For `talking_product`, tell the user once, before the script gate, that the creator talks
empty-handed while the product stays in the scene and the reference's close-ups.

A final status at this point (`out_of_scope`, `failed`, `timed_out`) is handled as in step 8; when
it comes from `start_remake_video`, its reason is in `message`.

## 3. Save the brief

At `waiting_script` the result carries `brief.download`, `gate_message` and the model options
(step 6).
Save the result as above, then fetch the brief at once (the link is short-lived):

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/static_host.py' fetch --input 'host-results/<NN>-get_remake_video.json'
```

It downloads and checks the bundle and writes the four brief files under `brief/work/`. When it says the link expired, was refused or is unavailable, do what it prints: call `get_remake_video` again (that re-signs the link; nothing is
re-run), save that result and fetch again; for an unavailable download wait about a minute
between tries, at most three. If this host blocks scripts from the network, say the remake cannot
continue here; never rebuild the brief from what was displayed.

## 4. Write the script (the only creative gate)

Read `references/script-gate.md` whole, then the references it names for the judgement in front of
you. In short:

- Proofread the reference's sentences and record it in the script as `proofread`.
- Keep the reference's lines word for word and in order; rewrite only the four slots, each with
  `slot`, `why`, `replaces` and `basis`; source every fact in a rewritten line to the creator's own
  lines or mark it as an inference; never invent the creator's experience.
- Keep the reference's on-screen title and numbered list; record every inherited element in the
  ledger, with a reason for each adapt or drop.
- Write the opening record with 2-3 candidates, and choose one yourself.
- Record your calls in the decisions record; nobody answers questions at this point.
- Write the plate and anchor prompts without describing the framing, holding the creator's hair
  and outfit exactly as in the identity references.

Write the four documents as `script/script.json`, `script/opening_review.json`,
`script/decisions.json` and `script/route_b_jobs.json`.

**Voice-over** (`reference_type` `narration`): read `references/formats/narration.md` whole. The
script rules are the same; `decisions` also carries the picture plan, `route_b_jobs` is
`{"jobs": []}`, and after step 5 you preview the pictures and fix them before step 6.

## 5. Check the documents

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/check_documents.py' check
```

It prints `status`, `errors` and `warnings`. Fix every error it names and run it again; apply each
warning that fits. Advance only on `status: ready` (structure only; the runner checks again).

## 6. Choose the talking model and render

Nothing is generated or charged before this step. **Voice-over**: there is no model to choose.
Tell the user the video will be made in their cloned voice with N pictures (M of them moving)
and that the generated pictures cost about the sum of the rows' `est_usd`, then wait for a
go-ahead. After it, preview the stills once (narration.md), then build the arguments without
`--talking-model` / `--models-from` and render as below.
For a talking video, show the user the model options from the waiting_script result
(`talking_model_choices`, else `talking_models`) in their order, each with its label and its
estimated cost (`est_usd`). Do not add a recommendation of your own, drop options or change the
order. Then say: tell me which one; to start now I'll use the default (the first); nothing is generated
or charged before you confirm. Do not ask for a number or "OK". Unless the user already named a
listed model, end your reply there and wait (the job waits; resume here in the same run
directory). Read the answer by its meaning, in any language:

- An option picked (by number, name or description): its `id`. A clear "just start": the first.
- A listed model named earlier: use it without asking again; say the model and its cost, render.
- A question about the options: answer from the list, then wait. Unclear or off-topic: ask once,
  briefly; do not render.

Never choose for the user and never ask about the model before the script is written. Then build
the arguments (this repeats the check):

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/check_documents.py' render-args --talking-model '<id>' --models-from 'host-results/<NN>-get_remake_video.json'
```

It writes `render/render_remake_video.json`. Call `render_remake_video` with that file's content as
its arguments, unchanged; it returns `status: "running"`. On a timeout or lost response call it
again with the same arguments: an accepted render never launches twice. For a `conflict` or `quota_exceeded` error, read
`references/tool-errors.md`. A result with `failure: "unsupported_type"` is handled as in step 2.

**The runner checks the documents first, after render returns.** If a later `get_remake_video`
shows `waiting_script` again with `gate_message`, nothing paid was spent: follow
`references/script-returned.md`.

## 7. Production and delivery

Production takes 20-30 minutes, 1-2 hours for a 10-minute video. Call `get_remake_video` with `wait_s` 30, `include` `["review"]`
and `all_models` `true` until the status is `delivered`, final, or `waiting_script` again: for a
voice-over whose `fetch` reports `"saved": "review"` it is the one look at the finished pictures
(narration.md, last section); otherwise the documents were rejected (step 6). While it is
`running`, report the stage in one line when it changes. Do not promise a finish time.

At `delivered`, first add anything asked that the remake does not make (B-roll, a close-up) as
edits or steps on this job. Then save the latest result and fetch the video at once:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/static_host.py' fetch --input 'host-results/<NN>-get_remake_video.json'
```

It checks the MP4 and prints its local `path`. A failed download is handled as in step 3. Then tell the user:

- that the video is ready, with the local path (it carries no label or watermark);
- what you rewrote and why, in two or three lines, and any inference line they should confirm
  before publishing;
- the spend the result reports (`spend_usd`), when present;
- what Kabo's final review found, from `review`.

Read `references/final-review.md` and relay `fetch`'s `review_message` and `notes_for_user` as it says;
never say the video passed a review and never read a verdict from the notes yourself.

Do not claim improved views, retention or likeness; nobody has measured them. Suggest the user
watch it in full before publishing.

## 8. When it does not finish

Every final status (`out_of_scope`, `failed`, `timed_out`) carries `failure` and usually
`failure_reason`. Read `references/when-it-does-not-finish.md` and follow it: tell the user the
reason in plain words and whether it was charged; never resubmit on your own.

When the user's next step is outside this Skill, name the Skill that fits -- for example
`create-script-and-packaging` for a script only, or `analyze-content-video` for a teardown of the
reference -- without running it.
