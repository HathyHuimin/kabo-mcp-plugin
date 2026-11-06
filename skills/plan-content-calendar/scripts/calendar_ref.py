#!/usr/bin/env python3
"""Fill every artifact ref in a Calendar Request with the id and hash of the file it names.

A ref pins one run-local artifact: its path, its id, its schema version and its SHA-256. The model
knows the path and the schema it meant; the id and the 64-character hash are read off the file, and
retyping them is where a Calendar Request went wrong before the validator ever saw it.

Write each ref's `path` and `schema_version` (an asset ref's `asset_id`, `path` and `media_type`),
then:

  python3 scripts/calendar_ref.py content-calendar-request.json [--run-root <run dir>]

Every ref in the Request gets `artifact_id` and `sha256` from its file; every asset ref gets
`sha256`. A value that was already there and disagrees with the file is replaced and reported.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from calendar_common import ARTIFACT_ID_FIELDS, file_sha256, load_json, safe_artifact_path  # noqa: E402

ARTIFACT_KEYS = {"path", "artifact_id", "schema_version", "sha256"}
ASSET_KEYS = {"asset_id", "path", "sha256", "media_type"}


def artifact_file(request_path: Path, run_root: Path, ref_path: str, label: str) -> Path:
    path = safe_artifact_path(request_path, run_root, ref_path)
    if not path.is_file():
        raise ValueError(f"{label}: {ref_path} does not exist yet; write it before its ref")
    return path


def fill(value: Any, request_path: Path, run_root: Path, report: dict[str, list[str]], label: str = "$") -> None:
    if isinstance(value, list):
        for index, child in enumerate(value):
            fill(child, request_path, run_root, report, f"{label}[{index}]")
        return
    if not isinstance(value, dict):
        return
    ref_path = value.get("path")
    if isinstance(ref_path, str) and ref_path and value.get("schema_version") in ARTIFACT_ID_FIELDS \
            and set(value) <= ARTIFACT_KEYS:
        path = artifact_file(request_path, run_root, ref_path, label)
        artifact = load_json(path)
        if artifact.get("schema_version") != value["schema_version"]:
            raise ValueError(f"{label}: {ref_path} is {artifact.get('schema_version')!r}, not {value['schema_version']!r}")
        identifier = artifact.get(ARTIFACT_ID_FIELDS[value["schema_version"]])
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"{label}: {ref_path} carries no {ARTIFACT_ID_FIELDS[value['schema_version']]}")
        derived = {"artifact_id": identifier, "sha256": file_sha256(path)}
    elif isinstance(ref_path, str) and ref_path and "media_type" in value and set(value) <= ASSET_KEYS:
        derived = {"sha256": file_sha256(artifact_file(request_path, run_root, ref_path, label))}
    else:
        for key, child in value.items():
            fill(child, request_path, run_root, report, f"{label}.{key}")
        return
    for key, fresh in derived.items():
        if value.get(key) not in (None, fresh):
            report["corrected"].append(f"{label}.{key}")
        value[key] = fresh
    report["filled"].append(label)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("request", type=Path, help="content-calendar-request.json, rewritten in place")
    parser.add_argument("--run-root", type=Path, default=None)
    args = parser.parse_args()
    try:
        request_path = args.request.resolve()
        run_root = (args.run_root or request_path.parent).resolve()
        request = load_json(request_path)
        if not isinstance(request, dict):
            raise ValueError(f"{args.request} must hold the Request object")
        report: dict[str, list[str]] = {"filled": [], "corrected": []}
        fill(request, request_path, run_root, report)
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", "refs": len(report["filled"]), "corrected": report["corrected"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
