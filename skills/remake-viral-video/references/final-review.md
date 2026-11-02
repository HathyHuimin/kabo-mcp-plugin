# Relaying Kabo's final review

Read this at step 7, after `fetch` has saved the video. Its output carries two fields made from
`review` in the final `get_remake_video` result; use them, not the raw `review`:

- `review_message`: Kabo's automated final review, already worded. If it is a string, tell the user
  exactly that, in your own sentence if you like but with every flagged item kept and nothing
  added. If it is null, say nothing at all about a review, checks or QC.
- `notes_for_user`: what the runner changed or could not do. Give the gist in one or two plain
  lines (for example a sign-off that was replaced, a line that was split, a framing that could not
  match the reference); do not quote stage names. If it is empty, skip this.

Never read the verdict from `delivery_notes` yourself: the notes also hold an earlier self-check,
likeness and speech figures and "delivered with qc ..." lines that are not the final verdict. A
note about framing is a production note, not a review flag, unless `review_message` names it.

In every case never say the video passed a review, and never call any check, score or measurement
good, successful or in range. Kabo's review measures; it does not watch the video.
