# Format: talking with a product

> Host edition (ported from creator-remake-web 0.1.32, creator-skills `f74df60a`). The runner does
> every picture step; take only the writing rules from this file. `brief_input.json` is
> `brief/work/brief_input.json` in the run directory.

**When this applies**: `brief_input.json` has `reference.type: "talking_product"`. The speaker
talks to the camera about a physical thing they show -- held up to the lens, standing on the
desk, worn -- and the video cuts to close-ups of it. The runner measured this from the video; you
do not decide it.

Read `references/formats/talking-head.md` first: everything there about the content layer, the four rewrite slots
and sourcing applies unchanged. This file only adds what differs.

## What the runner does with the product

- The opening keeps the product being shown and the things already on the desk: the setting is
  taken from the reference's opening shot; when objects are measured there, the plate prompt is
  told to keep them and the reference's own object pixels are put back into the creator's first
  frame wherever they do not cover the creator.
- A closer or wider shot of the same room is cut from the opening frame itself, so the creator
  looks the same and the room is the same all through.
- Close-ups of the product are the reference's own footage. A close-up that also shows the
  reference's speaker's face reuses only the product, laid over the creator's own footage, or
  the creator talks for that line instead. The reference's speaker never appears in the result.
- One setting for the whole video, unless the reference itself moves to another place.
- The voice is paced line by line to the length the same words take in the reference.

## What this means for the script and the prompts

- Keep the product's name and the lines that describe what it does as the reference says them;
  they are what the close-ups show. Claims about the product stay attributed to the reference,
  not to the creator's own testing, unless the creator's own lines say so.
- In the plate prompt, describe the setting as the reference shows it, objects included. Do not
  ask for an empty desk, and do not name or invent products the reference does not show.
- The creator's talking shots are empty-handed by design: talking models deform a held product, so
  the runner keeps the product on the desk and in the reference's own close-ups instead. Do not
  write that the creator holds, picks up or shows the product, and never promise the user a shot
  of them holding it.
