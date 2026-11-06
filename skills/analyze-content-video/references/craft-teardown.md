# Craft teardown

Read this file whole when the creator asks how a video was made — see *Which question is this* in
`SKILL.md`. It is this mode's workflow, how to judge sound, what it may not claim, and what it
writes.

A creator watched something and wants to know how it was made. Answer the question they asked, in
the shape that question has. Everything `SKILL.md` and `references/evidence-reading.md` say about
evidence holds here: frame sampling, transcript handling, when to stop waiting and *Output
boundary* in `SKILL.md` are shared. What
differs is what the evidence is read *for*, and that there is no fixed layout waiting at the end.

**The diagnosis pipeline does not belong to this path.** A teardown writes no `video_analysis.json`,
no `VIDEO_DIAGNOSIS.md` and no `CreatorTaskResult`. Its only fixed steps are staging and persisting
envelopes and building the evidence. Write `report/craft-teardown.md` yourself; there is no
renderer to look for.

## Workflow

1. **Write `analysis/craft-request.json` before retrieving anything**, following
   `references/craft-request.schema.json`. It carries the creator's question verbatim and whether
   a video was named. Written first, it records what this run was asked; written last, that can
   only be guessed from what the run produced.
2. **Get the evidence before forming an opinion.** Frames and transcript, the same way a
   diagnosis gets them. A video whose transcript never arrived is a video whose argument was never
   read.

   For a public URL with no ownership statement, record `subject.whose=unknown` in the craft
   request and `source.subject_state=unknown` in its evidence request. Keep `owner_data` null,
   `owner_metrics` skip and no account baseline; unknown does not mean third party. Do not hold
   public evidence or a transcript export while asking about ownership. A local file keeps the
   state the creator declared.

   **Then look at the frames.** They arrive as references — an id, a second, a checksum — and a
   reference is not a picture. Read the frame bytes with `read_research_data_artifacts` and
   `fetch` as *Reading the frame bytes* in `references/evidence-reading.md` says, then open the
   image files and look at them. Framing, shot size, camera movement, type, colour and where the eye
   goes all live in the picture. Read only the words and you have written what the video *said*,
   filed under a question about how it was *made*.
3. **Measure before you claim.** Put what can be counted on the page, each with the timecode it
   came from, before any judgement about why something works.

   The media call's `measure` operation measures in one job: shot boundaries and how long each
   runs, per-shot brightness, where the track goes quiet, loudness every tenth of a second, held
   frames including a trailing still, and the real frame size with its bars and the share of a
   phone screen actually filled.

   Measurement is its own readiness question, answered by the run's one
   `get_research_capabilities` check. When it is not available, say the numbers were unavailable
   and carry on from frames and transcript. A teardown without measurements is thinner; a teardown
   that waits for an operation this deployment does not have is nothing at all.
4. **Describe each shot that matters on the same five points**: what is in it, how the subject
   moves, where it happens, how it is framed (shot size and angle), and what the camera does.
   Mark a point as not visible rather than skipping it silently. Use the precise names — a
   medium close-up, a push-in, a match cut — instead of "a nice shot".
5. **Organise the answer around the question, not the timeline.** A question about what to reuse
   wants a ranked list of reusable things. A question about a look wants the passes that produce
   it, in order. A question about pacing wants the measurements first and the judgement after.
   Walking the timeline from second zero answers none of them.

   **Rank when ranking is possible**, and say what you ranked by. "These three are the big ones,
   because a three-minute video has seven times the captions" is worth more than seven items of
   equal weight.

   **When the reference does not match the creator's situation, say so and redirect.** A
   27-second ad and a three-minute vlog do not have the same time sinks. Noticing that is the most
   valuable thing a teardown does; burying it in closing caveats wastes it.
6. **Answer "which of this can I do alone".** It is the question under almost every one of these
   asks. A creator told a video used motion tracking and colour separation, and not told which of
   those they can do without hiring anybody, has been handed a list of reasons to feel behind.
   Name the condition when it is conditional: "you can, if" with no "if" is "yes" with extra
   words.
