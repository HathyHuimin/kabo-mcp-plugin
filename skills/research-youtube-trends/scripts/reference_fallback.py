#!/usr/bin/env python3
"""Choose a few source-backed video references when no recommendation survives."""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True
_path = Path(__file__).with_name("filter_candidate_eligibility.py")
_spec = importlib.util.spec_from_file_location(f"{_path.parent.parent.name}:reference_eligibility", _path)
eligibility = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eligibility)

LIMIT = 3
HOSTS = {"youtube": ("youtube.com", "youtu.be"), "instagram": ("instagram.com",),
         "tiktok": ("tiktok.com",)}


def score(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else 0.0


def select(request: dict[str, Any], candidates: list[dict[str, Any]],
           gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep real video links; low fit, missing metrics and absent classes cannot empty this list."""
    platform = (request.get("platforms") or [None])[0]
    if platform not in HOSTS:
        return []
    scores: dict[str, tuple[float, float]] = {}
    for gate in gates:
        for row in gate.get("items") or []:
            values = row.get("semantic_scores") or {}
            key = str(row.get("content_key") or "")
            pair = (score(values.get("topic_relevance")), score(values.get("locale_fit")))
            scores[key] = max(scores.get(key, (0.0, 0.0)), pair)
    excluded = eligibility.intake.excluded_topics(request)
    rows = []
    seen_keys, seen_urls = set(), set()
    for candidate_set in candidates:
        for item in candidate_set.get("items") or []:
            key, url = item.get("content_key"), item.get("url")
            if not isinstance(key, str) or not key or not isinstance(url, str):
                continue
            if item.get("platform", candidate_set.get("platform", platform)) != platform:
                continue
            try:
                parsed = urlsplit(url)
            except ValueError:
                continue
            host = (parsed.hostname or "").lower()
            if parsed.scheme not in {"https", "http"} or not any(
                    host == allowed or host.endswith("." + allowed) for allowed in HOSTS[platform]):
                continue
            if platform == "youtube":
                video_path = (bool(parsed.path.strip("/")) if host.endswith("youtu.be") else
                              (parsed.path == "/watch" and bool(parse_qs(parsed.query).get("v")))
                              or parsed.path.startswith("/shorts/"))
            elif platform == "tiktok":
                video_path = "/video/" in parsed.path
            else:
                video_path = parsed.path.startswith(("/reel/", "/reels/", "/p/", "/tv/"))
            if not video_path or eligibility.format_of(item, platform) == "photo":
                continue
            # 用户明确排除的话题仍不展示；画像匹配、广告标记、受众量级和资料缺失只影响正常推荐。
            if eligibility.excluded_topics_named(item, excluded) or key in seen_keys or url in seen_urls:
                continue
            seen_keys.add(key)
            seen_urls.add(url)
            rows.append({"content_key": key, "platform": platform, "url": url,
                         "title": str(item.get("title") or item.get("caption") or "").strip(),
                         "published_at": item.get("published_at") if isinstance(item.get("published_at"), str) else None})
    # 只复用已打出的相关性分和原始采集顺序，不补分、不重搜、不宣称这些视频通过了原门槛。
    rows.sort(key=lambda row: tuple(-value for value in scores.get(row["content_key"], (0.0, 0.0))))
    return rows[:LIMIT]


def errors(report: dict[str, Any], request: dict[str, Any] | None,
           candidates: list[dict[str, Any]] | None, gates: list[dict[str, Any]]) -> list[str]:
    references = report.get("reference_videos")
    if references is None and candidates is None:
        return []
    if references is not None and (report.get("top_content") or report.get("patterns") or report.get("emerging_creators")):
        return ["reference_videos are only for reports with no selected recommendation"]
    if references is not None and (candidates is None or request is None):
        return ["reference_videos require the original request and candidate files for validation"]
    if report.get("top_content") or request is None:
        return []
    expected = select(request, candidates or [], gates)
    if references != expected and (references is not None or expected):
        return ["reference_videos must equal the source-backed fallback selection"]
    if references and report.get("status") != "partial":
        return ["a report with reference_videos must be partial"]
    return []
