# Writing the script at the `waiting_script` gate

This is the only creative step of a remake, and the only one you do. The hosted runner has
analysed the reference and the creator's footage; after you submit, it makes the speech, first
frames, talking video, grading, captions and review on its own. Read this file whole before you
write anything.

## What you start from

`brief/work/brief_input.json` in the run directory holds everything you need: the reference's
sentences, shots, beats and cut rate, its type, the creator's measured habits and their own lines
(`creator.own_lines`, each with `video`, `t0`, `t1`, `text`). Start from the drafts next to it:

- `brief/work/script.draft.json` -- one line per reference sentence, each already marked
  `ref: viral_line`, the inherit ledger with every non-talking reference shot as `keep`, and any
  title or list overlay the analysis found;
- `brief/work/decisions.draft.json` -- the decisions record with its required choices;
- `brief/work/route_b/jobs.template.json` -- the plate and first-frame image jobs, with their
  references, outputs and each anchor's measured `framing` already filled in.

Read the matching sections of the bundled references for the judgement in front of you:

- `references/creator-into-scene.md` section 0 (the script: four rewrite slots, the record and
  the inherit ledger), section 0.5 (what the decisions record holds), section 2 (plate and
  first-frame prompts) and section 7 (the one wording that breaks this route).
- `references/opening-review.md` for the opening record.
- `references/rewrite-and-sources.md` for sourcing: every fact and number traces back to the
  creator's own line or is marked as an inference.
- `references/formats/talking-head.md` for the talking-head format, and, when
  `brief_input.json` says `reference.type` is `talking_screen` or `talking_product`, also
  `references/formats/talking-with-screens.md` or `references/formats/talking-with-product.md`.
- `references/formats/narration.md` for a voice-over reference (`reference_type` `narration`):
  the picture plan, previewing the pictures and the one picture review.

These references were written for a local command-line kit. Every command they name is run by the
hosted runner here; take only the writing rules from them. Do not ask the user to run anything.

The brief is text only: you cannot see the reference's keyframes or the creator's identity frames.
If a brief bundle ever carries preview images, the fetch step saves them in `brief/` and you may
open them with your own image viewer; the hair and outfit wording rules below still apply.

## Proofread the reference first

Before writing, proofread the reference's sentences in `brief_input.json` (`reference.sentences`).
They come from speech recognition, which sometimes hears a real word that does not fit the
meaning (for example "learn to self, communicate" for "learn to sell, communicate"). Read each
sentence as the speaker meant it; fix only such mis-hearings, never wording, grammar or filler.
Use the corrected wording in every kept line, and record the proofread in the `script` document
as a top-level field (the runner applies it to the analysis and removes the field):

```json
"proofread": {"fixes": {"6": "Two, learn to sell, communicate."},
              "note": "one line: what was misheard, or no mis-hearings"}
```

The keys of `fixes` are indices into `reference.sentences`; each value is the whole corrected
sentence. Always include `note`, with `fixes` empty when nothing was misheard.

## The four documents

Write each as a JSON file in the run's `script/` folder: `script/script.json`,
`script/opening_review.json`, `script/decisions.json` and `script/route_b_jobs.json`. Each must be
256 KiB or less.

1. `script` -- from `script.draft.json`. Change only the rewrite slots; give each a `why` and
   `replaces`. Adapt or drop inherited entries only with a reason. The reference's on-screen
   text is part of its format, so keep it:
   - Title overlay: replace the `TODO` with the text the draft reports as read on the reference
     (the lines joined with a line break, written `\n` inside the JSON string; never `\\n`, which
     shows a backslash on screen), word for word. Keep it even when you rewrote the opening line;
     drop it only when it states something false about the creator, and say so in the ledger.
   - Numbered list overlay: keep one item per number the reference speaks (remove items whose
     label is not part of that sequence, such as a stray `0`). You cannot see the reference's
     icons, so set each `icon` to one emoji that names the spoken item, and set the ledger row
     to `adapt` with that reason. Give each item `"line"`: the index in `lines` of the line
     that starts that item (for example the line `"2."`). The item lights up when that line is
     spoken in the finished cut; leave its `t` as drafted.

   Every `route_b.inherit` row with role `on_screen_text` records `ref_form` (`emoji_icon`,
   `image`, `text` or `mixed`) and `layout` as seen on the reference; when you change a row's
   `decision`, keep its other fields and add the `why`. The script must also pass the sourcing
   check:
   - Every beat of the reference has a line. A beat counts when its index starts some line's
     `viral_role` (for example b3); a line that carries several beats names them all (b3-b5). The
     draft names each line's beat only from its sentences, so a beat without a sentence has to be
     added to a neighbouring line's `viral_role`. A beat whose only lines you deleted is listed in
     `route_b.dropped` with its `beat`.
   - Every line has non-empty `text`. Delete a line instead of emptying it.
   - A line without `rewrite` is the reference's wording, in the reference's order, with the claim
     `{"phrase": <the line>, "ref": "viral_line"}`.
   - Every rewritten line has `rewrite` with `slot` (`opening`, `author_claim`, `summary` or
     `cta`), a non-empty `why`, `replaces` (the reference words it takes the place of) and `basis`.
     On `summary`, `basis` is `creator_quote` or `persona`; on `cta`, it is `creator_habit`,
     `platform_generic` or `user_given`; on `opening` and `author_claim`, say in a few words what
     the line rests on. `creator_quote`, `creator_habit` and every `author_claim` line cite the
     creator's own words with a claim carrying an inline `src`
     (`{"video", "field": "transcript", "t0", "t1", "quote"}` from the creator lines in the brief).
     If nothing can be cited, use `persona` or `platform_generic`, or delete the author's line into
     `route_b.dropped`.
   - The opening comes first; one summary at most, after every kept line and before the `cta`.
   - Each claim is `{"phrase", "ref"}` or `{"phrase", "src"}`, and `phrase` is a substring of the
     line. `ref` is one of: `viral_line` (a reference sentence kept word for word), `viral_frame`
     (the reference's sentence frame, carrying no fact), `scene` (describes the picture),
     `inference` (only on a line with `"origin": "inferred"` whose text starts with "I think",
     "My guess", "Probably" or "Maybe"), or a library item id from the brief. Basis names such
     as `persona` or `platform_generic`, and the word `inferred`, never go in `ref`: a summary or
     call to action resting on `persona` or `platform_generic` has `"claims": []`. No claim has
     `ref: null`.
   - A line whose words differ from the reference sentence is a rewrite: give it `rewrite` and do
     not tag it `viral_line`. Keep numbers as the reference wrote them (do not turn "three" into
     "3"); a number in a rewritten line has to appear in what that line cites.
   - Every reference sentence is kept, named in a `replaces`, or listed in `route_b.dropped` as
     `{text, slot, why}` with `slot` `author_claim`, `opening` or `cta`.
   - Prefer rewriting an author claim in the creator's voice (what they would say in that place,
     from their own lines) over dropping it. If after that the script still keeps less than about
     half of the reference's spoken length (for example most of the reference is the original
     author's own story), tell the user at step 6, before the model question, in one line: the
     remake will be about N seconds instead of the reference's M, and why.
