#!/usr/bin/env python3
"""Dependency-free structural and semantic validator for V3 Idea contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Keep sibling imports from modifying a signed skill cache.
sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_language import evidence_gaps, report_language, report_value_translations
from project_comments import has_comment_text, validate_projection, validate_target

COMMON_ROOT = Path(__file__).resolve().parent.parent
REQUEST_SCHEMA = COMMON_ROOT / "references/idea-request.schema.json"
IDEA_SET_SCHEMA = COMMON_ROOT / "references/idea-set.schema.json"
DERIVED_FIELDS = ("upstream_sources", "idea_context", "continuation_context", "conversation_context")
SUPPORTED_SCHEMA_KEYWORDS = {
    "$schema", "$id", "$defs", "$ref", "title", "description",
    "type", "const", "enum", "oneOf", "allOf", "if", "then", "else",
    "required", "properties", "additionalProperties", "items",
    "minItems", "maxItems", "uniqueItems", "minLength", "minimum", "maximum",
}


def load(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def previous_ideas_context(path: Path, base: Path) -> dict[str, Any]:
    """Project creative history without inheriting its evidence or factual claims."""
    previous = load(path)
    errors: list[str] = []
    validate_json_schema(previous, IDEA_SET_SCHEMA, "previous_ideas", errors)
    if errors:
        raise ValueError("; ".join(errors))
    if previous["status"] != "ready_for_selection" or not previous["ideas"]:
        raise ValueError("previous_ideas must be a nonempty ready_for_selection IdeaSet")
    return {
        "path": os.path.relpath(path.resolve(), base.resolve()), "sha256": file_sha256(path),
        "idea_set_id": previous["idea_set_id"], "request_id": previous["request_id"],
        "ideas": [{key: idea[key] for key in ("idea_id", "working_title", "making_direction")}
                  for idea in previous["ideas"]],
    }


def bound_conversation_context(path: Path, base: Path) -> dict[str, Any]:
    """Bind visible conversation text without treating it as research evidence.

    This verifies the supplied text and its reference, not its origin in a real
    conversation or the semantic correctness of the chosen reference.
    """
    source = load(path)
    schema = load(REQUEST_SCHEMA)
    errors = validate_schema_value(source, schema["$defs"]["conversationSource"], schema, "conversation_context")
    if errors:
        raise ValueError("; ".join(errors))
    messages = source["messages"]
    reference = source["reference"]
    index = reference["message_index"]
    if any(not message["text"].strip() for message in messages):
        raise ValueError("conversation_context messages must contain visible text")
    if index >= len(messages):
        raise ValueError("conversation_context reference.message_index is outside messages")
    quote = reference["quote"]
    if not quote.strip() or quote not in messages[index]["text"]:
        raise ValueError("conversation_context reference.quote must be copied verbatim from the referenced message")
    return {"path": os.path.relpath(path.resolve(), base.resolve()), "sha256": file_sha256(path), **source}


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
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def schema_ref(root: dict[str, Any], value: str) -> dict[str, Any]:
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


def validate_schema_value(
    value: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
) -> list[str]:
    errors: list[str] = []
    if "$ref" in schema:
        errors.extend(validate_schema_value(value, schema_ref(root, schema["$ref"]), root, path))

    for branch in schema.get("allOf") or []:
        errors.extend(validate_schema_value(value, branch, root, path))
    if schema.get("oneOf") is not None:
        branch_results = [
            validate_schema_value(value, branch, root, path)
            for branch in schema["oneOf"]
        ]
        matched = sum(not branch_errors for branch_errors in branch_results)
        if matched != 1:
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
        expected_types = [declared_type] if isinstance(declared_type, str) else declared_type
        if not isinstance(expected_types, list) or not all(
            isinstance(item, str) for item in expected_types
        ):
            raise ValueError(f"invalid type declaration at {path}")
        if not any(schema_type_matches(value, item) for item in expected_types):
            errors.append(f"{path}: expected type {expected_types!r}")
            return errors

    if isinstance(value, dict):
        required = schema.get("required") or []
        for field in required:
            if field not in value:
                errors.append(f"{path}: missing required property {field!r}")
        properties = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            for field in value:
                if field not in properties:
                    errors.append(f"{path}: additional property {field!r} is not allowed")
        for field, child_schema in properties.items():
            if field in value:
                errors.extend(
                    validate_schema_value(value[field], child_schema, root, f"{path}.{field}")
                )

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: allows at most {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(set(canonical)) != len(canonical):
                errors.append(f"{path}: items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_schema_value(item, item_schema, root, f"{path}[{index}]"))

    if isinstance(value, str) and "minLength" in schema and len(value) < schema["minLength"]:
        errors.append(f"{path}: string is shorter than {schema['minLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value is above {schema['maximum']}")
    return errors


def validate_json_schema(instance: Any, schema_file: Path, label: str, errors: list[str]) -> None:
    try:
        schema = load(schema_file)
        if not isinstance(schema, dict):
            raise ValueError("schema root must be an object")
        errors.extend(
            f"{label} schema {failure}"
            for failure in validate_schema_value(instance, schema, schema, "$")
        )
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
        errors.append(f"{label} schema could not be loaded: {type(error).__name__}: {error}")


def read_artifact(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    require(path.is_file(), f"{label} path not found: {path}", errors)
    if not path.is_file():
        return None
    try:
        artifact = load(path)
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"{label} is not valid JSON: {type(error).__name__}: {error}")
        return None
    require(isinstance(artifact, dict), f"{label} must contain a JSON object", errors)
    return artifact if isinstance(artifact, dict) else None


def validate_artifact_ref(
    ref: dict[str, Any],
    request_path: Path,
    *,
    label: str,
    id_field: str,
    expected_id: str,
    errors: list[str],
) -> dict[str, Any] | None:
    value = ref.get("path", "")
    artifact_path = resolve(request_path.parent, value)
    artifact = read_artifact(artifact_path, label, errors)
    if artifact is None:
        return None
    require(artifact.get(id_field) == expected_id, f"{label} {id_field} mismatch: {value}", errors)
    require(
        artifact.get("schema_version") == ref.get("schema_version"),
        f"{label} schema_version mismatch: {value}",
        errors,
    )
    declared_hash = ref.get("sha256")
    require(declared_hash == file_sha256(artifact_path), f"{label} sha256 mismatch: {value}", errors)
    return artifact


def has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


GENERATION_SOURCE_ORDER = ("creator", "account", "trend", "competitor", "video", "comments")
GENERIC_TOPIC_FRAGMENTS = {
    "a", "about", "an", "content", "for", "give", "idea", "ideas", "instagram",
    "me", "my", "next", "reel", "reels", "short", "shorts", "some", "the",
    "tiktok", "topic", "topics", "video", "videos", "youtube",
    "下一个", "下一条", "内容", "帮我", "想法", "方向", "的", "给我", "视频", "选题", "主题",
}
EVIDENCE_INTENT_PATTERNS = (
    r"(?:只)?(?:结合|参考|基于|根据|利用|使用|采用|只看)",
    r"(?:分析|研究|对比|比较|复盘|评估|检查|审查|核查|衡量)",
    r"\b(?:use|using|based\s+on|according\s+to|combine|consider|from|"
    r"look\s+at|draw\s+on|leverage|consult|factor\s+in|take\s+into\s+account)\b",
    r"\b(?:review(?:s|ed|ing)?|analy[sz](?:e|es|ed|ing)|research(?:es|ed|ing)?|"
    r"compar(?:e|es|ed|ing)|assess(?:es|ed|ing)?|evaluat(?:e|es|ed|ing)|"
    r"benchmark(?:s|ed|ing)?|examin(?:e|es|ed|ing)|check(?:s|ed|ing)?|"
    r"inspect(?:s|ed|ing)?|audit(?:s|ed|ing)?)\b",
)
IMPERATIVE_EVIDENCE_INTENT_PATTERNS = (
    r"^\s*(?:(?:请|帮我|麻烦)(?:你)?\s*)?(?:分析|研究|对比|复盘|评估|检查|审查|核查|衡量)",
    r"^\s*(?:please\s+)?(?:review|analy[sz]e|research|compare|assess|evaluate|benchmark|examine|check|audit|inspect)\b",
)
EVIDENCE_SOURCE_TERMS = {
    "creator": (
        r"creator\s+profile", r"创作者画像", r"我的.{0,4}(?:定位|受众)",
        r"\bmy\s+(?:positioning|audience|creator\s+profile)\b",
    ),
    "account": (
        r"(?:公开|我的|该)?(?:账号|频道).{0,8}(?:表现|数据|指标|分析|复盘)",
        r"\b(?:my|public)?\s*(?:account|channel).{0,24}(?:performance|analytics|metrics|stats|data|evidence)\b",
    ),
    "trend": (
        r"(?:最近|近期|当前|当下|最新).{0,8}(?:爆款|热门|趋势)",
        r"(?:爆款|热门)(?:内容|帖子|视频|方向)?", r"趋势",
        r"\b(?:recent|current|latest)?\s*(?:trend(?:ing|s)?|popular\s+(?:content|posts?|videos?)|viral\s+(?:hits?|posts?|videos?))\b",
    ),
    "competitor": (
        r"竞品", r"竞争对手", r"对标账号",
        r"\b(?:competitor(?:s)?|rivals?|rival\s+channels?|peer\s+channels?|peers?|competing\s+channels?)\b",
        r"\bcompetitive\s+(?:data|analysis|research)\b",
    ),
    "comments": (r"评论区", r"观众.{0,4}(?:提问|吐槽)", r"\bcomments?\b"),
    "video": (r"(?:这个|这条|上述|上面).{0,6}(?:视频|内容)", r"\b(?:this|that)\s+(?:video|post)\b"),
}


def brief_text_without_identifiers(value: Any) -> str:
    """Return brief text with URLs and account handles removed.

    A handle or URL is an identifier, not a content topic. Removing them before
    checking direct_topic makes the topic boundary deterministic without
    trying to infer a business domain from a name.
    """

    if not isinstance(value, str):
        return ""
    without_urls = re.sub(r"(?:https?://|www\.)\S+", " ", value, flags=re.IGNORECASE)
    return re.sub(r"(?<!\w)@[^\s,，。;；:：!?！？]+", " ", without_urls)


def canonical_phrase(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


def brief_without_framed_topic(value: Any, direct_topic: Any) -> str:
    """Remove a literal topic only when the user explicitly frames it as the topic.

    This prevents topic text such as "why account analytics misleads" from being
    mistaken for a request to use account evidence. A model cannot erase an
    evidence request merely by copying it into direct_topic because unframed
    phrases (for example "combine account performance and trends") stay intact.
    """

    if not isinstance(value, str) or not isinstance(direct_topic, str):
        return value if isinstance(value, str) else ""
    topic = direct_topic.strip()
    if not topic or not re.search(re.escape(topic), value, flags=re.IGNORECASE):
        return value
    escaped = re.escape(topic)
    framing_patterns = (
        rf"\b(?:use|using)\s+(?P<topic>{escaped})\s+as\s+(?:the|my|a)\s+(?:topic|subject)\b",
        rf"[\"'“‘](?P<topic>{escaped})[\"'”’]\s+as\s+(?:the|my|a)\s+(?:topic|subject)\b",
        rf"\b(?:topic|subject)\s*(?:is|:)?\s*[\"'“‘]?(?P<topic>{escaped})",
        rf"\b(?:ideas?|videos?|content)\s+(?:about|on)\s+[\"'“‘]?(?P<topic>{escaped})",
        rf"(?:用|使用|以|把)[\"'“‘]?(?P<topic>{escaped})[\"'”’]?\s*(?:作为|当作|设为)(?:我的|本次|这个)?(?:主题|选题方向)",
        rf"(?:关于|围绕)[\"'“‘]?(?P<topic>{escaped})[\"'”’]?.{{0,10}}(?:选题|内容|视频|主题)",
        rf"(?:主题|选题方向)\s*(?:是|为|:|：)?\s*[\"'“‘]?(?P<topic>{escaped})",
    )
    for pattern in framing_patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match:
            start, end = match.span("topic")
            return value[:start] + " " + value[end:]
    return value


def has_substantive_topic(value: Any) -> bool:
    canonical = canonical_phrase(value)
    if not canonical:
        return False
    remainder = canonical
    for fragment in sorted(GENERIC_TOPIC_FRAGMENTS, key=len, reverse=True):
        if re.fullmatch(r"[a-z0-9]+", fragment):
            remainder = re.sub(rf"\b{re.escape(fragment)}\b", " ", remainder)
        else:
            remainder = remainder.replace(fragment, " ")
    remainder = re.sub(r"\b\d+\b", " ", remainder)
    return len(re.sub(r"[\W_]+", "", remainder, flags=re.UNICODE)) >= 2


def detect_required_sources(value: Any) -> set[str]:
    """Detect explicit evidence use independently from model-authored fields."""

    if not isinstance(value, str):
        return set()
    detected: set[str] = set()
    sentences = re.split(r"[。.!！?？;；\n]+", value.casefold())
    for sentence in sentences:
        segments = [
            item for item in re.split(
                r"[,，]+|\b(?:but|however|yet|although|though|whereas|while)\b|"
                r"(?:但是|但|不过|然而|可是|尽管|虽然|而非|而不是)",
                sentence,
                flags=re.IGNORECASE,
            )
            if item.strip()
        ]
        evidence_segments = [evidence_segment_content(segment) for segment in segments]
        positive_intent = any(
            any(
                re.search(pattern, segment, flags=re.IGNORECASE)
                for pattern in EVIDENCE_INTENT_PATTERNS + IMPERATIVE_EVIDENCE_INTENT_PATTERNS
            )
            for segment in evidence_segments
        ) or brief_authorizes_fallback(sentence)
        for segment in evidence_segments:
            if not segment:
                continue
            if not positive_intent:
                continue
            for source, patterns in EVIDENCE_SOURCE_TERMS.items():
                if any(re.search(pattern, segment, flags=re.IGNORECASE) for pattern in patterns):
                    detected.add(source)
    return detected


def evidence_segment_content(value: str) -> str:
    """Return the positive prefix of one evidence segment.

    A leading negation excludes the whole segment. Mid-segment exclusions only
    remove their tail, so "assess public metrics without private analytics"
    still requires the public account source without treating the private tail
    as requested evidence.
    """

    normalized = value.strip().casefold()
    subject_negation = re.search(
        r"\b(?:i|we|you|they|he|she|it)\s+(?:(?:do|does|did|will|can|should|would|could|must)\s+not|cannot|"
        r"(?:don|doesn|didn|won|can|shouldn|wouldn|couldn|mustn)['’]t)\s+"
        r"(?:want\s+(?:you\s+)?to\s+)?(?:use|require|need|include|consider|review|analy[sz]e|"
        r"research|compare|assess|evaluate|benchmark|examine|check|inspect|audit)\b|"
        r"(?:我|我们|你|你们|他们|她们|它们)?(?:不|不要|不想)(?:让你)?(?:使用|分析|研究|对比|比较|"
        r"复盘|评估|检查|审查|核查|衡量)",
        normalized,
        flags=re.IGNORECASE,
    )
    if subject_negation:
        return normalized[:subject_negation.start()].strip()
    if re.search(
        r"\b(?:(?:is|are|was|were)\s+not|(?:isn|aren|wasn|weren)['’]t)\s+"
        r"(?:required|needed|used|included|available)\b",
        normalized,
        flags=re.IGNORECASE,
    ):
        return ""
    if re.search(
        r"^(?:please\s+)?(?:(?:(?:do|will|can|should|would|could|must)\s+not|cannot|"
        r"(?:don|won|can|shouldn|wouldn|couldn|mustn)['’]t|not|no)\b|"
        r"(?:avoid(?:s|ed|ing)?|exclud(?:e|es|ed|ing)|omit(?:s|ted|ting)?|"
        r"ignor(?:e|es|ed|ing)|skip(?:s|ped|ping)?)(?:\s+using)?\b|(?:use|using)\s+no\b)|"
        r"^(?:不要求|不需要|无需|无须|不要使用|不使用|不用|不要|别|不看|排除|忽略|跳过|不是|而非)",
        normalized,
        flags=re.IGNORECASE,
    ):
        return ""
    exclusion = re.search(
        r"\b(?:rather\s+than|instead\s+of|excluding|without)\b|(?:而非|而不是|排除|不包括)",
        normalized,
        flags=re.IGNORECASE,
    )
    if exclusion:
        return normalized[:exclusion.start()].strip()
    return normalized


def quoted_from_brief(quote: Any, brief: Any) -> bool:
    """Is `quote` a span the user actually wrote in `brief`?

    The permission to generate without a requested source is the user's to give, and they give it
    in their own words. Deciding whether they gave it by matching phrases only ever worked in the
    languages the phrases were written in: below, «If trend data is missing, still generate ideas
    from my brief» is recognised and «If you cannot find trend data, just use my topic and give me
    ideas anyway» is not, and nothing outside Chinese and English is recognised at all — so an
    English or Spanish user who authorised the fallback in plain words had the request refused.

    What can be checked in any language is grounding: the model names the span it read the
    permission in, and that span has to be in the brief, not invented. Whitespace is normalised
    because a quote copied out of a wrapped brief keeps the line breaks it was wrapped with.
    """
    if not isinstance(quote, str) or not isinstance(brief, str):
        return False
    tight = " ".join(quote.split())
    return len(tight) >= 8 and tight in " ".join(brief.split())


def brief_denies_fallback(value: Any) -> bool:
    """Does the brief refuse the fallback outright?

    A veto, and it outranks any quote: a brief that says «do not allow fallback if account
    evidence is missing» contains spans that read like permission once they are cut out of it,
    so a quote must never be able to turn a refusal into an authorization. It recognises only
    the wordings it lists, as it always has — a refusal in a third language is not detected
    here, and the flag then rests on the quote the model supplies.
    """
    if not isinstance(value, str):
        return False
    return bool(re.search(
        r"(?:不要|不允许|不能|永不|绝不).{0,16}(?:降级|兜底|生成|继续)|"
        r"(?:never|(?:do|does|did|will|can|should|would|could|must)\s+not|cannot|"
        r"(?:don|doesn|didn|won|can|shouldn|wouldn|couldn|mustn)['’]t)\s+"
        r"(?:ever\s+)?(?:want\s+(?:you\s+)?to\s+)?"
        r"(?:(?:allow|permit|enable|use)\s+(?:any\s+)?)?"
        r"(?:fall\s*back|fallback|proceed|generate|continue)|"
        r"(?:降级|兜底|fall\s*back|fallback).{0,16}(?:不允许|禁止|not\s+allowed|forbidden)|"
        r"\bno\s+(?:fall\s*back|fallback)\b",
        value,
        flags=re.IGNORECASE,
    ))


def brief_authorizes_fallback(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if brief_denies_fallback(value):
        return False
    patterns = (
        r"(?:缺少|没有|拿不到|不可用|无法获得).{0,40}(?:也|仍然|仍|继续|照样).{0,24}(?:生成|给|按|基于|使用).{0,32}(?:选题|想法|主题|方向|brief)?",
        r"允许.{0,16}(?:降级|兜底)",
        r"(?:if|when).{0,80}(?:missing|unavailable|absent).{0,48}(?:still\s+)?(?:generate|create|give|use|fall\s+back|proceed).{0,32}(?:ideas?|topic|brief|anyway)",
        r"(?:proceed|go\s+ahead|continue).{0,48}(?:topic|brief|ideas?).{0,96}(?:if.{0,48}(?:missing|unavailable|absent)|even\s+without.{0,48}(?:data|evidence|report))",
        r"allow.{0,16}fallback",
    )
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns)


def has_usable_account_context(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    coverage = value.get("coverage") or {}
    content_total = coverage.get("content_total") if isinstance(coverage, dict) else None
    return isinstance(content_total, int) and not isinstance(content_total, bool) and content_total > 0


def available_generation_sources(request: dict[str, Any]) -> set[str]:
    """Return evidence classes actually available to satisfy generation requirements."""
    context = request.get("idea_context") or {}
    source_types = {
        source.get("source_type") for source in request.get("upstream_sources") or []
    }
    available: set[str] = set()
    creator = context.get("creator") or {}
    if any(
        creator.get(field)
        for field in ("markets", "audience", "positioning", "content_profile")
    ):
        available.add("creator")
    if has_usable_account_context(context.get("account")):
        available.add("account")
    if context.get("trend"):
        available.add("trend")
    if context.get("competitor"):
        available.add("competitor")
    if "video_analysis" in source_types:
        available.add("video")
    comments = context.get("comments") or {}
    if "comment_context" in source_types and any(has_comment_text(row) for row in comments.get("comments", [])):
        available.add("comments")
    return available


def generation_decision(request: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic pre-synthesis decision for a valid request."""

    basis = request.get("generation_basis") or {}
    direct_topic = basis.get("direct_topic")
    has_direct_topic = has_text(direct_topic)
    required = set(basis.get("required_sources") or [])
    available = available_generation_sources(request)
    missing = [name for name in GENERATION_SOURCE_ORDER if name in required - available]
    fallback_used = bool(
        missing and basis.get("allow_brief_fallback") is True and has_direct_topic
    )
    if missing and not fallback_used:
        status = "blocked_missing_required_source"
    elif not available and not has_direct_topic and not (request.get("continuation_context") or request.get("conversation_context")):
        status = "needs_selection_context"
    else:
        status = "ready_for_generation"
    return {
        "status": status,
        "available_sources": [name for name in GENERATION_SOURCE_ORDER if name in available],
        "missing_required_sources": missing,
        "direct_topic_available": has_direct_topic,
        "brief_fallback_used": fallback_used,
    }


