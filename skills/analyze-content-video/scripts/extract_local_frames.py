#!/usr/bin/env python3
"""Read a video that is on this machine, and sample frames out of it the way the connector does.

A creator's unpublished cut has no URL, so the media capability cannot reach it: that capability
runs server-side and nothing about it runs here. The file still exists and can still be read -- by
this script, on the machine the run is executing on, over the one file the creator named.

**The output has to be the same shape as the connector's, or nothing downstream can stay simple.**
Everything after acquisition -- the sampling plan, the frame list, how a frame is cited, what the
report may say about what was seen -- was written once, for frames. Emitting a second shape here
would mean every consumer learns to branch on where a frame came from, and the first consumer that
forgets is one that treats a real frame as if nobody had looked at it. So this writes
`duration_scaled_v1` frames with the same fields, and the ledger -- not the frame -- records that
the step ran locally.

**The plan is the same algorithm, not the same floating-point result.** Dense across the opening,
steady through the body, one frame at the close. Measured against the two shipped fixtures it
reproduces the frame count, the hook and body counts and the reported body interval exactly, and
every timestamp to within a millisecond. The last millisecond is not recoverable: the connector
records its own duration rounded to three decimals, so the value it divided by is not in the
evidence. Chasing it would mean fitting this script to the rounding of a service it cannot see.

**Reading the file is not the same as trusting it.** A creator's filename can contain a space, a
quote or a leading dash; a container can lie about its duration or carry no audio at all. Every
invocation passes an argument list, never a shell string, and every one has a timeout. A file whose
duration cannot be established is refused rather than sampled against a guess -- a plan needs a
length, and inventing one produces a frame list that looks authoritative and points nowhere.

**What it writes has to survive being carried back by a host.** A host that runs this in a sandbox
copies the run directory back afterwards, and it refuses a whole step over one file name it cannot
replay or over a directory that grew past its size limit -- after the script already succeeded.
So frame files are named by a hash of their id, never by the id itself (the id carries `#` and the
creator's own file name), each frame is scaled down to a long edge that still reads on-screen text,
and the frame count and total bytes are capped. The id in the artifact is unchanged.

    python3 extract_local_frames.py --media FILE --out-dir DIR --artifact FILE
                                    [--hook-window-s 3.0] [--base-interval-s 1.0]
                                    [--hook-multiplier 2] [--budget 600] [--max-frames 90]
                                    [--max-total-bytes 12582912] [--jpeg-quality 3]
                                    [--measure-loudness] [--measure-signals] [--dry-run]

`--measure-signals` measures where the file cuts, where its audio drops below the speech floor and
where its picture holds still, with the thresholds the connector uses on a published video, and
writes them in that operation's shape under `media.signals`. Frames alone cannot answer pacing: a
cut shorter than the sampling interval falls between two frames, and silence has no pixels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# Every executable this package is allowed to start. Kept identical to the survey script's set and
# enforced the same way: these two read and decode media, and nothing else here needs a subprocess.
ALLOWED_TOOLS = frozenset({"ffmpeg", "ffprobe"})

PROBE_TIMEOUT_S = 60
FRAME_TIMEOUT_S = 60
LOUDNESS_TIMEOUT_S = 900

# The connector places its closing frame this far before the end rather than exactly at it: an input
# seek to the final timestamp lands past the last decodable frame on several containers and writes
# nothing at all.
CLOSING_OFFSET_S = 0.1
CLOSING_RETRY_OFFSET_S = 0.25

# Loudness integrated over less than roughly this much audio is not a measurement of anything.
MIN_LOUDNESS_AUDIO_S = 3.0

# A frame is scaled so its longer edge is at most this. 720 still reads a caption on a phone-sized
# frame; a full-resolution 4K master is ten times the bytes and reads the same words.
FRAME_LONG_EDGE_PX = 720
FRAME_SCALE_FILTER = (f"scale='if(gte(iw,ih),min({FRAME_LONG_EDGE_PX},iw),-2)'"
                      f":'if(gte(iw,ih),-2,min({FRAME_LONG_EDGE_PX},ih))'")

# A sandboxed host collects every file the step wrote and refuses the whole step past a file count
# or a total size (in production: 128 files and 16 MiB for the run directory, with the run's other
# files counted too). Both defaults leave room for those. A host that keeps everything can raise
# them; the plan then thins the body the same honest way it does for --budget.
DEFAULT_MAX_FRAMES = 90
DEFAULT_MAX_TOTAL_BYTES = 12 * 1024 * 1024

# Measurement thresholds, the connector's defaults verbatim so one video measured on either path gives
# the same numbers. Scene-change sensitivity 0.15: at 0.30 a six-cut talking-head clip shot in one
# room measured zero cuts, which reads as "one take" rather than as a gap.
SHOT_THRESHOLD = 0.15
SILENCE_FLOOR_DB = -40
SILENCE_MIN_S = 0.5
FREEZE_MIN_S = 1.0
SIGNAL_TIMEOUT_S = 900
# Below this a single shot is an ordinary one-take clip; above it, "no cuts" is worth a second look.
ONE_SHOT_SUSPECT_S = 15.0

# More than this share of the plan missing means the frame track is not a reading of the video any
# more, and the status says so rather than presenting a quarter-sampled video as sampled.
MAX_SKIPPED_SHARE = 0.25


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def readability(path: Path) -> tuple[str, str]:
    """Whether this file can actually be opened here, and why not when it cannot.

    Opening it rather than asking `exists()` or `os.access`, because on a sandboxed host those two
    answer a different question than the one that matters. A host may deny a whole directory tree,
    and a denied path then reports as absent: `is_file()` swallows the permission error and returns
    False. A creator whose video is right where they left it would be told there is no file there,
    which sends them looking for a missing file instead of at the one thing they can act on.

    The three answers are different situations with different advice, so they stay distinct all the
    way out to the message.
    """
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except FileNotFoundError:
        return "not_found", "no file exists at this path"
    except IsADirectoryError:
        return "not_a_file", "this path is a directory, not a video file"
    except PermissionError:
        return "denied", (
            "the file is there, but this host does not allow reading that location. Hosts that "
            "sandbox the agent commonly deny the personal folders whole -- Documents, Desktop, "
            "Downloads, Pictures, Movies, Music -- and external volumes with them. Ask the creator "
            "to put a copy somewhere outside those (a folder they make in their home directory "
            "works) and name that path instead"
        )
    except OSError as error:
        return "unreadable", f"this file could not be opened here: {error}"
    return "ok", ""


def invocation_digest(args: list[str], media: Path) -> str:
    """Hash the argument list with the media path standing in as a placeholder.

    Two machines that ran the same step should produce the same hash, and they will not if the hash
    covers macOS home directories. Substituting the path also keeps a home directory out of an
    artifact that travels downstream -- the same rule the evidence builder already applies to frame
    paths.
    """
    generic = ["<media>" if part == str(media) else part for part in args]
    return hashlib.sha256(json.dumps(generic, ensure_ascii=False).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    """sha256 of a file, read in chunks: a master export can be several gigabytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_tool(name: str, args: list[str], timeout: int) -> subprocess.CompletedProcess:
    if name not in ALLOWED_TOOLS:
        raise ValueError(f"{name!r} is not an allowed tool; this package starts only {sorted(ALLOWED_TOOLS)}")
    return subprocess.run(  # noqa: S603 - argv is a list, shell is never used, timeout is always set
        [name, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def tool_version(name: str) -> str | None:
    try:
        result = run_tool(name, ["-version"], 10)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (result.stdout or result.stderr or "").strip().splitlines()
    return lines[0] if lines else None


def as_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def as_int(value) -> int | None:
    number = as_float(value)
    return int(number) if number is not None else None


def probe(media: Path) -> tuple[dict, dict]:
    """One ffprobe call, read as JSON.

    JSON rather than parsing the human-readable output: that output is written for a person, changes
    between releases, and a regex over it fails silently by matching the wrong line rather than
    loudly by not matching.
    """
    args = ["-v", "error", "-print_format", "json", "-show_format", "-show_streams", "--", str(media)]
    started = now()
    result = run_tool("ffprobe", args, PROBE_TIMEOUT_S)
    step = {
        "step": "local_extraction",
        "operation": "probe_media",
        "tool": "ffprobe",
        "tool_version": tool_version("ffprobe"),
        "invocation_sha256": invocation_digest(args, media),
        "started_at": started,
        "status": "completed" if result.returncode == 0 else "failed",
    }
    if result.returncode != 0:
        step["note"] = (result.stderr or "").strip()[:500]
        return step, {}
    try:
        return step, json.loads(result.stdout)
    except json.JSONDecodeError as error:
        step["status"] = "failed"
        step["note"] = f"ffprobe returned output that is not JSON: {error}"
        return step, {}


def read_media_facts(payload: dict) -> dict:
    """Turn one ffprobe response into the media block's fields.

    Duration is taken from the container first and from the video stream second, because a stream
    duration can be absent on a remuxed file while the container's is right there. If neither is
    present the caller refuses the file: every part of the plan below is a fraction of the length,
    and a plan built on a guessed length is a frame list that cites seconds nothing was read at.
    """
    streams = payload.get("streams") or []
    container = payload.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = as_float(container.get("duration"))
    if duration is None and video is not None:
        duration = as_float(video.get("duration"))

    facts = {
        "duration_s": round(duration, 3) if duration is not None else None,
        "width": as_int(video.get("width")) if video else None,
        "height": as_int(video.get("height")) if video else None,
        "has_audio": audio is not None,
        "encoding": {
            "video_codec": (video or {}).get("codec_name"),
            "video_bitrate_bps": as_int((video or {}).get("bit_rate")) if video else None,
            # Kept as the string ffprobe reports ("30000/1001"): the evidence schema types this as a
            # string, and evaluating the fraction would turn an exact rate into 29.97.
            "frame_rate": (video or {}).get("avg_frame_rate") if video else None,
            "audio_codec": (audio or {}).get("codec_name"),
            "audio_bitrate_bps": as_int((audio or {}).get("bit_rate")) if audio else None,
            "audio_sample_rate": as_int((audio or {}).get("sample_rate")) if audio else None,
            "audio_channels": as_int((audio or {}).get("channels")) if audio else None,
        },
    }
    facts["_exact_duration_s"] = duration
    return facts


def plan_frames(duration_s: float, hook_window_s: float, base_interval_s: float,
                hook_multiplier: int, budget: int) -> dict:
    """Where to sample, and what to record about the choice.

    Dense across the opening because that is where a short-form video is decided, steady through the
    body, and one frame at the close. `body_frames` counts the closing frame, which is why the plan
    reports one more than the body run contains.

    Timestamps are computed from the unrounded interval and only the reported interval is rounded.
    Rounding first and stepping by the rounded value walks the whole body track off by a growing
    amount -- by the end of a minute it is several frames' worth.
    """
    hook_interval_s = base_interval_s / hook_multiplier
    hook_dense_window_s = min(hook_window_s, duration_s)
    hook_frames = int(hook_dense_window_s / hook_interval_s) + 1
    timestamps = [round(index * hook_interval_s, 3) for index in range(hook_frames)]

    remaining_s = duration_s - hook_dense_window_s
    body_frames = int(remaining_s / base_interval_s) if remaining_s > 0 else 0
    body_degraded = False
    if body_frames and hook_frames + body_frames > budget:
        # Only the body gives way. The hook is a fixed cost: sampling it sparsely to afford more of
        # the middle trades the part that decides the video for the part that describes it.
        body_frames = max(1, budget - hook_frames)
        body_degraded = True

    body_interval_s = remaining_s / body_frames if body_frames else 0.0
    timestamps += [round(hook_dense_window_s + index * body_interval_s, 3)
                   for index in range(1, body_frames)]
    closing_frame_s = round(max(0.0, duration_s - CLOSING_OFFSET_S), 3)
    if body_frames:
        timestamps.append(closing_frame_s)

    note = (f"Opening {hook_dense_window_s:g}s sampled every {hook_interval_s:g}s, "
            f"the rest every {round(body_interval_s, 3):g}s, and a closing frame at "
            f"{closing_frame_s:g}s. Sampled on this machine.")
    if body_degraded:
        note += (f" The body was thinned to fit a budget of {budget} frames, so a cut shorter than "
                 f"{round(body_interval_s, 3):g}s may fall between frames.")

    return {
        "base_interval_s": base_interval_s,
        "hook_window_s": hook_window_s,
        "hook_dense_window_s": hook_dense_window_s,
        "hook_interval_s": hook_interval_s,
        "hook_frames": hook_frames,
        "body_interval_s": round(body_interval_s, 3),
        "body_frames": body_frames,
        "closing_frame_s": closing_frame_s,
        "budget": budget,
        "frames": len(timestamps),
        "body_degraded": body_degraded,
        "note": note,
        "_timestamps": timestamps,
    }


def frame_identifier(content_id: str, ordinal: int, seconds: float) -> str:
    """The connector's id shape, with the local file's stem where the platform id would be.

    The shape carries the ordinal and the second, which is what makes a citation readable in a
    report without opening anything. Matching it means a consumer that learned to read one kind of
    frame id does not have to learn a second.
    """
    return f"{content_id}#frame-{ordinal:03d}-{seconds:.3f}s.jpg"


def frame_file_name(frame_id: str) -> str:
    """The file a frame is written to: a hash of its id, never the id itself.

    The id carries `#` and the creator's own file name, which can hold Unicode, brackets or quotes.
    A host that copies the run directory back accepts only plain names and rejects the whole step
    over one it cannot replay -- in production that turned 60 successfully written frames into a
    failed step. Hashing the exact id keeps two files extracted into one directory from colliding,
    which an ordinal-and-second name would not. The evidence builder names connector frames the
    same way for the same reason.
    """
    return "frame-" + hashlib.sha256(frame_id.encode("utf-8")).hexdigest() + ".jpg"


def extraction_order(frame_count: int, hook_frames: int) -> list[int]:
    """The order frames are written in: the opening, the close, then the body coarse-to-fine.

    Written in time order, a step that runs out of room loses the end of the video -- the ending is
    one of the four things a creator asks about before posting. Written in this order, running out
    leaves the hook and the close whole and the body evenly thinner, and the gap the reader has to
    know about is the widest interval between frames, not a missing final third.
    """
    indices = list(range(frame_count))
    hook = indices[:hook_frames]
    closing = [frame_count - 1] if frame_count > hook_frames else []
    body = indices[hook_frames:frame_count - 1] if closing else []
    ordered: list[int] = []
    taken: set[int] = set()
    step = 1
    while step < len(body):
        step *= 2
    while step >= 1:
        for position in range(0, len(body), step):
            if position not in taken:
                taken.add(position)
                ordered.append(body[position])
        step //= 2
    return hook + closing + ordered


def extract(media: Path, out_dir: Path, plan: dict, content_id: str, quality: int,
            max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES) -> tuple[dict, list, list]:
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamps = plan["_timestamps"]
    items: list[dict] = []
    skipped: list[dict] = []
    written_bytes = 0
    started = started_at = now()
    sample_args: list[str] = []

    for index in extraction_order(len(timestamps), plan["hook_frames"]):
        ordinal, seconds = index + 1, timestamps[index]
        is_closing = ordinal == len(timestamps)
        attempts = [seconds]
        if is_closing:
            attempts.append(round(max(0.0, seconds - CLOSING_RETRY_OFFSET_S), 3))

        if written_bytes >= max_total_bytes:
            # Out of room. The rest are skipped rather than written and then refused with the whole
            # step: a thinner frame track is a reading, a refused step is nothing at all.
            skipped.append({"ordinal": ordinal, "t_seconds": seconds,
                            "reason": "the frame byte budget for this step was used up"})
            continue

        written = None
        frame_id = frame_identifier(content_id, ordinal, seconds)
        for attempt in attempts:
            target = out_dir / frame_file_name(frame_id)
            args = ["-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{attempt:.3f}", "-i", str(media),
                    "-frames:v", "1", "-update", "1", "-vf", FRAME_SCALE_FILTER,
                    "-q:v", str(quality), str(target)]
            if not sample_args:
                sample_args = args
            try:
                result = run_tool("ffmpeg", args, FRAME_TIMEOUT_S)
            except (OSError, subprocess.SubprocessError) as error:
                skipped.append({"ordinal": ordinal, "t_seconds": seconds, "reason": str(error)[:300]})
                break
            if result.returncode == 0 and target.exists() and target.stat().st_size > 0:
                written = (target, attempt)
                break
            # An empty or absent output is a skip, not a rename of the next frame: because every
            # frame has its own named output rather than a numbered pattern, one failure here cannot
            # slide every later frame onto a second it was not read at.
            if target.exists() and target.stat().st_size == 0:
                target.unlink()
        if written is None:
            if not any(entry["ordinal"] == ordinal for entry in skipped):
                skipped.append({"ordinal": ordinal, "t_seconds": seconds,
                                "reason": "ffmpeg wrote no frame at this timestamp"})
            continue

        target, _ = written
        written_bytes += target.stat().st_size
        items.append({
            "frame_id": frame_id,
            "_file": target.name,
            "t_seconds": seconds,
            "sha256": file_digest(target),
            "bytes": target.stat().st_size,
            "retained_outside_git": True,
            "path": None,  # filled by the caller, relative to the artifact
        })

    items.sort(key=lambda item: item["t_seconds"])
    skipped.sort(key=lambda entry: entry["ordinal"])
    total = len(timestamps)
    if not items:
        status = "failed"
    elif len(skipped) > total * MAX_SKIPPED_SHARE:
        status = "completed_partial"
    else:
        status = "completed"

    step = {
        "step": "local_extraction",
        "operation": "extract_keyframes",
        "tool": "ffmpeg",
        "tool_version": tool_version("ffmpeg"),
        "invocation_sha256": invocation_digest(sample_args, media) if sample_args else "",
        "started_at": started_at,
        "status": status,
        "note": f"{len(items)} of {total} planned frames written" + (f"; {len(skipped)} skipped" if skipped else ""),
    }
    return step, items, skipped


def measure_loudness(media: Path, duration_s: float | None) -> tuple[dict | None, dict, list]:
    """Integrated loudness and true peak, when the caller asks for them.

    Off by default because it decodes the whole audio track, which on a long video costs more than
    every frame above put together. The numbers are worth having: a true peak above zero is what a
    platform's own re-encode turns into audible distortion, and it is the one reading in this block
    that means the same thing on the master as on the delivered copy.
    """
    limitations: list[str] = []
    if duration_s is not None and duration_s < MIN_LOUDNESS_AUDIO_S:
        limitations.append("The track is too short for an integrated loudness measurement to mean "
                           "anything, so none was taken.")
        return None, {}, limitations

    args = ["-nostdin", "-hide_banner", "-i", str(media), "-map", "0:a:0",
            "-af", "loudnorm=print_format=json", "-f", "null", "-"]
    started = now()
    try:
        result = run_tool("ffmpeg", args, LOUDNESS_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as error:
        limitations.append("Loudness could not be measured on this machine.")
        return None, {"step": "local_extraction", "operation": "measure_loudness", "tool": "ffmpeg",
                      "tool_version": tool_version("ffmpeg"),
                      "invocation_sha256": invocation_digest(args, media), "started_at": started,
                      "status": "failed", "note": str(error)[:300]}, limitations

    step = {
        "step": "local_extraction",
        "operation": "measure_loudness",
        "tool": "ffmpeg",
        "tool_version": tool_version("ffmpeg"),
        "invocation_sha256": invocation_digest(args, media),
        "started_at": started,
        "status": "completed",
    }
    stderr = result.stderr or ""
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end < start:
        step["status"] = "failed"
        limitations.append("Loudness could not be measured on this machine.")
        return None, step, limitations
    try:
        payload = json.loads(stderr[start:end + 1])
    except json.JSONDecodeError:
        step["status"] = "failed"
        limitations.append("Loudness could not be measured on this machine.")
        return None, step, limitations

    return {
        "integrated_loudness_lufs": as_float(payload.get("input_i")),
        "true_peak_dbfs": as_float(payload.get("input_tp")),
    }, step, limitations


def shots_from_cuts(cuts: list[float], duration_s: float) -> list[dict]:
    """Cut times and the length into a shot list; N cuts give N+1 shots."""
    bounds = [0.0, *[cut for cut in cuts if 0 < cut < duration_s], duration_s]
    shots = []
    for start, end in zip(bounds, bounds[1:]):
        if end <= start:
            continue
        shots.append({"index": len(shots), "start_s": round(start, 3), "end_s": round(end, 3),
                      "duration_s": round(end - start, 3)})
    return shots


def parse_spans(stderr: str, start_key: str, end_pattern: str, duration_s: float) -> list[dict]:
    """Start/end pairs out of a detector's log, the one the file ends inside closed at its end.

    The trailing span is the one a reader most wants -- a video that ends in silence or on a held
    frame -- and the detector prints no end line for it because the file stopped first. Here the
    length is measured, so its end is a fact rather than a guess, and the evidence schema wants a
    number there.
    """
    spans: list[dict] = []
    for line in stderr.splitlines():
        start = re.search(start_key + r":\s*(-?[\d.]+)", line)
        if start:
            spans.append({"start_s": max(0.0, round(float(start.group(1)), 3)), "end_s": None,
                          "duration_s": None})
            continue
        end = re.search(end_pattern, line)
        if end and spans and spans[-1]["end_s"] is None:
            spans[-1]["end_s"] = round(float(end.group(1)), 3)
            spans[-1]["duration_s"] = round(spans[-1]["end_s"] - spans[-1]["start_s"], 3)
    if spans and spans[-1]["end_s"] is None:
        spans[-1]["end_s"] = round(duration_s, 3)
        spans[-1]["duration_s"] = round(max(0.0, duration_s - spans[-1]["start_s"]), 3)
    return spans


def measure_signals(media: Path, duration_s: float, has_audio: bool) -> tuple[dict, list, list]:
    """Cuts, silences and held frames, each group standing or falling on its own.

    One group failing leaves that group in `unavailable` and the others measured: a failed silence
    pass is "the sound was not measured", never "there was no silence".
    """
    steps: list[dict] = []
    limitations: list[str] = []
    data: dict = {"duration_s": round(duration_s, 3), "measured": [], "unavailable": []}

    def detect(group: str, filter_flag: str, filter_value: str) -> str | None:
        args = ["-nostdin", "-hide_banner", "-nostats", "-i", str(media),
                filter_flag, filter_value, "-f", "null", "-"]
        started = now()
        try:
            result = run_tool("ffmpeg", args, SIGNAL_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as error:
            result, note = None, str(error)[:300]
        else:
            note = "" if result.returncode == 0 else (result.stderr or "").strip()[-300:]
        step = {"step": "local_extraction", "operation": f"measure_{group}", "tool": "ffmpeg",
                "tool_version": tool_version("ffmpeg"), "invocation_sha256": invocation_digest(args, media),
                "started_at": started, "status": "completed" if not note else "failed"}
        if note:
            step["note"] = note
        steps.append(step)
        return None if note or result is None else (result.stderr or "")

    score = max(1, min(99, round(SHOT_THRESHOLD * 100)))
    log = detect("shots", "-vf", f"scdet=threshold={score}")
    if log is None:
        data["unavailable"].append("shots")
    else:
        cuts = [round(float(hit), 3) for hit in re.findall(r"lavfi\.scd\.time:\s*([\d.]+)", log)]
        shots = shots_from_cuts(cuts, duration_s)
        data["shots"] = {"count": len(shots), "cuts_at_s": cuts, "shots": shots}
        data["measured"].append("shots")
        if len(shots) == 1 and duration_s >= ONE_SHOT_SUSPECT_S:
            limitations.append(
                f"No cuts were detected across {duration_s:.1f}s at sensitivity {SHOT_THRESHOLD}. "
                "Cutting inside one location -- same room, same subject, same light -- often scores "
                "below this, so a single shot here may mean the detector was too blunt rather than "
                "that the video is one take. Check the frames before describing it as uncut.")

    if not has_audio:
        data["unavailable"].append("silence")
        limitations.append("The file carries no audio track, so where speech pauses was not measured.")
    else:
        log = detect("silence", "-af", f"silencedetect=n={SILENCE_FLOOR_DB}dB:d={SILENCE_MIN_S}")
        if log is None:
            data["unavailable"].append("silence")
        else:
            data["silence"] = {"floor_db": SILENCE_FLOOR_DB, "min_s": SILENCE_MIN_S,
                               "spans": parse_spans(log, "silence_start",
                                                    r"silence_end:\s*([\d.]+)", duration_s)}
            data["measured"].append("silence")

    log = detect("freeze", "-vf", f"freezedetect=n=-60dB:d={FREEZE_MIN_S:g}")
    if log is None:
        data["unavailable"].append("freeze")
    else:
        data["freeze"] = {"min_s": FREEZE_MIN_S,
                          "spans": parse_spans(log, r"freezedetect\.freeze_start",
                                               r"freezedetect\.freeze_end:\s*([\d.]+)", duration_s)}
        data["measured"].append("freeze")

    if data["unavailable"]:
        limitations.append("These measurements could not be taken on this machine: "
                           f"{', '.join(data['unavailable'])}. Treat each as unread rather than as "
                           "measured-and-found-nothing.")
    return data, steps, limitations


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample frames from a video on this machine.")
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--hook-window-s", type=float, default=3.0,
                        help="How far in the hook is judged: about 3s for a Short, 15-30 for long-form.")
    parser.add_argument("--base-interval-s", type=float, default=1.0)
    parser.add_argument("--hook-multiplier", type=int, default=2)
    parser.add_argument("--budget", type=int, default=600)
    parser.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES,
                        help="Cap on frames written; the body is thinned to fit, the hook never is.")
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES,
                        help="Stop writing frames once this many bytes are on disk.")
    parser.add_argument("--jpeg-quality", type=int, default=3)
    parser.add_argument("--measure-loudness", action="store_true")
    parser.add_argument("--measure-signals", action="store_true",
                        help="Measure cuts, speech pauses and held frames.")
    parser.add_argument("--dry-run", action="store_true", help="Probe and plan, write no frames.")
    args = parser.parse_args()

    media = args.media.expanduser()
    if not media.is_absolute():
        print(f"ERROR: --media must be an absolute path; got {media}", file=sys.stderr)
        return 2
    state, why = readability(media)
    if state != "ok":
        print(f"ERROR: {why}", file=sys.stderr)
        # A denied path is a host limit, not a mistake the creator made and not a missing file, so
        # it gets its own exit status: the caller reports it as the gap it is and carries on with
        # whatever else the run has.
        return 4 if state == "denied" else 2
    if (args.hook_multiplier < 1 or args.base_interval_s <= 0 or args.budget < 2
            or args.max_frames < 2 or args.max_total_bytes < 1):
        print("ERROR: --hook-multiplier must be at least 1, --base-interval-s positive, "
              "--budget and --max-frames at least 2, --max-total-bytes positive", file=sys.stderr)
        return 2

    probe_step, payload = probe(media)
    steps = [probe_step]
    limitations: list[str] = []
    if probe_step["status"] != "completed":
        print("ERROR: the file's properties could not be read on this machine", file=sys.stderr)
        return 1

    facts = read_media_facts(payload)
    duration = facts.pop("_exact_duration_s")
    if duration is None or duration <= 0:
        print("ERROR: this file states no duration, so there is no length to sample against",
              file=sys.stderr)
        return 1

    content_id = media.stem
    plan = plan_frames(duration, args.hook_window_s, args.base_interval_s,
                       args.hook_multiplier, min(args.budget, args.max_frames))
    timestamps = plan.pop("_timestamps")

    items: list[dict] = []
    skipped: list[dict] = []
    if args.dry_run:
        frames_status = "not_attempted"
    else:
        plan["_timestamps"] = timestamps
        extract_step, items, skipped = extract(media, args.out_dir, plan, content_id, args.jpeg_quality,
                                               args.max_total_bytes)
        plan.pop("_timestamps")
        steps.append(extract_step)
        frames_status = "available" if items else "unavailable"
        artifact_parent = args.artifact.expanduser().resolve().parent
        for item in items:
            # Relative to the artifact, never absolute: this file travels into the evidence object
            # and an operator's home directory has no business travelling with it.
            file_name = item.pop("_file")
            try:
                item["path"] = str((args.out_dir.resolve() / file_name).relative_to(artifact_parent))
            except ValueError:
                item["path"] = str(Path(args.out_dir.name) / file_name)
        over_budget = [entry for entry in skipped if "byte budget" in entry["reason"]]
        unreadable = len(skipped) - len(over_budget)
        if unreadable:
            limitations.append(f"{unreadable} of {len(timestamps)} planned frames could not be "
                               "read from the file, so those seconds were not seen.")
        if over_budget and items:
            seen = [item["t_seconds"] for item in items]
            widest = max((later - earlier for earlier, later in zip(seen, seen[1:])), default=0.0)
            # Only claim what was actually written: a budget used up inside the opening leaves
            # part of the hook, and the closing frame, unseen.
            missing = {entry["ordinal"] for entry in skipped}
            whole = (not any(ordinal <= plan["hook_frames"] for ordinal in missing)
                     and len(timestamps) not in missing)
            coverage = ("The opening and the closing frame are whole; the body is thinner"
                        if whole else
                        "Not all of the opening and closing frames were written, so do not describe "
                        "the whole hook or the ending as seen")
            limitations.append(f"{len(over_budget)} of {len(timestamps)} planned frames were not "
                               f"written because this step's frame byte budget was used up. {coverage}, "
                               f"with up to {widest:.3g}s between written frames, so a cut shorter "
                               "than that may fall between them.")

    if args.measure_loudness and facts["has_audio"] and not args.dry_run:
        loudness, step, notes = measure_loudness(media, duration)
        if step:
            steps.append(step)
        limitations.extend(notes)
        if loudness:
            facts["encoding"].update(loudness)
    elif args.measure_loudness and not facts["has_audio"]:
        limitations.append("The file carries no audio track, so nothing about its sound was measured.")

    if args.measure_signals and not args.dry_run:
        signals, signal_steps, notes = measure_signals(media, duration, facts["has_audio"])
        steps.extend(signal_steps)
        limitations.extend(notes)
        facts["signals"] = signals

    artifact = {
        "schema_version": "local-media-extraction.v1",
        "generated_at": now(),
        "media_path": media.name,
        "content_id": content_id,
        "input_sha256": file_digest(media),
        "tools": {step["tool"]: step["tool_version"] for step in steps},
        "steps": steps,
        "media": facts,
        "frames": {
            "status": frames_status,
            "selection": "duration_scaled_v1",
            "sampling_plan": plan,
            "requested_timestamps": timestamps,
            "items": items,
            "skipped": skipped,
        },
        "limitations": limitations,
    }

    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    args.artifact.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "artifact": str(args.artifact),
        "duration_s": facts["duration_s"],
        "planned": len(timestamps),
        "written": len(items),
        "skipped": len(skipped),
        "frames_status": frames_status,
    }
    if "signals" in facts:
        signals = facts["signals"]
        summary["signals"] = {
            "measured": signals["measured"],
            "unavailable": signals["unavailable"],
            "shots": signals.get("shots", {}).get("count"),
            "silences": len(signals.get("silence", {}).get("spans", [])) if "silence" in signals else None,
            "held_frames": len(signals.get("freeze", {}).get("spans", [])) if "freeze" in signals else None,
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if (args.dry_run or items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
