# Format: narration (the creator is heard, never seen)

**When this applies**: `brief_input.json` has `reference.type: "narration"` (the result's
`reference_type` is `narration`). The reference is carried by a voice over pictures --
a screen recording, an animated or drawn explainer, stock or live-action scenes, kinetic type --
and nobody talks to the camera for more than a moment. The runner measured this from the video;
you do not decide it.

The creator's cloned voice reads the reference's script; every picture is re-made. Nobody is put
on camera, so there are no plate or anchor prompts.

## The script

The script, the four rewrite slots, sourcing, the opening record and the proofread are exactly as
in `talking-head.md` and `../creator-into-scene.md` section 0, with these differences:

- **Call to action only where the reference has one.** A reference that ends on its payoff keeps
  ending there: record the `cta` choice as "none, the reference ends on its payoff". Where the
  reference does sign off (usually promoting its own author), replace it with the creator's
  recurring sign-off from their own lines, or a plain follow prompt.
- `route_b_jobs` is not used: write `script/route_b_jobs.json` as `{"jobs": []}`. There are no
  plate or anchor prompts, so the hair and outfit wording rules do not apply.

## The picture plan (in `decisions`)

`decisions.draft.json` carries two extra fields. Fill both; the runner checks them before anything
is paid for.

1. `picture_system` -- one paragraph before the lines: what kind of picture the reference is and
   how it moves (an app or a page on screen, drawn or animated, live action, ...), the look you
   make yours in, and the one way every line is made so the cut reads as one piece.
2. `pictures` -- one row per script line (counted from 0 in `script.json` `lines`) or per group of neighbouring lines that stay in one scene:

```json
{"lines": [3, 4],
 "reference": "what the reference shows while these lines are heard, and what kind of picture it is",
 "plan": "how yours is made, the same kind of picture",
 "evidence": false,
 "needs": [],
 "make": {"kind": "composition", "markup": "...", "style": "...", "script": "..."}}
```

The runner carries out `make` after the script is submitted. Choose by one
question: **does the picture have words in it that must read?**

- **Words in it -- `kind: "composition"`.** An interface, a web page, a settings dialog, a list,
  steps, a chart, a number, a map with place names, a formula. You write it now as a motion
  graphic (HTML, CSS and an animation), and the runner renders it after the voice, so every word
  is real text and every move lands on the word that names it. Rebuild what the reference shows
  in this form -- an app's window with its real menus and labels, the list item by item -- not a
  screenshot of anything real. An image model cannot write words: every interface it drew in the
  first web batch came out with garbled text.
- **No words -- `kind: "generated"`.** People, places, objects, an illustrated scene. The runner
  generates the still from `prompt` with frames from the creator's own videos as its references
  (so "in the look of the reference images" carries the creator's look; you cannot see those
  frames, so do not describe colours or characters you have not been told about), and asks for
  no text in it. With `motion` it becomes a 5-second moving clip; leave `motion` empty only where
  the reference itself holds a still. **When the reference moves, every picture moves.**
  Neighbouring short lines of one scene share one row and one clip.
- `kind: "motion_graphic"` -- a plain card of the line's words, for a line where nothing better
  fits.

### Writing a composition

The runner puts your three strings into a 1080x1920 page sized to the lines' length:

