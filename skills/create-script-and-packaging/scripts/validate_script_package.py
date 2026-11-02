#!/usr/bin/env python3
"""Semantic validator for create-script-and-packaging contracts (stdlib only)."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from creative_evidence import load_verified
from script_duration import validate_plan, validate_fit


def supplementary_facts(request, request_path, errors, run_root=None):
    """Validate captured source lineage and return separate claim-level evidence."""
    if request.get('supplemental_evidence') is None:
        return {}
    try:
        evidence = load_verified(request['supplemental_evidence'], request_path, run_root)
        return {fact['id']: fact for fact in evidence['facts']}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        errors.append(f'Supplementary evidence invalid: {error}')
        return {}


def validate_supplementary_surfaces(package, supplementary, supplementary_supplied, errors):
    """Check explicit surface-to-ledger links, including non-numeric packaging facts."""
    claims = {c.get('claim_id'): c for c in package.get('claim_ledger') or []}
    links = package.get('publishable_claim_links') or []
    paths = [link['path'] for link in links]
    surfaces = list(publishable_claim_surfaces(package))
    available = {path for path, text, *_ in surfaces if isinstance(text, str) and text}
    require(len(paths) == len(set(paths)), 'Duplicate publishable claim link paths', errors)
    require(set(paths) <= available, 'Publishable claim link names an absent or empty surface', errors)
    if supplementary_supplied:
        require(set(paths) == available, 'Supplementary evidence requires explicit publishable claim links for every nonempty surface', errors)
    for path, text, evidence_refs, claim_ids, _proof in surfaces:
        require(set(claim_ids) <= set(claims), f'{path}: unknown linked claim', errors)
        linked = [claims[c] for c in claim_ids if c in claims]
        refs = set(evidence_refs) & set(supplementary)
        refs |= {ref for claim in linked for ref in claim.get('evidence_refs') or [] if ref in supplementary}
        if not refs or not text:
            continue
        projected = []
        for ref in sorted(refs):
            projection = supplementary[ref]['report_text']
            require(any(ref in c.get('evidence_refs', []) and c.get('text') == projection
                        and c.get('status') in {'verified', 'hypothesis'} for c in linked),
                    f'{path}: supplementary fact requires a matching reviewed claim', errors)
            projected.append(projection)
        require(text == ' '.join(projected),
                f'{path}: supplementary text must match reviewed projection; separate creative narration', errors)


DIRECT_SOURCE_ID = "direct-user-selection"
SKILL_ROOT = Path(__file__).resolve().parent.parent
REQUEST_SCHEMA = SKILL_ROOT / "references/script-package-request.schema.json"
PACKAGE_SCHEMA = SKILL_ROOT / "references/script-package.schema.json"
SELECTED_IDEA_SCHEMA = SKILL_ROOT / "references/selected-idea.schema.json"
RUN_MANIFEST_SCHEMA = SKILL_ROOT / "references/run-manifest.schema.json"
SUPPORTED_SCHEMA_KEYWORDS = {
    "$schema", "$id", "$defs", "$ref", "title", "description",
    "type", "const", "enum", "oneOf", "allOf", "if", "then", "else",
    "required", "properties", "additionalProperties", "items", "prefixItems",
    "minItems", "maxItems", "uniqueItems", "minLength", "maxLength",
    "minimum", "maximum", "pattern", "format",
}
SUPPORTED_SCHEMA_TYPES = {
    "null", "object", "array", "string", "boolean", "integer", "number",
}
RFC3339_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def load(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def resolve(base: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (base / candidate).resolve()


def schema_type_matches(value: Any, expected: str) -> bool:
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
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
        )
    return False


def schema_ref(root: dict[str, Any], value: Any) -> dict[str, Any]:
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


def validate_schema_document(schema: Any, path: str = "$") -> None:
    """Reject unsupported or malformed schema vocabulary before using a schema."""

    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise ValueError(f"schema at {path} must be an object")
    unknown = sorted(set(schema) - SUPPORTED_SCHEMA_KEYWORDS)
    if unknown:
        raise ValueError(f"unsupported schema keyword(s) at {path}: {unknown}")

    declared_type = schema.get("type")
    if declared_type is not None:
        types = [declared_type] if isinstance(declared_type, str) else declared_type
        if not isinstance(types, list) or not types or not all(
            isinstance(item, str) and item in SUPPORTED_SCHEMA_TYPES for item in types
        ):
            raise ValueError(f"invalid type declaration at {path}: {declared_type!r}")

    if "format" in schema and schema["format"] != "date-time":
        raise ValueError(f"unsupported string format at {path}: {schema['format']!r}")
    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            raise ValueError(f"pattern at {path} must be a string")
        try:
            re.compile(schema["pattern"])
        except re.error as error:
            raise ValueError(f"invalid pattern at {path}: {error}") from error
    if "$ref" in schema:
        value = schema["$ref"]
        if not isinstance(value, str) or not value.startswith("#/$defs/"):
            raise ValueError(f"unsupported schema ref at {path}: {value!r}")

    for keyword in ("required",):
        value = schema.get(keyword)
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(item, str) for item in value)
        ):
            raise ValueError(f"{keyword} at {path} must be an array of strings")
    for keyword in ("oneOf", "allOf"):
        branches = schema.get(keyword)
        if branches is not None:
            if not isinstance(branches, list) or not branches:
                raise ValueError(f"{keyword} at {path} must be a non-empty array")
            for index, branch in enumerate(branches):
                validate_schema_document(branch, f"{path}.{keyword}[{index}]")
    prefix_items = schema.get("prefixItems")
    if prefix_items is not None:
        if not isinstance(prefix_items, list):
            raise ValueError(f"prefixItems at {path} must be an array")
        for index, branch in enumerate(prefix_items):
            validate_schema_document(branch, f"{path}.prefixItems[{index}]")
    for keyword in ("if", "then", "else", "items"):
        branch = schema.get(keyword)
        if branch is not None:
            validate_schema_document(branch, f"{path}.{keyword}")
    for keyword in ("properties", "$defs"):
        children = schema.get(keyword)
        if children is not None:
            if not isinstance(children, dict):
                raise ValueError(f"{keyword} at {path} must be an object")
            for name, child in children.items():
                validate_schema_document(child, f"{path}.{keyword}.{name}")
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        validate_schema_document(additional, f"{path}.additionalProperties")


def validate_schema_value(
    value: Any,
    schema: Any,
    root: dict[str, Any],
    path: str,
) -> list[str]:
    if isinstance(value, float) and not math.isfinite(value):
        return [f"{path}: must be a finite JSON number"]
    if schema is True:
        return []
    if schema is False:
        return [f"{path}: value is not allowed"]
    errors: list[str] = []
    if "$ref" in schema:
        errors.extend(validate_schema_value(value, schema_ref(root, schema["$ref"]), root, path))

    for branch in schema.get("allOf") or []:
        errors.extend(validate_schema_value(value, branch, root, path))
    if schema.get("oneOf") is not None:
        results = [validate_schema_value(value, branch, root, path) for branch in schema["oneOf"]]
        if sum(not branch_errors for branch_errors in results) != 1:
            errors.append(f"{path}: must match exactly one allowed shape")

    if "if" in schema:
        condition_matches = not validate_schema_value(value, schema["if"], root, path)
        branch = schema.get("then") if condition_matches else schema.get("else")
        if branch is not None:
            errors.extend(validate_schema_value(value, branch, root, path))

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']!r}")

    declared_type = schema.get("type")
    if declared_type is not None:
        expected_types = [declared_type] if isinstance(declared_type, str) else declared_type
        if not any(schema_type_matches(value, item) for item in expected_types):
            errors.append(f"{path}: expected type {expected_types!r}")
            return errors

    if isinstance(value, dict):
        for field in schema.get("required") or []:
            if field not in value:
                errors.append(f"{path}: missing required property {field!r}")
        properties = schema.get("properties") or {}
        additional = schema.get("additionalProperties")
        for field, child in value.items():
            if field in properties:
                errors.extend(
                    validate_schema_value(child, properties[field], root, f"{path}.{field}")
                )
            elif additional is False:
                errors.append(f"{path}: additional property {field!r} is not allowed")
            elif isinstance(additional, dict):
                errors.extend(validate_schema_value(child, additional, root, f"{path}.{field}"))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: allows at most {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(set(canonical)) != len(canonical):
                errors.append(f"{path}: items must be unique")
        prefix_items = schema.get("prefixItems") or []
        for index, item_schema in enumerate(prefix_items):
            if index < len(value):
                errors.extend(
                    validate_schema_value(value[index], item_schema, root, f"{path}[{index}]")
                )
        item_schema = schema.get("items")
        start = len(prefix_items)
        if item_schema is not None:
            for index, item in enumerate(value[start:], start=start):
                errors.extend(validate_schema_value(item, item_schema, root, f"{path}[{index}]"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: string does not match required pattern")
        if schema.get("format") == "date-time":
            valid = bool(RFC3339_DATETIME.fullmatch(value))
            if valid:
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    valid = False
            if not valid:
                errors.append(f"{path}: must be an RFC 3339 date-time")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value is above {schema['maximum']}")
    return errors


def validate_json_schema(
    instance: Any,
    schema_file: Path,
    label: str,
    errors: list[str],
) -> None:
    try:
        schema = load(schema_file)
        validate_schema_document(schema)
        errors.extend(
            f"{label} schema {failure}"
            for failure in validate_schema_value(instance, schema, schema, "$")
        )
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
        errors.append(
            f"{label} schema could not be loaded: {type(error).__name__}: {error}"
        )


def direct_ids(request_id: str) -> tuple[str, str]:
    return f"direct-selected:{request_id}", f"direct-user:{request_id}"


def validate_sha256(ref: dict, artifact_path: Path, label: str, errors: list[str]) -> None:
    declared = ref.get("sha256")
    if declared is None:
        return
    actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    require(declared == actual, f"{label} sha256 mismatch", errors)


def walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_keys(child)


def all_text(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from all_text(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_text(child)


NUMERIC_RESULT_TOKEN = re.compile(
    r"(?:\d+(?:[.,]\d+)?\s*(?:%|％|[xX]\b|×|倍)|百分之\s*\d+(?:\.\d+)?)"
)
NUMERIC_RESULT_TERM = re.compile(
    r"(?:效率|生产力|转化率?|营收|收入|销量|销售额|成交|留存|点击率|回报率|"
    r"提升|提高|增长|增加|降低|减少|节省|加快|更快|"
    r"efficien(?:cy|t)|productiv(?:ity|e)|conversion|revenue|income|sales?|"
    r"retention|click[- ]?through|\bctr\b|\broi\b|increase|improve|boost|grow|"
    r"reduce|decrease|save|faster|more\s+(?:leads?|customers?|users?))",
    re.IGNORECASE,
)
PROOF_QUALIFIER = re.compile(
    r"(?:待验证|待证实|未经验证|假设|占位|示例|演示|如果属实|声称|不能保证|不保证|"
    r"unverified|to\s+be\s+verified|needs?\s+verification|hypothesis|placeholder|"
    r"example|demo(?:nstration)?|if\s+verified|claimed?|not\s+guaranteed)",
    re.IGNORECASE,
)


def qualifier_shown(text: str, claim: dict[str, Any]) -> bool:
    """Does this surface mark its number as unverified?

    A draft may carry a numeric result as long as the copy itself says the number is not proven
    yet. Which words do that is a property of the language the copy is written in, and the list
    above holds only Chinese and English: a Portuguese draft writing «ROI de 30% (hipótese, não
    verificado)» or a Japanese one writing «ROI 30%（未検証）» hedges exactly as asked and was
    refused for it, with the package blocked rather than the sentence.

    So the claim may name its own qualifier: `qualifier` is the span the copy uses, and the check
    is that the copy really contains it. That holds in any language. The word list stays as the
    fallback, so packages that carry no `qualifier` validate exactly as before.
    """
    qualifier = claim.get("qualifier")
    if isinstance(qualifier, str) and qualifier.strip():
        return " ".join(qualifier.split()) in " ".join(text.split())
    return bool(PROOF_QUALIFIER.search(text))


def has_numeric_result_claim(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    for numeric in NUMERIC_RESULT_TOKEN.finditer(value):
        start = max(0, numeric.start() - 48)
        end = min(len(value), numeric.end() + 48)
        if NUMERIC_RESULT_TERM.search(value[start:end]):
            return True
    return False


def normalized_claim(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def claims_match(surface: str, claim: str) -> bool:
    """Require the publishable surface and ledger claim to say the same thing."""

    return normalized_claim(surface) == normalized_claim(claim)


def raw_publishable_claim_surfaces(package: dict[str, Any]):
    """Enumerate every creator-facing text surface and its native references."""
    overview = package.get("overview") or {}
    for field in ("headline", "creative_strategy"):
        yield f"overview.{field}", overview.get(field), [], [], []

    for index, hook in enumerate(package.get("hook_options") or []):
        for field in ("spoken_line", "on_screen_text", "promised_payoff"):
            yield (
                f"hook_options[{index}].{field}",
                hook.get(field),
                hook.get("evidence_refs") or [],
                [],
                hook.get("proof_required") or [],
            )

    script = package.get("script") or {}
    for index, scene in enumerate(script.get("scenes") or []):
        for field in ("spoken_text", "on_screen_text"):
            yield (
                f"script.scenes[{index}].{field}",
                scene.get(field),
                scene.get("evidence_refs") or [],
                scene.get("claim_ids") or [],
                [],
            )
    cta = script.get("cta") or {}
    for field in ("spoken_text", "on_screen_text"):
        yield f"script.cta.{field}", cta.get(field), [], [], []

    packaging = package.get("packaging") or {}
    for index, headline in enumerate(packaging.get("headline_options") or []):
        yield f"packaging.headline_options[{index}].text", headline.get("text"), [], [], []
    yield "packaging.caption_or_description", packaging.get("caption_or_description"), [], [], []
    visual = packaging.get("cover_or_thumbnail") or {}
    for field in ("information_split", "primary_concept"):
        yield f"packaging.cover_or_thumbnail.{field}", visual.get(field), [], [], []
    for index, variant in enumerate(visual.get("variants") or []):
        yield f"packaging.cover_or_thumbnail.variants[{index}]", variant, [], [], []


def publishable_claim_surfaces(package: dict[str, Any]):
    """Retain field-level claim links without erasing scene or hook provenance."""
    links = {link['path']: link for link in package.get('publishable_claim_links') or []}
    for path, text, refs, claims, proof in raw_publishable_claim_surfaces(package):
        link = links.get(path, {})
        yield path, text, sorted(set(refs) | set(link.get('evidence_refs') or [])), sorted(set(claims) | set(link.get('claim_ids') or [])), proof


def validate_numeric_result_claims(
    package: dict[str, Any],
    allowed_evidence: set[str],
    errors: list[str],
) -> None:
    claims = {
        claim.get("claim_id"): claim
        for claim in package.get("claim_ledger") or []
        if isinstance(claim, dict)
    }
    verified = [
        claim
        for claim in claims.values()
        if claim.get("status") == "verified"
        and bool(claim.get("evidence_refs"))
        and set(claim.get("evidence_refs") or []) <= allowed_evidence
    ]
    unresolved = [
        claim
        for claim in claims.values()
        if claim.get("status") in {"hypothesis", "placeholder", "prohibited"}
        and bool(claim.get("required_action"))
    ]
    draft_status = package.get("status") in {"draft_needs_user_input", "blocked"}

    for path, text, evidence_refs, claim_ids, proof_required in publishable_claim_surfaces(package):
        if not has_numeric_result_claim(text):
            continue
        explicit_link = any(link['path'] == path for link in package.get('publishable_claim_links') or [])
        linked_verified_claims = [c for c in verified if c.get('claim_id') in claim_ids] if explicit_link else verified
        linked_unresolved_claims = [c for c in unresolved if c.get('claim_id') in claim_ids] if explicit_link else unresolved
        linked_verified = any(
            isinstance(claim.get("text"), str) and claims_match(text, claim["text"])
            for claim in linked_verified_claims
        )
        explicitly_gated = (
            draft_status
            and any(
                isinstance(claim.get("text"), str)
                and claims_match(text, claim["text"])
                and qualifier_shown(text, claim)
                for claim in linked_unresolved_claims
            )
            and (bool(proof_required) or bool(unresolved))
        )
        require(
            linked_verified or explicitly_gated,
            f"{path}: numeric result claim requires verified evidence or an explicit draft proof gate",
            errors,
        )


def validate_spoken_copy_duration(
    package: dict[str, Any],
    request: dict[str, Any],
    errors: list[str],
) -> None:
    script = package.get("script")
    if not isinstance(script, dict):
        return
    spoken = [
        scene.get("spoken_text", "")
        for scene in script.get("scenes") or []
        if isinstance(scene, dict)
    ]
    cta = script.get("cta") or {}
    if isinstance(cta, dict):
        spoken.append(cta.get("spoken_text", ""))

    selected_hook_id = script.get("selected_hook_id")
    selected_hook = next(
        (
            hook.get("spoken_line", "")
            for hook in package.get("hook_options") or []
            if isinstance(hook, dict) and hook.get("hook_id") == selected_hook_id
        ),
        "",
    )
    joined = "\n".join(item for item in spoken if isinstance(item, str))
    if isinstance(selected_hook, str) and selected_hook and selected_hook not in joined:
        joined = f"{selected_hook}\n{joined}"

    cjk_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", joined))
    without_cjk = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff]", " ", joined)
    word_count = len(re.findall(r"\b[\wÀ-ÖØ-öø-ÿ]+(?:['’’-][\wÀ-ÖØ-öø-ÿ]+)*\b", without_cjk))
    fastest_plausible_seconds = cjk_count / 6.0 + word_count / 4.5
    target = (request.get("content_format") or {}).get("target_duration_seconds")
    if isinstance(target, int) and not isinstance(target, bool) and target > 0:
        conservative_limit = max(target * 2.0, target + 30.0)
        require(
            fastest_plausible_seconds <= conservative_limit,
            "script spoken copy is extremely long for target_duration_seconds "
            f"(fast-pacing floor {fastest_plausible_seconds:.1f}s > {conservative_limit:.1f}s)",
            errors,
        )


def semantic_package_shape_is_safe(package: Any, request: Any) -> bool:
    """Only enter semantic checks when malformed JSON cannot cause type errors."""

    if not isinstance(package, dict) or not isinstance(request, dict):
        return False
    if not isinstance(request.get("content_format"), dict):
        return False
    if not isinstance(request.get("selected_idea"), dict):
        return False
    for field in ("input_snapshot", "overview", "production_plan", "interaction", "provenance"):
        if not isinstance(package.get(field), dict):
            return False
    for field in ("hook_options", "claim_ledger", "quality_checks"):
        value = package.get(field)
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            return False
    if "publishable_claim_links" in package:
        links = package["publishable_claim_links"]
        if not isinstance(links, list):
            return False
        for link in links:
            if not isinstance(link, dict) or not isinstance(link.get("path"), str):
                return False
            for field in ("claim_ids", "evidence_refs"):
                value = link.get(field)
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    return False
    questions = (package.get("interaction") or {}).get("required_questions")
    if not isinstance(questions, list) or not all(isinstance(item, dict) for item in questions):
        return False
    script = package.get("script")
    if script is not None:
        if not isinstance(script, dict) or not isinstance(script.get("cta"), dict):
            return False
        scenes = script.get("scenes")
        if not isinstance(scenes, list) or not all(isinstance(item, dict) for item in scenes):
            return False
    packaging = package.get("packaging")
    if packaging is not None:
        if not isinstance(packaging, dict) or not isinstance(
            packaging.get("cover_or_thumbnail"), dict
        ):
            return False
        headlines = packaging.get("headline_options")
        if not isinstance(headlines, list) or not all(
            isinstance(item, dict) for item in headlines
        ):
            return False
    return True


def _validate_request_with_facts(request: Any, request_path: Path, run_root: Path | None = None) -> tuple[list[str], dict]:
    """Check native contracts, frozen evidence lineage and semantic production gates."""
    errors: list[str] = []
    validate_json_schema(request, REQUEST_SCHEMA, "request", errors)
    if errors or not isinstance(request, dict):
        return errors, {}
    require(request.get("schema_version") == "script-package-request.v1", "request schema_version must be script-package-request.v1", errors)
    require(bool(request.get("request_id")), "request_id is required", errors)
    require(request.get("mode") in {"script_only", "packaging_only", "full_package"}, "invalid request mode", errors)
    require(bool(request.get("platform")), "platform is required", errors)
    require(bool(request.get("language")), "language is required", errors)
    content_format = request.get("content_format") or {}
    require(content_format.get("kind") in {"short_form", "long_form"}, "invalid content_format.kind", errors)
    duration = content_format.get("target_duration_seconds")
    require(isinstance(duration, int) and 5 <= duration <= 7200, "target_duration_seconds must be 5..7200", errors)

    selected_ref = request.get("selected_idea") or {}
    selected_path_value = selected_ref.get("path")
    selected_path = resolve(request_path.parent, selected_path_value) if selected_path_value else None
    require(bool(selected_path and selected_path.is_file()), f"selected idea path not found: {selected_path_value}", errors)
    if selected_path and selected_path.is_file():
        try:
            selected = load(selected_path)
        except (OSError, json.JSONDecodeError) as error:
            errors.append(
                f"selected idea is not valid JSON: {type(error).__name__}: {error}"
            )
            return errors, {}
        if not isinstance(selected, dict):
            errors.append("selected idea must contain a JSON object")
            return errors, {}
        if selected.get("schema_version") == "selected-idea.v2":
            selected_schema_errors: list[str] = []
            validate_json_schema(
                selected,
                SELECTED_IDEA_SCHEMA,
                "selected idea",
                selected_schema_errors,
            )
            errors.extend(selected_schema_errors)
            if selected_schema_errors:
                return errors, {}
        require(selected.get("schema_version") in {"selected-idea.v1", "selected-idea.v2"}, "selected artifact must use selected-idea.v1 or selected-idea.v2", errors)
        require(selected_ref.get("schema_version") == selected.get("schema_version"), "selected idea schema_version mismatch", errors)
        require(selected.get("selection_status") in {"selected", "selected_with_edits"}, "idea must be selected before scripting", errors)
        require(selected_ref.get("artifact_id") == selected.get("selected_idea_id"), "selected idea artifact_id mismatch", errors)
        validate_sha256(selected_ref, selected_path, "selected idea", errors)
        raw_constraints = selected.get("confirmed_constraints")
        constraints = raw_constraints if isinstance(raw_constraints, dict) else {}
        require(isinstance(raw_constraints, dict), "selected idea confirmed_constraints must be an object", errors)
        for field in ("goal", "audience", "desired_audience_action", "language"):
            require(bool(constraints.get(field)), f"selected idea missing confirmed {field}", errors)

        direct = request.get("direct_user_selection")
        selected_source = selected.get("source_idea_set_id")
        if direct is None:
            require(
                selected_source != DIRECT_SOURCE_ID,
                "direct-user-selection requires request.direct_user_selection",
                errors,
            )
        elif not isinstance(direct, dict):
            require(False, "direct_user_selection must be an object or null", errors)
        else:
            literal = direct.get("literal_direction")
            require(
                isinstance(literal, str) and bool(literal.strip()),
                "direct_user_selection.literal_direction is required",
                errors,
            )
            for field in ("goal", "audience", "desired_audience_action"):
                require(
                    isinstance(direct.get(field), str) and bool(direct.get(field).strip()),
                    f"direct_user_selection.{field} is required",
                    errors,
                )
            expected_selected_id, expected_candidate_id = direct_ids(str(request.get("request_id") or ""))
            require(selected.get("schema_version") == "selected-idea.v2", "direct selection must use selected-idea.v2", errors)
            require(selected_source == DIRECT_SOURCE_ID, "direct selection source_idea_set_id must be direct-user-selection", errors)
            require(selected.get("selected_idea_id") == expected_selected_id, "direct selected_idea_id is not request-scoped", errors)
            require(selected.get("source_candidate_id") == expected_candidate_id, "direct source_candidate_id is not request-scoped", errors)
            require(selected.get("selection_status") == "selected_with_edits", "direct selection must use selected_with_edits", errors)
            require(selected.get("user_edits") == [literal], "direct selection must preserve the exact literal direction as its only user_edit", errors)
            raw_idea = selected.get("idea")
            idea = raw_idea if isinstance(raw_idea, dict) else {}
            require(isinstance(raw_idea, dict), "direct selection idea must be an object", errors)
            for field in ("working_title", "content_promise", "angle"):
                require(idea.get(field) == literal, f"direct selection {field} must preserve the exact literal direction", errors)
            raw_hook = idea.get("hook_preview")
            hook = raw_hook if isinstance(raw_hook, dict) else {}
            require(isinstance(raw_hook, dict), "direct selection hook_preview must be an object", errors)
            require(
                hook.get("opening_line") == literal,
                "direct selection hook_preview.opening_line must preserve the exact literal direction",
                errors,
            )
            require(idea.get("platforms") == [request.get("platform")], "direct selection platforms must match the request", errors)
            require(
                idea.get("formats") == [(request.get("content_format") or {}).get("kind")],
                "direct selection formats must match the request",
                errors,
            )
            require(idea.get("source_context_refs") == [], "direct selection source_context_refs must be empty", errors)
            require(idea.get("evidence_refs") == [], "direct selection evidence_refs must be empty", errors)
            require(idea.get("primary_evidence_ref") is None, "direct selection primary_evidence_ref must be null", errors)
            raw_outline = idea.get("content_outline")
            outline = raw_outline if isinstance(raw_outline, list) else []
            require(
                isinstance(raw_outline, list) and 3 <= len(raw_outline) <= 5,
                "direct selection content_outline must contain 3..5 items",
                errors,
            )
            require(
                all(isinstance(item, dict) and item.get("evidence_refs") == [] for item in outline),
                "direct selection outline evidence_refs must be empty",
                errors,
            )
            require(constraints.get("goal") == direct.get("goal"), "direct selection goal was changed", errors)
            require(constraints.get("audience") == direct.get("audience"), "direct selection audience was changed", errors)
            require(
                constraints.get("desired_audience_action") == direct.get("desired_audience_action"),
                "direct selection desired_audience_action was changed",
                errors,
            )
            require(constraints.get("language") == request.get("language"), "direct selection language must match the request", errors)

    for field in ("creator_profile", "source_script_package"):
        ref = request.get(field)
        if ref:
            artifact_path = resolve(request_path.parent, ref.get("path", ""))
            require(artifact_path.is_file(), f"{field} path not found: {ref.get('path')}", errors)
            if artifact_path.is_file():
                artifact = load(artifact_path)
                expected_id_field = "profile_id" if field == "creator_profile" else "package_id"
                require(ref.get("artifact_id") == artifact.get(expected_id_field), f"{field} artifact_id mismatch", errors)
                require(ref.get("schema_version") == artifact.get("schema_version"), f"{field} schema_version mismatch", errors)
                validate_sha256(ref, artifact_path, field, errors)
    for ref in request.get("video_analysis_sources") or []:
        artifact_path = resolve(request_path.parent, ref.get("path", ""))
        require(artifact_path.is_file(), f"video analysis path not found: {ref.get('path')}", errors)
        if artifact_path.is_file():
            try:
                artifact = load(artifact_path)
            except (UnicodeDecodeError, json.JSONDecodeError):
                artifact = None
            if not isinstance(artifact, dict) or artifact.get("schema_version") != "video-analysis.v1":
                # A plugin run on 2026-10-29 pointed this at the analysis run's report Markdown, then
                # at a summary it wrote itself, and at the third mismatch dropped the source to pass.
                errors.append(f"video analysis source {ref.get('path')} is not a VideoAnalysis: point it at the "
                              "analysis run's analysis/video_analysis.json (artifact_id = its analysis_id, "
                              "schema_version video-analysis.v1); a report or a summary is not one. Do not "
                              "remove the source to pass.")
                continue
            require(ref.get("artifact_id") == artifact.get("analysis_id"), "video analysis artifact_id mismatch", errors)
            require(ref.get("schema_version") == artifact.get("schema_version"), "video analysis schema_version mismatch", errors)
            validate_sha256(ref, artifact_path, "video analysis", errors)
    supplementary = supplementary_facts(request, request_path, errors, run_root)
    try:
        validate_plan(request)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        errors.append(f'Duration plan invalid: {error}')
    if request.get("mode") == "packaging_only":
        require(request.get("source_script_package") is not None, "packaging_only requires source_script_package", errors)
    return errors, supplementary

def validate_request(request: Any, request_path: Path, run_root: Path | None = None) -> list[str]:
    """Validate a request while keeping the public list-of-errors contract."""
    errors, _ = _validate_request_with_facts(request, request_path, run_root)
    return errors


def validate_package(package: Any, request: Any, request_path: Path, run_root: Path | None = None) -> list[str]:
    """Check native contracts, frozen evidence lineage and semantic production gates."""
    errors, supplementary = _validate_request_with_facts(request, request_path, run_root)
    package_schema_errors: list[str] = []
    validate_json_schema(package, PACKAGE_SCHEMA, "package", package_schema_errors)
    errors.extend(package_schema_errors)
    if not semantic_package_shape_is_safe(package, request):
        return errors
    require(package.get("schema_version") == "script-package.v1", "package schema_version must be script-package.v1", errors)
    require(package.get("request_id") == request.get("request_id"), "package request_id mismatch", errors)
    require(package.get("mode") == request.get("mode"), "package mode mismatch", errors)
    status = package.get("status")
    approval = package.get("approval_status")
    require(status in {"draft_needs_user_input", "ready_for_review", "approved_for_production", "blocked"}, "invalid package status", errors)
    require(approval in {"pending", "approved", "revise"}, "invalid approval_status", errors)
    if status == "approved_for_production":
        require(approval == "approved", "approved_for_production requires human approval", errors)
    elif approval == "approved":
        require(status == "approved_for_production", "approved status must match approved_for_production", errors)

    selected_ref = request.get("selected_idea") or {}
    selected_path = resolve(request_path.parent, selected_ref.get("path", ""))
    if selected_path.is_file():
        try:
            selected = load(selected_path)
        except (OSError, json.JSONDecodeError):
            return errors
        if not isinstance(selected, dict):
            return errors
    else:
        selected = {}
    raw_frozen = selected.get("idea")
    frozen = raw_frozen if isinstance(raw_frozen, dict) else {}
    raw_constraints = selected.get("confirmed_constraints")
    constraints = raw_constraints if isinstance(raw_constraints, dict) else {}
    raw_snapshot = package.get("input_snapshot")
    snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else {}
    require(isinstance(raw_frozen, dict), "selected idea idea must be an object", errors)
    require(isinstance(raw_constraints, dict), "selected idea confirmed_constraints must be an object", errors)
    require(isinstance(raw_snapshot, dict), "package input_snapshot must be an object", errors)
    require(snapshot.get("selected_idea_id") == selected.get("selected_idea_id"), "input_snapshot selected_idea_id mismatch", errors)
    require(snapshot.get("source_candidate_id") == selected.get("source_candidate_id"), "input_snapshot source_candidate_id mismatch", errors)
    require(snapshot.get("working_title") == frozen.get("working_title"), "selected working title was changed", errors)
    require(snapshot.get("content_promise") == frozen.get("content_promise"), "selected content promise was changed", errors)
    require(snapshot.get("audience") == constraints.get("audience"), "selected audience was changed", errors)
    require(snapshot.get("desired_audience_action") == constraints.get("desired_audience_action"), "desired audience action was changed", errors)
    require(snapshot.get("platform") == request.get("platform"), "input_snapshot platform mismatch", errors)
    require(snapshot.get("format") == (request.get("content_format") or {}).get("kind"), "input_snapshot format mismatch", errors)
    require(snapshot.get("language") == request.get("language"), "input_snapshot language mismatch", errors)
    selected_v2 = selected.get("schema_version") == "selected-idea.v2"
    allowed_evidence = set(frozen.get("evidence_refs") or [])
    selected_context_refs = set(frozen.get("source_context_refs") or [])
    require(set(snapshot.get("evidence_refs") or []) == allowed_evidence, "input_snapshot must preserve selected evidence_refs", errors)
    if selected_v2:
        require(set(snapshot.get("source_context_refs") or []) == selected_context_refs, "input_snapshot must preserve selected source_context_refs", errors)
    else:
        require(snapshot.get("evidence_signals") == frozen.get("evidence_signals"), "input_snapshot must preserve selected evidence_signals", errors)
    require(set(frozen.get("must_differ") or []) <= set(snapshot.get("must_differ") or []), "input_snapshot dropped must_differ", errors)
    for field in ("required_assets", "risks"):
        require(list(snapshot.get(field) or []) == list(frozen.get(field) or []),
                f"input_snapshot must preserve selected {field}", errors)
    if allowed_evidence:
        require(snapshot.get("evidence_basis") == "source_backed",
                "an idea with evidence refs is source_backed in input_snapshot", errors)
    else:
        require(snapshot.get("evidence_basis") in {"brief_based", "hypothesis"},
                "an idea without evidence refs cannot be source_backed", errors)
    if request.get("direct_user_selection") is not None:
        require(
            snapshot.get("evidence_basis") in {"brief_based", "hypothesis"},
            "direct selection must remain brief_based or hypothesis",
            errors,
        )

    require(snapshot.get('supplemental_evidence') == request.get('supplemental_evidence'),
            'input_snapshot must preserve supplementary evidence reference', errors)
    require(not (set(supplementary) & allowed_evidence), 'Supplementary evidence IDs collide with selected evidence', errors)
    allowed_evidence |= set(supplementary)
    mode = request.get("mode")
    script = package.get("script")
    packaging = package.get("packaging")
    hooks = package.get("hook_options") or []
    if mode == "script_only":
        require(script is not None and packaging is None, "script_only requires script and null packaging", errors)
    elif mode == "packaging_only":
        require(script is None and packaging is not None, "packaging_only requires null script and packaging", errors)
        require(not hooks, "packaging_only must not regenerate hook options", errors)
    elif mode == "full_package":
        require(script is not None and packaging is not None, "full_package requires script and packaging", errors)

    if mode != "packaging_only":
        require(3 <= len(hooks) <= 5, "script modes require 3..5 hook options", errors)
        hook_ids = [item.get("hook_id") for item in hooks]
        require(len(set(hook_ids)) == len(hook_ids), "hook_id values must be unique", errors)
        require(sum(bool(item.get("recommended")) for item in hooks) == 1, "exactly one hook must be recommended", errors)
        for hook in hooks:
            require(set(hook.get("evidence_refs") or []) <= allowed_evidence, f"{hook.get('hook_id')}: unknown evidence ref", errors)
            require(bool(hook.get("spoken_line") and hook.get("first_visual") and hook.get("promised_payoff")), f"{hook.get('hook_id')}: incomplete hook", errors)

    claim_ids = {item.get("claim_id") for item in package.get("claim_ledger") or []}
    for claim in package.get("claim_ledger") or []:
        for ref in set(claim.get('evidence_refs') or []) & set(supplementary):
            fact = supplementary[ref]
            require(claim.get('text') == fact['report_text'], 'Supplementary claim text must match its reviewed source projection', errors)
            if claim.get('status') == 'verified':
                require(fact['publishable_as_verified'], 'Brand claims are not verified efficacy evidence', errors)
        if claim.get("status") == "verified":
            require(bool(claim.get("evidence_refs")), f"{claim.get('claim_id')}: verified claim requires evidence", errors)
            require(set(claim.get("evidence_refs") or []) <= allowed_evidence, f"{claim.get('claim_id')}: unknown evidence ref", errors)
        if claim.get("status") in {"placeholder", "prohibited"}:
            require(bool(claim.get("required_action")), f"{claim.get('claim_id')}: unresolved claim requires action", errors)

    if script:
        require(script.get("format") == (request.get("content_format") or {}).get("kind"), "script format mismatch", errors)
        require(script.get("language") == request.get("language"), "script language mismatch", errors)
        require(script.get("selected_hook_id") in {item.get("hook_id") for item in hooks}, "selected_hook_id not found", errors)
        target = (request.get("content_format") or {}).get("target_duration_seconds") or 0
        estimated = script.get("estimated_duration_seconds") or 0
        require(abs(estimated - target) <= max(10, round(target * 0.2)), "estimated duration is outside 20% tolerance", errors)
        scenes = script.get("scenes") or []
        require([item.get("order") for item in scenes] == list(range(1, len(scenes) + 1)), "scene order must be sequential", errors)
        for scene in scenes:
            require(set(scene.get("evidence_refs") or []) <= allowed_evidence, f"scene {scene.get('order')}: unknown evidence ref", errors)
            require(set(scene.get("claim_ids") or []) <= claim_ids, f"scene {scene.get('order')}: unknown claim id", errors)
        require((script.get("cta") or {}).get("goal") == constraints.get("desired_audience_action"), "CTA goal must preserve selected desired audience action", errors)

    if packaging:
        headlines = packaging.get("headline_options") or []
        require(3 <= len(headlines) <= 5, "packaging requires 3..5 headline options", errors)
        headline_ids = {item.get("headline_id") for item in headlines}
        require(packaging.get("recommended_headline_id") in headline_ids, "recommended headline not found", errors)
        require((packaging.get("cover_or_thumbnail") or {}).get("generation_status") == "brief_only", "core Skill outputs a visual brief, not a generated image", errors)
        keyword_status = packaging.get("keyword_research_status")
        require(keyword_status in {"not_requested", "heuristic_only", "provider_validated"}, "invalid keyword_research_status", errors)
        if keyword_status == "provider_validated":
            require(bool((package.get("provenance") or {}).get("connector_or_provider")), "provider_validated keywords require provider provenance", errors)

    for path, _text, refs, _claims, _proof in publishable_claim_surfaces(package):
        require(set(refs) <= allowed_evidence, f'{path}: unknown evidence ref', errors)
    validate_supplementary_surfaces(package, supplementary, request.get("supplemental_evidence") is not None, errors)
    validate_numeric_result_claims(package, allowed_evidence, errors)
    validate_spoken_copy_duration(package, request, errors)
    require(snapshot.get('duration_plan') == request.get('duration_plan'), 'input_snapshot duration plan drift', errors)
    try:
        validate_fit(package, request)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        errors.append(f'Duration fit invalid: {error}')

    missing_assets = (package.get("production_plan") or {}).get("missing_assets") or []
    unresolved_claims = [item for item in package.get("claim_ledger") or [] if item.get("status") in {"placeholder", "prohibited"}]
    questions = ((package.get("interaction") or {}).get("required_questions") or [])
    next_action = (package.get("interaction") or {}).get("next_action")
    selected_requires_proof = bool(frozen.get("required_assets")) and (request.get("proof_assets") or {}).get("status") != "confirmed"
    needs_input = bool(missing_assets or unresolved_claims or selected_requires_proof or (request.get("creative_direction") or {}).get("status") == "missing")
    if needs_input:
        require(status == "draft_needs_user_input", "missing proof/assets/voice requires draft_needs_user_input", errors)
        require(next_action == "ask_user_once" and bool(questions), "missing inputs require one question batch", errors)
    elif status != "approved_for_production":
        require(status == "ready_for_review", "complete draft must be ready_for_review", errors)
        require(next_action in {"review_and_approve", "revise"}, "complete draft must request review", errors)
        require(not questions, "complete draft must not contain unanswered questions", errors)

    forbidden_keys = {"publish_day", "publish_time", "best_publish_time", "schedule_at", "predicted_ctr", "predicted_views", "predicted_retention", "rpm"}
    found_keys = forbidden_keys & set(walk_keys(package))
    require(not found_keys, f"out-of-scope or unsupported fields present: {sorted(found_keys)}", errors)
    unsupported = re.compile(r"\b(guaranteed viral|will go viral|一定会爆|保证爆款|guaranteed ctr)\b", re.IGNORECASE)
    require(not any(unsupported.search(text) for text in all_text(package)), "package contains a performance guarantee", errors)
    checks = package.get("quality_checks") or []
    check_names = {item.get("check") for item in checks}
    required_checks = {"idea_alignment", "hook_payoff", "single_cta", "evidence_and_claims", "originality", "duration", "production_feasibility", "packaging_consistency"}
    require(required_checks <= check_names, "quality_checks missing required checks", errors)
    if status in {"ready_for_review", "approved_for_production"}:
        require(not any(item.get("status") != "pass" for item in checks), "ready/approved package requires all quality checks to pass", errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    request_parser = sub.add_parser("request")
    request_parser.add_argument("request", type=Path)
    package_parser = sub.add_parser("package")
    package_parser.add_argument("package", type=Path)
    package_parser.add_argument("request", type=Path)
    request_parser.add_argument("--run-root", type=Path)
    package_parser.add_argument("--run-root", type=Path)
    args = parser.parse_args()
    if args.command == "request":
        errors = validate_request(load(args.request), args.request.resolve(), args.run_root)
    else:
        errors = validate_package(load(args.package), load(args.request), args.request.resolve(), args.run_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"VALID {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
