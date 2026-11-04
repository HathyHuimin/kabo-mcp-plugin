"""Derive the report's sampling facts; extraction and citation never prove pixel viewing."""
from __future__ import annotations

import math
import unicodedata
from typing import Any


def record(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def number(value: Any, field: str, *, positive: bool = False) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or (positive and value == 0)):
        raise ValueError(f"cannot derive data_note: {field} is unknown or invalid")
    return float(value)


def measurement_basis(evidence: dict, analysis: dict, view: dict) -> dict:
    """Count existing frame records and transitive card citations, without a viewing ledger."""
    platform = {"instagram": "Instagram video", "youtube": "YouTube video", "tiktok": "TikTok video"}.get(
        record(evidence.get("content_ref")).get("platform"))
    local = (record(evidence.get("media")).get("duration_source") == "local_probe"
             or any(record(step).get("step") == "local_extraction" for step in evidence.get("acquisition") or []))
    if not local and platform is None:
        raise ValueError("cannot derive data_note: the video source is not known")
    facts: dict = {"source": "Local video file" if local else platform, "viewing": "unverified"}
    frames = record(evidence.get("frames"))
    frame_ids = set()
    status = frames.get("status")
    if status == "available" and frames.get("origin") == "extracted_frames":
        items = frames.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("cannot derive data_note: the extracted-frame sample is missing")
        for item in items:
            item = record(item)
            identifier = item.get("frame_id")
            if not isinstance(identifier, str) or not identifier.strip() or identifier in frame_ids:
                raise ValueError("cannot derive data_note: frame IDs are missing or ambiguous")
            number(item.get("t_seconds"), "frame timestamp")
            frame_ids.add(identifier)
        facts.update(frame_kind="extracted", extracted_frames=len(frame_ids))
        plan = record(frames.get("sampling_plan"))
        count = plan.get("frames")
        # 计划帧数是请求量：provider 解不出个别时间点（如末帧越过时长）时会少返回，
        # 少于计划照实记录缺口；多于计划才说明帧记录与这份计划对不上。
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < len(frame_ids)):
            raise ValueError("cannot derive data_note: sampling-plan frame count disagrees with frame records")
        if count is not None and count > len(frame_ids):
            facts["requested_frames"] = count
        dense = plan.get("hook_dense_window_s")
        interval = plan.get("hook_interval_s")
        if dense is not None or interval is not None:
            number(dense, "dense sampling window")
            number(interval, "dense sampling interval", positive=True)
        body = plan.get("body_interval_s")
        if body is not None or plan.get("body_degraded") is True:
            number(body, "reduced sampling interval", positive=True)
        # The plan and t_seconds may both be nominal extraction requests. Neither
        # confirms actual decoded timestamps, so no spacing enters measured facts.
    elif status == "available" and frames.get("origin") == "model_observation":
        if not frames.get("observations"):
            raise ValueError("cannot derive data_note: the reported visual descriptions are missing")
        facts["frame_kind"] = "descriptions"
    elif status in ("unavailable", "not_attempted"):
        facts["frame_kind"] = status
    else:
        raise ValueError("cannot derive data_note: the frame provenance is not known")

    observations = {}
    for observation in analysis.get("observations") or []:
        observation = record(observation)
        key = observation.get("observation_id")
        if not isinstance(key, str) or not key.strip() or key in observations:
            raise ValueError("cannot derive data_note: observation IDs are ambiguous")
        observations[key] = observation
    cited = set()
    for card in view.get("cards") or []:
        for ref in record(card).get("evidence_refs") or []:
            if isinstance(ref, str) and ref.startswith("observations."):
                key = ref[len("observations."):]
                if key not in observations:
                    raise ValueError(f"cannot derive data_note: unknown observation {key}")
                cited.update(ref for ref in observations[key].get("evidence_refs") or [] if ref in frame_ids)
            elif isinstance(ref, str) and ref in frame_ids:
                cited.add(ref)
    facts["cited_frame_refs"] = len(cited)

    transcript = record(evidence.get("transcript"))
    if transcript.get("status") == "available":
        label = {"platform_captions": "platform captions", "provider_transcript": "external transcript",
                 "speech_recognition": "machine transcript (wording unverified)",
                 "creator_supplied": "creator-supplied transcript (not checked against audio)"}.get(transcript.get("origin"))
        cues = transcript.get("cues") or []
        if not cues and not str(transcript.get("plain_text") or "").strip():
            raise ValueError("cannot derive data_note: available transcript contains no text")
        if cues:
            for cue in cues:
                cue = record(cue)
                number(cue.get("start_s"), "caption timestamp")
                if not isinstance(cue.get("text"), str) or not cue["text"].strip():
                    raise ValueError("cannot derive data_note: caption text is missing")
            facts["caption_cues"] = len(cues)
    else:
        label = {"no_speech_detected": "no speech detected", "no_audio_track": "no audio track",
                 "no_platform_captions": "platform captions unavailable", "provider_error": "transcript unavailable",
                 "not_attempted": "transcript not retrieved"}.get(transcript.get("status"))
    if label is None:
        raise ValueError("cannot derive data_note: the transcript provenance is not known")
    facts["transcript"] = label
    owner = record(evidence.get("owner_metrics")).get("status")
    if owner not in ("available", "partial", "unavailable"):
        raise ValueError("cannot derive data_note: owner-metric availability is not known")
    facts["owner_metrics"] = owner
    return facts


