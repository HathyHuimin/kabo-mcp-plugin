#!/usr/bin/env python3
"""Validate source-bound content-language judgments independently of topic and popularity."""
from __future__ import annotations

import re
from typing import Any

DECISIONS = {"match", "mismatch", "unknown", "not_applicable"}
POSITIVE_SOURCES = {"caption", "tiktok_caption", "description", "audio_language", "transcript"}


def code(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", value):
        raise ValueError("content languages must be language codes such as en or pt-BR")
    return value.lower()


def scope(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != {"mode", "accepted_languages", "basis"}:
        raise ValueError("language_scope needs mode, accepted_languages and an evidence-based basis")
    if value["mode"] not in {"restricted", "unrestricted", "unknown"}:
        raise ValueError("language_scope mode must be restricted, unrestricted or unknown")
    languages = value["accepted_languages"]
    if not isinstance(languages, list) or len(set(code(x) for x in languages)) != len(languages):
        raise ValueError("accepted_languages must be a list of unique language codes")
    if bool(languages) != (value["mode"] == "restricted"):
        raise ValueError("only a restricted language scope has nonempty accepted_languages")
    if not isinstance(value["basis"], str) or not value["basis"].strip():
        raise ValueError("language_scope needs the matching account or explicit request evidence")
    return value


def evidence(raw: dict, platform: str) -> list[dict]:
    # 只投影内容语言证据；作者国籍、地区、音乐名、搜索词和报告译文均不是视频语言。
    result = []
    for field in ("title", "caption", "description", "transcript"):
        text = raw.get(field)
        if isinstance(text, dict):
            text = text.get("text")
        if isinstance(text, str) and text.strip():
            # TikTok 采集器把上游 desc 投影为 title；保留原文并显式区分 YouTube 标题。
            source = "tiktok_caption" if platform == "tiktok" and field == "title" else field
            result.append({"source": source, "text": text.strip()})
    signals = raw.get("content_signals")
    signals = signals if isinstance(signals, dict) else {}
    for value in (signals.get("default_audio_language"), raw.get("default_audio_language")):
        if isinstance(value, str) and value.strip():
            entry = {"source": "audio_language", "text": value.strip()}
            if entry not in result:
                result.append(entry)
    return result


def accepts(observed: str, accepted: list[str]) -> bool:
    observed = code(observed)
    return any(observed == code(target) or observed.startswith(code(target) + "-") for target in accepted)


def validate(value: Any, item: dict, language_scope: dict) -> bool:
    if not isinstance(value, dict) or set(value) != {"decision", "languages", "evidence", "reason"}:
        raise ValueError("each language judgment needs decision, languages, evidence and reason")
    decision, languages, citations = value["decision"], value["languages"], value["evidence"]
    if not isinstance(decision, str) or decision not in DECISIONS:
        raise ValueError("language decision must be match, mismatch, unknown or not_applicable")
    if not isinstance(languages, list) or len(set(code(x) for x in languages)) != len(languages):
        raise ValueError("detected languages must be unique language codes")
    if not isinstance(value["reason"], str) or not value["reason"].strip():
        raise ValueError("each language judgment needs a brief reason")
    if not isinstance(citations, list):
        raise ValueError("language evidence must be an array of source and verbatim quote")
    sources = item["language_evidence"]
    for citation in citations:
        if (not isinstance(citation, dict) or set(citation) != {"source", "quote"}
                or not isinstance(citation["quote"], str) or not citation["quote"].strip()
                or not any(citation["source"] == source["source"] and citation["quote"] in source["text"]
                           for source in sources)):
            raise ValueError("language evidence quote must occur in this candidate's original source")
    if decision in {"match", "mismatch"} and (not languages or not citations):
        raise ValueError("a known language judgment requires detected languages and source evidence")
    if decision == "match" and not any(c["source"] in POSITIVE_SOURCES for c in citations):
        raise ValueError("a title alone cannot establish a language match")
    if language_scope["mode"] == "unrestricted":
        return True
    if language_scope["mode"] == "unknown":
        return False
    accepted = language_scope["accepted_languages"]
    audio = [source["text"] for source in sources if source["source"] == "audio_language"
             and source["text"].lower() not in {"und", "mul", "zxx"}]
    if decision == "match":
        if not all(accepts(language, accepted) for language in languages):
            raise ValueError("language match contradicts the accepted language set")
        if any(not accepts(language, accepted) for language in audio):
            raise ValueError("language match contradicts the source's declared audio language")
        return True
    if decision == "not_applicable":
        # zxx 是来源明确声明的无语言内容，不从英语标题或缺字幕推断没有口播。
        if languages:
            raise ValueError("no-language content cannot also have detected linguistic content; resolve the conflict")
        if (not any(s == {"source": "audio_language", "text": "zxx"} for s in sources)
                or audio or not any(c == {"source": "audio_language", "quote": "zxx"} for c in citations)):
            raise ValueError("language-independent content requires an explicit no-language source declaration")
        return True
    return False


def notes(audit: dict) -> list[str]:
    language = audit.get("language_gate")
    if not language:
        return []
    context = language["scope"]
    counts = language["on_topic_counts"]
    if context["mode"] == "unrestricted":
        return ["Content language was explicitly left unrestricted; the report language did not filter videos. "
                "Some videos may have unconfirmed spoken language."]
    if context["mode"] == "unknown":
        return ["The account's accepted content languages could not be established; no videos were ranked."]
    return [f"Content languages: {', '.join(context['accepted_languages'])}. Language review excluded "
            f"{counts['mismatch']} mismatches and {counts['unknown']} unknowns among on-topic candidates "
            "before ranking. Caption/description judgments are metadata evidence, not verified spoken language."]