- `markup` -- the elements, inside the page's root. Every visible element is `class="clip"`; it
  shows for the whole of the row's lines unless you give it `data-start` and `data-duration`
  (seconds) and `data-track-index` yourself. Keep what matters inside the class `safe` box (it
  leaves the bottom third for captions and the right edge for the platform's buttons). Write the
  interface's real words: menu names, button labels, column headings, values.
- `style` -- CSS for those elements: positions, sizes, colours, fonts. Use the fonts the renderer has: "Noto Sans"
  (Latin) and "Noto Sans CJK SC" (Chinese, Japanese, Korean), with sizes that read on a phone (body
  text 34px or more).
- `script` -- lines that add tweens to the paused GSAP timeline `tl`. `cue("word")` is the time
  that word is heard, counted from the start of the row; `DUR` is the row's length. Example:

```js
tl.from("#win", { opacity: 0, y: 30, duration: 0.4 }, 0);
tl.to("#cursor", { x: -320, y: -90, duration: 0.6, ease: "power2.inOut" }, cue("layout"));
tl.to("#tabular", { backgroundColor: "#cfe6d8", duration: 0.2 }, cue("tabular"));
tl.to(".filled", { opacity: 1, duration: 0.4, stagger: 0.15 }, cue("form") + 0.4);
```

It has to pass the renderer's checks, or the line falls back to a plain card:

- Move with transforms only: `x`, `y`, `scale`, `rotation`, `opacity`, colours. Never animate
  `left`, `top`, `width` or `height`.
- Give every tween fixed numbers. Never a function that measures the page
  (`getBoundingClientRect`, `offsetLeft`): the renderer's workers seek on their own, so a measured
  value differs between them.
- Nothing from the network: no web fonts, no image URLs (draw icons as inline SVG or text).
  Never paste an image in as `data:`; the runner drops it.
- One timeline: no timers, no `requestAnimationFrame`, no randomness.
- Keep each row's three strings under about 20,000 characters together.

A composition that needs a real-looking image inside it -- the picture an app produced, a photo
on a page, the base of a map -- names it in `make.images` and shows it by name. The runner
generates each one (about $0.04, no text in it, in the creator's look) before rendering, at most
six per row:

```json
"make": {"kind": "composition",
         "images": {"result.png": {"prompt": "a watercolor portrait of a young woman, soft light"}},
         "markup": "<div id=\"app\" class=\"clip safe\">... <img id=\"out\" class=\"clip\" src=\"result.png\"> ...</div>",
         "style": "...", "script": "..."}
```

What makes one good: one idea per row, a few things moving at once, the same window and colours
across rows so the cut reads as one piece, and each element landing on the word that names it.
Fill the vertical frame: show the part of the interface the line is about at phone size -- the
menu, the column, the dialog -- not a whole desktop window shrunk into the middle with black
above and below. Give each scene its own row (a few lines); one composition for the whole video
is slow to render and hard to keep readable.
The captions already carry the narration, so do not spell the line out on screen as well.

What the runner cannot do here: open a real web page or an app, or use the user's own material.
Where the reference shows a real interface, rebuild it as a composition (`evidence: false`) and
keep the line's claim to what the picture can carry. A picture that cannot be made becomes a card
of its line, and the delivery notes say so.

Estimate `est_usd` per row: a composition costs nothing to make; about $0.04 a still, and about
$0.35 for each moving clip.

## Look before you render -- as often as it takes

The r30 runs that matched their references rendered every picture two to four times and looked
at it each time; written once, blind, the map and result-image lines came out empty. Once the four
documents pass step 5, and before the paid render, preview the pictures:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/check_documents.py' render-args --preview pictures
```

It writes `render/preview_remake_video.json`; call `render_remake_video` with that file's content,
unchanged; it costs nothing and you can repeat it freely. `--preview stills` also generates the
stills of the `generated` rows: about $0.04 each, the same stills the video then uses (not paid
twice), but it is paid -- run it only after the user's go-ahead in SKILL.md step 6, look at the
stills, fix any, and then render. The job stays at `waiting_script`. About 30 seconds later call `get_remake_video` with `wait_s` 30,
save that result and fetch the pages (they ride in its brief link):

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/static_host.py' fetch-preview --input 'host-results/<NN>-get_remake_video.json'
```

When it says the preview is still rendering, wait about 30 seconds and repeat both steps. It
saves `preview/<n>/page_NN.jpg`: one strip per row with the row and lines, the line, and four
frames. Open every page with your image viewer and check each strip: the words read at phone
size, the frame is filled, nothing important sits in the bottom third under the captions, and the
picture shows what the line says. `errors` names rows that did not render. Fix what does not
work in `script/decisions.json`, run step 5 again and preview again -- there is no limit. A
preview does not start production; the job stays at the script gate.

## One look at the finished pictures

After the video is made, a voice-over job stops once more: `get_remake_video` shows
`waiting_script` again with a `gate_message` asking you to look at every line's picture. Save that
result and run `fetch` on it (step 3's command); it reports `"saved": "review"` and saves
`review/pages/page_NN.jpg` -- each row: the video's
picture while the line is heard | the reference's picture where the same line was heard | the
line -- and `review/brief/` with the current decisions and
`production/review/beats/beat_review.template.json`.

1. Open every page. Copy the template to `script/beat_review.json` and set `ok: true` for each
   line whose picture shows what the line says and reads at phone size; `ok: false` with a short
   `note` for the others.
2. For every line that is not ok, change its row's `make` in `script/decisions.json` (fix the
   composition, give the still a new prompt, or switch its kind) and preview it as above.
3. Build the answer and call `render_remake_video` with it, unchanged:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/check_documents.py' render-args --beat-review 'script/beat_review.json'
```

It writes `render/review_remake_video.json`. Only the rows of the lines marked not ok are made
again; everything else is kept. There is one such round; after it the video is delivered as it
stands (step 7). Tell the user only that the video is being finished.

## Red lines

- Every picture is an illustration unless it shows the real thing: never present a generated
  interface, chart or scene as a real screenshot or as evidence.
- No people who look like real, identifiable persons; no logos or brand marks drawn as if they
  were the creator's.
- Nothing from the reference's pixels is reused: the reference's look is not the creator's.