def required_source_reasons(decision: dict[str, Any]) -> set[str]:
    return {
        f"missing_required_source:{name}"
        for name in decision.get("missing_required_sources") or []
    }


def derive_source_mode(request: dict[str, Any]) -> str:
    """Derive the contract source mode from the supplied artifacts and contexts."""
    context = request.get("idea_context") or {}
    creator = bool(context.get("creator")) or any(
        source.get("source_type") == "creator_profile" for source in request.get("upstream_sources") or []
    )
    external = bool(context.get("trend") or context.get("competitor") or context.get("comments"))
    account = bool(context.get("account")) or any(
        source.get("source_type") == "account_idea_context" for source in request.get("upstream_sources") or []
    )
    videos = any(
        source.get("source_type") == "video_analysis" for source in request.get("upstream_sources") or []
    )
    if external and (creator or account or videos):
        return "evidence_rich"
    if external:
        return "context_only"
    if (creator or account) and videos:
        return "profile_video"
    if creator or account:
        return "profile_only"
    if videos:
        return "video_only"
    return "brief_only"


def validate_authored_fields(request: dict[str, Any], errors: list[str]) -> None:
    """Check structurally valid authored fields without reading any upstream files."""
    try:
        report_language(request)
    except ValueError as exc:
        errors.append(str(exc))

    for field in ("goal", "audience", "desired_audience_action"):
        item = request.get(field) or {}
        status = item.get("status")
        if status == "missing":
            require(item.get("value") is None, f"missing {field} must have null value", errors)
        elif status in {"hypothesis", "confirmed"}:
            require(has_text(item.get("value")), f"{field} value is required for {status}", errors)

    brief = request.get("user_brief") or {}
    has_brief = brief.get("status") == "provided" and has_text(brief.get("text"))
    if brief.get("status") == "absent":
        require(brief.get("text") is None, "absent user_brief must have null text", errors)
    elif brief.get("status") == "provided":
        require(has_brief, "provided user_brief must contain text", errors)

    basis = request.get("generation_basis") or {}
    direct_topic = basis.get("direct_topic")
    has_direct_topic = has_text(direct_topic)
    quote = basis.get("continuation_quote")
    if quote is not None:
        require(has_brief and has_text(quote) and quote in brief.get("text", ""),
                "generation_basis.continuation_quote must be copied verbatim from user_brief.text", errors)
    if direct_topic is not None:
        require(has_direct_topic, "generation_basis.direct_topic must contain text or be null", errors)
    if has_direct_topic:
        topic = canonical_phrase(direct_topic)
        semantic_brief = canonical_phrase(brief_text_without_identifiers(brief.get("text")))
        require(has_brief, "generation_basis.direct_topic requires a provided user_brief", errors)
        require(
            "@" not in direct_topic and not re.search(r"(?:https?://|www\.)", direct_topic, re.IGNORECASE),
            "generation_basis.direct_topic must not be an account handle or URL",
            errors,
        )
        require(
            has_substantive_topic(direct_topic),
            "generation_basis.direct_topic must name a content topic, not only a platform, format, or generic idea request",
            errors,
        )
        require(
            bool(topic) and topic in semantic_brief,
            "generation_basis.direct_topic must be a literal user-supplied phrase outside handles and URLs",
            errors,
        )
    declared_required_sources = set(basis.get("required_sources") or [])
    evidence_brief = brief_without_framed_topic(brief.get("text"), direct_topic)
    # A video mentioned only as the location of comments is not a request to
    # analyse its content. Remove only the local noun phrase; a separate request
    # about this video's visuals/structure remains visible to source detection.
    evidence_brief = re.sub(
        r"(?:这条|这个|上述|上面)(?:的)?(?:视频|内容)(?:下面的|里的|中的|下的|的|下|里)?(?:评论区|评论|留言)"
        r"|(?:this|that)\s+(?:video|post)(?:['’]s)?\s+comments?"
        r"|comments?\s+(?:on|under|from|of)\s+(?:this|that)\s+(?:video|post)",
        "comments", evidence_brief, flags=re.I,
    )
    detected_required_sources = detect_required_sources(evidence_brief)
    undeclared_required_sources = detected_required_sources - declared_required_sources
    require(
        not undeclared_required_sources,
        "generation_basis.required_sources omitted explicit user evidence requests: "
        + ", ".join(name for name in GENERATION_SOURCE_ORDER if name in undeclared_required_sources),
        errors,
    )
    if basis.get("allow_brief_fallback") is True:
        require(
            has_direct_topic,
            "allow_brief_fallback=true requires an explicit generation_basis.direct_topic",
            errors,
        )
        # A brief that refuses the fallback refuses it however the flag is argued for.
        text = brief.get("text")
        if brief_denies_fallback(text):
            require(
                False,
                "allow_brief_fallback=true requires explicit fallback authorization in "
                "user_brief.text, which refuses it",
                errors,
            )
        else:
            # Either the model quotes the span it read the permission in — which works in any
            # language — or the brief is phrased one of the ways the older check recognises. The
            # phrase list stays so existing requests, which carry no quote, keep validating
            # unchanged; on its own it refused every language it was not written in.
            quote = basis.get("brief_fallback_quote")
            require(
                isinstance(quote, str) and bool(quote.strip()) and quoted_from_brief(quote, text),
                "allow_brief_fallback=true requires explicit fallback authorization in "
                "user_brief.text: quote the span that gave it in "
                "generation_basis.brief_fallback_quote, word for word"
                + (" (the quote given is not in the brief)" if quote else " (quote missing)"),
                errors,
            )

    intent = request.get("task_intent")
    idea_count = request.get("idea_count")
    hook_count = request.get("hook_option_count")
    if intent == "rework_content":
        require(idea_count == 1, "rework_content requires idea_count=1", errors)
        require(isinstance(hook_count, int) and 3 <= hook_count <= 5, "rework_content requires 3..5 Hook options", errors)
    elif intent in {"open_shortlist", "capacity_plan", "brief_only"}:
        require(isinstance(idea_count, int) and 3 <= idea_count <= 10, f"{intent} requires idea_count=3..10", errors)
        require(hook_count in {1, 2}, f"{intent} requires hook_option_count=1 or 2", errors)

    if intent == "brief_only":
        require(has_brief, "brief_only requires a provided brief", errors)
        require(not (basis.get("required_sources") or []), "brief_only must not declare required external sources", errors)
    if intent == "capacity_plan":
        constraints = request.get("production_constraints") or {}
        require(
            isinstance(constraints.get("total_time_minutes"), int)
            and constraints["total_time_minutes"] > 0,
            "capacity_plan requires production_constraints.total_time_minutes",
            errors,
        )
        require(bool(constraints.get("available_assets")), "capacity_plan requires declared available_assets", errors)

    constraints = request.get("production_constraints") or {}
    constraint_values = [
        constraints.get("total_time_minutes"),
        constraints.get("time_per_item"),
        constraints.get("budget"),
        *(constraints.get("available_assets") or []),
        constraints.get("filming_mode"),
        constraints.get("editing_capacity"),
        constraints.get("publishing_cadence"),
    ]
    present = [value for value in constraint_values if value is not None and value != ""]
    if constraints.get("status") == "absent":
        require(not present, "absent production_constraints must not contain values or assets", errors)
    elif constraints.get("status") in {"partial", "confirmed"}:
        require(bool(present), f"{constraints.get('status')} production_constraints must contain at least one value", errors)


