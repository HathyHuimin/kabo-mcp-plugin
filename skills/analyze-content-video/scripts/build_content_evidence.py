#!/usr/bin/env python3
"""Build the ContentEvidence object from the connector envelopes this run persisted.

Every field written here is a copy, or a unit conversion, of something a connector returned, so it
is this script's to write and never the model's. A model transcribing a fenced, possibly truncated
tool result into JSON is how a cue start in milliseconds reached the evidence as seconds (off by a
factor of a thousand) and how a whole cue set was dropped over a field name that did not match.
What the envelopes cannot carry (a limitation the run observed elsewhere, owner metrics read from a
creator's export) comes in through --limitation and --owner-metrics.

Inputs are the persisted public-video-media envelopes -- collect_public_metadata, fetch_transcript
and extract_keyframes / extract_keyframes_hosted_media -- one --envelope each, in any order. A job
resource that carries its terminal envelope is accepted as is. Any other envelope is recorded in
``acquisition`` only. When one operation was retried, the last usable response is the evidence and
every attempt stays in ``acquisition``. Web kabo-video/get_video_transcript envelopes also supply
captions, preserving their actual connector, operation and source metadata.

A local run has no envelopes for the media at all. Its file was read here, by this package's own
scripts, and their artifacts come in through --local-media and --local-transcript. They are not
envelopes and are never recorded as though they were: each becomes an ``acquisition`` entry marked
``local_extraction``, carrying the tool, its version and a hash of the invocation instead of a
connector and a request id. Everything downstream of that -- the media block, the frame list, the
cues -- is written in exactly the shape the connector path writes, because a frame read here and a
frame read there are the same evidence and nothing reading them should have to ask which.

The two paths never serve one request. A local file and a connector response are readings of two
different copies of the video -- the creator's master and whatever the platform re-encoded and
delivered -- and merging them would produce one object claiming both sets of numbers describe the
same thing. Mixing them is refused, loudly, rather than resolved.

Standard library only: this runs on a user's machine through the host's python3.
"""

from __future__ import annotations

import argparse
import importlib.util
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

_linked_spec = importlib.util.spec_from_file_location("kabo_linked_media", Path(__file__).with_name("linked_media.py"))
linked_media = importlib.util.module_from_spec(_linked_spec)
_linked_spec.loader.exec_module(linked_media)

SCHEMA_VERSION = "content-evidence.v1"
MEDIA_CONNECTOR = "public-video-media"
# YouTube metadata comes from the platform's own data API, not from the media connector's
# downloader. The downloader reads a video page from a datacentre address and YouTube refuses
# those: in production collect_public_metadata on a YouTube URL is a service_unavailable every
# time, which cost every YouTube run its title, tags, cover and publish date. The data API has
# no such problem -- it is the platform answering about its own video -- so that is where the
# metadata role comes from on YouTube, and the downloader keeps it on the other platforms.
YOUTUBE_METADATA_CONNECTOR = "youtube-public"
YOUTUBE_METADATA_OPERATION = "get_video"
KEYFRAME_OPERATIONS = {
    "extract_keyframes",
    "extract_keyframes_hosted_media",
    "extract_keyframes_hosted_stream",
}
ROLE_OF_OPERATION = {"collect_public_metadata": "metadata", "fetch_transcript": "captions",
                     "measure_media_signals": "measurements"}
ROLE_OF_OPERATION.update({operation: "frames" for operation in KEYFRAME_OPERATIONS})
# The published post row is a platform fact about the post, not a measurement of the file, so it
# comes from a discovery connector rather than from the downloader. Two of them serve the same
# operation; the role follows the operation, not which one answered.
POST_CONNECTORS = {"instagram-discovery", "instagram-detail-proxy"}
POST_OPERATION = "get_post"
# lookup_instagram_post 不论哪条路线作答，data 都是这份固定投影；按它判形状，
# 不按 connector_id。没有这个标记的是旧的单操作工具信封（GraphQL 原样或详情白名单），照旧读。
POST_PROJECTION = "kabo.instagram_post.v1"
PLAYS_NOTE = "A play count, which is neither reach nor an owner view figure."
# A digitally silent sample reads as an extreme negative rather than as an absent one. The floor is
# what separates "the track has not started" from "the track is quiet here".
SILENT_LUFS = -100.0
# A hashtag runs to the next space or hash. Kept in the order the creator wrote them.
HASHTAG = re.compile(r"#([^\s#]+)")
# Copied verbatim from the measurement response, less any key it may grow later: the evidence
# declares these and nothing else, and a block that quietly gained a field would be carrying
# something no reader has a definition for.
PROBED_KEYS = {
    "shots": {"count", "cuts_at_s", "shots"},
    "silence": {"min_s", "floor_db", "spans"},
    "freeze": {"min_s", "spans"},
    "framing": {"fps", "width", "height", "aspect_ratio", "vertical_screen_fill", "bars", "content_rect"},
    "brightness": {"samples", "whole_video", "per_shot"},
}
USABLE = {"completed", "completed_partial"}
ACQUISITION_STATUSES = {
    "completed", "completed_partial", "pending", "deferred_incomplete",
    "blocked_setup", "feature_gated", "unsupported", "failed",
}
FRAME_SELECTIONS = {"duration_scaled_v1", "evenly_spaced_bounded_v1", "explicit_timestamps_v1", "none"}
TRANSCRIPT_ORIGINS = {"platform_captions", "provider_transcript", "speech_recognition"}
PLATFORM_PREFIX = {"youtube": "yt", "instagram": "ig", "tiktok": "tt"}
# The connector's blanket "nothing came back" sentence. It is true of one call, not of the run, so
# it never travels into the evidence when anything else was retrieved.
BLANKET = "no data was retrieved"
# Statuses that say the path is not available here, rather than that a provider failed.
NOT_A_FAILURE = {"unsupported", "blocked_setup", "feature_gated"}
# YouTube names its cover renditions by file name; the evidence tiers are those names.
YOUTUBE_THUMBNAIL_TIERS = {"maxresdefault": "maxres", "sddefault": "standard", "hqdefault": "high",
                           "mqdefault": "medium", "default": "default"}