2. `opening_review` -- the opening decision record, in the shape shown in
   `references/opening-review.md` with `"stage": "script"` and `"route": "B"`: non-empty
   `intent`, `picture`, `sound`, `relationship`, `creator_fit` and `conditions`; `evidence` rows
   with `source` and `observation`; `checks` rows with `status` (`pass`, `fail`, `unavailable`
   or `not_applicable`) and `basis`; 2-3 `candidates` with `first_line` and `assessment`;
   `chosen` (the candidate index) and `choice_why`; `verdict` `ready`, `revise` or `limited`.
   `checks` must not be empty: record at least one item by name, for example
   `{"first_frame": {"status": "pass", "basis": "<what you looked at>"}}`. A `ready` verdict
   cannot have a check that is `fail` or `unavailable`; use `revise` or `limited`.
3. `decisions` -- from `decisions.draft.json`, keeping its fields: your calls on the summary, call
   to action and author lines, the brief summary and the rights line. Nobody answers questions at
   this point; record your call with a reason. Keep `status` as `pending`: the runner closes the
   brief and copies your choices into the script. Each choice's `by` is `default`, `agent` or
   `user`.
4. `route_b_jobs` -- from `jobs.template.json`: the plate and anchor prompts. Keep the template's
   tags, references, outputs and each anchor's `framing` field exactly as they are. A reference
   whose shots are framed differently has one plate and set of anchors per framing (`plate`,
   `anchor_0` ...; `plate_1`, `anchor_1_0` ...): write every one. Do not describe the framing,
   shot size, distance or posture in a prompt: the runner adds the framing measured on the
   reference shot. You cannot see the identity frames, so do not name hair features
   you have not seen: words such as wavy, curly, bangs, fringe, long, loose or a colour make the
   image model draw that hair instead of hers (a creator whose hair is tied back came out with
   loose waves and bangs). For each person job write the hair as "her hair exactly as in the
   identity references: the same hairstyle and the way it is worn, the same length and colour;
   not straightened, not smoothed, not tidied", and the clothing as "her own outfit from the
   identity references" (use the creator's pronoun). Replace the template's itemised hair list
   with that sentence.

## Inserts

The reference's shots that are not its host on camera (cutaways, inserts, other people) are
reused from the reference's own footage, its captions removed, under the lines spoken over them;
only the host's shots are remade with the creator. `brief_input.json` lists them under
`reference.inserts` with the draft lines that fall on each (`draft_lines`, indices into the
draft's `lines`). Keep at least one line on every insert (rewording it is fine); deleting all of
an insert's lines drops that shot from the video. Do not add image jobs for inserts.

## Before you submit

Run the local check (SKILL.md step 5). It catches structural mistakes only; the runner checks the
script, the opening record and the decisions again after `render_remake_video` has accepted them,
before anything paid is generated. If the runner rejects them, a later `get_remake_video` returns
`waiting_script` again with `gate_message` listing each failure (for example `cr check-script: ...`,
`cr check-opening: ...`, `cr decisions brief: ...`) and a fresh brief, with nothing spent. Save the
fresh brief, fix only what `gate_message` names, keep the rest of the documents as they were, run
the local check again and submit all four files again with the same talking model.