def validate_authored_intent(intent: dict[str, Any], *, has_previous_ideas: bool = False,
                             has_conversation_context: bool = False) -> list[str]:
    """Reuse request rules for input feedback before missing artifacts can mask errors.

    Reject authored derived fields without hiding independent input errors.
    Build and full request validation still own their provenance and evidence gates.
    """
    schema = load(REQUEST_SCHEMA)
    # Legacy intents may still carry source_mode; build always recomputes it.
    derived = {*DERIVED_FIELDS, "source_mode"}
    authored_schema = {**schema,
        "required": [key for key in schema["required"] if key not in derived],
        "properties": {key: value for key, value in schema["properties"].items() if key not in derived}}
    authored = {key: value for key, value in intent.items() if key not in derived}
    errors = [f"request schema {failure}" for failure in validate_schema_value(authored, authored_schema, schema, "$")]
    if not errors:
        validate_authored_fields(authored, errors)
        quote = (authored.get("generation_basis") or {}).get("continuation_quote")
        require(not (has_previous_ideas and has_conversation_context),
                "choose either --previous-ideas or --conversation-context for this continuation", errors)
        if has_previous_ideas or has_conversation_context:
            flag = "--previous-ideas" if has_previous_ideas else "--conversation-context"
            require(has_text(quote), f"{flag} requires generation_basis.continuation_quote", errors)
        elif quote is not None:
            errors.append("generation_basis.continuation_quote requires --previous-ideas with the original IdeaSet JSON or --conversation-context with exact earlier visible messages")
    errors.extend(f"request.{field}: is derived from source artifacts; leave it out of intent and supply the original source file instead"
                  for field in DERIVED_FIELDS if field in intent)
    return errors


