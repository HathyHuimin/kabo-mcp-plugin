# Format: talking with screens

> Host edition (ported from creator-remake-web 0.1.32, creator-skills `f74df60a`). The runner does
> every picture step; take only the writing rules from this file. `brief_input.json` is
> `brief/work/brief_input.json` in the run directory.

**When this applies**: `brief_input.json` has `reference.type: "talking_screen"`. The speaker
talks to the camera and the video cuts to screens -- a screen recording or screenshots of an app,
a page or a chat, either filling the frame or floating over the speaker's room. The runner
measured this from the video; you do not decide it.

Read `references/formats/talking-head.md` first: everything there about the content layer, the four rewrite slots
and sourcing applies unchanged. This file only adds what differs.

## What the runner does with the screens

- The screens are the reference's own pixels. A screen that fills the frame plays full frame. A
  screen floating over the speaker's room is cut out at its own place and size and laid over the
  creator's own talking footage for that line, so the creator is the person around it.
- Each screen plays at the reference's own pace: no zooming into a corner, no stretching.
- The reference's speaker never appears in the result. Any reused frame that shows them is
  replaced by the creator talking for that line, and the review checks the whole cut for them.
- The reference's own subtitles on a screen are blurred out so the new captions do not double
  them.
- The creator stays on camera at least about as much as the reference's speaker was.

## What this means for the script

- Keep the lines spoken over the screens (`reference.inserts` entries with `shows: "screen"`) in
  the reference's words where you can. A kept line is split where the reference cuts between the
  speaker and a screen; a rewritten line over a screen stays whole and is shown on the screen or
  on the creator, whichever covers it better.
- The screens show what the reference typed and got. A line that names what is on screen must
  still match it: do not rewrite a line over a screen into something the screen does not show.
- Do not write image jobs for screens; only the plate and anchor jobs of the template.
