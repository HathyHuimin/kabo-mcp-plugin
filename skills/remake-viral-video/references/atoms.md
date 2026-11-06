# Projects: building a video step by step

Which path and which steps follow "Choose what to make" in `SKILL.md`: keep what the user did
not ask to change, keep real pixels, use the material already here, and keep the whole remake for
"the same structure again". A project is the same kinds of steps one at a time, for whatever the
whole remake does not make. Kabo runs every
step on its servers; you decide what to make, in what order and with which words, and call the
tools. Never draw, record or assemble a picture yourself.

Tools: `start_video_project`, `make_video_step`, `finish_video_project`, and `get_remake_video`
with `"project": true` to read the project (its `handles`, `last_step`, `revisions` and status).

## When to build a project

For a new video the whole remake does not make, for example (examples, not a routing table):

- the creator doing the moves of a video (a dance, a workout, a trend);
- the creator doing something without speaking, under long text cards and music;
- a short product ad from product pictures and the creator's footage;
- the creator's own footage placed somewhere else, with new words, or in another language;
- a reference's own shot and moves kept, with the creator in it;
- a reference the remake refused because nobody talks to the camera in it (below);
- a short clip without the creator in it (a scene, an animal, an object), from no-face steps only;
- no material of the user at all but someone should present: an invented AI presenter (`persona`);
- the creator's footage has no speech: their lines are read by a stock voice (below).

A change to a video that was already delivered is an edit or steps on that job
(`references/edits.md`), not a project.

## What goes into a project

`start_video_project` takes, all optional:

- the creator's own videos (an Instagram handle, or one to four video links) and/or one to eight
  public photo links of the creator (with `--creator-name`); they become `creator/...` handles and
  are what every face step is checked against;
- one to four links of other videos (`--reference-video`: a movement to copy, a shot to recast, a
  reference to reuse), which become `input/ref_NN.mp4`;
- one to eight public picture links that are not the creator (`--reference-image`: a product, a
  place), which become `input/img_NN.<ext>`.

Video links are YouTube, Instagram or TikTok; picture links are public JPEG or PNG. Files on the
user's computer cannot be sent in this version. More photos, reference videos or pictures can be
added to a ready project later (`--job-id`, below).

**Without the creator.** A project with no creator footage or photos can use only the steps that
draw no face of the creator (`still`, `animate`, `text_cards`, `music`, `tts` with a stock voice,
and the editing steps); a face step is refused with `no_creator`. Never present a generated person
as the creator or as any real person.

**An invented AI presenter (`persona`).** Only when the user has no footage or photos of themselves
or of the creator at all and wants someone on screen: offer an invented presenter and, once they
agree, make one `persona` step first:

```json
{"op": "persona", "inputs": {}, "params": {"description": "an invented woman in her thirties, short dark hair, warm smile, casual denim shirt", "count": 3}}
```

It makes two to four portrait pictures of the same invented person (`creator/persona_NN.png`);
after that the face steps work as usual and are checked against those pictures, and the voice is a
stock voice the user picks (`tts` `voice`, below). `count` is two to four portraits (optional);
`inputs.seed_images` may add up to four style pictures (handles of pictures that show no real
person, never `creator/...`). The project then reports `persona: true`.

- The persona is always fictional. Never describe, name or imitate a real, identifiable person (a
  celebrity, a public figure, someone the user names, or the user); refuse such a request and offer
  an invented person instead. No handles or links in the description.
- Never mix: a project with the creator's real footage or photos cannot get a persona, and a
  persona project cannot take creator footage later. When real footage exists, use it instead.
- At delivery, say plainly that the person on screen is an AI-generated fictional presenter.

**No speech in the creator's footage.** `tts` reads lines in the creator's cloned voice by default;
with nothing to clone it is refused with `no_voice`. First ask for a link to a video of the
creator speaking (10-30 seconds is enough) and add it to the project. Only if they have none,
offer the stock voices and let the user pick; never pick one for them:

- `933563129e564b19a115bedd57b7406a`: female, young, soft and conversational
- `b545c585f631496c914815291da4e893`: female, young, bright
- `536d3a5e000945adb7038665781a4aca`: male, calm and professional
- `802e3bc2b27e49c2995d23ef70e6ac89`: male, young and energetic

Pass the pick as `params.voice` of `tts`, and say at delivery that a stock voice was used.

## What each step takes

