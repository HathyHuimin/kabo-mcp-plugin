"""Compile source-bound author choices; availability is never a viewing receipt."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.dont_write_bytecode = True
from baseline_availability import METRICS
from point_observations import finite_seconds
from synthesis_contract import synthesis_schema, synthesis_seed

AUTHOR_VERSION = "account-deep-authoring.v1"
BASIS_VERSION = "deep-authoring-basis.v1"
SOURCE_KEYS = {"evidence", "watch_set", "account_analysis", "stability"}


def _unique(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = child
    return value


def load_strict(path: Path) -> Any:
    def invalid(value):
        raise ValueError(f"nonfinite JSON constant: {value}")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique, parse_constant=invalid)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _projection_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _inside(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def require_open_current_run() -> Path:
    directory = Path.cwd().resolve()
    # A real host manifest is authoritative; Web does not need an invented one.
    for parent in (directory, *directory.parents):
        manifest = parent / "run-manifest.json"
        if manifest.exists():
            row = load_strict(manifest)
            if row.get("status") != "running":
                raise ValueError("source-bound authoring requires an open running run")
            if row.get("skill_id", "review-creator-account-deep") != "review-creator-account-deep":
                raise ValueError("source-bound authoring belongs to a different skill")
            break
    return directory


def make_basis(payload: dict[str, Any], output: Path, paths: dict[str, Path]) -> dict[str, Any]:
    directory = require_open_current_run()
    if set(paths) != SOURCE_KEYS or not _inside(output.resolve(), directory):
        raise ValueError("authoring basis needs the original four sources and a current-run output")
    sources = {}
    originals = {}
    for name, path in paths.items():
        actual = path.resolve(strict=True)
        if name != "account_analysis" and not _inside(actual, directory):
            raise ValueError(f"{name} must belong to the current run")
        # 本 run 内的来源记相对路径：Web 每条命令挂在不同的绝对目录下，同一个 run 的绝对路径会变。
        stored = str(actual.relative_to(directory)) if _inside(actual, directory) else str(actual)
        sources[name] = {"path": stored, "sha256": _digest(actual)}
        originals[name] = load_strict(actual)
    validate_source_identity(originals, payload)
    point_index(originals["evidence"])
    return {"schema_version": BASIS_VERSION, "run_dir": directory.name,
            "projection_sha256": _projection_digest(payload), "sources": sources}


def validate_source_identity(originals: dict[str, Any], source: dict[str, Any]) -> None:
    identity = source.get("analysis_id")
    if not isinstance(identity, str) or not identity.strip() or any(
            not isinstance(row, dict) or row.get("analysis_id") != identity for row in originals.values()):
        raise ValueError("all original source analysis_ids must match")
    platform = source.get("platform")
    if not isinstance(platform, str) or not platform.strip():
        raise ValueError("source platform is required")
    for name, row in originals.items():
        if (name in {"watch_set", "account_analysis"} or "platform" in row) and row.get("platform") != platform:
            raise ValueError("original source platforms must match")
    if originals["stability"].get("schema_version") != "catalog-stability.v1":
        raise ValueError("original catalog stability schema is required")


def load_authoring(path: Path) -> dict[str, Any]:
    if not _inside(path.resolve(strict=True), require_open_current_run()):
        raise ValueError("authoring file must belong to the current run")
    return load_strict(path)


def bound_sources(source_path: Path, source: dict[str, Any], *, evidence_path: Path | None = None,
                  stability_path: Path | None = None) -> dict[str, Any]:
    basis = source.get("authoring_basis")
    if not isinstance(basis, dict) or set(basis) != {"schema_version", "run_dir", "projection_sha256", "sources"}:
        raise ValueError("new authoring requires a complete generated authoring_basis")
    directory = require_open_current_run()
    # run 按名字认（新写法）或按旧的绝对路径认；字节与投影仍逐项核对。
    if basis["schema_version"] != BASIS_VERSION or basis["run_dir"] not in (directory.name, str(directory)):
        raise ValueError("authoring_basis is not bound to this current run")
    if not _inside(source_path.resolve(strict=True), directory):
        raise ValueError("synthesis input must belong to the current run")
    projection = {key: value for key, value in source.items() if key != "authoring_basis"}
    if _projection_digest(projection) != basis["projection_sha256"]:
        raise ValueError("synthesis input projection changed after generation")
    sources = basis["sources"]
    if not isinstance(sources, dict) or set(sources) != SOURCE_KEYS:
        raise ValueError("authoring_basis sources are incomplete")
    loaded = {}
    for name in sorted(SOURCE_KEYS):
        row = sources[name]
        if not isinstance(row, dict) or set(row) != {"path", "sha256"} or not isinstance(row["path"], str):
            raise ValueError("invalid authoring source binding")
        relative = not Path(row["path"]).is_absolute()
        actual = (directory / row["path"] if relative else Path(row["path"])).resolve(strict=True)
        if (not relative and str(actual) != row["path"]) or (relative and not _inside(actual, directory)) or \
                (name != "account_analysis" and not _inside(actual, directory)):
            raise ValueError("authoring source path left its original run")
        if _digest(actual) != row["sha256"]:
            raise ValueError(f"original {name} bytes changed")
        supplied = {"evidence": evidence_path, "stability": stability_path}.get(name)
        if supplied is not None and supplied.resolve(strict=True) != actual:
            raise ValueError(f"{name} differs from the bound original path")
        loaded[name] = load_strict(actual)
    validate_source_identity(loaded, source)
    from build_deep_synthesis_input import build_deep_synthesis_input
    expected = build_deep_synthesis_input(loaded["evidence"], loaded["watch_set"],
                                         user_question=source.get("user_question"),
                                         account_analysis=loaded["account_analysis"])
    if expected != projection:
        raise ValueError("synthesis input does not match its original source projection")
    if loaded["stability"].get("analysis_id") != source["analysis_id"]:
        raise ValueError("catalog stability has a different analysis_id")
    return loaded


def point_index(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index = {}
    ids = set()
    for item in evidence.get("items", []):
        if not item.get("watched"):
            continue
        cid = item.get("content_id")
        if not isinstance(cid, str) or not cid.strip() or cid in ids:
            raise ValueError("watched videos need unique content_ids")
        ids.add(cid)
        frame_ids = set()
        for frame in (item.get("frames") or {}).get("items", []):
            fid = frame.get("frame_id")
            if not isinstance(fid, str) or not fid.strip() or fid in frame_ids or not finite_seconds(frame.get("t_seconds")):
                raise ValueError("frames need unique canonical IDs and finite source seconds")
            frame_ids.add(fid)
            ref = f"frame:{quote(cid, safe='')}:{quote(fid, safe='')}"
            index[ref] = {"content_id": cid, "kind": "frame", "start_s": frame["t_seconds"],
                          "frame_id": fid, "object_ref": frame.get("path")}
        for number, cue in enumerate((item.get("transcript") or {}).get("cues", [])):
            if not finite_seconds(cue.get("start_s")) or not isinstance(cue.get("text"), str):
                raise ValueError("cues need their literal source text and finite source seconds")
            ref = f"cue:{quote(cid, safe='')}:{number:06d}"
            index[ref] = {"content_id": cid, "kind": "cue", "start_s": cue["start_s"],
                          "quote": cue["text"], "end_s": cue.get("end_s")}
    # The legacy strict validator identifies a point by (video, kind, seconds).
    # Ref-specific choices must not collapse different original points onto that key.
    keys = set()
    for point in index.values():
        key = (point["content_id"], point["kind"], point["start_s"])
        if key in keys:
            raise ValueError("ambiguous source points share a canonical video/kind/second")
        keys.add(key)
    return index


def authoring_schema() -> dict[str, Any]:
    return load_strict(Path(__file__).resolve().parent.parent / "references/account_deep_authoring.schema.json")


def authoring_seed(source: dict[str, Any], language: str, stability: dict[str, Any]) -> dict[str, Any]:
    seed = synthesis_seed(source, language, stability)
    for key in ("analysis_id", "data_gaps", "video_observations"):
        seed.pop(key, None)
    seed.update(schema_version=AUTHOR_VERSION, observations={}, other_limitations=[])
    return seed


def _closed(value: Any, allowed: set[str], required: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise ValueError(f"{label} has missing or unsupported author fields")


def compile_authoring(author: dict[str, Any], source: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    schema = authoring_schema()
    _closed(author, set(schema["properties"]), set(schema["required"]), "authoring")
    if author["schema_version"] != AUTHOR_VERSION:
        raise ValueError("unsupported authoring schema_version")
    points = point_index(evidence)
    selected = author["observations"]
    if not isinstance(selected, dict):
        raise ValueError("observations must select canonical point refs")
    rows = {item["content_id"]: {"content_id": item["content_id"], "cue_samples": [], "frame_samples": []}
            for item in evidence["items"] if item.get("watched")}
    for ref, value in selected.items():
        if ref not in points:
            raise ValueError(f"unknown same-video point_ref: {ref}")
        point = points[ref]
        if point["kind"] == "frame":
            _closed(value, {"description"}, {"description"}, "frame observation")
            rows[point["content_id"]]["frame_samples"].append(
                {"start_s": point["start_s"], "description": value["description"]})
        else:
            _closed(value, set(), set(), "cue observation")
            rows[point["content_id"]]["cue_samples"].append(
                {key: point[key] for key in ("start_s", "quote", "end_s")})

    def pins(values):
        if not isinstance(values, list):
            raise ValueError("pins must be canonical point_ref strings")
        result = []
        for ref in values:
            if not isinstance(ref, str) or ref not in points:
                raise ValueError("pin has an unknown same-video point_ref")
            result.append({key: points[ref][key] for key in ("content_id", "kind", "start_s")})
        return result

    canonical = {key: copy.deepcopy(value) for key, value in author.items()
                 if key not in {"schema_version", "observations", "other_limitations"}}
    canonical.update(schema_version="account-deep-synthesis.v1", analysis_id=source["analysis_id"],
                     video_observations=list(rows.values()))
    for category in ("repeated_strengths", "repeated_drags"):
        if not isinstance(author[category], list):
            raise ValueError("patterns must be an array")
        for pattern in canonical[category]:
            _closed(pattern, {"pattern", "explanation", "pins"}, {"pattern", "explanation", "pins"}, "pattern")
            pattern["pins"] = pins(pattern["pins"])
    _closed(canonical["next_shoot"], {"keep", "stop", "pins"}, {"keep", "stop"}, "next_shoot")
    if "pins" in canonical["next_shoot"]:
        canonical["next_shoot"]["pins"] = pins(canonical["next_shoot"]["pins"])
    details = author["other_limitations"]
    if not isinstance(details, list) or any(not isinstance(text, str) or not text.strip() for text in details):
        raise ValueError("other_limitations must contain nonblank authored text")
    availability = source["baseline_availability"]
    canonical["data_gaps"] = [{"metric": metric, "availability": "not_carried"} for metric in METRICS
                              if availability[metric]["status"] == "not_carried"]
    canonical["data_gaps"] += [{"detail": text} for text in details]
    # Existing canonical contract and source/point validators remain authoritative in compose.
    return canonical


def create_authoring_seed(path: Path, seed: dict[str, Any]) -> None:
    directory = require_open_current_run()
    if not _inside(path.resolve(), directory):
        raise ValueError("authoring output must belong to the current run")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(seed, ensure_ascii=False, indent=2) + "\n")
