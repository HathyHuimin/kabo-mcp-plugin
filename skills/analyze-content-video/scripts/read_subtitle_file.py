#!/usr/bin/env python3
"""Read a subtitle or text file the creator handed over, and turn it into cues.

A local cut has no platform captions to fetch, and this version of the Skill does not transcribe
audio. That leaves one tier between "the spoken track was read" and "it was not", and it is a good
one: a creator who cut with captions already has the file, and unlike anything a machine hears, the
wording in it is theirs. That is why the evidence records this origin separately -- it is what
licenses quoting a line as what the creator actually says.

What it is not is a measurement of the cut in front of us. A caption file can belong to an earlier
version, or to a different export entirely, and nothing here can tell. The timings are the file's
claim about the video, and the evidence says so beside them.

The refusals mirror the exporter's, because the same reasoning applies in the other direction:

**A file that parses to nothing is a failure, not an empty success.** An `.srt` with no readable
cue, or a `.txt` with no words, is reported with the reason. Recording it as `available` with an
empty cue list would put "this video says nothing" into the evidence when what happened is that a
file could not be read.

**The text is carried verbatim, inline tags included.** It is the creator's own file. Stripping
markup would be editing evidence to look tidier, and a caption that reads `<i>like this</i>` is
telling us something about how it was styled.

**`.vtt` is not advertised.** Its cue payload is close enough to SRT to half-work and different
enough to silently mangle positioning and voice spans, and a half-parsed subtitle file is worse
than a refused one.

    python3 read_subtitle_file.py --subtitle FILE --artifact FILE [--language en]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# hh:mm:ss,mmm and hh:mm:ss.mmm, and the hours field left off, which plenty of editors do on short
# clips. Anything after the end time on the same line -- WebVTT cue settings pasted in, a stray
# comment -- is matched and ignored rather than making the whole block unreadable.
TIME_RE = re.compile(
    r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*"
    r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*(?:\s.*)?$"
)

SUPPORTED_SUFFIXES = {".srt", ".txt"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_seconds(hours: str | None, minutes: str, seconds: str, fraction: str) -> float:
    # A three-digit field is milliseconds; a shorter one is a truncated fraction, so pad rather than
    # read "5" as five milliseconds when the file meant half a second.
    millis = int(fraction.ljust(3, "0"))
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + millis / 1000.0


def parse_srt(text: str) -> tuple[list[dict], list[str]]:
    """Cue list from SRT, tolerant of how real editors write it.

    Blocks are split on blank lines and each is searched for the timing line rather than assumed to
    start with an index: the index line is optional in practice, and a parser that requires it drops
    every cue in a file that omits them.
    """
    notes: list[str] = []
    cues: list[dict] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    for block in blocks:
        lines = [line.rstrip("\r") for line in block.split("\n")]
        timing_at = next((i for i, line in enumerate(lines) if TIME_RE.match(line)), None)
        if timing_at is None:
            if block.strip():
                notes.append(f"a block with no readable timing line was skipped: {block.strip()[:60]!r}")
            continue
        match = TIME_RE.match(lines[timing_at])
        start = to_seconds(match.group(1), match.group(2), match.group(3), match.group(4))
        body = "\n".join(lines[timing_at + 1:]).strip()
        if not body:
            # Same rule the evidence builder applies to fetched cues: a cue with no words is a gap
            # in the speech, not a line that was said.
            continue
        cues.append({"start_s": round(start, 3), "text": body})

    cues.sort(key=lambda cue: cue["start_s"])
    return cues, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Read a creator-supplied subtitle or text file.")
    parser.add_argument("--subtitle", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--language", default=None, help="ISO 639-1 code, when the creator states it.")
    # Who produced the words. A file the creator handed over is their own wording; a transcript a
    # host's speech recognition made of the same upload is a machine's hearing of it, and quoting
    # that as the creator's exact line is the claim the evidence exists to prevent.
    parser.add_argument("--origin", choices=("creator_supplied", "speech_recognition"),
                        default="creator_supplied")
    args = parser.parse_args()

    path = args.subtitle.expanduser()
    # Opened rather than tested with `is_file()`, for the reason the sibling extractor states at
    # length: on a host that denies a directory tree, a denied file reports as absent, and telling a
    # creator their file does not exist sends them after the wrong thing. The check is duplicated
    # rather than shared because these scripts deliberately do not import one another.
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except FileNotFoundError:
        print("ERROR: no file exists at this path", file=sys.stderr)
        return 2
    except IsADirectoryError:
        print("ERROR: this path is a directory, not a subtitle file", file=sys.stderr)
        return 2
    except PermissionError:
        print("ERROR: the file is there, but this host does not allow reading that location. "
              "Hosts that sandbox the agent commonly deny the personal folders whole -- Documents, "
              "Desktop, Downloads, Pictures, Movies, Music. Ask the creator for a copy somewhere "
              "outside those.", file=sys.stderr)
        return 4
    except OSError as error:
        print(f"ERROR: this file could not be opened here: {error}", file=sys.stderr)
        return 2
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        print(f"ERROR: {suffix or 'a file with no extension'} is not read here; "
              f"supply {' or '.join(sorted(SUPPORTED_SUFFIXES))}", file=sys.stderr)
        return 2

    raw = path.read_bytes()
    # A BOM is ordinary on files exported from Windows editors and turns the first index line into
    # something no parser recognises, which loses exactly one cue -- usually the hook.
    text = raw.decode("utf-8-sig", errors="replace")

    source_sha = hashlib.sha256(raw).hexdigest()
    artifact = {
        "schema_version": "local-transcript.v1",
        "generated_at": now(),
        "source_file_name": path.name,
        "source_file_sha256": source_sha,
        "language": args.language,
        "origin": args.origin,
        "cues": None,
        "plain_text": None,
        "notes": [],
        # The acquisition ledger records one entry per step a run took, and reading this file is a
        # step. Without it a run whose only evidence is a subtitle file -- which the local workflow
        # explicitly allows when the media tools are absent -- would build an evidence object with
        # an empty acquisition array, which the schema refuses. The step also makes the read
        # replayable the same way every other step is.
        "steps": [{
            "step": "local_extraction",
            "operation": "read_subtitle_file",
            "tool": "read_subtitle_file",
            "tool_version": "local-transcript.v1",
            # The file name rather than its path: the same rule the sibling extractor follows, so
            # the hash is comparable between machines and carries no home directory.
            "invocation_sha256": hashlib.sha256(
                json.dumps(["read_subtitle_file", "<subtitle>", args.language],
                           ensure_ascii=False).encode("utf-8")).hexdigest(),
            "input_sha256": source_sha,
            "started_at": now(),
            "status": "completed",
        }],
    }

    if suffix == ".srt":
        cues, notes = parse_srt(text)
        artifact["notes"] = notes
        if cues:
            artifact["status"] = "available"
            artifact["granularity"] = "timestamped"
            artifact["cues"] = cues
        else:
            artifact["status"] = "failed"
            artifact["granularity"] = "none"
            artifact["status_basis"] = ("the subtitle file carried no readable cue, so it was not "
                                        "used as a transcript")
    else:
        body = text.strip()
        if body:
            artifact["status"] = "available"
            artifact["granularity"] = "plain_text"
            artifact["plain_text"] = body
        else:
            artifact["status"] = "failed"
            artifact["granularity"] = "none"
            artifact["status_basis"] = "the text file was empty, so it was not used as a transcript"

    if artifact["status"] != "available":
        artifact["steps"][0]["status"] = "failed"
        artifact["steps"][0]["note"] = artifact.get("status_basis")
    if artifact["status"] == "available":
        artifact["status_basis"] = (
            f"read from the subtitle file {path.name!r} the creator supplied; its timings were not "
            "checked against the media"
        )

    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    args.artifact.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "artifact": str(args.artifact),
        "status": artifact["status"],
        "granularity": artifact["granularity"],
        "cues": len(artifact["cues"] or []),
        "characters": len(artifact["plain_text"] or ""),
        "notes": len(artifact["notes"]),
    }, ensure_ascii=False, indent=2))
    return 0 if artifact["status"] == "available" else 1


if __name__ == "__main__":
    raise SystemExit(main())
