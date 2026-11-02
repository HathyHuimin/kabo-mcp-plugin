#!/usr/bin/env python3
"""Assemble this run's history Envelopes into the public snapshot the projection reads.

The shared assembler takes one repeatable `--envelope` per file, which a fixed pipeline cannot
express, so this step used to be a command of its own: the model listed the files, ran it, then
read the result back. Two things went wrong often enough to be worth a script. It passed the
Envelope that names the account along with the history, and the assembler recognises
`resolve_profile` as a profile response but not `get_channel`, so a YouTube run came out carrying
`Skipped 1 row(s) with no resolvable content id, creator or publish time` -- a sentence about an
unreadable post, in a snapshot whose posts were all readable. And it chose where to write: three
of the seventeen runs on this machine put the snapshot in `snapshot/` while the projection reads
`analysis/`, which is a broken run that looks like a missing file.

Here the history Envelopes are selected by what they are, the destination is fixed, and what the
next step reads is printed, so nothing has to be listed, chosen or read back.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

SCRIPTS = Path(__file__).resolve().parent
BUILDER = SCRIPTS / "build_public_snapshot.py"
# Responses that name an account rather than list its posts. They carry no publish times, so the
# assembler counts each one as a row it could not read and says so in the snapshot's limitations.
PROFILE_OPERATIONS = {"resolve_profile", "resolve_channel", "check_handle", "get_channel", "get_profile"}


def envelope_of(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {}
    inner = document.get("envelope")
    return inner if isinstance(inner, dict) else document


def history_envelopes(snapshot_dir: Path) -> tuple[list[Path], list[tuple[str, str]]]:
    """Every response in this run's snapshot directory that lists posts, in file-name order.

    Names are read, not assumed: runs on this machine wrote `envelope-01.json`, `history-envelope.json`
    and `profile-envelope.json`, so a pattern that only knows the numbered form finds nothing in two
    of the three. What decides is the operation the Envelope records.
    """
    kept: list[Path] = []
    dropped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in sorted(snapshot_dir.glob("*.json")):
        try:
            envelope = envelope_of(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            dropped.append((path.name, "unreadable"))
            continue
        operation = str(envelope.get("operation") or "")
        if not operation:
            dropped.append((path.name, "no operation recorded"))
            continue
        if operation in PROFILE_OPERATIONS:
            dropped.append((path.name, operation))
            continue
        # One response, however many files hold it: a run that placed the same page twice would
        # otherwise have its posts counted twice.
        identity = str(envelope.get("request_id") or envelope.get("raw_sha256") or path.name)
        if identity in seen:
            dropped.append((path.name, "duplicate of an Envelope already taken"))
            continue
        seen.add(identity)
        kept.append(path)
    return kept, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble the history Envelopes into this run's public snapshot")
    parser.add_argument("--snapshot-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--platform", required=True, choices=("youtube", "instagram", "tiktok"))
    parser.add_argument("--window", default="auto")
    parser.add_argument("--plugin-root", default=None)
    args = parser.parse_args()

    if not args.snapshot_dir.is_dir():
        raise SystemExit(f"{args.snapshot_dir} is not a directory; persist this run's Envelopes first")
    kept, dropped = history_envelopes(args.snapshot_dir)
    if not kept:
        raise SystemExit(
            f"no history Envelope in {args.snapshot_dir}: every file there names an account rather "
            f"than listing its posts, so there is no publish history to project")

    if not BUILDER.is_file():
        raise SystemExit("This skill package is incomplete: scripts/build_public_snapshot.py is missing; "
                         "reload a complete verified package")
    command = [sys.executable, str(BUILDER), "--platform", args.platform, "--window", args.window,
               "--output", str(args.output)]
    for path in kept:
        command += ["--envelope", str(path)]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        return completed.returncode

    try:
        snapshot = json.loads(args.output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"the assembler wrote {args.output} but it cannot be read back: {error}")
    # What the projection reads next, so it does not have to be read back.
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "kept": [path.name for path in kept],
        "dropped": [{"file": name, "because": why} for name, why in dropped],
        "items": len(snapshot.get("items") or []),
        "creators": len(snapshot.get("creators") or []),
        "limitations": (snapshot.get("source") or {}).get("limitations") or [],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