def validate_request(request: dict[str, Any], request_path: Path) -> list[str]:
    """Validate idea inputs, explicit evidence requirements and source alignment."""
    errors: list[str] = []
    validate_json_schema(request, REQUEST_SCHEMA, "request", errors)
    # Semantic checks dereference the schema's objects and arrays. Return all
    # structural errors first so callers can repair invalid input in one pass.
    if errors:
        return errors
    validate_authored_fields(request, errors)
    brief = request.get("user_brief") or {}
    has_brief = brief.get("status") == "provided" and has_text(brief.get("text"))
    basis = request.get("generation_basis") or {}
    has_direct_topic = has_text(basis.get("direct_topic"))
    continuation = request.get("continuation_context")
    conversation = request.get("conversation_context")
    quote = basis.get("continuation_quote")
    require(not (continuation is not None and conversation is not None),
            "choose either continuation_context or conversation_context for this continuation", errors)
    if continuation is not None:
        require(has_brief and has_text(quote) and quote in brief.get("text", ""),
                "continuation_context requires a verbatim generation_basis.continuation_quote from user_brief.text", errors)
        require(continuation["request_id"] != request["request_id"],
                "continuation_context must refer to an earlier request", errors)
        try:
            expected = previous_ideas_context(resolve(request_path.parent, continuation["path"]), request_path.parent)
            require(continuation == expected, "continuation_context differs from the original previous IdeaSet or its checksum", errors)
        except (OSError, ValueError) as exc:
            errors.append(f"continuation_context: {exc}")
    if conversation is not None:
        require(has_brief and has_text(quote) and quote in brief.get("text", ""),
                "conversation_context requires a verbatim generation_basis.continuation_quote from user_brief.text", errors)
        try:
            expected = bound_conversation_context(resolve(request_path.parent, conversation["path"]), request_path.parent)
            require(conversation == expected, "conversation_context differs from the supplied messages, reference or checksum", errors)
        except (OSError, ValueError) as exc:
            errors.append(f"conversation_context: {exc}")
    if continuation is None and conversation is None and quote is not None:
        errors.append("generation_basis.continuation_quote requires --previous-ideas with the original IdeaSet JSON or --conversation-context with exact earlier visible messages")
    context = request.get("idea_context") or {}
    has_evidence = bool(request.get("upstream_sources") or context.get("creator") or context.get("trend") or context.get("competitor") or context.get("account"))
    require(has_evidence or has_brief, "at least one evidence source or user brief is required", errors)
    expected_mode = derive_source_mode(request)
    require(request.get("source_mode") == expected_mode, f"source_mode must be {expected_mode}", errors)

    intent = request.get("task_intent")
    if intent == "rework_content":
        require(any(source.get("source_type") == "video_analysis" for source in request.get("upstream_sources") or []), "rework_content requires at least one VideoAnalysis source", errors)
    if intent == "brief_only":
        require(has_direct_topic or continuation is not None or conversation is not None,
                "brief_only requires an explicit generation_basis.direct_topic or bound continuation_context/conversation_context", errors)
        require(not has_evidence, "brief_only must not include upstream artifacts or idea context", errors)

    validate_upstream_sources(request, request_path, errors)
    validate_context_alignment(request, errors)
    return errors