7. **Say what each claim stands on.** Two levels, kept distinguishable in the prose:

   - **Read off this video** — cite the frame, the cue or the measurement. "The captions sit at
     73% of frame height" belongs here only if you looked.
   - **General practice** — not from this video. You may still give the number, but it must be
     visibly unsourced: "a common starting point is…", never stated flat like a measurement.

   The failure this prevents is the one that makes a teardown untrustworthy: a setting nobody
   measured, printed in the same voice as a figure read off the frame.

## Judging sound

Sound questions are answered inside whichever mode applies. A sound's title and author are
metadata: they say which track was used, never how it sounds.

**What the evidence can carry.** A video analysis that listened to the soundtrack gives a broad
impression: whether there is music, its general character, how its energy develops, whether speech
is present and whether the music drowns it, whether sound effects are used. It cannot reliably say
whether a cut lands on the beat, what the lyrics are, or the exact second the energy changes — two
runs on the same video disagree on all three. Measurements (loudness over time, where the track
goes quiet) can say some of that; a listening impression cannot.

**Six questions worth asking.**

1. Original sound or an existing track — and what is it buying? An existing track can borrow
   recognition; an original one can carry the creator's voice. Whether a track is trending needs
   usage data, not a listen.
2. Is sound doing work in the first second? Silence or a slow ramp at frame zero leaves the
   opening to the picture alone.
3. Does the music's energy rise where the video pays off? A broad match or mismatch is judgeable;
   an exact beat is not.
4. When speech carries the point, does the music stay under it? If the music drowns the speech,
   that is the fix, whatever the track is.
5. Do sung words compete with on-screen text? Only when the words were actually transcribed.
6. Does the end sound like the start, on a video meant to loop? Only when it was heard clearly.

**Signs a judgement did not come from listening**: it would be equally true of any video using a
track with that title; it names a genre or mood the evidence never described; it places a cut on
or off the beat from an impression alone; it recommends swapping the music without naming the
problem the swap solves.

With no heard or measured sound for this video, say the sound was not analysed. A pattern's
`sound` line from `scripts/prepare_video_hook.py` is a proposal for the next video, not a reading
of this one.

## What may not be claimed in a teardown

- **No performance, no prediction.** Not how this video did, not how the creator's next one will
  do, not what a change is worth in views. This mode holds no metrics and no baseline. A creator
  who wants both asked a diagnosis question.
- **Give the craft whole: the tool, the setting and the command.** Encoding, bitrate, loudness
  target, frame size, the program that does it — these are the trade, not incidental detail.
  `H.264 at 10–12 Mbps, audio 192 kbps AAC, loudness to -14 LUFS` is what a creator needs to act;
  "export at a good bitrate" helps no one. Source every figure as in step 7.
- **Name the feature; do not invent the click path.** "CapCut calls these text templates" is
  stable. Which tab or sub-menu is not known here and moves between versions.
- **Admit the gaps.** Gear to buy, phone shooting technique and editor presets have no source in
  this Skill. Answer what the video supports and say which part is general practice. A guessed
  menu path costs the creator an afternoon.
- **Borrow the mechanism, never the wording.** Say which pass transfers to someone with no
  audience and which only works because that creator already had one. A teardown is not a repair
  order for somebody else's published video.
- **Do not rewrite the question.** `analysis/craft-request.json` carries the creator's own words.

### When no video was named

"What should I buy first", "how do I light this with one phone" — answer from general practice and
say plainly that no video was read. Do not go and find a video to look at: footage nobody asked
about is not evidence about their question.

### Before delivering

No validator checks this report. Hold two things yourself: every figure says where it came from,
and no number is stated about this creator's own results that nobody measured.

## What this mode writes

`analysis/craft-request.json` before retrieval and `report/craft-teardown.md` at the end. The
answer is the prose, shaped by the question rather than by a schema.
