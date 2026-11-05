#!/usr/bin/env python3
"""Shared, dependency-free contract helpers for plan-content-calendar."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REQUEST_SCHEMA = PACKAGE_ROOT / "references" / "content-calendar-request.schema.json"
CALENDAR_SCHEMA = PACKAGE_ROOT / "references" / "content-calendar.schema.json"
# Upstream contracts ship flat beside this Skill's own schemas, prefixed upstream-.
UPSTREAM_SCHEMA_DIR = PACKAGE_ROOT / "references"

ARTIFACT_ID_FIELDS = {
    "selected-idea.v2": "selected_idea_id",
    "idea-set.v3-candidate1": "idea_set_id",
    "script-package-request.v1": "request_id",
    "script-package.v1": "package_id",
    "publish-request.v1": "request_id",
    "publish-history.v2": "history_id",
    "publish-recommendation.v2": "recommendation_id",
    "publish-schedule.v1": "schedule_id",
    "creator-profile.v3": "profile_id",
    "content-calendar.v1": "calendar_id",
    "content-calendar-request.v1": "request_id",
}

UPSTREAM_SCHEMAS = {
    "selected-idea.v2": UPSTREAM_SCHEMA_DIR / "upstream-selected-idea.v2.schema.json",
    "idea-set.v3-candidate1": UPSTREAM_SCHEMA_DIR / "upstream-idea-set.v3-candidate1.schema.json",
    "script-package-request.v1": UPSTREAM_SCHEMA_DIR / "upstream-script-package-request.v1.schema.json",
    "script-package.v1": UPSTREAM_SCHEMA_DIR / "upstream-script-package.v1.schema.json",
    "publish-request.v1": UPSTREAM_SCHEMA_DIR / "upstream-publish-request.v1.schema.json",
    "publish-history.v2": UPSTREAM_SCHEMA_DIR / "upstream-publish-history.v2.schema.json",
    "publish-recommendation.v2": UPSTREAM_SCHEMA_DIR / "upstream-publish-recommendation.v2.schema.json",
    "publish-schedule.v1": UPSTREAM_SCHEMA_DIR / "upstream-publish-schedule.v1.schema.json",
    "creator-profile.v3": UPSTREAM_SCHEMA_DIR / "upstream-creator-profile.v3.schema.json",
    "content-calendar.v1": CALENDAR_SCHEMA,
    "content-calendar-request.v1": REQUEST_SCHEMA,
}


# A numbered clone is the same content with an episode marker appended, and creators write that
# marker in their own language. Keep every existing branch and match the common English words as
# well, so the semantic key does not depend on the title being Chinese. A bare trailing number is
# never a suffix here: a meaningful year must survive.
_ENUMERATION_SUFFIX = re.compile(
    r"""\s*(?:
        [#＃]\s*\d+
        | 第\s*\d+\s*(?:期|条)
        | [（(]\s*\d+\s*[）)]
        | \b(?:part|pt|ep|episode|no|vol|volume)\s*\.?\s*\d+
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def _normalized_semantic_text(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip().casefold()


def selected_idea_content_preview(selected: dict[str, Any]) -> dict[str, Any]:
    """Copy the minimum creator-readable content identity from SelectedIdea."""

    idea = selected.get("idea") or {}
    hook = idea.get("hook_preview") or {}
    return {
        "audience_problem": idea.get("audience_problem"),
        "content_promise": idea.get("content_promise"),
        "angle": idea.get("angle"),
        "hook": {
            "opening_line": hook.get("opening_line"),
            "progressive_line": hook.get("progressive_line"),
            "mechanism": hook.get("mechanism"),
        },
    }


def selected_idea_semantic_key(selected: dict[str, Any]) -> str:
    """Return a stable key that catches numbered clones without erasing meaningful numbers."""

    idea = selected.get("idea") or {}
    title = _normalized_semantic_text(idea.get("working_title"))
    title = _ENUMERATION_SUFFIX.sub("", title).strip()
    outline = [
        {
            "beat": _normalized_semantic_text(entry.get("beat")),
            "purpose": _normalized_semantic_text(entry.get("purpose")),
        }
        for entry in idea.get("content_outline") or []
    ]
    payload = {
        "working_title": title,
        "audience_problem": _normalized_semantic_text(idea.get("audience_problem")),
        "content_promise": _normalized_semantic_text(idea.get("content_promise")),
        "angle": _normalized_semantic_text(idea.get("angle")),
        "hook_preview": {
            key: _normalized_semantic_text((idea.get("hook_preview") or {}).get(key))
            for key in ("opening_line", "progressive_line", "mechanism")
        },
        "content_outline": outline,
        "differentiation": _normalized_semantic_text(idea.get("differentiation")),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def planning_end(planning: dict[str, Any]) -> date:
    """Inclusive end; an absent end preserves the original four-week contract."""
    start = date.fromisoformat(planning["start_date"])
    if planning.get("end_date") is not None:
        return date.fromisoformat(planning["end_date"])
    return start + timedelta(days=planning["weeks"] * 7 - 1)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _schema_ref(root: dict[str, Any], value: str) -> dict[str, Any]:
    prefix = "#/$defs/"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError(f"unsupported schema ref: {value!r}")
    target: Any = root.get("$defs", {})
    for part in value[len(prefix):].split("/"):
        if not isinstance(target, dict) or part not in target:
            raise ValueError(f"unresolved schema ref: {value}")
        target = target[part]
    if not isinstance(target, dict):
        raise ValueError(f"schema ref is not an object: {value}")
    return target


def _valid_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def validate_schema_value(
    value: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the JSON-Schema subset used by this package without dependencies."""

    errors: list[str] = []
    if "$ref" in schema:
        errors.extend(validate_schema_value(value, _schema_ref(root, schema["$ref"]), root, path))
    for branch in schema.get("allOf") or []:
        errors.extend(validate_schema_value(value, branch, root, path))
    if schema.get("oneOf") is not None:
        results = [validate_schema_value(value, branch, root, path) for branch in schema["oneOf"]]
        if sum(not result for result in results) != 1:
            errors.append(f"{path}: must match exactly one allowed shape")
    if "if" in schema:
        condition_matches = not validate_schema_value(value, schema["if"], root, path)
        branch = schema.get("then") if condition_matches else schema.get("else")
        if isinstance(branch, dict):
            errors.extend(validate_schema_value(value, branch, root, path))

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']!r}")

    declared_type = schema.get("type")
    if declared_type is not None:
        expected = [declared_type] if isinstance(declared_type, str) else declared_type
        if not any(_type_matches(value, item) for item in expected):
            errors.append(f"{path}: expected type {expected!r}")
            return errors

    if isinstance(value, dict):
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(f"{path}: needs at least {schema['minProperties']} properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(f"{path}: allows at most {schema['maxProperties']} properties")
        for field in schema.get("required") or []:
            if field not in value:
                errors.append(f"{path}: missing required property {field!r}")
        properties = schema.get("properties") or {}
        additional = schema.get("additionalProperties")
        if additional is False:
            for field in value:
                if field not in properties:
                    errors.append(f"{path}: additional property {field!r} is not allowed")
        elif isinstance(additional, dict):
            for field, child in value.items():
                if field not in properties:
                    errors.extend(validate_schema_value(child, additional, root, f"{path}.{field}"))
        for field, child_schema in properties.items():
            if field in value:
                errors.extend(validate_schema_value(value[field], child_schema, root, f"{path}.{field}"))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: allows at most {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(canonical) != len(set(canonical)):
                errors.append(f"{path}: items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_schema_value(item, item_schema, root, f"{path}[{index}]"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            errors.append(f"{path}: string does not match {schema['pattern']!r}")
        if schema.get("format") == "date" and not _valid_date(value):
            errors.append(f"{path}: must be an ISO date")
        if schema.get("format") == "date-time" and not _valid_datetime(value):
            errors.append(f"{path}: must be an offset-aware ISO date-time")
        if schema.get("format") == "uri":
            parsed = urlparse(value)
            if not parsed.scheme:
                errors.append(f"{path}: must be an absolute URI")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value is above {schema['maximum']}")
    return errors


def validate_against_schema(value: Any, schema_path: Path) -> list[str]:
    schema = load_json(schema_path)
    return validate_schema_value(value, schema, schema)


def safe_artifact_path(request_path: Path, run_root: Path, ref_path: str) -> Path:
    """Resolve a run-local ref and reject absolute or escaping paths."""

    value = Path(ref_path)
    if value.is_absolute():
        raise ValueError(f"absolute artifact paths are not allowed: {ref_path}")
    root = run_root.resolve()
    candidate = (request_path.parent / value).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"artifact path escapes run root: {ref_path}")
    return candidate


def load_artifact_ref(
    ref: dict[str, Any],
    request_path: Path,
    run_root: Path,
    expected_schema: str,
    label: str,
    errors: list[str],
) -> tuple[Path | None, dict[str, Any] | None]:
    if not isinstance(ref, dict):
        errors.append(f"{label}: artifact reference must be an object")
        return None, None
    try:
        path = safe_artifact_path(request_path, run_root, str(ref.get("path", "")))
    except ValueError as error:
        errors.append(f"{label}: {error}")
        return None, None
    if not path.is_file():
        errors.append(f"{label}: artifact path not found: {ref.get('path')}")
        return path, None
    try:
        artifact = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"{label}: invalid JSON artifact: {type(error).__name__}: {error}")
        return path, None
    if not isinstance(artifact, dict):
        errors.append(f"{label}: artifact root must be an object")
        return path, None
    require(ref.get("schema_version") == expected_schema, f"{label}: ref schema_version must be {expected_schema}", errors)
    require(artifact.get("schema_version") == expected_schema, f"{label}: artifact schema_version must be {expected_schema}", errors)
    id_field = ARTIFACT_ID_FIELDS[expected_schema]
    require(ref.get("artifact_id") == artifact.get(id_field), f"{label}: artifact_id mismatch", errors)
    require(ref.get("sha256") == file_sha256(path), f"{label}: sha256 mismatch", errors)
    upstream_schema = UPSTREAM_SCHEMAS.get(expected_schema)
    if upstream_schema is not None:
        try:
            errors.extend(
                f"{label}: upstream schema {error}"
                for error in validate_against_schema(artifact, upstream_schema)
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"{label}: upstream schema could not be evaluated: {type(error).__name__}: {error}")
    return path, artifact


def validate_asset_ref(
    ref: dict[str, Any],
    request_path: Path,
    run_root: Path,
    label: str,
    errors: list[str],
) -> Path | None:
    if not isinstance(ref, dict):
        errors.append(f"{label}: asset reference must be an object")
        return None
    try:
        path = safe_artifact_path(request_path, run_root, str(ref.get("path", "")))
    except ValueError as error:
        errors.append(f"{label}: {error}")
        return None
    if not path.is_file():
        errors.append(f"{label}: asset path not found: {ref.get('path')}")
        return path
    require(ref.get("sha256") == file_sha256(path), f"{label}: sha256 mismatch", errors)
    return path


def request_input_paths(request: dict[str, Any], request_path: Path, run_root: Path) -> set[Path]:
    paths = {request_path.resolve()}

    def add_nested_path(parent_path: Path, raw_path: object) -> None:
        try:
            paths.add(safe_artifact_path(parent_path, run_root, str(raw_path or "")))
        except (AttributeError, ValueError):
            pass

    refs: list[dict[str, Any]] = list(request.get("candidate_idea_set_refs") or [])
    single_slot = request.get("single_slot_decision") or {}
    for ref in (single_slot.get("pending_request_ref"), single_slot.get("proposal_ref")):
        if ref is not None:
            refs.append(ref)
    for item in request.get("items") or []:
        refs.append(item.get("selected_idea_ref") or {})
        if item.get("script_package_request_ref"):
            refs.append(item["script_package_request_ref"])
        if item.get("script_package_ref"):
            refs.append(item["script_package_ref"])
        timing = item.get("timing") or {}
        for name in ("publish_request_ref", "publish_history_ref", "publish_recommendation_ref", "publish_schedule_ref"):
            if timing.get(name):
                refs.append(timing[name])
        assets = item.get("publication_assets") or {}
        refs.extend(assets.get("media_refs") or [])
        if assets.get("cover_ref"):
            refs.append(assets["cover_ref"])
    for ref in refs:
        try:
            paths.add(safe_artifact_path(request_path, run_root, str(ref.get("path", ""))))
        except (AttributeError, ValueError):
            continue
    # Protect artifacts referenced by upstream request artifacts too. The
    # exporter writes before its final parity check, so omitting these paths
    # would let a malicious output name destroy an input and only then fail.
    for item in request.get("items") or []:
        package_request_ref = item.get("script_package_request_ref")
        if isinstance(package_request_ref, dict):
            package_request = None
            package_request_path = request_path
            try:
                package_request_path = safe_artifact_path(
                    request_path, run_root, str(package_request_ref.get("path", ""))
                )
                package_request = load_json(package_request_path)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            if isinstance(package_request, dict):
                nested_refs = [package_request.get("selected_idea")]
                nested_refs.extend(
                    package_request.get(field)
                    for field in ("creator_profile", "source_script_package")
                    if package_request.get(field)
                )
                nested_refs.extend(package_request.get("video_analysis_sources") or [])
                for nested_ref in nested_refs:
                    if isinstance(nested_ref, dict):
                        add_nested_path(package_request_path, nested_ref.get("path"))
        publish_request_ref = (item.get("timing") or {}).get("publish_request_ref")
        if isinstance(publish_request_ref, dict):
            publish_request = None
            publish_request_path = request_path
            try:
                publish_request_path = safe_artifact_path(
                    request_path, run_root, str(publish_request_ref.get("path", ""))
                )
                publish_request = load_json(publish_request_path)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            if isinstance(publish_request, dict):
                nested_refs = [
                    (publish_request.get("content") or {}).get("source_idea_ref"),
                    (publish_request.get("content") or {}).get("source_script_ref"),
                    (publish_request.get("creator") or {}).get("profile_ref"),
                ]
                for nested_ref in nested_refs:
                    if isinstance(nested_ref, dict):
                        add_nested_path(publish_request_path, nested_ref.get("path"))
                history_path = (publish_request.get("history") or {}).get("history_ref")
                if history_path:
                    add_nested_path(publish_request_path, history_path)
    return paths


def safe_output_path(value: Path, run_root: Path, protected: set[Path], label: str) -> Path:
    root = run_root.resolve()
    path = value.resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"{label} must stay inside the run root")
    if path in protected:
        raise ValueError(f"{label} must not overwrite an input artifact")
    if path.exists() or path.is_symlink():
        raise ValueError(f"{label} must be a new path; refusing to overwrite an existing file")
    return path


def write_new_bytes(path: Path, value: bytes) -> None:
    """Create a new regular output without following links or overwriting bytes."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def parse_offset_datetime(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("date-time must be a string with a UTC offset")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("date-time must include a UTC offset")
    return parsed


def datetime_matches_zone(value: str, zone_name: str) -> bool:
    """Reject impossible or ambiguous wall-clock/offset pairs for the declared zone."""

    try:
        parsed = parse_offset_datetime(value)
        zone = get_zone(zone_name)
    except (TypeError, ValueError):
        return False
    local = parsed.astimezone(zone)
    return (
        local.replace(tzinfo=None) == parsed.replace(tzinfo=None)
        and local.utcoffset() == parsed.utcoffset()
    )


def parse_hhmm(value: str) -> time:
    return time.fromisoformat(value)


def get_zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise ValueError(f"invalid IANA timezone: {value!r}") from error


def recommendation_contains_instant(recommendation: dict[str, Any], instant: datetime) -> bool:
    if recommendation.get("outcome") != "recommended":
        return False
    windows = ((recommendation.get("recommendation") or {}).get("windows") or [])
    for window in windows:
        try:
            zone = get_zone(window.get("zone"))
            local = instant.astimezone(zone)
            start = parse_hhmm(window.get("start_local"))
            end = parse_hhmm(window.get("end_local"))
        except (TypeError, ValueError):
            continue
        weekday = window.get("weekday")
        weekday_labels = {
            "monday": 1, "mon": 1, "周一": 1,
            "tuesday": 2, "tue": 2, "周二": 2,
            "wednesday": 3, "wed": 3, "周三": 3,
            "thursday": 4, "thu": 4, "周四": 4,
            "friday": 5, "fri": 5, "周五": 5,
            "saturday": 6, "sat": 6, "周六": 6,
            "sunday": 7, "sun": 7, "周日": 7,
        }
        named_weekday = weekday_labels.get(weekday.casefold()) if isinstance(weekday, str) else None
        weekday_matches = (
            weekday == "all"
            or weekday == "weekday" and local.weekday() < 5
            or weekday == "weekend" and local.weekday() >= 5
            or isinstance(weekday, int) and weekday == local.isoweekday()
            or named_weekday == local.isoweekday()
        )
        current = local.timetz().replace(tzinfo=None)
        if end == start:
            continue
        if end > start:
            time_matches = start <= current < end
        else:
            time_matches = current >= start or current < end
        if weekday_matches and time_matches:
            return True
    return False


def artifact_key(ref: dict[str, Any] | None) -> str | None:
    return ref.get("artifact_id") if isinstance(ref, dict) else None