V3_SOURCE_VERSIONS = {
    "creator_profile": "creator-profile.v3",
    "account_review_report": "account-review-report.v3",
    "account_idea_context": "account-idea-context.v1",
    "trend_report": "trend-research-report.v3",
    "competitor_report": "competitor-research-report.v3",
    "comment_context": "comment-idea-context.v1",
}


def validate_native_source(source: dict[str, Any], artifact: dict[str, Any], errors: list[str]) -> None:
    """Validate the stable V3 boundary before the Idea model sees a projection."""
    source_type = source.get("source_type")
    expected_version = V3_SOURCE_VERSIONS.get(source_type)
    if expected_version:
        require(source.get("schema_version") == expected_version, f"{source_type} must declare {expected_version}", errors)
        artifact_version = artifact.get("schema_version")
        if source_type == "account_idea_context":
            # The frozen upstream AccountIdeaContext envelope is {"account": ...}
            # and deliberately carries its contract version in the lineage ref.
            # Accept a future annotated envelope only when it agrees with lineage.
            require(
                artifact_version in {None, expected_version},
                f"{source_type} embedded schema_version must match {expected_version}",
                errors,
            )
        else:
            require(artifact_version == expected_version, f"{source_type} must be {expected_version}", errors)
    if source_type == "creator_profile":
        require(isinstance(artifact.get("profile_id"), str), "CreatorProfile v3 requires profile_id", errors)
        accounts = artifact.get("accounts")
        require(isinstance(accounts, list) and bool(accounts), "CreatorProfile v3 requires a non-empty accounts array", errors)
        require("profile_status" not in artifact, "CreatorProfile v3 must not contain profile_status", errors)
    elif source_type in {"trend_report", "competitor_report"}:
        required = {"report_id", "scope", "coverage", "overview", "patterns", "data_gaps"}
        if source_type == "competitor_report":
            required |= {"competitors", "top_content", "outliers", "gaps"}
        else:
            required |= {"top_content", "emerging_creators"}
        require(required <= set(artifact), f"{source_type} is missing V3 report fields", errors)
        require(isinstance(artifact.get("patterns"), list), f"{source_type}.patterns must be an array", errors)
        for index, pattern in enumerate(artifact.get("patterns") or []):
            require(isinstance(pattern, dict), f"{source_type}.patterns[{index}] must be an object", errors)
            if isinstance(pattern, dict):
                for field in ("pattern", "mechanism", "content_promise", "differentiation", "must_avoid", "proof_points"):
                    require(field in pattern, f"{source_type}.patterns[{index}] missing {field}", errors)
    elif source_type == "account_idea_context":
        account = artifact.get("account")
        require(isinstance(account, dict), "AccountIdeaContext requires account", errors)
        if isinstance(account, dict):
            for field in ("platform", "window", "coverage", "baselines", "diagnosis", "what_works", "what_hurts", "recommendations", "data_gaps"):
                require(field in account, f"AccountIdeaContext.account missing {field}", errors)
    elif source_type == "account_review_report":
        require(isinstance(artifact.get("report_id"), str), "AccountReviewReport v3 requires report_id", errors)
    elif source_type == "video_analysis":
        require(isinstance(artifact.get("analysis_id"), str), "VideoAnalysis requires analysis_id", errors)


