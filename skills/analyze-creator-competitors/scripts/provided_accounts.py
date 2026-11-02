"""Preserve explicitly requested public identities through account selection."""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import unquote, urlparse

CHANNEL_ID = re.compile(r"UC[\w-]{22}", re.ASCII)
# First path segments that are Instagram routes, never usernames (`/p/posts` is a post, not @p).
INSTAGRAM_ROUTES = {"p", "reel", "reels", "tv", "explore", "stories", "accounts", "direct"}
PROFILE_TABS = {"instagram": {"reels", "tagged", "posts"},
                "youtube": {"featured", "videos", "shorts", "streams", "playlists", "podcasts",
                            "community", "posts", "about"}}


def reference_params(platform: str, reference: str) -> dict[str, str]:
    value = reference.strip()
    if "://" in value or value.lower().startswith(("www.", "instagram.com/", "youtube.com/", "m.youtube.com/")):
        url = urlparse(value if "://" in value else "https://" + value)
        hosts = {"instagram": {"instagram.com", "www.instagram.com"},
                 "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com"}}
        if url.scheme not in {"http", "https"} or url.hostname not in hosts[platform] or url.username or url.port:
            raise ValueError(f"Account reference must belong to {platform}: {reference}")
        parts = [unquote(part) for part in url.path.split("/") if part]
        # A profile tab (`/@name/shorts`, `/name/reels/`) still names that one account.
        if len(parts) == 2 and parts[1].lower() in PROFILE_TABS[platform] and (
                (platform == "instagram" and parts[0].lower() not in INSTAGRAM_ROUTES)
                or parts[0].startswith("@")):
            parts = parts[:1]
        elif (platform == "youtube" and len(parts) == 3 and parts[0] == "channel"
              and parts[2].lower() in PROFILE_TABS[platform]):
            parts = parts[:2]
        if platform == "youtube" and len(parts) == 2 and parts[0] == "channel":
            value = parts[1]
        elif len(parts) == 1 and (platform == "instagram" or parts[0].startswith("@")):
            value = parts[0]
        else:
            raise ValueError(f"Expected a public account URL, not a content or search URL: {reference}")
    value = value.removeprefix("@")
    if platform == "youtube" and CHANNEL_ID.fullmatch(value):
        return {"channel_id": value}
    # Instagram usernames are ASCII; YouTube handles may use letters and digits of any script plus
    # `_`, `-`, `.` and `·`, so a Chinese or Japanese handle is valid input, not a malformed one.
    pattern = r"[A-Za-z0-9._]{1,30}" if platform == "instagram" else r"[\w.\-·]{2,64}"
    if not re.fullmatch(pattern, value):
        raise ValueError(f"Invalid {platform} public account reference: {reference}")
    return {"handle": value.casefold()}


def discovery_mode(request: dict[str, Any]) -> str:
    """The mode the run follows: references written under auto are kept, as hybrid, not dropped."""
    mode = request.get("discovery_mode") or "auto"
    if mode not in {"auto", "provided", "hybrid"}:
        raise ValueError("discovery_mode must be auto, provided or hybrid")
    return "hybrid" if mode == "auto" and request.get("competitor_refs") else mode


def requested_accounts(request: dict[str, Any]) -> list[tuple[str, dict[str, str]]]:
    mode = discovery_mode(request)
    refs = request.get("competitor_refs") or []
    if not isinstance(refs, list) or len(refs) > 20 or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise ValueError("competitor_refs must contain at most twenty non-empty public account references")
    if mode == "auto":
        return []
    if not refs:
        raise ValueError("provided and hybrid require competitor_refs")
    platforms = request.get("platforms") or [request.get("platform")]
    if len(platforms) != 1 or platforms[0] not in {"instagram", "youtube"}:
        raise ValueError("Run each competitor platform separately when competitor_refs are given")
    return [(ref, reference_params(platforms[0], ref)) for ref in refs]


def resolved_channel(params: dict[str, str], envelope: dict[str, Any]) -> str | None:
    if envelope.get("status") not in {"completed", "completed_partial"}:
        return None
    rows = (envelope.get("data") or {}).get("rows") or []
    if len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    channel_id = row.get("id")
    if not isinstance(channel_id, str) or not CHANNEL_ID.fullmatch(channel_id):
        return None
    if "channel_id" in params:
        return channel_id if params["channel_id"] == channel_id else None
    handle = row.get("custom_url")
    if not isinstance(handle, str) or not handle.startswith("@"):
        return None
    return channel_id if handle[1:].casefold() == params["handle"].casefold() else None


def pin_requested_accounts(request: dict[str, Any], identities: list[dict[str, Any]],
                           discovery: dict[str, Any] | None = None) -> dict[str, Any]:
    requested_accounts(request)
    if [row.get("ref") for row in identities] != request["competitor_refs"]:
        raise ValueError("Identity results must cover every requested reference in order")
    mode = discovery_mode(request)
    result = copy.deepcopy(discovery) if discovery else {
        "schema_version": "competitor-discovery-selection.v1", "request_id": request["request_id"],
        "selection_strategy": "provided-v1", "selected_handles": [], "reserve_handles": [], "authors": [],
    }
    pinned = list(dict.fromkeys(row["handle"] for row in identities if row.get("handle")))
    automatic = [handle for handle in result["selected_handles"] + result["reserve_handles"] if handle not in pinned]
    selected = pinned + (automatic[:max(0, 6 - len(pinned))] if mode == "hybrid" else [])
    reserves = [handle for handle in automatic if handle not in selected][:2] if mode == "hybrid" else []
    rows = {row["handle"]: row for row in result["authors"]}
    for handle in pinned:
        rows.setdefault(handle, {
            "handle": handle, "relevance": "provided_candidate", "content_refs": [], "visible_content": [],
            "matched_intents": 0, "source_routes": [], "content_count": 0, "author_summary": {},
            "newest_published_at": None, "max_play_count": None, "best_source_rank": None,
            "discovery_rank": None, "decision": "selected_for_profile", "reason": "Explicitly requested account.",
        })
    for index, handle in enumerate(selected + reserves, 1):
        rows[handle].update(discovery_rank=index, decision="selected_for_profile" if handle in selected else "reserve")
    for handle, row in rows.items():
        if handle not in selected + reserves and row["decision"] in {"selected_for_profile", "reserve"}:
            row.update(discovery_rank=None, decision="not_selected_capacity")
    result.update(discovery_mode=mode, provided_handles=pinned, requested_refs=identities,
                  selected_handles=selected, reserve_handles=reserves, authors=list(rows.values()))
    return result