ZONED = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")
SAMPLING_PLAN_KEYS = {
    "base_interval_s", "hook_window_s", "hook_dense_window_s", "hook_interval_s", "hook_frames",
    "body_interval_s", "body_frames", "budget", "frames", "body_degraded", "note", "closing_frame_s",
}
# media.encoding closes to additional properties, so the writer filters to exactly its key set
# rather than passing a probe response through and discovering the mismatch at validation.
ENCODING_KEYS = {
    "video_codec", "video_bitrate_bps", "frame_rate", "audio_codec", "audio_bitrate_bps",
    "audio_sample_rate", "audio_channels", "integrated_loudness_lufs", "true_peak_dbfs",
    "loudness_segments",
}
FRAME_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
FRAME_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def materialise_frame_aliases(block: dict[str, Any], snapshot: Path, analysis: Path) -> None:
    """Bind staged pixels to their connector IDs by bytes, never by staging order.

    Hosts may stage only a subset and rename those images sequentially. The envelope still
    carries the full canonical frame list. Keep unavailable references untouched; copy a
    matching image under a portable name derived from its canonical ID; the evidence ID,
    not the filename, remains the reference. Different IDs can identify identical pixels.
    """
    if snapshot.is_symlink() or analysis.is_symlink():
        raise ValueError("frame roots must not be symlinks")
    snapshot, analysis = snapshot.resolve(), analysis.resolve()
    if (snapshot.name != "snapshot" or analysis.name != "analysis"
            or snapshot.parent != analysis.parent or not snapshot.is_dir()):
        raise ValueError("frame roots must be this run's sibling snapshot and analysis directories")
    items = block.get("items") or []
    wanted = {str(item.get("sha256")).lower() for item in items
              if FRAME_SHA256.fullmatch(str(item.get("sha256") or ""))}
    matches: dict[str, tuple[bytes, str]] = {}
    for folder, dirs, names in os.walk(snapshot, followlinks=False):
        directory = Path(folder)
        if any((directory / name).is_symlink() for name in dirs):
            raise ValueError("snapshot frame directories must not be symlinks")
        for name in sorted(names):
            source = directory / name
            if source.suffix.lower() not in FRAME_EXTENSIONS:
                continue
            if source.is_symlink() or not source.is_file():
                raise ValueError("snapshot frames must be regular files, not symlinks")
            body = source.read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            if digest in wanted:
                matches.setdefault(digest, (body, source.suffix.lower()))
    if not matches:
        return
    # Keep aliases one level below the analysis directory: hosts may replay a nested run
    # with a bounded collection depth, so an extra frames/ level would lose these bytes.
    target = analysis
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValueError("canonical frame directory must be a regular directory")
    target.mkdir(mode=0o700, parents=False, exist_ok=True)
    os.chmod(target, 0o700)
    for item in items:
        match = matches.get(str(item.get("sha256") or "").lower())
        if match is None:
            continue
        frame_id = str(item.get("frame_id") or "")
        if (frame_id in {"", ".", ".."} or any(char in frame_id for char in ("/", "\\", "\0"))):
            raise ValueError("unsafe canonical frame_id")
        body, extension = match
        # IDs may contain '#' or Unicode, which some hosts cannot replay in filenames.
        # Hash the exact ID, never an ordinal or timestamp; keep the ID itself unchanged.
        name = "frame-" + hashlib.sha256(frame_id.encode("utf-8")).hexdigest() + extension
        destination = target / name
        if destination.is_symlink():
            raise ValueError("canonical frame destination must not be a symlink")
        if destination.exists():
            if not destination.is_file() or destination.read_bytes() != body:
                raise ValueError("canonical frame destination already contains different bytes")
        else:
            # Exclusive creation also refuses a destination appearing between the checks.
            with destination.open("xb") as stream:
                os.chmod(destination, 0o600)
                stream.write(body)
        item["path"] = str(destination)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def envelope_of(document: Any) -> dict[str, Any]:
    """An envelope, or the terminal envelope a deferred job resource carries."""
    record = as_record(document)
    if {"connector_id", "operation", "status"} <= set(record):
        return record
    inner = as_record(record.get("envelope"))
    if {"connector_id", "operation", "status"} <= set(inner):
        return inner
    if record.get("job_id") and not inner:
        raise ValueError(f"job {record.get('job_id')} carries no terminal envelope yet: pass it as "
                         "--deferred-incomplete <operation>=<job id> instead of the job resource")
    raise ValueError("not a connector envelope or a job resource carrying one")


def role_of(envelope: dict[str, Any]) -> str | None:
    if (envelope.get("connector_id") == "kabo-video"
            and envelope.get("operation") == "get_video_transcript"):
        return "captions"
    if (envelope.get("connector_id") == YOUTUBE_METADATA_CONNECTOR
            and str(envelope.get("operation")) == YOUTUBE_METADATA_OPERATION):
        return "metadata"
    if (envelope.get("connector_id") in POST_CONNECTORS
            and str(envelope.get("operation")) == POST_OPERATION):
        return "public_post"
    if envelope.get("connector_id") != MEDIA_CONNECTOR:
        return None
    # A YouTube measurement now runs through the same hosted fetch as its keyframes and comes back
    # under that execution's name; it is still the measurement that was asked for.
    if envelope.get("operation") == "measure_media_signals_hosted_stream":
        return "measurements"
    return ROLE_OF_OPERATION.get(str(envelope.get("operation")))


def normalise_metadata(envelope: dict[str, Any]) -> dict[str, Any]:
    """Give the data API's row the shape the downloader's projection already has.

    Everything downstream reads ``data.video.<field>``. Normalising here rather than teaching
    each reader a second shape keeps one definition of what a metadata envelope looks like --
    and the fields are the same facts under different names, not different facts.
    """
    if (envelope.get("connector_id") != YOUTUBE_METADATA_CONNECTOR
            or str(envelope.get("operation")) != YOUTUBE_METADATA_OPERATION):
        return envelope
    rows = as_record(envelope.get("data")).get("rows")
    row = as_record(rows[0]) if isinstance(rows, list) and rows else {}
    if not row:
        return envelope
    metrics = as_record(row.get("metrics"))
    thumbnails = row.get("thumbnails")
    # The connector orders the tiers highest first; take its first rather than re-ranking.
    cover = as_record(thumbnails[0]).get("url") if isinstance(thumbnails, list) and thumbnails else None

    def count(value: Any) -> Any:
        # The data API returns counts as strings; the downloader returned integers.
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return value if isinstance(value, int) else None

    normalised = dict(envelope)
    normalised["data"] = {
        "video": {
            "id": row.get("id"),
            "title": row.get("title"),
            "description": row.get("description"),
            "webpage_url": row.get("source_url"),
            "channel": row.get("channel_title"),
            "channel_id": row.get("channel_id"),
            "duration_seconds": row.get("duration_seconds"),
            # RFC 3339 here, compact YYYYMMDD from the downloader. content_ref() already parses
            # both, and an ISO instant is the more precise of the two -- do not truncate it.
            "upload_date": row.get("published_at"),
            "view_count": count(metrics.get("views")),
            "like_count": count(metrics.get("likes")),
            "comment_count": count(metrics.get("comments")),
            "thumbnail": cover,
            "tags": row.get("tags") or [],
        }
    }
    return normalised


def usable(envelope: dict[str, Any] | None) -> bool:
    return envelope is not None and envelope.get("status") in USABLE