def validate_upstream_sources(request: dict[str, Any], request_path: Path, errors: list[str]) -> None:
    """Verify artifact identity, version, checksum and native comment projection."""
    seen_ids: set[str] = set()
    for source in request.get("upstream_sources") or []:
        artifact_id = source.get("artifact_id")
        require(artifact_id not in seen_ids, f"duplicate upstream artifact_id: {artifact_id}", errors)
        seen_ids.add(artifact_id)
        path = resolve(request_path.parent, source.get("path", ""))
        artifact = read_artifact(path, f"{source.get('source_type')} artifact", errors)
        if artifact is None:
            continue
        expected_id_field = "analysis_id" if source.get("source_type") == "video_analysis" else "report_id"
        if source.get("source_type") == "creator_profile":
            expected_id_field = "profile_id"
        if source.get("source_type") not in {"account_idea_context"}:
            require(artifact.get(expected_id_field) == artifact_id, f"{source.get('source_type')} artifact_id mismatch", errors)
        source_type = source.get("source_type")
        artifact_version = artifact.get("schema_version")
        if source_type == "account_idea_context":
            require(
                artifact_version in {None, source.get("schema_version")},
                f"{source_type} schema_version mismatch",
                errors,
            )
        else:
            require(artifact_version == source.get("schema_version"), f"{source_type} schema_version mismatch", errors)
        require(source.get("sha256") == file_sha256(path), f"{source.get('source_type')} sha256 mismatch", errors)
        validate_native_source(source, artifact, errors)
        if source_type == "comment_context":
            try:
                validate_projection(artifact, path.parent)
                validate_target(artifact, (request.get("user_brief") or {}).get("text"))
                require((request.get("idea_context") or {}).get("comments") == artifact,
                        "idea_context.comments must equal its validated artifact", errors)
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append(f"invalid comment context: {error}")


def validate_context_alignment(request: dict[str, Any], errors: list[str]) -> None:
    """Ensure every projected context is backed by its declared source type."""
    context = request.get("idea_context") or {}
    source_types = {source.get("source_type") for source in request.get("upstream_sources") or []}
    if context.get("creator") is not None:
        require("creator_profile" in source_types, "idea_context.creator requires a creator_profile source", errors)
        require(context["creator"].get("schema_version") == "creator-profile.v3", "idea_context.creator must be CreatorProfile v3", errors)
    if context.get("trend"):
        require("trend_report" in source_types, "idea_context.trend requires a trend_report source", errors)
    if context.get("competitor"):
        require("competitor_report" in source_types, "idea_context.competitor requires a competitor_report source", errors)
    if context.get("comments") is not None:
        require("comment_context" in source_types, "idea_context.comments requires comment_context source", errors)
    if context.get("account") is not None:
        require("account_idea_context" in source_types, "idea_context.account requires an account_idea_context source", errors)
    if "account_review_report" in source_types:
        require("account_idea_context" in source_types, "account_review_report must be projected to account_idea_context before Idea synthesis", errors)


def collect_values(node: Any, key: str) -> set[str]:
    values: set[str] = set()
    if isinstance(node, dict):
        for name, value in node.items():
            if name == key:
                if isinstance(value, str) and value:
                    values.add(value)
                elif isinstance(value, list):
                    values.update(item for item in value if isinstance(item, str) and item)
            values.update(collect_values(value, key))
    elif isinstance(node, list):
        for value in node:
            values.update(collect_values(value, key))
    return values


