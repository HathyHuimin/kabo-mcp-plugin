"""Read public profile facts already fetched for this run.

Bio and follower counts often sit on the resolve_profile envelope and then get
dropped before synthesis. This module only copies fields that are already present.
It does not invent a positioning, and it does not call a model.
"""

from __future__ import annotations

from typing import Any


IDENTITY_SCOPE_KEYS = ("display_name", "biography", "followers")


def dig(value: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def as_text(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def first_text(row: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> str | None:
    for path in paths:
        text = as_text(dig(row, path))
        if text is not None:
            return text
    return None


def first_number(row: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> float | None:
    for path in paths:
        number = as_number(dig(row, path))
        if number is not None:
            return number
    return None


def identity_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    identity: dict[str, Any] = {}
    display_name = first_text(profile, (
        ("full_name",), ("title",), ("name",), ("channelTitle",), ("snippet", "title"),
    ))
    biography = first_text(profile, (
        ("biography",), ("bio",), ("description",), ("snippet", "description"),
    ))
    followers = first_number(profile, (
        ("follower_count",), ("followers_count",), ("followers",), ("followerCount",),
        ("edge_followed_by", "count"), ("statistics", "subscriberCount"), ("subscriberCount",),
        ("metrics", "subscribers"),
    ))
    if display_name:
        identity["display_name"] = display_name
    if biography:
        identity["biography"] = biography
    if followers is not None and followers >= 0:
        identity["followers"] = followers
    return identity


def identity_from_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    data = envelope.get("data")
    if not isinstance(data, dict):
        return {}
    profile = data.get("profile")
    if isinstance(profile, dict):
        return identity_from_profile(profile)
    operation = str(envelope.get("operation") or "")
    if operation in {"resolve_profile", "resolve_channel", "check_handle"}:
        return identity_from_profile(data)
    if operation == "get_channel":
        # The YouTube channel arrives as `data.rows[0]` (description, metrics.subscribers); read it
        # as the profile so the channel's own description and subscribers reach the identity.
        rows = data.get("rows")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return identity_from_profile(rows[0])
    return {}


def identity_from_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(scope, dict):
        return {}
    identity: dict[str, Any] = {}
    for key in IDENTITY_SCOPE_KEYS:
        value = scope.get(key)
        if key == "followers":
            number = as_number(value)
            if number is not None and number >= 0:
                identity[key] = number
            continue
        text = as_text(value)
        if text:
            identity[key] = text
    return identity


def merge_identity(*parts: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for part in parts:
        for key, value in identity_from_scope(part).items():
            if key not in merged:
                merged[key] = value
    return merged


def quote_bio(biography: str, limit: int = 80) -> str:
    line = " ".join((biography.splitlines() or [""])[0].split())
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + "…"


def apply_identity(scope: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    updated = dict(scope)
    updated.update(identity)
    return updated


if __name__ == "__main__":
    raise SystemExit(0)