# 这一行随报告原样转述：中文视图写中文，其余语言仍写英文、由宿主按 SKILL.md 本地化。
# 两种写法是同一组事实、同一组数字；英文措辞保持原样逐字不变，校验器按视图语言逐字比对。
ZH_WORDS = {
    "source": {"YouTube video": "YouTube 视频", "Instagram video": "Instagram 视频",
               "TikTok video": "TikTok 视频", "Local video file": "本地视频文件"},
    "frames": {"descriptions": "画面来自文字描述，未直接查看像素", "unavailable": "画面帧不可用",
               "not_attempted": "未抽帧"},
    "transcript": {"platform captions": "平台字幕", "external transcript": "外部转写稿",
                   "machine transcript (wording unverified)": "机器转写（措辞未核实）",
                   "creator-supplied transcript (not checked against audio)": "创作者提供的文字稿（未与音频核对）",
                   "no speech detected": "未检测到语音", "no audio track": "没有音轨",
                   "platform captions unavailable": "平台字幕不可用", "transcript unavailable": "转写不可用",
                   "transcript not retrieved": "未获取转写"},
    "owner": {"available": "可用", "partial": "部分可用", "unavailable": "不可用"},
}


def note_language(view: dict) -> str:
    """`zh` for a Chinese view (`zh`, `zh-CN`, `zh-Hant`), `en` for anything else or nothing."""
    return "zh" if str(record(view).get("language") or "").strip().lower().startswith("zh") else "en"


def display_width(text: str) -> int:
    """Columns as the shape gate counts them: a full-width character is two."""
    return sum(2 if unicodedata.east_asian_width(character) in ("W", "F") else 1 for character in text)


def measurement_note(facts: dict, language: str = "en") -> str:
    """One measurement line, with fixed rounding and no claim about actual viewing."""
    if language == "zh":
        note = _zh_note(facts)
    else:
        note = _en_note(facts)
    if display_width(note) > 240:
        raise ValueError("cannot derive data_note within the source-note length limit")
    return note


def _en_note(facts: dict) -> str:
    kind = facts["frame_kind"]
    frames = {"descriptions": "reported visual descriptions, not inspected pixels",
              "unavailable": "frames unavailable", "not_attempted": "frames not sampled"}.get(kind)
    if kind == "extracted":
        frames = f"{facts['extracted_frames']} extracted frames"
        if "requested_frames" in facts:
            frames += f" of {facts['requested_frames']} requested"
    parts = [facts["source"], frames, f"{facts['cited_frame_refs']} cited frame refs"]
    transcript = facts["transcript"]
    if "caption_cues" in facts:
        count = facts["caption_cues"]
        transcript += f" ({count} cue{'s' if count != 1 else ''})"
    parts += [transcript, f"owner metrics {facts['owner_metrics']}", "viewing unverified"]
    return "; ".join(parts) + "."


def _zh_note(facts: dict) -> str:
    kind = facts["frame_kind"]
    frames = ZH_WORDS["frames"].get(kind)
    if kind == "extracted":
        frames = f"抽取 {facts['extracted_frames']} 帧"
        if "requested_frames" in facts:
            frames += f"（请求 {facts['requested_frames']} 帧）"
    parts = [ZH_WORDS["source"][facts["source"]], frames, f"报告引用 {facts['cited_frame_refs']} 帧"]
    transcript = ZH_WORDS["transcript"][facts["transcript"]]
    if "caption_cues" in facts:
        transcript += f"（{facts['caption_cues']} 条）"
    parts += [transcript, f"后台数据{ZH_WORDS['owner'][facts['owner_metrics']]}", "画面查看未核实"]
    return "；".join(parts) + "。"


def canonical_note(evidence: dict, analysis: dict, view: dict) -> str:
    return measurement_note(measurement_basis(evidence, analysis, view), note_language(view))