def collect_videos(
    request: dict[str, Any], request_path: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    videos: dict[str, dict[str, Any]] = {}
    evidence_by_video: dict[str, set[str]] = {}
    for source in request.get("upstream_sources") or []:
        if source.get("source_type") != "video_analysis":
            continue
        artifact = load(resolve(request_path.parent, source["path"]))
        analysis_id = artifact.get("analysis_id")
        videos[analysis_id] = artifact
        evidence = {analysis_id}
        evidence.update(collect_values(artifact, "evidence_ref"))
        evidence.update(collect_values(artifact, "evidence_refs"))
        evidence.update(collect_values(artifact, "observation_id"))
        evidence.update(collect_values(artifact, "revision_id"))
        evidence_by_video[analysis_id] = evidence
    return videos, evidence_by_video


def normalized(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def validate_gate_state(idea_set: dict[str, Any], errors: list[str]) -> None:
    status = idea_set.get("status")
    gates = idea_set.get("gates") or {}
    selection = gates.get("selection_missing") or []
    production = gates.get("production_missing") or []
    blocked = gates.get("blocked_reasons") or []
    actions = set((idea_set.get("interaction") or {}).get("next_actions") or [])

    if status == "ready_for_selection":
        require(not selection, "ready_for_selection must not have selection_missing gates", errors)
        require(not blocked, "ready_for_selection must not have blocked_reasons", errors)
        require("choose" in actions, "ready_for_selection must offer choose", errors)
    elif status == "needs_selection_context":
        require(bool(selection), "needs_selection_context requires selection_missing gates", errors)
        require(not blocked, "needs_selection_context must not have blocked_reasons", errors)
        require(
            "answer_selection_question" in actions,
            "needs_selection_context must offer answer_selection_question",
            errors,
        )
    elif status in {"blocked_invalid_or_unsafe_input", "blocked_missing_required_source"}:
        require(bool(blocked), f"{status} requires blocked_reasons", errors)
        require("blocked" in actions, f"{status} must expose blocked action", errors)
        require("choose" not in actions, f"{status} must not offer choose", errors)

    if production:
        require(
            "answer_production_question" in actions or status.startswith("blocked_"),
            "production_missing must offer answer_production_question without blocking selection",
            errors,
        )


def validate_idea_set(
    idea_set: dict[str, Any], request: dict[str, Any], request_path: Path
) -> list[str]:
    """Check the output against request scope, evidence lineage and selection gates."""
    errors = validate_request(request, request_path)
    validate_json_schema(idea_set, IDEA_SET_SCHEMA, "idea_set", errors)
    if errors:
        # Report missing presentation translations together with schema errors:
        # core allows two generation attempts, so a second round that only then
        # learns about the translations runs out of attempts.
        for project in (evidence_gaps, report_value_translations):
            try:
                project(idea_set)
            except (ValueError, TypeError, AttributeError) as exc:
                errors.append(str(exc))
                break
        return errors
    decision = generation_decision(request)

    require(idea_set.get("request_id") == request.get("request_id"), "idea_set request_id mismatch", errors)
    try:
        output_language, request_language = report_language(idea_set), report_language(request)
        # Catalog lookup uses the primary language, but an explicit script or
        # region is part of the requested output contract. Preserve the existing
        # fallback only for already-recognized legacy Chinese omissions.
        if "report_language" in idea_set and "report_language" in request:
            output_language, request_language = idea_set["report_language"].lower(), request["report_language"].lower()
        require(output_language == request_language, "idea_set report_language mismatch", errors)
    except ValueError as exc:
        errors.append(str(exc))
    for project in (evidence_gaps, report_value_translations):
        try:
            project(idea_set)
        except ValueError as exc:
            errors.append(str(exc))
    require(idea_set.get("task_intent") == request.get("task_intent"), "idea_set task_intent mismatch", errors)
    require(idea_set.get("source_mode") == request.get("source_mode"), "idea_set source_mode mismatch", errors)

    ideas = idea_set.get("ideas") or []
    status = idea_set.get("status")
    blocked_statuses = {"blocked_invalid_or_unsafe_input", "blocked_missing_required_source"}
    is_blocked = status in blocked_statuses
    decision_requires_no_ideas = decision["status"] in {
        "blocked_missing_required_source",
        "needs_selection_context",
    }
    if decision["status"] == "blocked_missing_required_source":
        require(
            status == "blocked_missing_required_source",
            "missing required generation sources must yield status=blocked_missing_required_source",
            errors,
        )
        blocked_reasons = set((idea_set.get("gates") or {}).get("blocked_reasons") or [])
        require(
            required_source_reasons(decision) <= blocked_reasons,
            "blocked_missing_required_source must name every missing source as missing_required_source:<source>",
            errors,
        )
        actions = set((idea_set.get("interaction") or {}).get("next_actions") or [])
        require(
            {"refresh_evidence", "provide_own_direction"} <= actions,
            "missing-source block must offer refresh_evidence and provide_own_direction",
            errors,
        )
    elif decision["status"] == "needs_selection_context":
        require(
            status == "needs_selection_context",
            "a request without evidence or an explicit topic must yield status=needs_selection_context",
            errors,
        )
        selection_missing = set((idea_set.get("gates") or {}).get("selection_missing") or [])
        require(
            "explicit_content_topic_or_upstream_evidence" in selection_missing,
            "needs_selection_context must request explicit_content_topic_or_upstream_evidence",
            errors,
        )
        actions = set((idea_set.get("interaction") or {}).get("next_actions") or [])
        require(
            "provide_own_direction" in actions,
            "needs_selection_context must offer provide_own_direction",
            errors,
        )
    else:
        require(
            status != "blocked_missing_required_source",
            "blocked_missing_required_source is only valid when required generation sources are missing",
            errors,
        )
        require(
            status != "needs_selection_context",
            "needs_selection_context is only valid when neither evidence nor an explicit topic is available",
            errors,
        )

    if is_blocked or decision_requires_no_ideas:
        require(not ideas, f"{status} must return an empty ideas array", errors)
    else:
        require(len(ideas) == request.get("idea_count"), "ideas count must equal request.idea_count", errors)
    ids = [item.get("idea_id") for item in ideas]
    ranks = [item.get("rank") for item in ideas]
    titles = [normalized(item.get("working_title")) for item in ideas]
    require(len(set(ids)) == len(ids), "idea_id values must be unique", errors)
    require(sorted(ranks) == list(range(1, len(ideas) + 1)), "ranks must be unique and sequential", errors)
    require(len(set(titles)) == len(titles), "working titles must be distinct", errors)
    previous_titles = {normalized(item["working_title"])
                       for item in (request.get("continuation_context") or {}).get("ideas", [])}
    require(not previous_titles.intersection(titles), "continuation must not repeat a previous working title", errors)

    overview = idea_set.get("overview") or {}
    recommended = overview.get("recommended_candidate_ids") or []
    if is_blocked or decision_requires_no_ideas:
        require(not recommended, f"{status} must not recommend candidates", errors)
    require(set(recommended) <= set(ids), "recommended_candidate_ids contain unknown ideas", errors)
    ranked = sorted(ideas, key=lambda item: item.get("rank", 10**9))
    if ranked and recommended:
        require(recommended[0] == ranked[0].get("idea_id"), "rank 1 must be the first recommended candidate", errors)

    try:
        videos, evidence_by_video = collect_videos(request, request_path)
    except (OSError, json.JSONDecodeError, KeyError) as error:
        errors.append(f"could not load validated source artifacts: {type(error).__name__}: {error}")
        videos, evidence_by_video = {}, {}

    context = request.get("idea_context") or {}
    upstream_sources = request.get("upstream_sources") or []
    upstream_ids = {source.get("artifact_id") for source in upstream_sources}
    allowed_context_refs = set(upstream_ids)
    allowed_context_refs.add("creator")
    allowed_context_refs.add("account")
    allowed_context_refs.update(f"trend:{index}" for index, _ in enumerate(context.get("trend") or []))
    allowed_context_refs.update(f"competitor:{index}" for index, _ in enumerate(context.get("competitor") or []))
    allowed_context_refs.update(f"video:{video_id}" for video_id in videos)
    context_evidence_refs = set(collect_values(context, "content_refs")) | set(collect_values(context, "proof_points"))
    context_evidence_refs |= set(collect_values(context, "pattern"))
    comment_context = context.get("comments") or {}
    comment_refs = {row["ref"] for row in comment_context.get("comments", []) if has_comment_text(row)}
    context_evidence_refs.update(comment_refs)
    allowed_context_refs.update(comment_refs)
    allowed_video_ids = set(videos)
    creator_context = context.get("creator") or {}
    profile_pending = bool(creator_context.get("key_missing_info")) or not any(
        creator_context.get(field) for field in ("markets", "audience", "positioning", "content_profile")
    )
    adopted_context_refs: set[str] = set()
    adopted_videos: set[str] = set()
    max_total_minutes = 0
    available_assets = set((request.get("production_constraints") or {}).get("available_assets") or [])
    target_platforms = set(request.get("platform_targets") or [])
    excluded_formats = {normalized(value) for value in ((request.get("exclusions") or {}).get("formats") or [])}
    excluded_assets = {normalized(value) for value in ((request.get("exclusions") or {}).get("assets") or [])}
    intent = request.get("task_intent")
    hook_count = request.get("hook_option_count")

    for item in ideas:
        prefix = item.get("idea_id") or "<unknown>"
        basis = item.get("evidence_basis")
        require(basis in {"source_backed", "brief_based", "hypothesis"}, f"{prefix}: invalid evidence_basis", errors)
        source_context_refs = set(item.get("source_context_refs") or [])
        video_refs = {ref.removeprefix("video:") for ref in source_context_refs if ref.startswith("video:")}
        adopted_context_refs.update(source_context_refs)
        adopted_videos.update(video_refs)
        require(source_context_refs <= allowed_context_refs, f"{prefix}: unknown source context ref", errors)
        require(video_refs <= allowed_video_ids, f"{prefix}: unknown VideoAnalysis ref", errors)

        allowed_video_evidence: set[str] = set()
        for video_ref in video_refs:
            allowed_video_evidence.update(evidence_by_video.get(video_ref, set()))

        actual_evidence = set(item.get("evidence_refs") or [])
        if "comments" in (request.get("generation_basis") or {}).get("required_sources", []) and not decision.get("brief_fallback_used"):
            require(bool(actual_evidence & source_context_refs & comment_refs), f"{prefix}: comment-based ideas must cite a retrieved text comment in both evidence_refs and source_context_refs", errors)
        require(actual_evidence <= upstream_ids | allowed_context_refs | context_evidence_refs | allowed_video_evidence, f"{prefix}: evidence_refs contain values absent from upstream artifacts or projected context", errors)
        required_differences = set(collect_values(context.get("trend") or [], "must_avoid")) | set(collect_values(context.get("competitor") or [], "must_avoid"))
        require(required_differences <= set(item.get("must_differ") or []), f"{prefix}: projected must_avoid constraints were dropped from must_differ", errors)

        primary = item.get("primary_evidence_ref")
        if primary is not None:
            require(primary in actual_evidence, f"{prefix}: primary_evidence_ref must belong to evidence_refs", errors)

        outline = item.get("content_outline") or []
        require(
            [beat.get("order") for beat in outline] == list(range(1, len(outline) + 1)),
            f"{prefix}: outline order must be sequential",
            errors,
        )
        for beat in outline:
            require(
                set(beat.get("evidence_refs") or []) <= actual_evidence,
                f"{prefix}: outline contains evidence_refs not adopted by the idea",
                errors,
            )

        if profile_pending:
            require(
                (item.get("creator_fit") or {}).get("status") != "confirmed",
                f"{prefix}: pending profile cannot yield confirmed Creator fit",
                errors,
            )

        hook_preview = item.get("hook_preview") or {}
        hook_options = item.get("hook_options") or []
        if intent == "rework_content":
            require(len(hook_options) == hook_count, f"{prefix}: hook_options count must equal request.hook_option_count", errors)
            option_ids = [option.get("option_id") for option in hook_options]
            mechanisms = [normalized(option.get("mechanism")) for option in hook_options]
            opening_lines = [normalized(option.get("opening_line")) for option in hook_options]
            require(len(set(option_ids)) == len(option_ids), f"{prefix}: Hook option IDs must be unique", errors)
            require(len(set(mechanisms)) == len(mechanisms), f"{prefix}: rework Hook mechanisms must be distinct", errors)
            require(len(set(opening_lines)) == len(opening_lines), f"{prefix}: rework Hook opening lines must be distinct", errors)
            require(bool(video_refs), f"{prefix}: rework idea must reference a VideoAnalysis", errors)
            if hook_options:
                first = hook_options[0]
                require(
                    hook_preview.get("opening_line") == first.get("opening_line")
                    and hook_preview.get("progressive_line") == first.get("progressive_line")
                    and hook_preview.get("mechanism") == first.get("mechanism"),
                    f"{prefix}: hook_preview must mirror the first recommended Hook option",
                    errors,
                )
        else:
            require(not hook_options, f"{prefix}: hook_options are reserved for rework_content", errors)
            progressive = hook_preview.get("progressive_line")
            if hook_count == 2:
                require(has_text(progressive), f"{prefix}: two-Hook request requires progressive_line", errors)
                require(
                    normalized(progressive) != normalized(hook_preview.get("opening_line")),
                    f"{prefix}: progressive_line must not repeat opening_line",
                    errors,
                )
            elif hook_count == 1:
                require(progressive is None, f"{prefix}: one-Hook request must set progressive_line to null", errors)

        feasibility = item.get("production_feasibility") or {}
        estimate = feasibility.get("estimated_minutes")
        if isinstance(estimate, dict):
            minimum = estimate.get("min")
            maximum = estimate.get("max")
            if isinstance(minimum, int) and isinstance(maximum, int):
                require(minimum <= maximum, f"{prefix}: estimated_minutes.min must be <= max", errors)
                max_total_minutes += maximum
        elif intent == "capacity_plan":
            errors.append(f"{prefix}: capacity_plan requires estimated_minutes")

        assets_used = set(feasibility.get("assets_used") or [])
        required_assets = set(feasibility.get("required_assets") or [])
        item_platforms = set(item.get("platforms") or [])
        require(item_platforms <= target_platforms, f"{prefix}: platforms must be within request platform_targets", errors)
        require(not ({normalized(value) for value in (item.get("formats") or [])} & excluded_formats), f"{prefix}: format is excluded by request", errors)
        require(not ({normalized(value) for value in (feasibility.get("assets_used") or [])} & excluded_assets), f"{prefix}: asset is excluded by request", errors)
        require(
            required_assets <= available_assets,
            f"{prefix}: required_assets must be a subset of request available_assets",
            errors,
        )
        require(assets_used <= available_assets, f"{prefix}: assets_used must be a subset of request available_assets", errors)

        if request.get("source_mode") == "brief_only":
            require(not source_context_refs and not video_refs, f"{prefix}: brief-only idea must not adopt source refs", errors)
            require(not actual_evidence, f"{prefix}: brief-only idea must have empty evidence", errors)
            require(primary is None, f"{prefix}: brief-only idea must have null primary_evidence_ref", errors)
            require(
                (item.get("creator_fit") or {}).get("status") in {"hypothesis", "unassessed"},
                f"{prefix}: brief-only Creator fit cannot be confirmed",
                errors,
            )
            require(basis in {"brief_based", "hypothesis"}, f"{prefix}: brief-only evidence_basis must be brief_based or hypothesis", errors)
        elif basis == "source_backed":
            require(bool(item.get("source_context_refs")) and bool(item.get("evidence_refs")), f"{prefix}: source_backed idea requires context and evidence refs", errors)

    if intent == "rework_content" and not is_blocked:
        require(len(ideas) == 1, "rework_content must contain one target idea", errors)
    if intent == "capacity_plan":
        budget = (request.get("production_constraints") or {}).get("total_time_minutes")
        if isinstance(budget, int):
            require(
                max_total_minutes <= budget,
                f"capacity_plan maximum estimated minutes {max_total_minutes} exceed budget {budget}",
                errors,
            )

    validate_gate_state(idea_set, errors)

    summary = idea_set.get("evidence_summary") or {}
    require(set(summary.get("upstream_source_ids") or []) == set(upstream_ids), "evidence_summary upstream_source_ids mismatch", errors)
    require(set(summary.get("source_context_refs") or []) == adopted_context_refs, "evidence_summary source_context_refs mismatch", errors)
    require(set(summary.get("video_analysis_ids") or []) == adopted_videos, "evidence_summary video_analysis_ids mismatch", errors)
    require(summary.get("has_account_context") is (context.get("account") is not None), "evidence_summary has_account_context mismatch", errors)
    require(set(summary.get("data_gaps") or []) >= set(collect_values(context.get("account") or {}, "data_gaps")), "evidence_summary data_gaps dropped account gaps", errors)
    require(set(comment_context.get("limitations") or []) <= set(summary.get("data_gaps") or []),
            "evidence_summary data_gaps dropped comment collection limits", errors)
    if decision.get("brief_fallback_used"):
        require(
            required_source_reasons(decision) <= set(summary.get("data_gaps") or []),
            "brief fallback must preserve every missing required source in evidence_summary.data_gaps",
            errors,
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    request_parser = commands.add_parser("request")
    request_parser.add_argument("request", type=Path)
    decision_parser = commands.add_parser("decision")
    decision_parser.add_argument("request", type=Path)
    idea_set_parser = commands.add_parser("idea-set")
    idea_set_parser.add_argument("idea_set", type=Path)
    idea_set_parser.add_argument("request", type=Path)
    args = parser.parse_args()

    try:
        if args.command in {"request", "decision"}:
            request_path = args.request.resolve()
            request = load(request_path)
            errors = validate_request(request, request_path)
        else:
            request_path = args.request.resolve()
            errors = validate_idea_set(load(args.idea_set), load(request_path), request_path)
    except (OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    if errors:
        for error in dict.fromkeys(errors):
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.command == "decision":
        print(json.dumps(generation_decision(request), ensure_ascii=False, sort_keys=True))
        return 0
    print(f"VALID {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
