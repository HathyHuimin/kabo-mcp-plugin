#!/usr/bin/env python3
"""Bind a deep synthesis to watched evidence and render the Creator-facing report.

Fails closed if a pin names an unwatched video or a second that is not in evidence.
Does not call the connector or a model.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
from media_payload import load_json, write_json
from baseline_availability import data_gap_lines
from synthesis_contract import TextTooLong, validate_contract
from point_observations import MissingPointObservation, validate_observations

SCHEMA_VERSION = "account-deep-report.v1"
SECOND_TOLERANCE = 0.051
def _display_label(synthesis: dict[str, Any], category: str, value: Any) -> str:
    """Use model-written display text without changing canonical evidence codes."""
    text = str(value or "")
    labels = (synthesis.get("presentation") or {}).get(category, {})
    if text in labels:
        return labels[text]
    if synthesis.get("output_language") != "en":
        raise ValueError(f"presentation.{category} missing used key {text!r}")
    return text


def _percent(value: float) -> str:
    """A share as a Creator reads one. `0.716998` in a report is a leaked intermediate."""
    return f"{round(value * 100)}%"


def _baseline_lines(stability: dict[str, Any] | None, language: str, synthesis: dict[str, Any] | None = None) -> list[str]:
    """The catalog facts, said to the Creator, in the language the report is written in.

    `stability["readings"]` is not this. Those lines are the analyst's own working notes — they
    name the rule being applied ("按份额判为上升，不是按单条中位数"), warn which metric not to
    trust, and record what the data did not allow, all of it addressed to whoever is doing the
    analysis. Rendering them verbatim put "水位被少数条拉开" and a raw `0.716998` in front of the
    Creator, in Chinese, inside an English report. Same numbers here, read off the structured
    fields beside them, phrased as a finding about the account rather than an instruction about
    how to read it.
    """
    if not isinstance(stability, dict):
        return []
    english = language == "en"
    lines: list[str] = []

    ratio = stability.get("median_views_per_follower")
    if isinstance(ratio, (int, float)) and not isinstance(ratio, bool):
        share = _percent(float(ratio))
        lines.append(
            f"Median views are {share} of the follower count. Public counts do not identify "
            "follower views or traffic sources."
            if english else
            f"播放中位数相当于粉丝数的 {share}；这组公开数字无法区分粉丝观看和推荐流量。"
        )

    top_share = stability.get("top10_share")
    if isinstance(top_share, (int, float)) and not isinstance(top_share, bool) and top_share >= 0.5:
        share = _percent(float(top_share))
        lines.append(
            f"The top 10% of posts took {share} of all views: the account's total rests on a "
            "handful of them."
            if english else
            f"播放最高的 10% 内容拿走了 {share} 的播放，账号的总播放主要由少数几条撑起来。"
        )

    hidden = stability.get("hidden_likes_n")
    if isinstance(hidden, int) and not isinstance(hidden, bool) and hidden > 0:
        lines.append(
            f"{hidden} {'post has its' if hidden == 1 else 'posts have their'} likes hidden, so engagement here is read from comments "
            "against views only."
            if english else
            f"{hidden} 条内容的点赞对外不可见，所以这里的互动只按评论和播放的比例来看。"
        )

    for row in stability.get("rise_fade") or []:
        if not isinstance(row, dict) or row.get("status") not in {"rise", "fade"}:
            continue
        earlier, later = row.get("earlier_view_share"), row.get("later_view_share")
        if not isinstance(earlier, (int, float)) or not isinstance(later, (int, float)):
            continue
        name = _display_label(synthesis or {"output_language": language}, "format_labels", row.get("format")).strip() or "?"
        lines.append(
            f"{name} moved from {_percent(float(earlier))} to {_percent(float(later))} of views "
            f"and is {'rising' if row['status'] == 'rise' else 'falling'}."
            if english else
            f"{name}的播放占比从 {_percent(float(earlier))} 变到 {_percent(float(later))}，"
            f"正在{'上升' if row['status'] == 'rise' else '走低'}。"
        )

    offsite = stability.get("other_handle_n")
    if isinstance(offsite, int) and not isinstance(offsite, bool) and offsite > 0:
        lines.append(
            f"{offsite} {'post has its' if offsite == 1 else 'posts have their'} views counted on someone else's profile (a collab or an "
            "interview), so that reach is not all this account's own."
            if english else
            f"{offsite} 条内容的播放计在别人的主页上（合作或受访），这部分播放不完全来自这个账号。"
        )

    return lines


def _watched_index(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in evidence.get("items") or []:
        if isinstance(item, dict) and item.get("watched") and item.get("content_id"):
            index[str(item["content_id"])] = item
    return index


def _has_cue(item: dict[str, Any], start_s: float) -> bool:
    transcript = item.get("transcript") if isinstance(item.get("transcript"), dict) else {}
    for raw in transcript.get("cues") or []:
        if not isinstance(raw, dict):
            continue
        value = raw.get("start_s")
        if isinstance(value, (int, float)) and abs(float(value) - start_s) <= SECOND_TOLERANCE:
            return True
    return False


def _matching_frames(item: dict[str, Any], start_s: float) -> list[dict[str, Any]]:
    frames = item.get("frames") if isinstance(item.get("frames"), dict) else {}
    matches = []
    for raw in frames.get("items") or []:
        if not isinstance(raw, dict):
            continue
        value = raw.get("t_seconds")
        if isinstance(value, (int, float)) and abs(float(value) - start_s) <= SECOND_TOLERANCE:
            matches.append(raw)
    return matches


def _has_frame(item: dict[str, Any], start_s: float) -> bool:
    return bool(_matching_frames(item, start_s))


def validate_pin(pin: dict[str, Any], watched: dict[str, dict[str, Any]]) -> None:
    content_id = str(pin.get("content_id") or "").strip()
    if content_id not in watched:
        raise ValueError(f"pin names unwatched or unknown content_id {content_id!r}")
    start = pin.get("start_s")
    if not isinstance(start, (int, float)) or isinstance(start, bool) or not math.isfinite(float(start)):
        raise ValueError(f"pin {content_id!r} is missing a finite start_s")
    kind = pin.get("kind")
    item = watched[content_id]
    if kind == "cue" and not _has_cue(item, float(start)):
        raise ValueError(f"pin {content_id!r} cue {start} is not in watched evidence")
    if kind == "frame" and not _has_frame(item, float(start)):
        raise ValueError(f"pin {content_id!r} frame {start} is not in watched evidence")
    if kind not in {"cue", "frame"}:
        raise ValueError(f"pin {content_id!r} has unknown kind {kind!r}")


def validate_pattern(pattern: dict[str, Any], watched: dict[str, dict[str, Any]]) -> None:
    pins = [row for row in (pattern.get("pins") or []) if isinstance(row, dict)]
    if len(pins) < 2:
        raise ValueError("a craft pattern needs at least two pins")
    ids = {str(row.get("content_id") or "") for row in pins}
    ids.discard("")
    if len(ids) < 2:
        raise ValueError("a craft pattern must pin at least two watched videos")
    for pin in pins:
        validate_pin(pin, watched)


def _validate_render_inputs(synthesis: dict[str, Any], evidence: dict[str, Any],
                            stability: dict[str, Any] | None) -> None:
    """Check every used display value before a correctable point omission is returned."""
    _baseline_lines(stability, synthesis.get("output_language") or "en", synthesis)
    for row in evidence.get("items") or []:
        if not isinstance(row, dict):
            continue
        _display_label(synthesis, "role_labels", row.get("role"))
        if not row.get("watched"):
            _display_label(synthesis, "not_watched_reason_labels", row.get("unwatched_reason"))
    pins = [pin for name in ("repeated_strengths", "repeated_drags")
            for pattern in synthesis.get(name, []) for pin in pattern.get("pins", [])]
    pins += (synthesis.get("next_shoot") or {}).get("pins", [])
    for pin in pins:
        _display_label(synthesis, "pin_kind_labels", pin["kind"])


def validate_synthesis(synthesis: dict[str, Any], evidence: dict[str, Any],
                       synthesis_input: dict[str, Any] | None = None,
                       stability: dict[str, Any] | None = None) -> None:
    validate_contract(synthesis)
    if synthesis.get("analysis_id") != evidence.get("analysis_id"):
        raise ValueError("synthesis analysis_id does not match watched evidence")
    data_gap_lines(synthesis, synthesis_input)
    watched = _watched_index(evidence)
    if synthesis.get("schema_version") != "account-deep-synthesis.v1":
        raise ValueError("synthesis schema_version must be account-deep-synthesis.v1")
    if not str(synthesis.get("question_answer") or "").strip():
        raise ValueError("question_answer must answer the frozen original question")
    if len(watched) < 2 and (
        synthesis.get("repeated_strengths") or synthesis.get("repeated_drags")
    ):
        raise ValueError("craft patterns need at least two watched videos")
    for key in ("repeated_strengths", "repeated_drags"):
        for pattern in synthesis.get(key) or []:
            if isinstance(pattern, dict):
                validate_pattern(pattern, watched)
    next_shoot = synthesis.get("next_shoot") if isinstance(synthesis.get("next_shoot"), dict) else {}
    for pin in next_shoot.get("pins") or []:
        if isinstance(pin, dict):
            validate_pin(pin, watched)
    _validate_render_inputs(synthesis, evidence, stability)
    # Defer the only correctable category until all other strict constraints pass.
    validate_observations(synthesis, evidence, synthesis_input)


def _video_labels(evidence: dict[str, Any], language: str) -> dict[str, str]:
    """Number evidence videos for display; platform content IDs stay out of creator-facing text."""
    word = "Video" if language == "en" else "视频"
    labels: dict[str, str] = {}
    rows = [row for row in evidence.get("items") or [] if isinstance(row, dict)]
    for row in sorted(rows, key=lambda row: not row.get("watched")):
        if row.get("content_id") and str(row["content_id"]) not in labels:
            labels[str(row["content_id"])] = f"{word} {len(labels) + 1}"
    return labels


def _pin_line(pin: dict[str, Any], synthesis: dict[str, Any], observations: dict | None = None,
              labels: dict[str, str] | None = None) -> str:
    kind = _display_label(synthesis, "pin_kind_labels", pin["kind"])
    unit = (synthesis.get("presentation") or {}).get("seconds_unit", "s")
    video = (labels or {}).get(str(pin['content_id']), pin['content_id'])
    line = f"{video} {kind} {pin['start_s']}{unit}"
    if observations is not None:
        sample = observations[(pin['content_id'], pin['kind'], float(pin['start_s']))]
        if pin['kind'] == 'cue':
            if sample['end_s'] is not None:
                line = f"{video} {kind} {pin['start_s']}–{sample['end_s']}{unit}"
            line += f" “{sample['quote']}”"
        else:
            line += f" — {sample['description']}"
    return line


def render_markdown(
    synthesis: dict[str, Any],
    evidence: dict[str, Any],
    stability: dict[str, Any] | None = None,
    synthesis_input: dict[str, Any] | None = None,
) -> str:
    observations = validate_observations(synthesis, evidence, synthesis_input)
    language = synthesis.get("output_language") or "en"
    watched = [row for row in evidence.get("items") or [] if isinstance(row, dict) and row.get("watched")]
    unwatched = [row for row in evidence.get("items") or [] if isinstance(row, dict) and not row.get("watched")]
    labels = _video_labels(evidence, language)
    if language == "en":
        titles = {
            "answer": "## Answer",
            "catalog": "## Account baseline",
            "seen": "## What this rests on",
            "insight": "## Key finding",
            "practices": "## What works",
            "works": "## Craft that repeats",
            "hurts": "## Cuts to change or stop",
            "next": "## Do more / change / stop",
            "none": "No repeated craft pattern: fewer than two videos carried frames or a transcript, or none repeated.",
            "keep": "Do more",
            "stop": "Stop",
            "unwatched": "Left out",
        }
    else:
        titles = {
            "answer": "## 先回答问题",
            "catalog": "## 账号基本盘",
            "seen": "## 这份结论基于什么",
            "insight": "## 关键发现",
            "practices": "## 有效的做法",
            "works": "## 重复出现的拍法",
            "hurts": "## 要改或停的拍法",
            "next": "## 多做 / 改做 / 停做",
            "none": "取到画面或台词的视频不足两条，或没有重复机制，所以没有拍法结论。",
            "keep": "多做",
            "stop": "停做",
            "unwatched": "未纳入",
        }

    lines = [
        f"# {synthesis.get('headline') or ''}".rstrip(),
        "",
        titles["answer"],
        "",
        str(synthesis.get("question_answer") or ""),
        "",
    ]
    baseline = _baseline_lines(stability, language, synthesis)
    if baseline:
        lines.extend([titles["catalog"], ""])
        for row in baseline:
            lines.append(f"- {row}")
        lines.append("")
    lines.extend(
        [
            str(synthesis.get("seen_summary") or ""),
            "",
            titles["seen"],
            "",
        ]
    )
    for row in watched:
        role = _display_label(synthesis, "role_labels", row.get("role"))
        lines.append(f"- {labels[str(row['content_id'])]} ({role}) {row.get('url')}")
    for row in unwatched:
        reason = _display_label(synthesis, "not_watched_reason_labels", row.get("unwatched_reason"))
        role = _display_label(synthesis, "role_labels", row.get("role"))
        url = f" {row['url']}" if row.get("url") else ""
        lines.append(f"- {labels.get(str(row.get('content_id')), '')} ({role}){url} — {titles['unwatched']} {reason}".rstrip())
    insight = str(synthesis.get("insight") or "").strip()
    if insight:
        lines.extend(["", titles["insight"], "", insight])
    practices = str(synthesis.get("practices") or "").strip()
    if practices:
        lines.extend(["", titles["practices"], "", practices])
    lines.extend(["", titles["works"], ""])
    strengths = [row for row in (synthesis.get("repeated_strengths") or []) if isinstance(row, dict)]
    if strengths:
        for pattern in strengths:
            pins = ", ".join(_pin_line(row, synthesis, observations, labels) for row in pattern.get("pins") or [] if isinstance(row, dict))
            lines.append(f"- **{pattern.get('pattern')}** — {pattern.get('explanation')} ({pins})")
    else:
        lines.append(titles["none"])
    lines.extend(["", titles["hurts"], ""])
    drags = [row for row in (synthesis.get("repeated_drags") or []) if isinstance(row, dict)]
    if drags:
        for pattern in drags:
            pins = ", ".join(_pin_line(row, synthesis, observations, labels) for row in pattern.get("pins") or [] if isinstance(row, dict))
            lines.append(f"- **{pattern.get('pattern')}** — {pattern.get('explanation')} ({pins})")
    else:
        lines.append(titles["none"])
    next_shoot = synthesis.get("next_shoot") if isinstance(synthesis.get("next_shoot"), dict) else {}
    lines.extend(["", titles["next"], ""])
    if next_shoot.get("keep"):
        lines.append(f"- {titles['keep']}: {next_shoot['keep']}")
    if next_shoot.get("stop"):
        lines.append(f"- {titles['stop']}: {next_shoot['stop']}")
    if observations is not None and next_shoot.get("pins"):
        lines.append("(" + ", ".join(_pin_line(pin, synthesis, observations, labels) for pin in next_shoot["pins"]) + ")")
    gaps = data_gap_lines(synthesis, synthesis_input)
    if gaps:
        lines.extend(["", "- " + " ".join(gaps)])
    return "\n".join(lines).rstrip() + "\n"


def compose_deep_review(
    synthesis: dict[str, Any],
    evidence: dict[str, Any],
    stability: dict[str, Any] | None = None,
    synthesis_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_synthesis(synthesis, evidence, synthesis_input, stability)
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_id": str(synthesis.get("analysis_id") or evidence.get("analysis_id") or ""),
        "output_language": synthesis.get("output_language"),
        "headline": synthesis.get("headline"),
        "markdown": render_markdown(synthesis, evidence, stability, synthesis_input),
    }


def grounding_review(synthesis: dict[str, Any], evidence: dict[str, Any],
                     synthesis_input: dict[str, Any] | None,
                     stability: dict[str, Any] | None) -> dict[str, Any]:
    """Project already validated claims beside their source modalities, not a pixel verdict."""
    claims = []
    for name in ("headline", "question_answer", "seen_summary", "insight", "practices"):
        if synthesis.get(name):
            claims.append({"path": name, "text": synthesis[name], "pins": []})
    for name in ("repeated_strengths", "repeated_drags"):
        for index, pattern in enumerate(synthesis.get(name) or []):
            for field in ("pattern", "explanation"):
                claims.append({"path": f"{name}[{index}].{field}", "text": pattern[field],
                               "pins": pattern["pins"]})
    for field in ("keep", "stop"):
        if (synthesis.get("next_shoot") or {}).get(field):
            claims.append({"path": f"next_shoot.{field}", "text": synthesis["next_shoot"][field],
                           "pins": synthesis["next_shoot"].get("pins", [])})
    observed = {row["content_id"]: (index, row)
                for index, row in enumerate(synthesis.get("video_observations") or [])}
    videos = []
    for content_id, item in _watched_index(evidence).items():
        index, row = observed.get(content_id, (None, {}))
        frames, cues = [], []
        for position, sample in enumerate(row.get("frame_samples") or []):
            matches = _matching_frames(item, sample["start_s"])
            frames.append({"path": f"video_observations[{index}].frame_samples[{position}]",
                           "nominal_start_s": sample["start_s"],
                           "authored_description": sample["description"],
                           "source_frames": [{key: frame[key] for key in
                                              ("frame_id", "object_ref", "path", "sha256", "t_seconds")
                                              if key in frame} for frame in matches]})
        for position, sample in enumerate(row.get("cue_samples") or []):
            cues.append({"path": f"video_observations[{index}].cue_samples[{position}]",
                         "start_s": sample["start_s"], "end_s": sample["end_s"],
                         "recorded_text": sample["quote"]})
        videos.append({"content_id": content_id, "authored_frame_observations": frames,
                       "recorded_speech": cues})
    return {"schema_version": "deep-grounding-review.v1", "semantic_status": "not_verified_by_script",
            "scope": "Source points and author descriptions are not proof of viewing or correct object recognition. Cue starts do not establish first visual appearance; frames retain nominal sample times, not continuous coverage.",
            "claim_fields": claims, "videos": videos,
            "authored_display_labels": synthesis.get("presentation") or {},
            "account_baseline_lines": _baseline_lines(stability, synthesis.get("output_language") or "en", synthesis),
            "rendered_gap_lines": data_gap_lines(synthesis, synthesis_input),
            "packet_end": {"claim_fields": len(claims), "content_ids": [row["content_id"] for row in videos],
                           "frame_observations": sum(len(row["authored_frame_observations"]) for row in videos),
                           "speech_cues": sum(len(row["recorded_speech"]) for row in videos)}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("synthesis", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--check-only", action="store_true", help="Validate authoring without writing a report")
    parser.add_argument("--stability", type=Path, default=None)
    parser.add_argument("--synthesis-input", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.check_only and (args.output is not None or args.markdown is not None):
        parser.error("--check-only does not accept report output paths")
    if not args.check_only and (args.output is None or args.markdown is None):
        parser.error("--output and --markdown are required unless --check-only is used")
    synthesis, evidence = load_json(args.synthesis), load_json(args.evidence)
    synthesis_input = load_json(args.synthesis_input) if args.synthesis_input else None
    stability = load_json(args.stability) if args.stability else None
    if synthesis.get("schema_version") == "account-deep-authoring.v1":
        from deep_authoring import load_strict, load_authoring, bound_sources, compile_authoring
        if args.synthesis_input is None:
            raise ValueError("new authoring requires its original bound synthesis input")
        synthesis_input = load_strict(args.synthesis_input)
        original = bound_sources(args.synthesis_input, synthesis_input, evidence_path=args.evidence, stability_path=args.stability)
        evidence, stability = original["evidence"], original["stability"]
        synthesis = compile_authoring(load_authoring(args.synthesis), synthesis_input, evidence)
    if args.check_only:
        for directory in args.synthesis.resolve().parents:
            manifest = directory / "run-manifest.json"
            if manifest.exists():
                if load_json(manifest).get("status") in {"ok", "failed"}:
                    raise ValueError("cannot correct authoring in a closed run")
                break
        try:
            try:
                compose_deep_review(synthesis, evidence, stability, synthesis_input)
            except TextTooLong as error:
                compose_deep_review(error.shortened(synthesis), evidence, stability, synthesis_input)
                print(json.dumps({"status": "needs_correction",
                                  "issue": {"text_too_long": [dict(row, path=row["path"].removeprefix("synthesis."))
                                                              for row in error.violations]},
                                  "next_action": "Rewrite only each listed authored field in the open-run authoring file so it fits within max_length characters while keeping its evidence boundary; do not truncate mid-sentence or change other fields. Do not refetch or reopen a closed run. Run this check again before final composition."}, ensure_ascii=False))
                return 0
        except MissingPointObservation as error:
            print(json.dumps({"status": "needs_correction", "issue": error.issue,
                              "next_action": "Reuse this run's source material. Correct only the open-run authored synthesis: record a literal cue only after reading it, or a frame description only after viewing that frame; otherwise remove or narrow the unsupported pin and claim. Do not invent observations, refetch, or reopen a closed run. Run this check again before final composition."}, ensure_ascii=False))
            return 0
        print(json.dumps({"status": "valid", "analysis_id": synthesis["analysis_id"],
                          "grounding_review": grounding_review(synthesis, evidence, synthesis_input, stability)},
                         ensure_ascii=False))
        return 0
    report = compose_deep_review(
        synthesis,
        evidence,
        stability,
        synthesis_input,
    )
    write_json(args.output, report)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(report["markdown"], encoding="utf-8")
    # 与其他出报告的 Skill 一致：最终步打印 creator_report 路径与报告全文，模型照原文交付，不再自己读文件转述
    # （实测里模型转述时删掉了证据引用）。
    print(f"creator_report: {args.markdown.resolve()}")
    print(report["markdown"].rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
