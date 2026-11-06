# When the video is a file on this machine

Read this file whole when the video is a file on the creator's own machine (`source.acquisition`
is `local_file`). Everything in `SKILL.md` still applies; this is what differs.

A creator asking about a cut they have not published has no link to give, and that is the ordinary
case for the question they most want answered: is this ready. The file is right there and this
package reads it.

1. **Ask what the machine has, before committing to it.**
   `cd '<run directory>' && python3 <skill directory>/scripts/probe_media_tools.py --require ffmpeg,ffprobe`. Exit 3 means the tools are not
   installed: **say so and install nothing.** Hand the creator the one line the script printed and
   carry on with whatever evidence the run does have — a subtitle file, what they told you, the
   platform they are making it for. A run that stops dead because a tool is missing has done less
   for them than one that names the single command that would fix it. Pass each `gaps[].limitation`
   through to the builder with `--limitation`.
2. **Read the file.**
   `cd '<run directory>' && python3 <skill directory>/scripts/extract_local_frames.py --media <absolute path> --out-dir analysis/frames
   --artifact analysis/local-media.json --hook-window-s <3 for a Short, 15–30 for long-form>`,
   adding `--measure-loudness` when the question is about sound. It writes the same frame shape the
   connector writes, so everything in *Frame sampling* in `references/evidence-reading.md` applies unchanged — `frames.origin`
   included. **Locally sampled frames are real pixels and are read exactly like any other frame:
   open them, quote what is on them, cite them the same way.** Nothing in the analysis branches on
   where a frame came from; the ledger records that, and the ledger is not the report.
3. **Measure, in a command of its own**, whenever the question touches pacing, cuts, pauses or the
   ending: `cd '<run directory>' && python3 <skill directory>/scripts/extract_local_frames.py --media <the same absolute path>
   --artifact analysis/local-media.json --signals-only`. Frames place no cut shorter than their
   spacing and hear no pause; the measurement does both with the connector's own thresholds and adds
   them to the artifact step 2 wrote, landing in `media.probed` exactly as a published video's would.
   **Never fold it into the frame command.** Sharing a command means sharing its time, and a
   measurement that runs out or fails on its own leaves the frames already written untouched.
4. **Get the words** — see the three tiers under *Getting the words* below.
5. **Then build**, with `--local-media analysis/local-media.json` and, where there is one,
   `--local-transcript analysis/local-transcript.json`.

### When the file is long or heavy

Some hosts end a command after about a minute and keep nothing it wrote, so every command above
stops itself at `--deadline-s` (40 seconds after it started, by default) and writes what it has.
A large or high-resolution file can need longer than that, and the output says what was left out:

- **Frames** — the opening and the closing frame are written first, so running out of time thins
  the body; the summary counts the skipped frames and a limitation gives the widest gap left.
- **Measurement** — it reads the file from the start and stops at the deadline. `measured_until_s`
  in its summary names the second it reached, and a limitation in the artifact says the rest was not
  read. Pass that on: past that second, cuts and pauses are **unmeasured, not absent**. Place a
  change there between the two frames that bracket it, as you would with no measurement at all.

Raise `--deadline-s` only where the host lets a command run longer. Rerunning the same command
with the same deadline gets no further.

### Where the file is allowed to be read

**Some hosts deny whole folders, and a denied file looks exactly like a missing one.** A sandboxed
host commonly puts the personal folders entirely out of reach — Documents, Desktop, Downloads,
Pictures, Movies, Music, and external volumes with them — which is precisely where creators keep
video. The path is real, the file is there, and the read fails.

`extract_local_frames.py` and `read_subtitle_file.py` tell the two apart and **exit 4 for a denied
location**, separately from exit 2 for a path that does not exist. Treat them as different answers:

- **Exit 2 — no such file.** Ask the creator to check the path. They may have renamed or moved it.
- **Exit 4 — the host will not read there.** This is not the creator's mistake and not a missing
  file, and telling them the file does not exist sends them hunting for something that is not lost.
  Say the analysis could not open it from that folder, and ask them to put a copy somewhere outside
  those folders — a folder they make in their home directory is enough — and give that path. Then
  carry on with whatever else the run has.

Never report a denied read as an absent file, and never tell the creator to change a setting: this
is the host's boundary, not something they configure.

### A local file is the master, not what viewers received

On this path `media.encoding` is read off the creator's own export. Every platform re-encodes on
upload and normalises loudness to its own target, so the integrated loudness, the bitrate and the
frame size say what was **sent**, not what was **heard or seen**.

Name which one a recommendation is about. "Your master sits at −26 LUFS and the platform will lift
it to about −14, so the quiet passages will come up more than you expect" is usable. "Your video is
at −26 LUFS, too quiet for the feed" is a claim about a file no viewer ever played, and a creator
who tunes to it has tuned to a number that never reached anyone.

**True peak is the exception and travels either way**: above zero is exactly what a platform's own
re-encode turns into audible distortion, so it is worth saying on the master.

## Getting the words

**A file on this machine has no platform captions to fetch, and three tiers in order:**

1. **Platform captions** — only where the same cut is also published and the creator points at the
   post. That is a `public_url` run and the media call's `transcript` operation is the whole path, as *Transcript* in `references/evidence-reading.md` says.
2. **A subtitle file the creator supplies** — `.srt` or `.txt`, read by
   `cd '<run directory>' && python3 <skill directory>/scripts/read_subtitle_file.py --subtitle <file> --artifact analysis/local-transcript.json`.
   **Ask for one before giving up on the words.** A creator who cut with captions already has this
   file, and unlike anything a machine hears it carries their own wording — `transcript.origin` is
   `creator_supplied` and that is what lets you quote a line as what they actually say. Its timings
   were never checked against this cut, so quote the words freely and say once that the timing is
   the file's claim about the video rather than a measurement of it.
   **Where the host can transcribe the attached file itself**, that is the same tier with a machine
   behind it: save the cues it returned, timings unchanged, as `analysis/host-transcript.srt` and
   read that with `--origin speech_recognition` added. The origin is the point — those words are
   what the analysis heard, quotable as heard but never as the creator's exact wording.
3. **Local speech recognition** — not implemented in this version. `probe_media_tools.py` reports
   it as a gap and prints the one line that would install it. This Skill never runs an install, and
   **it does not run this tier even where the library is already present**: nothing here would call
   it, and reporting it as available would send a creator to install a dependency that changes
   nothing.

When neither of the first two produced words, say the argument was never read — the same sentence a
connector run says when its transcript did not arrive — and carry on from frames and metadata.
