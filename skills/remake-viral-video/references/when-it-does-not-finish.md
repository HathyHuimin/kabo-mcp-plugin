# When a remake does not finish

Take the reason from where the final status came: from `start_remake_video`, its `message`; from
`get_remake_video`, `failure_reason` when it is not null. If there is none, say that no reason was
given and do not invent one. Always tell the user the reason in plain words, not only that
the remake was refused or stopped (for example: the reference is filmed with a camera that keeps
moving, like a walking or travel vlog, rather than one person talking to the camera). Do not quote
stage names or internal terms. Always say whether it was charged:

- `out_of_scope`: relay the reason. For a reference that is not a short talking-head video, suggest
  one of 10 minutes or less where one person speaks to the camera for most of it. When no clear
  frontal face of the creator was found, ask for links to videos where they talk facing the camera.
  Nothing was charged and the daily limit was not used; say so.
  When the reason is that nobody talks to the camera in the reference (a product demo, scenes,
  footage without a speaker), offer to build it as a project instead (`references/atoms.md`,
  "A reference nobody talks in"): the reference's own footage is reused and the creator's segments
  are made and cut in. Start it only after the user agrees to the plan and cost.
- `failed` with a likeness reason: the generated person did not look like the creator, so the video
  was stopped or not delivered. Say so plainly and ask for clearer footage of the creator facing the
  camera; do not resubmit the same inputs.
- `failed` or `timed_out`: say the remake did not finish, why, and that no video was produced. These
  jobs ran paid steps and count toward the daily limit; do not say otherwise. Do not resubmit on
  your own; offer to start a new remake if the user wants one.
- Any other `failure` value: relay `failure_reason` and the reported spend; do not guess whether it
  was charged.
- A `get_remake_video` call that fails: try once more; if it still fails, say the job is preserved
  and can be checked later with its job id. Never start a replacement job for an unknown outcome.