| The request | Step (`op`) | Why |
|---|---|---|
| The creator's own footage saying other words | `tts` for the new line, then `lipsync` (video + that audio) | real pixels, new mouth |
| The creator's own footage somewhere else | `still` for the plate (no person), then `bg` (video + plate) | real pixels, new background |
| The creator doing the moves of a video | `motion` (a picture of the creator + the movement video) | the movement comes from the video, the person from the picture (a `frame` of their footage will do); likeness is modest. The default draws full-body motion; `fal-ai/kling-video/v3/pro/motion-control` keeps the face better but keeps the picture's framing, so use it only with a standing full-body picture |
| A new shot of the creator doing something without speaking | `refvideo` (1-10 pictures + what happens in words; optional `ref_video` to follow a motion) | the model draws the shot from the pictures; it is checked against the creator. Up to 15 s; up to 30 s with `model` `kling/v4/reference-to-video` |
| A new still of the creator: another pose, framing, outfit or setting | `first_frame` (`image`: a `frame`, a photo or `frame_sNN.png`; optional `refs` 0-3; `prompt`) | edits the picture; redraws the face and is checked against the creator |
| A still of the creator speaking a line | `tts`, then `talking` (picture + line audio; optional `prompt` for the performance) | a talking take from one picture |
| A reference's own shot and moves with the creator in it | `replace` (shot + picture of the creator), then `lipsync` with the creator's words from `tts` | keeps the camera, props and gestures; a whole remake instead recreates the scene. Small moving text can garble |
| B-roll, a scene, a product shot (no creator) | `still` (product pictures as `refs`), then `animate` to move it | no face is drawn |
| Words that must be read on screen | `text_cards` on a base video | real text; never ask a generated picture to contain words |
| Background music | `music` with a mood | royalty-free; if it says it is not available, leave music out and say so |
| Captions in the creator's style | `captions` (optional `max_words` 1-8 per caption, `upper` for capitals) | |
| Part of a video or audio | `trim` (`t0`, `t1`) | |
| Another sound track on a video | `mux` (`mode` `replace` or `mix`), `extract_audio` to take a video's sound | |
| What a video or sound says | `transcribe` (`media`, optional `language`) | timed segments, no file; free |
| The same words in a different, non-creator voice (for example a reference speaker's track that must not keep their voice) | `voice_change` with `voice: "warm_female"` (the only stock voice it has) | it cannot make the creator's voice: for that, `tts` with the creator's voice, then `lipsync` or `mux` |
| A picture over a range, a clip spliced in, clips joined | `overlay`, `splice`, `concat` | |
| A still from a video (to use as a picture, or to look at a moment) | `frame` (`t` in seconds, or `at` `first` / `last`; any video handle) | cheap; also how you see a video |
| Pictures held as a clip | `still_video` (`images` 1-8, `duration` in total, optional `zoom` 1-1.5 for a slow push-in, `fit` `cover` / `contain`) | tool step: silent 1080x1920 |
| A sound's level, fades, tempo or length | `audio_edit` (`media`; `gain_db` or `normalize`, not both; `fade_in`, `fade_out`, `speed` 0.5-2 with pitch kept, `duration` to cut or pad) | tool step |
| Someone on screen when the user has no footage or photos of anyone real | `persona` first (above), then the face steps | an invented presenter; never a real person |
| A video or part of it faster, slower, looped, played back and forth, or held on its last frame | `retime` | tool step (below) |
| One region of a video or picture, at a chosen output size | `crop` | tool step (below) |
| Text burned into a video or picture taken out | `inpaint_text` | tool step (below) |
| Several pictures or videos placed in boxes on one frame, each for its own time range | `layout` | tool step (below) |

**Tool steps** (`retime`, `crop`, `inpaint_text`, `layout`, `still_video`, `audio_edit`) run
without a model, free, and draw no face; combine steps for a scene or ad. Boxes are `[x, y, width, height]` as fractions of the
frame (0-1, inside the frame).

- `retime` (`inputs.video`): `mode` `speed` takes exactly one of `speed` (0.25-4) or `duration`
  (the new length in seconds); `loop`, `bounce` (the part at most 10 s) and `hold` take `duration`
  only. `t0`/`t1` change just that part and keep the rest; `audio` `keep` (pitch kept when sped up)
  or `none`. Time added by `bounce` or `hold` is silent.
- `crop` (`inputs.media`, a video or a picture): `box`, and `size` `[width, height]` in pixels
  (16-4096; the source size when left out). The box is widened around its centre to the output's
  shape, never stretched; the result notes the box actually used and warns when it enlarges more
  than three times.
- `inpaint_text` (`inputs.media`): `boxes` (1-8) around the text, or left out to find the text
  automatically (refused as `unsupported` where that is not available: then give `boxes`);
  `fill` `strokes` (the default: only the letters and their outline) or `box` (the whole box);
  `t0`/`t1` to limit it in time.
- `layout`: `inputs.layers` (1-8 handles, later ones on top) and optionally `inputs.base`;
  `params.layers` has one entry per layer, in the same order: `box` (where it goes), and optionally
  `src_box` (the part of the layer to use), `fit` `contain`/`cover`/`stretch`, `t0`/`t1`, `opacity`
  0-1, `key` `light`/`dark` (keep only the layer's near-white or near-black pixels, for example
  text strokes) with `grow` 0-0.05 to widen them. Without a base, `canvas` sets `width`, `height`,
  `color` (#RRGGBB) and `duration` (default 1080x1920 black). The length is the base's, else
  `canvas.duration`, else the latest layer end; `audio` `base` or `none`.

Two settings on other steps: `lipsync` takes `sync_mode` (`cut_off` by default, `loop`, `bounce`,
`silence` or `remap`) for when the audio and the video differ in length, only with the default
sync-lipsync model (`loop`, `bounce` and `silence` are charged on the longer of the two); `tts`
takes `speed` 0.5-2 for the voice's own pace (1 is normal).

Pictures of the creator come from their photos or from their videos: choose a `frame` yourself
where the face is clear and frontal (for `motion`, the whole body when there is such a moment;
otherwise use what there is and say so); look at it before using it. Words are yours too: take
what is said with `transcribe` and write their version. Never use the
creator's own footage as the movement source when the user asked for someone else's moves: add that
video as a reference video.

## A reference nobody talks in

When a remake was refused because nobody talks to the camera in the reference, and the user agrees
to a project: start one with the creator's footage and the reference as `--reference-video`. Reuse
the reference's own footage with `trim` (and `concat`) for the parts without a speaker; make the
creator's parts with `tts` + `talking` from a frame of the creator, or `refvideo`; put them back
with `splice` (a full-screen segment) or `overlay` (the creator in a corner); add `captions` last.
Keep the reference's on-screen text where it is; never put the reference's people forward as the
creator.

## Rules for the face

- `refvideo`, `motion`, `replace`, `lipsync`, `talking` and `first_frame` redraw the creator's face. Kabo checks
  each one against the creator's own footage and reports `face.verdict` `pass` or `degraded`.
- Redraw the face as few times as possible: every pass moves it further from the creator. Prefer
  steps that keep real pixels (`lipsync` on real footage over `talking` from a still; `bg` over
  drawing the creator into a new scene). Chain two face steps only when the chain is the point
  (`replace` then `lipsync`), and look at the result after each.
- A provider refusal is retried once on Kabo's side with another tested model, named in
  `fallback_from`. Mention it when you report the step.
- A `degraded` result is kept, never a reason to stop. You may try the step once more with another
  listed model (a `model` param or `--attempt 2`); keep the better take and say what fell short.
- When a step's notes say a box you gave covers on-screen text or a face, redo it without `box`
  so it is placed automatically, or tell the user; never ignore it.
- Never put anyone else in the creator's place, and never the speaker of a reference video.
  `still` and `animate` must not show the creator (that would be a face step without the check).

## Plan before spending

Before the first paid step, write `project/plan.json` in the run directory and tell the user in two
or three lines what you will make, which steps, and roughly what it costs, then go ahead.

```json
{"goal": "one line: what the user asked for",
 "steps": [{"op": "frame", "why": "a clear picture of the creator", "est_usd": 0},
           {"op": "motion", "why": "the creator does the moves", "est_usd": 4.0}],
 "est_usd_total": 4.0}
```

Rough provider prices per second of output, for the estimate only (the result's `spend_usd` is
what is charged): `motion` about $0.17-0.40, `refvideo` about $0.20-0.24, `replace` about $0.30,
`lipsync` about $0.08 (or about $0.20 per call up to 40 s with `fal-ai/latentsync`), `talking` from
under $0.01 to about $0.30 depending on the model. `still` is a few cents per picture and `persona` about $0.15 per portrait; `animate`
and `voice_change` are cents per second; `bg` about $0.02 per second of the input video (30 s is
about $0.70); `tts`, `frame`, `trim`, `mux`, `extract_audio`,
`overlay`, `splice`, `concat`, `text_cards`, `music`, `captions` and `face_check` cost little or
nothing; `first_frame` about $0.15 a picture; `retime`, `crop`, `inpaint_text`, `layout`, `still_video` and `audio_edit` cost nothing.

## Run it

Use one run directory as in `SKILL.md` ("Set up one run"). Every command below starts with
`cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/static_host.py'`,
written here as `static_host.py`.

1. **Start the project**: `static_host.py project-call` with any of `--instagram-handle '<handle>'`
   or `--creator-video '<link>'` (one to four), `--creator-photo '<link>'`, `--creator-name '<name>'`
   (required with creator videos or photos), `--reference-video '<link>'`, `--reference-image
   '<link>'`. Call `start_video_project` with exactly the printed arguments. `fetching_inputs` is
   handled as in `SKILL.md` step 2. Then call `get_remake_video` with `{"job_id": "<id>", "wait_s":
   30, "project": true}` until `project.status` is `ready`; `project.handles` lists the files.
   Always read a project's state from `project` (`project.status` can also be `working`,
   `delivered`, `failed` or `cancelled`); the top-level `status` keeps a remake's own values.
   To add files to a ready project later: `static_host.py project-call --job-id '<id>'` with
   `--creator-photo`, `--reference-video` or `--reference-image`, and call `start_video_project`.
   A delivered remake's `job_id` can be used as a project directly; its handles include
   `final.mp4`, `master.mp4` (the current cut without captions), `take_sNN.mp4`, `frame_sNN.png`
   (each take's first frame), `broll_bNN.mp4` / `.png`, `line_NN.wav` and `input/reference.mp4` --
   look at these before asking the user for material.
2. **One step at a time.** Write the step as `project/steps/<NN>-<op>.json`:

   ```json
   {"op": "frame", "inputs": {"video": "creator/creator-1.mp4"}, "params": {"t": 2.5}}
   ```

   Then `static_host.py step-call --job-id '<id>' --step 'project/steps/<NN>-<op>.json'`. It checks
   the step against the op's inputs and params and prints the `make_video_step` arguments; call the
   tool with exactly those. A refusal from the script is a mistake in the step file: fix it. Inputs
   are handles from `project.handles` only, never paths or links. One step runs at a time.
3. **Wait and look.** The step returns `working`. Call `get_remake_video` with `wait_s` 30 and
   `project` true until `project.last_step.status` is `done` or `failed`. Save the result to
   `host-results/<NN>-get_remake_video.json` and run
   `static_host.py fetch-step --input 'host-results/<NN>-get_remake_video.json'`. It saves the
   step's file under `project/outputs/` and prints its `path`, handle, spend, model, fallback and
   face verdict, plus `preview_path` (a picture, or four frames of a video) or, for `transcribe`,
   the `transcript`. Open the preview with the host's image viewer before using the result.
4. **Same step again.** The key is stable for the same step file: a retry after a timeout returns
   the same result and is not charged twice. For a new take of the same step on purpose, add
   `--attempt 2` (then 3, ...). A `conflict` error means the key was used with other arguments: use
   the next `--attempt`.
5. **Finish.** `static_host.py finish-call --job-id '<id>' --handle '<aNN.mp4>'`, call
   `finish_video_project` with the printed arguments, poll `get_remake_video` with `project` true
   until `last_step` is done and a revision is listed, save the result and run
   `static_host.py fetch-revision --input 'host-results/<NN>-get_remake_video.json'` for the
   local path.

## Errors

Errors from `start_video_project` are read as in `references/tool-errors.md`.

- `validation_error`: read `message`. `workspace_expired`: the job's files are gone (kept 14 days);
  offer a new project. `no_creator`: a face step in a project without the creator. `no_voice`:
  nothing to clone; ask for a recording, then offer a stock voice (above). A `persona` refused
  because the project has creator material: use that material instead.
- `quota_exceeded` whose `message` says another remake or step is still running: not a limit;
  wait for it (`get_remake_video`), then call again with the same key.
- Any other `quota_exceeded`: a daily limit or the spending safety stop; nothing was generated. Tell the
  user; do not split the step to get around it.
- A step that ends `failed`: relay `message` in plain words and the spend, if any; try another
  listed model once if it was a refusal or a provider failure, otherwise stop and ask the user.
- Wait at least half a minute between status calls; never poll in a tight loop.

## Delivering

Tell the user: the local path; what was made, step by step, in two or three lines; the total spend
(`project.spend_usd`); each face verdict, and in plain words which part looks less like the
creator when a step was `degraded`; any fallback model or stock voice used; for a persona, that
the presenter is an AI-generated fictional person. A degraded face check
never blocks delivery. Do not claim views, retention or likeness that nobody measured, and suggest
the user watch it in full before publishing.
