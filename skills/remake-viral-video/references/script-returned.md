# When the runner sends the script back

The runner checks the documents first, after `render_remake_video` returns. If they fail, a later
`get_remake_video` shows `waiting_script` again, with `gate_message` (the runner's reason, for
example `cr check-script: ...`) and a fresh brief; nothing paid has been spent. Then:

1. Save that result and run `fetch` on it (`SKILL.md` step 3). It keeps the earlier brief as
   `brief.previous-<N>/` and writes the fresh one to `brief/`; your documents in `script/` stay.
2. Fix only what `gate_message` names, keep the rest of the documents as they were, and run `SKILL.md` step 5.
3. Build the arguments again with `--models-from` pointing at this newest result and the same
   talking model, and call `render_remake_video` again. Do not ask about the model again.