def pick(envelopes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The last usable response of one operation (a retry after a failure), else the last one."""
    for envelope in reversed(envelopes):
        if usable(envelope):
            return envelope
    return envelopes[-1] if envelopes else None


def limitation_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    record = as_record(value)
    message = record.get("message")
    return message.strip() if isinstance(message, str) and message.strip() else None


def content_ref(request: dict[str, Any], metadata: dict[str, Any] | None, published_at: str | None,
                post_published_at: str | None = None) -> dict[str, Any]:
    source = as_record(request.get("source"))
    video = as_record(as_record(metadata.get("data")).get("video")) if usable(metadata) else {}
    platform = source.get("platform")
    if platform not in PLATFORM_PREFIX:
        raise ValueError(f"request source.platform is {platform!r}, but the evidence is for one platform "
                         f"({', '.join(sorted(PLATFORM_PREFIX))}): set the platform the creator named in the request")
    file_ref = source.get("file_ref")
    content_id = source.get("content_id") or video.get("id") or (Path(str(file_ref)).stem if file_ref else None)
    permalink = source.get("url") or video.get("webpage_url")
    if not permalink and file_ref:
        # An unpublished draft has no platform address; its locator is the file itself.
        permalink = Path(str(file_ref)).expanduser().resolve().as_uri()
    if not content_id or not permalink:
        raise ValueError("content_ref needs a content_id and a URL or file_ref, from the request or from "
                         "collect_public_metadata")
    # 帖子投影的发布时间只补 metadata 没给的：metadata 是平台对自己视频的回答，两者都有时它优先。
    published = published_at or video.get("upload_date") or post_published_at
    if not published and source.get("subject_state") == "own_unpublished":
        published = "unpublished"
    precise = False
    if published and published != "unpublished":
        text = str(published).strip()
        try:
            if re.fullmatch(r"\d{8}", text):  # the metadata's compact YYYYMMDD upload date
                datetime.strptime(text, "%Y%m%d")
            elif "T" in text:
                datetime.fromisoformat(text.replace("Z", "+00:00"))
                precise = True
            else:
                datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"published_at {text!r} is neither a date nor an ISO date-time") from None
    return {
        "platform": platform,
        "content_id": str(content_id),
        "permalink": str(permalink),
        "published_at": str(published) if published else "unknown",
        "published_at_precision": "second" if precise else "day",
        # Only a time the creator stated with its offset says which clock it was read on.
        "published_at_timezone_source": "creator_confirmed" if published_at and precise and ZONED.search(str(published_at)) else "unknown",
        "format": source.get("declared_format") or "unknown",
        "owner_ref": video.get("channel") or None,
    }


def acquisition_entry(envelope: dict[str, Any]) -> dict[str, Any]:
    status = envelope.get("status")
    return {
        "step": "connector_call",
        "connector_id": str(envelope.get("connector_id")),
        "operation": str(envelope.get("operation")),
        "connector_request_id": str(envelope.get("request_id") or envelope.get("job_id") or "unknown"),
        "status": status if status in ACQUISITION_STATUSES else "failed",
        "retrieved_at": envelope.get("retrieved_at"),
        "raw_hash": envelope.get("raw_sha256"),
        "raw_retained_outside_git": True,
        "credits_charged": None,
        "note": None,
    }


def local_acquisition_entry(step: dict[str, Any], input_sha256: str | None) -> dict[str, Any]:
    """One step this run ran here, recorded as what it was.

    The connector keys are omitted rather than set to null or to a placeholder. Nothing was asked of
    a connector, so an entry saying `connector_id: "local"` or `connector_request_id: "unknown"`
    would be describing a call that never happened -- and the schema refuses those keys on this
    branch for that reason, including when they are present and null.
    """
    status = step.get("status")
    return {
        "step": "local_extraction",
        "operation": str(step.get("operation")),
        "status": status if status in ACQUISITION_STATUSES else "failed",
        "tool": str(step.get("tool")),
        "tool_version": str(step.get("tool_version") or "unreported"),
        "invocation_sha256": str(step.get("invocation_sha256") or ""),
        "input_sha256": input_sha256,
        "retrieved_at": step.get("started_at"),
        "raw_hash": None,
        "raw_retained_outside_git": True,
        "credits_charged": None,
        "note": step.get("note"),
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 2)


def loudness_summary(loudness: dict[str, Any]) -> dict[str, Any] | None:
    """What an analysis cites from a several-hundred-sample series: the lead-in, the range, the mean.

    Copying the series itself would move the cost of reading it from the one envelope that holds it
    to every consumer of the evidence, which is the opposite of the point. The envelope keeps the
    samples; a reading that needs one goes there.
    """
    points = []
    for sample in loudness.get("series") or []:
        record = as_record(sample)
        at, value = record.get("t_seconds"), record.get("momentary_lufs")
        if isinstance(at, (int, float)) and isinstance(value, (int, float)):
            points.append((float(at), float(value)))
    if not points:
        return None
    points.sort()
    values = [value for _, value in points]
    audible = [(at, value) for at, value in points if value > SILENT_LUFS]
    first_at, first_value = audible[0] if audible else (None, None)
    working = [value for _, value in audible] or values
    # The mean is of the audible track. A digitally silent sample is a sentinel near -120 LUFS, and
    # averaging it in says a video with a quiet opening is quieter throughout than it is: five
    # silent seconds at the head of a thirty-second clip drag the mean down by about twenty dB.
    interval = loudness.get("interval_s")
    lead_in = None
    if isinstance(first_at, float) and isinstance(interval, (int, float)):
        lead_in = round(max(0.0, first_at - float(interval)), 3)
    return {
        "interval_s": interval if isinstance(interval, (int, float)) else None,
        "samples": len(points),
        "mean_lufs": round(sum(working) / len(working), 2),
        "min_lufs": round(min(values), 2),
        "max_lufs": round(max(values), 2),
        "p10_lufs": percentile(working, 0.1),
        "p90_lufs": percentile(working, 0.9),
        "lead_in_silence_s": lead_in,
        "first_audible_s": first_at,
        "first_audible_lufs": round(first_value, 2) if isinstance(first_value, float) else None,
    }


def probed_block(measurements: dict[str, Any] | None) -> dict[str, Any] | None:
    """The measured file, as numbers. Every reading of them is an observation and lives elsewhere."""
    if not usable(measurements):
        return None
    data = as_record(measurements.get("data"))
    block: dict[str, Any] = {}
    for key, allowed in PROBED_KEYS.items():
        value = as_record(data.get(key))
        kept = {name: item for name, item in value.items() if name in allowed}
        if kept:
            block[key] = kept
    summary = loudness_summary(as_record(data.get("loudness")))
    if summary:
        block["loudness"] = summary
        block["mean_volume_db"] = summary["mean_lufs"]
    for key in ("measured", "unavailable"):
        value = data.get(key)
        if isinstance(value, list):
            block[key] = [item for item in value if isinstance(item, str)]
    return block or None


def metadata_public_metrics(metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Public counters from the normalized metadata, without inventing missing values."""
    if not usable(metadata):
        return []
    video = as_record(as_record(metadata.get("data")).get("video"))
    metrics = []
    source = f"{metadata.get('connector_id')}.{metadata.get('operation')}"
    for native_name in ("view_count", "like_count", "comment_count"):
        value = video.get(native_name)
        # Zero is observed; null, booleans and non-finite numbers are not counters.
        if type(value) is int or (type(value) is float and math.isfinite(value)):
            metrics.append({
                "native_name": native_name, "value": value, "equals_owner_metric": None,
                "note": f"Public {native_name} from {source}; not reach or an owner metric.",
            })
    return metrics


def graphql_post(row: dict[str, Any]) -> tuple[Any, list[tuple[str, Any, str | None]], dict[str, Any]]:
    """Caption, counters and sound of an old single-operation post row (GraphQL field names)."""
    edges = as_record(row.get("edge_media_to_caption")).get("edges")
    caption = (as_record(as_record(edges[0]).get("node")).get("text")
               if isinstance(edges, list) and edges else None)
    counters = [
        ("video_play_count", row.get("video_play_count"), PLAYS_NOTE),
        ("edge_media_preview_like.count", as_record(row.get("edge_media_preview_like")).get("count"), None),
        ("comment_count", row.get("comment_count"), None),
    ]
    return caption, counters, as_record(row.get("clips_music_attribution_info"))


def projected_post(row: dict[str, Any],
                   facts: dict[str, Any]) -> tuple[Any, list[tuple[str, Any, str | None]], dict[str, Any]]:
    """Caption, counters and sound of a `kabo.instagram_post.v1` post; also its publish time."""
    # native_name 写投影里的字段路径，报告引用的就是这个名字。IG 播放数与 IG+FB 合并值分开记，
    # 合并值不冒充 IG 播放数；点赞被作者隐藏时 likes 为 null，记一条限制，说明它不是 0。
    # thumbnail_url 是会过期的 CDN 链接，不当封面地址写进证据。
    metrics = as_record(row.get("metrics"))
    if metrics.get("likes_hidden") is True:
        facts["limitations"] = ["The post hides its like count, so likes are unavailable, not zero."]
    if isinstance(row.get("published_at"), str) and row["published_at"].strip():
        facts["published_at"] = row["published_at"]
    counters = [
        ("metrics.plays_instagram", metrics.get("plays_instagram"),
         "An Instagram-only play count, which is neither reach nor an owner view figure."),
        ("metrics.plays_combined", metrics.get("plays_combined"),
         "Instagram and Facebook plays combined: not an Instagram-only count, and neither reach nor an owner "
         "view figure."),
        ("metrics.likes", None if metrics.get("likes_hidden") is True else metrics.get("likes"), None),
        ("metrics.comments", metrics.get("comments"), None),
    ]
    return row.get("caption"), counters, as_record(row.get("audio"))


def post_facts(post: dict[str, Any] | None) -> dict[str, Any]:
    """Platform facts about the published post: its public counters, its caption, its sound.

    These were retrieved on every Instagram run and then dropped on the floor, so an analysis that
    wanted the play count had to read the raw envelope and retype the number. A figure that only
    ever exists because a model typed it is not evidence, and the shape check downstream - which
    grounds every number in the report against this object and the analysis - was grounding those
    numbers against the analysis alone.
    """
    if not usable(post):
        return {}
    data = as_record(post.get("data"))
    row = as_record(data.get("post"))
    if not row:
        return {}
    facts: dict[str, Any] = {}
    if data.get("projection") == POST_PROJECTION:
        caption, counters, sound = projected_post(row, facts)
    else:
        caption, counters, sound = graphql_post(row)
    if isinstance(caption, str) and caption.strip():
        facts["caption"] = caption
        facts["hashtags"] = HASHTAG.findall(caption)  # the creator's own order
    metrics = []
    for native_name, value, note in counters:
        # null 与布尔都不是计数：拿不到就不写，不当 0。
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics.append({"native_name": native_name, "value": value,
                            "equals_owner_metric": None, "note": note})
    if metrics:
        facts["public_metrics"] = metrics
    if sound:
        facts["audio_attribution"] = {
            "uses_original_audio": sound.get("uses_original_audio"),
            "audio_id": sound.get("audio_id"),
            "artist_name": sound.get("artist_name"),
            "song_name": sound.get("song_name"),
            # No registered connector supplies audio usage velocity or competition.
            "trend_metrics_available": False,
        }
    derived = []
    caption = facts.get("caption")
    if isinstance(caption, str):
        derived.append({
            "name": "caption_length_characters", "formula": "len(caption)",
            "formula_version": "v1", "inputs": ["content_metadata.caption"],
            "value": len(caption), "unit": "characters", "denominator_known": True, "caveat": None,
        })
        derived.append({
            "name": "caption_hashtag_count", "formula": "count of '#' tokens in caption",
            "formula_version": "v1", "inputs": ["content_metadata.caption"],
            "value": len(facts.get("hashtags") or []), "unit": "hashtags",
            "denominator_known": True, "caveat": None,
        })
    if derived:
        facts["derived"] = derived
    return facts


def media_block(frames: dict[str, Any] | None, metadata: dict[str, Any] | None,
                local: dict[str, Any] | None = None,
                measurements: dict[str, Any] | None = None) -> dict[str, Any]:
    if local is not None:
        facts = as_record(local.get("media"))
        duration = facts.get("duration_s")
        encoding = {key: value for key, value in as_record(facts.get("encoding")).items()
                    if key in ENCODING_KEYS}
        # `declared` and `discrepancies` stay absent on this path, and that is correct rather than
        # missing: nothing declared a duration to disagree with. The platform's own numbers are not
        # here because the platform was never asked -- this is the file the creator exported.
        local_block = {
            "status": "available" if isinstance(duration, (int, float)) and duration > 0 else "unavailable",
            "duration_s": duration,
            "duration_source": "local_probe" if isinstance(duration, (int, float)) and duration > 0 else "unavailable",
            "width": facts.get("width"),
            "height": facts.get("height"),
            "has_audio": facts.get("has_audio"),
            "encoding": encoding or None,
        }
        # Measured here with the connector's thresholds and in its shape, so it reads through the
        # same filter: a reader of `probed` never needs to know which side measured the file.
        signals = facts.get("signals")
        probed = probed_block({"status": "completed", "data": signals}) if isinstance(signals, dict) else None
        if probed:
            local_block["probed"] = probed
        return local_block
    observed = as_record(frames.get("data")).get("duration_s") if usable(frames) else None
    declared = as_record(as_record(metadata.get("data")).get("video")).get("duration_seconds") if usable(metadata) else None
    observed = observed if isinstance(observed, (int, float)) and observed > 0 else None
    declared = declared if isinstance(declared, (int, float)) and declared > 0 else None
    if observed is not None:
        duration, source = float(observed), "connector_extract_keyframes"
    elif declared is not None:
        duration, source = float(declared), "connector_metadata"
    else:
        duration, source = None, "unavailable"
    block: dict[str, Any] = {
        "status": "available" if duration is not None else ("not_attempted" if frames is None and metadata is None else "unavailable"),
        "duration_s": duration,
        "duration_source": source,
        "width": None,
        "height": None,
        "has_audio": None,
    }
    # Where the media could only be fetched once, the keyframe job measures the same file it sampled
    # and carries the result in the measurement operation's shape. A separate measurement response,
    # when one arrived, still wins: it is the call that was asked for.
    carried = as_record(as_record(frames.get("data")).get("signals")) if usable(frames) else {}
    if not usable(measurements) and carried:
        measurements = {"status": "completed", "data": carried}
    probed = probed_block(measurements)
    if probed and duration is not None:
        # The connector leaves the span a file ends inside open (no end line was printed); the
        # evidence wants a number there, and with the length known its end is the end of the file.
        for key in ("silence", "freeze"):
            for span in as_record(probed.get(key)).get("spans") or []:
                if isinstance(span, dict) and span.get("end_s") is None and isinstance(span.get("start_s"), (int, float)):
                    span["end_s"] = round(duration, 3)
                    span["duration_s"] = round(max(0.0, duration - span["start_s"]), 3)
    if probed:
        block["probed"] = probed
        framing = as_record(probed.get("framing"))
        # The measured frame is the one viewers received; nothing declared it, so this fills a
        # field that has been null on every connector run rather than contradicting one.
        for field in ("width", "height"):
            value = framing.get(field)
            if isinstance(value, int) and value > 0:
                block[field] = value
    if usable(metadata):
        block["declared"] = {"width": None, "height": None, "duration_s": declared}
        # One second is the precision the platforms declare durations at.
        if observed is not None and declared is not None and abs(float(observed) - float(declared)) > 1:
            block["discrepancies"] = [{"field": "duration_s", "declared": declared, "observed": observed}]
    return block


def cues_from(records: Any) -> list[dict[str, Any]]:
    """Cue records to evidence cues, in one place.

    Both the connector path and a creator's subtitle file arrive here. The millisecond-to-second
    conversion, the blank-text drop and the rounding are written once on purpose: a second copy of
    this loop is exactly how a start pasted across unchanged came out a thousand times too large,
    and how a cue set was dropped because one field name did not match.
    """
    cues = []
    for cue in records or []:
        cue = as_record(cue)
        text = cue.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        # The connector carries each start in milliseconds; the evidence carries seconds.
        if isinstance(cue.get("start_ms"), (int, float)):
            start = round(float(cue["start_ms"]) / 1000.0, 3)
        elif isinstance(cue.get("start_s"), (int, float)):
            start = round(float(cue["start_s"]), 3)
        else:
            continue
        cues.append({"start_s": start, "text": text.strip()})
    return cues


def local_transcript_block(local: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """The transcript a creator handed over, as evidence.

    `origin` is `creator_supplied` and that is the load-bearing field. It is not machine-heard, so
    a line may be quoted as the creator's own wording; it is also not what the platform published
    and was never checked against the audio, so a second taken from it is the file's claim about
    the video rather than a measurement of it.
    """
    block: dict[str, Any] = {
        "status": "not_attempted",
        "status_basis": "no subtitle file was supplied for this run",
        "source_connector": None,
        "granularity": "none",
        "timestamps_available": False,
        "language_requested": as_record(request.get("acquire")).get("transcript_language"),
        "language": local.get("language"),
        "segment_count": None,
        "character_count": None,
        "text_sha256": None,
        "origin": None,
    }
    if local.get("status") != "available":
        block["status"] = "provider_error"
        block["status_basis"] = str(local.get("status_basis") or "the supplied subtitle file could not be read")
        return block

    cues = cues_from(local.get("cues"))
    plain = local.get("plain_text") if isinstance(local.get("plain_text"), str) else None
    name = local.get("source_file_name") or "the file the creator supplied"
    # A host's machine transcription of the same upload arrives through the same reader, and keeps
    # its own origin: its words are what the analysis heard, never the creator's exact wording.
    heard = local.get("origin") == "speech_recognition"
    if cues:
        text = "\n".join(cue["text"] for cue in cues)
        block.update({
            "status": "available", "granularity": "timestamped", "timestamps_available": True,
            "segment_count": len(cues), "cues": cues,
            "status_basis": (f"read {len(cues)} cues the host transcribed from this file by machine"
                             if heard else
                             f"read {len(cues)} cues from {name}, the subtitle file the creator "
                             "supplied; its timings were not checked against the media"),
        })
    elif plain and plain.strip():
        text = plain.strip()
        block.update({
            "status": "available", "granularity": "plain_text", "plain_text": text,
            "status_basis": ("read untimed text the host transcribed from this file by machine"
                             if heard else f"read untimed text from {name}, the file the creator supplied"),
        })
    else:
        block["status"] = "provider_error"
        block["status_basis"] = f"{name} carried no readable text"
        return block

    block["origin"] = "speech_recognition" if heard else "creator_supplied"
    block["character_count"] = len(text)
    block["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return block


def transcript_block(captions: dict[str, Any] | None, request: dict[str, Any]) -> dict[str, Any]:
    language_requested = as_record(request.get("acquire")).get("transcript_language")
    block: dict[str, Any] = {
        "status": "not_attempted",
        "status_basis": "no fetch_transcript response was persisted for this run",
        "source_connector": None,
        "granularity": "none",
        "timestamps_available": False,
        "language_requested": language_requested,
        "language": None,
        "segment_count": None,
        "character_count": None,
        "text_sha256": None,
    }
    if captions is None:
        return block
    data = as_record(captions.get("data"))
    block["source_connector"] = captions["connector_id"]
    operation = str(captions["operation"])
    native = captions["connector_id"] == "kabo-video"
    language = data.get("language")
    block["language"] = language if isinstance(language, str) and language else None
    if captions.get("status") not in USABLE:
        status = captions.get("status")
        why = ("the transcript path is not available for this video here" if status in NOT_A_FAILURE
               else "the call failed")
        block["status"] = "provider_error"
        block["status_basis"] = f"{operation} returned status {status!r}: {why}"
        return block
    if native and data.get("status") != "available":
        block.update(status="provider_error", status_basis=f"{operation} did not return an available transcript")
        return block
    cues = cues_from(data.get("cues"))
    plain = data.get("plain_text") if isinstance(data.get("plain_text"), str) else None
    origin = data.get("origin") if data.get("origin") in TRANSCRIPT_ORIGINS else None
    if cues:
        text = "\n".join(cue["text"] for cue in cues)
        block.update({
            "status": "available",
            "status_basis": f"{operation} returned {len(cues)} timed cues",
            "granularity": "timestamped",
            "timestamps_available": True,
            "segment_count": len(cues),
            "cues": cues,
        })
    elif plain and plain.strip():
        text = plain.strip()
        block.update({
            "status": "available",
            "status_basis": f"{operation} returned text without cue timing",
            "granularity": "plain_text",
            "plain_text": text,
        })
    else:
        block.update({
            "status": "provider_error" if native else "no_platform_captions",
            "status_basis": (f"{operation} completed with no readable transcript" if native else
                             "fetch_transcript completed with no cue text: the video exposes no subtitle track"),
        })
        block["origin"] = origin
        return block
    block["character_count"] = len(text)
    block["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    block["origin"] = origin
    return block


def frames_block(frames: dict[str, Any] | None, local: dict[str, Any] | None = None) -> dict[str, Any]:
    empty = {"status": "not_attempted", "selection": "none", "requested_timestamps": None, "items": [],
             "sampling_plan": None, "origin": None}
    if local is not None:
        block = as_record(local.get("frames"))
        items = []
        for frame in block.get("items") or []:
            frame = as_record(frame)
            if not isinstance(frame.get("t_seconds"), (int, float)) or not frame.get("frame_id"):
                continue
            items.append({
                "frame_id": str(frame["frame_id"]),
                "t_seconds": float(frame["t_seconds"]),
                "sha256": frame.get("sha256"),
                "retained_outside_git": True,
                "path": frame.get("path"),
            })
        plan = block.get("sampling_plan")
        plan = {key: value for key, value in plan.items() if key in SAMPLING_PLAN_KEYS} if isinstance(plan, dict) else None
        selection = block.get("selection") if block.get("selection") in FRAME_SELECTIONS else "none"
        if not items:
            return {**empty, "status": "unavailable", "selection": selection, "sampling_plan": plan}
        return {
            "status": "available",
            "selection": selection,
            "requested_timestamps": [item["t_seconds"] for item in items],
            "items": items,
            "sampling_plan": plan,
            # Real pixels, sampled from the file the creator named. The same origin the connector
            # path sets, because it is the same claim: somebody looked at this frame this run.
            "origin": "extracted_frames",
        }
    if frames is None:
        return empty
    if not usable(frames):
        return {**empty, "status": "unavailable"}
    data = as_record(frames.get("data"))
    items = []
    for frame in data.get("frames") or []:
        frame = as_record(frame)
        if not isinstance(frame.get("t_seconds"), (int, float)) or not frame.get("frame_id"):
            continue
        items.append({
            "frame_id": str(frame["frame_id"]),
            "t_seconds": float(frame["t_seconds"]),
            "sha256": frame.get("sha256"),
            "retained_outside_git": True,
            "path": frame.get("object_ref"),
        })
    plan = data.get("sampling_plan")
    plan = {key: value for key, value in plan.items() if key in SAMPLING_PLAN_KEYS} if isinstance(plan, dict) else None
    selection = data.get("selection") if data.get("selection") in FRAME_SELECTIONS else "none"
    if items:
        return {
            "status": "available",
            "selection": selection,
            "requested_timestamps": [item["t_seconds"] for item in items],
            "items": items,
            "sampling_plan": plan,
            "origin": "extracted_frames",
        }
    observations = []
    for entry in data.get("observations") or []:
        entry = as_record(entry)
        if isinstance(entry.get("t_seconds"), (int, float)) and isinstance(entry.get("description"), str):
            text = entry.get("on_screen_text")
            observations.append({
                "t_seconds": float(entry["t_seconds"]),
                "description": entry["description"],
                "on_screen_text": text if isinstance(text, str) else None,
            })
    if observations:
        return {**empty, "status": "available", "origin": "model_observation", "observations": observations}
    return {**empty, "status": "unavailable"}


def thumbnail_tier(url: str) -> str:
    """The rendition a cover URL names. YouTube spells it in the file name; another platform's single
    cover is its default one, which is the only one it has."""
    stem = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    for name, tier in YOUTUBE_THUMBNAIL_TIERS.items():
        if stem == name or stem.startswith(name + "_"):
            return tier
    return "default"


def metadata_block(metadata: dict[str, Any] | None,
                   post: dict[str, Any] | None = None) -> dict[str, Any]:
    # The post row fills what the metadata operation did not return, never what it did: on Instagram
    # the downloader answers with no title and no tags, and the caption a viewer actually reads is
    # in the post row. Where both speak, the metadata operation is the platform's own answer about
    # its own video and wins.
    facts = post_facts(post)

    def filled(block: dict[str, Any]) -> dict[str, Any]:
        # The post row fills fields; it does not vouch for the metadata operation. Promoting the
        # status here would turn "the platform was not asked, or did not answer" into "the platform
        # published no cover and no tags", and the empty `thumbnails` array would then read as a
        # fact. It would also silence the downstream check that only fires on `unavailable`.
        if not facts:
            return block
        if not block.get("caption") and facts.get("caption"):
            block["caption"] = facts["caption"]
        if not block["tags"] and facts.get("hashtags"):
            block["tags"] = facts["hashtags"]
        return block

    if metadata is None:
        return filled({"status": "not_attempted", "title": None, "caption": None,
                       "tags": [], "thumbnails": []})
    # Only a complete metadata response says what the platform declares; a partial one cannot tell
    # "no tags" from "tags not returned".
    if metadata.get("status") != "completed":
        return filled({"status": "unavailable", "title": None, "caption": None,
                       "tags": [], "thumbnails": []})
    video = as_record(as_record(metadata.get("data")).get("video"))
    tags = [tag for tag in video.get("tags") or [] if isinstance(tag, str)]  # the creator's own order
    thumbnail = video.get("thumbnail")
    thumbnails = ([{"tier": thumbnail_tier(thumbnail), "url": thumbnail}]
                  if isinstance(thumbnail, str) and thumbnail else [])
    title = video.get("title")
    caption = video.get("description")
    caption = caption if isinstance(caption, str) and caption.strip() else None
    return filled({"status": "available", "title": title if isinstance(title, str) else None,
                   "caption": caption, "tags": tags, "thumbnails": thumbnails})


def build(
    request: dict[str, Any],
    envelope_paths: list[Path],
    *,
    run_id: str | None = None,
    owner_metrics: dict[str, Any] | None = None,
    limitations: list[str] | None = None,
    published_at: str | None = None,
    pending: list[str] | None = None,
    deferred_incomplete: list[str] | None = None,
    local_media: dict[str, Any] | None = None,
    local_transcript: dict[str, Any] | None = None,
    snapshot_root: Path | None = None,
    analysis_root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    from prepare_link_request import public_only_request_errors
    scope_errors = public_only_request_errors(request)
    if scope_errors:
        raise ValueError("; ".join(scope_errors))
    if as_record(request.get("source")).get("subject_state") == "unknown" and owner_metrics is not None:
        raise ValueError("unknown ownership cannot consume an owner export")
    acquisition_mode = as_record(request.get("source")).get("acquisition")
    if local_media is not None and acquisition_mode != "local_file":
        raise ValueError(
            f"a local media artifact was given but the request declares acquisition "
            f"{acquisition_mode!r}: the evidence would claim it measured a file the request never named"
        )
    # A deferred job still unfinished has a job id and no envelope yet. It is recorded as pending
    # (polling window over, run still going) or deferred_incomplete (the run is closing), never as
    # failed: nothing came back, and nothing failed either.
    pending_entries = []
    for status, entries, flag in (("pending", pending, "--pending"),
                                  ("deferred_incomplete", deferred_incomplete, "--deferred-incomplete")):
        for entry in entries or []:
            operation, _, job_id = entry.partition("=")
            if not operation or not job_id:
                raise ValueError(f"{flag} takes <operation>=<job id>, got {entry!r}")
            pending_entries.append({
                "step": "connector_call",
                "connector_id": MEDIA_CONNECTOR, "operation": operation, "connector_request_id": job_id,
                "status": status, "retrieved_at": None, "raw_hash": None, "raw_retained_outside_git": True,
                "credits_charged": None, "note": "deferred job still unfinished when the acquisition window closed",
            })
    if not envelope_paths and not pending_entries and local_media is None and local_transcript is None:
        # An evidence object records at least one acquisition; with none there is nothing to derive.
        raise ValueError("no connector envelope was given: persist every fetch this run made, failed ones "
                         "included, and pass each with --envelope")
    loaded = [(path, load_json(path)) for path in envelope_paths]
    linked_bundles = [candidate for _, body in loaded
                      for candidate in (body, body.get("linked_media", {}))
                      if isinstance(candidate, dict) and candidate.get("schema_version") == linked_media.SCHEMA]
    envelopes: list[tuple[Path, dict[str, Any]]] = [
        (path, normalise_metadata(envelope_of(body))) for path, body in loaded
        if body.get("schema_version") != linked_media.SCHEMA
        and not (set(body) == {"schema_version", "generated_at", "max_in_flight", "connectors"}
                 and body.get("schema_version") == "1.0"
                 and isinstance(body.get("generated_at"), str)
                 and type(body.get("max_in_flight")) is int
                 and isinstance(body.get("connectors"), list)
                 and all(isinstance(connector, dict) for connector in body["connectors"]))
    ]
    # Hosts also retain readiness catalogs in the same staging directory. They are acquisition
    # metadata, not connector evidence; unknown documents and malformed envelopes still fail.
    if not envelopes and not linked_bundles and not pending_entries and local_media is None and local_transcript is None:
        raise ValueError("no content acquisition was given: a readiness catalog is not post evidence")
    by_role: dict[str, list[dict[str, Any]]] = {}
    for _, envelope in envelopes:
        role = role_of(envelope)
        if role:
            by_role.setdefault(role, []).append(envelope)
    metadata = pick(by_role.get("metadata", []))
    captions = pick(by_role.get("captions", []))
    frames = pick(by_role.get("frames", []))
    measurements = pick(by_role.get("measurements", []))
    post = pick(by_role.get("public_post", []))

    if acquisition_mode == "local_file" and (frames is not None or captions is not None
                                             or measurements is not None):
        # The enforcement half of the Skill's gate. A media-connector response to a request naming a
        # local file did not measure this video -- it could not have, the file is unreachable from
        # there -- so it is not evidence about it, and merging the two would put two different
        # copies of one video into a single object.
        raise ValueError(
            "this request names a local file, which no public-video-media operation can reach: the "
            "media envelopes passed here did not measure this video and are not evidence about it. "
            "Run the local scripts instead, and pass their artifacts with --local-media / "
            "--local-transcript"
        )

    # Every step a run took gets a ledger entry, the subtitle read included -- a run whose media
    # tools were absent and whose only evidence is that file would otherwise build an evidence
    # object with an empty acquisition array, which the schema refuses.
    local_steps = [as_record(step) for step in as_record(local_media).get("steps") or []]
    local_steps += [as_record(step) for step in as_record(local_transcript).get("steps") or []]

    ref = content_ref(request, metadata, published_at, post_facts(post).get("published_at"))
    # Tier 1 before tier 2, which is the order SKILL.md states. A supplied subtitle file is the
    # fallback for a cut with no published captions, not an override of the ones the platform
    # returned: taking it unconditionally would throw away what a viewer with captions on actually
    # reads, and relabel the creator's own published wording as a file they handed over.
    transcript = transcript_block(captions, request)
    if local_transcript is not None and transcript["status"] != "available":
        transcript = local_transcript_block(local_transcript, request)
        used_local_transcript = True
    else:
        used_local_transcript = False
    frame_block = frames_block(frames, local_media)
    # Local extraction already writes its own real files under hashed names and records each
    # path. Only connector pixels are renamed by host envelope staging.
    if snapshot_root is not None and local_media is None:
        if analysis_root is None:
            raise ValueError("snapshot frame binding requires this run's analysis directory")
        materialise_frame_aliases(frame_block, snapshot_root, analysis_root)
    anything = transcript["status"] == "available" or frame_block["status"] == "available" or usable(metadata)
    notes: list[str] = []
    for _, envelope in envelopes:
        fact = f"{envelope.get('operation')} returned {envelope.get('status')}."
        for value in envelope.get("limitations") or []:
            text = limitation_text(value)
            if not text:
                continue
            # The blanket sentence is true of one call, not of the run: keep the fact, drop the words.
            if anything and BLANKET in text.lower():
                text = fact
            if text not in notes:
                notes.append(text)
        if not usable(envelope) and fact not in notes and not any(fact[:-1] in note for note in notes):
            notes.append(fact)
    for text in as_record(local_media).get("limitations") or []:
        if isinstance(text, str) and text.strip() and text.strip() not in notes:
            notes.append(text.strip())
    if local_transcript is not None and not used_local_transcript:
        notes.append("A subtitle file was supplied, but the platform's own captions were retrieved "
                     "and were used instead: they are what a viewer with captions on reads.")
    if used_local_transcript and transcript["status"] == "available":
        last_cue = max((cue["start_s"] for cue in transcript.get("cues") or []), default=None)
        duration = as_record(as_record(local_media).get("media")).get("duration_s")
        if last_cue is not None and isinstance(duration, (int, float)) and last_cue > duration:
            # Cheap catch for the commonest way this tier goes wrong: a subtitle file belonging to a
            # different, longer cut. Nothing here can check the wording, but a cue that starts after
            # the video ends is a file that is not about this video.
            notes.append(
                f"The supplied subtitle file runs past the end of this cut (a line starts at "
                f"{last_cue:g}s in a {duration:g}s video), so it may belong to a different version."
            )
    if ref["published_at"] == "unknown":
        notes.append("No publish date was available from the retrieved metadata.")
    for text in post_facts(post).get("limitations") or []:
        if text not in notes:
            notes.append(text)
    for text in limitations or []:
        if text.strip() and text.strip() not in notes:
            notes.append(text.strip())

    hashes: dict[str, str] = {}
    for role, chosen in (("metadata_raw", metadata), ("captions_raw", captions), ("frames_raw", frames)):
        if chosen is not None and isinstance(chosen.get("raw_sha256"), str):
            hashes[role] = chosen["raw_sha256"]
    # Keyed by position and file name: a local absolute path would carry the operator's user name and
    # workspace into an artifact that travels downstream, and would key the same input differently per run.
    for index, (path, _) in enumerate(envelopes, start=1):
        hashes.setdefault(f"file:{index:02d}:{path.name}", hashlib.sha256(path.read_bytes()).hexdigest())
    # The replay anchor for a local run: which bytes were read. The invocation that read them is on
    # each acquisition entry, so between the two the step can be run again and compared.
    if isinstance(as_record(local_media).get("input_sha256"), str):
        hashes["local_media_input"] = local_media["input_sha256"]
    if isinstance(as_record(local_transcript).get("source_file_sha256"), str):
        hashes["local_subtitle_input"] = local_transcript["source_file_sha256"]

    evidence: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": f"ev-{PLATFORM_PREFIX[ref['platform']]}-{ref['content_id']}",
        "request_ref": request.get("request_id"),
        "content_ref": ref,
        "acquisition": (
            [acquisition_entry(envelope) for _, envelope in envelopes]
            + [local_acquisition_entry(step, step.get("input_sha256")
                                       or as_record(local_media).get("input_sha256"))
               for step in local_steps]
            + pending_entries
        ),
        "media": media_block(frames, metadata, local_media, measurements),
        "transcript": transcript,
        "frames": frame_block,
        "owner_metrics": owner_metrics or {"status": "unavailable", "source_mode": "unavailable",
                                           "account_ref": None, "snapshot_sha256": None, "items": []},
        "content_metadata": metadata_block(metadata, post),
        "limitations": notes,
        "reproducibility": {
            "run_id": run_id or str(request.get("request_id") or "unknown"),
            "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
            "input_hashes": hashes,
        },
    }
    # Prefer the post row's existing counter names and semantics. When it has no counters,
    # metadata may still carry public ones; absent values stay absent rather than becoming zero.
    facts = post_facts(post)
    if not facts.get("public_metrics"):
        facts["public_metrics"] = metadata_public_metrics(metadata)
    for key in ("public_metrics", "derived", "audio_attribution"):
        if facts.get(key):
            evidence[key] = facts[key]
    linked = linked_media.bind(linked_bundles, [e for _, e in envelopes], request, analysis_root)
    if linked is not None:
        evidence["images"] = linked
        for index, (path, body) in enumerate(loaded):
            if body.get("schema_version") == linked_media.SCHEMA:
                evidence["reproducibility"]["input_hashes"][f"linked_media:{index}:{path.name}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    post_data = (linked_media.public_post_facts([e for _, e in envelopes], as_record(request.get("source")))
                 if linked is not None else None)
    if post_data is not None:
        metadata = evidence["content_metadata"]
        if metadata.get("caption") is None and post_data["caption"] is not None:
            metadata["caption"] = post_data["caption"]
            if not metadata.get("tags"):
                metadata["tags"] = HASHTAG.findall(post_data["caption"])
        elif metadata.get("tags") is None and isinstance(metadata.get("caption"), str):
            metadata["tags"] = HASHTAG.findall(metadata["caption"])
        if "public_metrics" not in evidence and post_data["metrics"]:
            evidence["public_metrics"] = post_data["metrics"]
    caption = evidence["content_metadata"]["caption"]
    if isinstance(caption, str):
        for item in evidence.get("derived", []):
            if item["name"] == "caption_length_characters":
                item["value"] = len(caption)
            elif item["name"] == "caption_hashtag_count":
                item["value"] = len(HASHTAG.findall(caption))
    return evidence


def write_evidence_with_reference_index(output: Path, evidence_bytes: bytes,
                                        reference_path: Path, reference_bytes: bytes) -> None:
    """Stage both files before committing the index, then atomically replace evidence."""
    staged: list[Path] = []
    created_index = False
    try:
        for target, body in ((output, evidence_bytes), (reference_path, reference_bytes)):
            with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent,
                                             prefix=f".{target.name}.", delete=False) as stream:
                staged.append(Path(stream.name))
                stream.write(body)
        # A hard link commits the complete index without replacing a concurrent file.
        os.link(staged[1], reference_path)
        created_index = True
        os.replace(staged[0], output)
    except OSError:
        # Never remove a different index another writer installed after our commit.
        if created_index:
            try:
                current = reference_path.lstat()
                ours = staged[1].stat()
                if (current.st_dev, current.st_ino) == (ours.st_dev, ours.st_ino):
                    reference_path.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        for path in staged:
            path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", type=Path, required=True, help="the VideoAnalysisRequest of this run")
    parser.add_argument("--envelope", type=Path, action="append", default=[],
                        help="one persisted connector envelope (repeat for each)")
    parser.add_argument("--owner-metrics", type=Path, help="the owner_metrics block, when an owner export was read")
    parser.add_argument("--limitation", action="append", default=[], help="a limitation observed outside the envelopes")
    parser.add_argument("--published-at", help="the publish time when the metadata carried none")
    parser.add_argument("--pending", action="append", default=[],
                        help="<operation>=<job id> for a deferred job whose polling window ended, run still going")
    parser.add_argument("--deferred-incomplete", action="append", default=[],
                        help="<operation>=<job id> for a deferred job still unfinished as this run closes")
    parser.add_argument("--local-media", type=Path,
                        help="the artifact extract_local_frames.py wrote, for a file read on this machine")
    parser.add_argument("--local-transcript", type=Path,
                        help="the artifact read_subtitle_file.py wrote, for a subtitle file the creator supplied")
    parser.add_argument("--run-id")
    parser.add_argument("--snapshot-root", type=Path,
                        help="this run's snapshot directory; bind staged frame bytes to canonical IDs")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-index", type=Path,
                        help="optional same-source structural reference index for the judgment")
    args = parser.parse_args()
    try:
        reference_path = args.reference_index
        if reference_path is not None:
            inputs = [args.request, *args.envelope, args.owner_metrics,
                      args.local_media, args.local_transcript]
            inputs = [path for path in inputs if path is not None]
            if reference_path.resolve() == args.output.resolve():
                raise ValueError("the evidence and reference index must use different paths")
            for output in (args.output, reference_path):
                for source in inputs:
                    if (output.resolve() == source.resolve()
                            or (output.exists() and source.exists() and output.samefile(source))):
                        raise ValueError("evidence and reference outputs must not overwrite an input")
            if reference_path.exists() or reference_path.is_symlink():
                raise ValueError("the reference sidecar already exists; no input or output was overwritten")
        evidence = build(
            load_json(args.request),
            args.envelope,
            run_id=args.run_id,
            owner_metrics=load_json(args.owner_metrics) if args.owner_metrics else None,
            limitations=args.limitation,
            published_at=args.published_at,
            pending=args.pending,
            deferred_incomplete=args.deferred_incomplete,
            local_media=load_json(args.local_media) if args.local_media else None,
            local_transcript=load_json(args.local_transcript) if args.local_transcript else None,
            snapshot_root=args.snapshot_root,
            analysis_root=args.snapshot_root.parent / "analysis" if args.snapshot_root else None,
        )
        evidence_bytes = (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if reference_path is not None:
            # The validator owns reference resolution; do not maintain a second spelling table
            # or repair the model's judgment. CLI opt-in leaves historical callers unchanged.
            from validate_video_analysis import evidence_handles
            reference_index = {
                "schema_version": "content-evidence-references.v1",
                "evidence_id": evidence["evidence_id"],
                "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                "reference_semantics": "Legal structural references, not proof a fact was observed. "
                                       "Read the evidence value and availability before making a claim.",
                "evidence_refs": sorted(evidence_handles(evidence)),
            }
            reference_path.parent.mkdir(parents=True, exist_ok=True)
            reference_bytes = (json.dumps(reference_index, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if reference_path is None:
            args.output.write_text(evidence_bytes.decode("utf-8"), encoding="utf-8")
        else:
            write_evidence_with_reference_index(args.output, evidence_bytes, reference_path, reference_bytes)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        **({"reference_index": str(reference_path)} if reference_path is not None else {}),
        "transcript": evidence["transcript"]["status"],
        "cues": len(evidence["transcript"].get("cues") or []),
        "frames": evidence["frames"]["status"],
        "frame_count": len(evidence["frames"]["items"]),
        "transcript_origin": evidence["transcript"].get("origin"),
        "local_steps": sum(1 for entry in evidence["acquisition"] if entry.get("step") == "local_extraction"),
        "content_metadata": evidence["content_metadata"]["status"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
