# Changing a delivered video

`edit_remake_video` changes a video Kabo already delivered through this connection (a remake, an
approved-script video, or a finished project). Kabo redoes only the parts named and saves the
result as a new revision of the same job; earlier revisions stay downloadable. Change the smallest
part that answers the request, never remake the whole video for a local change, and never start a
new remake or project for it.

Tools: `edit_remake_video`, `get_remake_video` (with `"project": true` for the job's handles,
lines and revisions), and `make_video_step` when an edit needs a new picture or clip first.

## What maps to what

| The user asks | Edit (`kind`) |
|---|---|
| Different words in one or more lines (a remake or an approved-script video; a project has no script) | `lines`: `[{"row": <n>, "text": "<new words>"}]`. `row` is the line's index (from 0) in `project.lines` (the same as the run's `script/script.json` `lines`). Kabo re-voices those lines in the creator's voice, re-syncs only the takes whose words changed, and re-times the captions. |
| Caption colour or size | `captions`: `color` (`#RRGGBB`, or a plain name such as `white`, `yellow` or `red`, which `edit-call` turns into a fixed colour) and/or `size` (a multiple of the current size, 0.5-2) |
| Another background for a time range | first a `still` step for the plate (a place, no person), then `background`: `t0`, `t1`, `plate` |
| A picture, screen or product shot over a time range | `overlay`: `t0`, `t1`, `media`, optional `box` `[x, y, width]` as fractions of the frame |
| A new shot in place of a time range (B-roll, a reaction) | make it first (`still` + `animate`, or `refvideo`), then `clip`: `t0`, `t1`, `clip`; the original sound under that range is kept |

Times are seconds from the start of the delivered video. Up to ten edits go in one call; line
changes are applied first, the rest in order. Words that must be read on screen go in an overlay
made with `text_cards`, never into a generated picture.

**Where new pictures and clips come from.** Call `make_video_step` on the delivered job's own
`job_id` (see `references/atoms.md`, "Run it", steps 2-3) and use the handle it returns. The
creator's videos are handles too (`creator/...`). This version cannot take photos, screen
recordings or other files from the user's computer. A public picture link (a product, a place) can be
added to the job first with `project-call --job-id` and `--reference-image` (`references/atoms.md`,
"Run it"). Look first at what the job already has: a `frame` or `crop` of `master.mp4`, its takes,
B-roll and first frames, the creator's footage or the reference (a product close-up is often already in it). Look at each
`frame` or `crop` before using it and try other moments; ask for a picture only last.

**Finding the time range.** `get_remake_video` with `project` true lists the delivered video's
`lines` with `row`, `text`, `t0` and `t1` (seconds). When the user names a moment by its words,
take that line's `t0` and `t1`; for "the second half" and the like, use the length of
`final.mp4` in `handles`. Take a `frame` at the second you chose and look at it before paying for
an edit when the moment is not obvious.

## Plan before spending

Tell the user in one or two lines exactly what will change and what stays the same, and the rough
cost: a caption change costs next to nothing; a line change costs the new voice line plus
re-syncing the changed take (usually well under a dollar per line); a new background, overlay or
clip costs what its picture or clip costs (`references/atoms.md`, "Plan before spending"). Then go
ahead.

## Run it

Use the run directory of the delivered video, so `script/script.json` is there. Write the edits as
a JSON list to `project/edits/<NN>.json`:

```json
[{"kind": "lines", "lines": [{"row": 3, "text": "the new words for that line"}]},
 {"kind": "captions", "color": "yellow"}]
```

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/static_host.py' edit-call --job-id '<job id>' --edits 'project/edits/<NN>.json'
```

It checks each edit and prints the `edit_remake_video` arguments; call the tool with exactly those.
A refusal from the script is a mistake in the edits file: fix it. On a timeout or lost response,
call again with the same arguments (the key returns the same edit, not a second charge). For a new
edit after this one finished, write a new edits file; a `conflict` error means the key was used
with other arguments, so build again with `--attempt 2` (then 3, ...).

The call returns `working`. Call `get_remake_video` with `{"job_id": "<id>", "wait_s": 30,
"project": true, "include": ["review"]}` until `project.last_step.status` is `done` or `failed`.
Save the result and run:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/static_host.py' fetch-revision --input 'host-results/<NN>-get_remake_video.json'
```

It saves the newest revision as `output/remake-<id>-r<N>.mp4` next to the earlier one and prints
`revision_notes` (which parts were redone, whether anything outside them changed, the spend) and
`last_step` (spend, face verdict).

## Errors

- `validation_error` with reason `workspace_expired`: the job's working files are kept 14
  days and are gone; the video cannot be edited any more. Say so and offer a new remake.
- Any other `validation_error`: read `message` (for example a time range past the end of the video
  or a handle that does not exist), fix the edits file and build again with the next `--attempt`.
- `quota_exceeded`: if `message` says another remake or step is still running, wait for it and call
  again with the same key; otherwise a daily limit or the spending safety stop; nothing was changed.
- `last_step.status` `failed`: relay `error` in plain words; the delivered video is unchanged.

## Telling the user

Give the new revision's local path and say that the earlier one is kept; what changed, in one or
two lines; from `revision_notes`, whether anything outside the request changed; the spend (this edit's in `last_step`; the total,
with every edit, in `project.spend_usd`; notes from before the edit describe the earlier version);
and the
face verdict when a line was re-synced. A `degraded` face check does not stop delivery: say in
plain words which part looks less like the creator. Relay the final review as in
`references/final-review.md`.
