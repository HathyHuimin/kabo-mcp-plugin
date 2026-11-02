#!/usr/bin/env python3
"""Catalog-only memo: Leo-style labels without watching.

Uses account_analysis.json + catalog_stability.json. Does not fetch media.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
from run_paths import missing_input, resolve_input  # noqa: E402
from build_catalog_stability import handle_from_url
from media_payload import load_json
from select_watch_set import account_handle


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def compose_catalog_plus(analysis: dict[str, Any], stability: dict[str, Any]) -> str:
    account = account_handle(analysis)
    platform = str(analysis.get("platform") or "")
    coverage = analysis.get("coverage") if isinstance(analysis.get("coverage"), dict) else {}
    total = coverage.get("content_total") or coverage.get("n") or len(analysis.get("rankings") or [])
    lines = [
        f"# Catalog review (without watching) · @{account or 'unknown'} · {platform}",
        "",
        "Provisional categories use titles, duration ranges and links to other profiles. No videos have been watched.",
        "",
        "## Facts available without watching",
        "",
    ]
    for reading in stability.get("readings") or []:
        if str(reading).strip():
            lines.append(f"- {reading}")
    lines.extend(["", f"Approximate sample size: {total}. Public YouTube / Instagram samples contain at most 100 posts.", ""])
    lines.extend(["## Top and bottom performers, provisionally grouped by title", ""])
    ranked: list[dict[str, Any]] = []
    for raw in analysis.get("rankings") or []:
        if not isinstance(raw, dict):
            continue
        views = _as_float((raw.get("metrics") or {}).get("views") if isinstance(raw.get("metrics"), dict) else None)
        if views is None:
            continue
        ranked.append(raw)
    ranked.sort(
        key=lambda row: (
            -(_as_float(row.get("view_multiple_vs_format_median")) or 0.0),
            -(_as_float((row.get("metrics") or {}).get("views")) or 0.0),
        )
    )
    for row in ranked[:8]:
        url = str(row.get("url") or "")
        handle = handle_from_url(url)
        flag = ""
        if handle and account and handle != account:
            flag = " · another account's profile"
        title = str(row.get("title") or row.get("content_id") or "")[:80]
        multiple = row.get("view_multiple_vs_format_median")
        views = (row.get("metrics") or {}).get("views") if isinstance(row.get("metrics"), dict) else None
        lines.append(
            f"- `{row.get('content_id')}` {title} · {row.get('format')} · "
            f"{views} {'view' if views == 1 else 'views'} · {multiple}×{flag}"
        )
    lines.extend(["", "## Category-level suggestions (no timestamped editing advice)", ""])
    rise = [
        row
        for row in (stability.get("rise_fade") or [])
        if isinstance(row, dict) and row.get("status") == "rise"
    ]
    fade = [
        row
        for row in (stability.get("rise_fade") or [])
        if isinstance(row, dict) and row.get("status") == "fade"
    ]
    if rise:
        names = ", ".join(str(row.get("format")) for row in rise)
        lines.append(f"- Do more: formats gaining view share ({names}). These categories come from catalog metadata, not from watching the videos.")
    else:
        lines.append("- Do more: the title category with the highest performance multiple in the catalog. Keep the provisional category until the videos have been watched.")
    if fade:
        names = ", ".join(str(row.get("format")) for row in fade)
        lines.append(f"- Stop: formats losing view share ({names}).")
    else:
        lines.append("- Stop: the title category with the lowest performance multiple in the catalog. This suggestion may be wrong because the videos have not been watched.")
    other_n = stability.get("other_handle_n")
    if other_n:
        lines.append(f"- Reconsider: views for {other_n} {'post are' if other_n == 1 else 'posts are'} counted on another profile. The catalog can only flag that the content may not be self-produced; it cannot establish an interview or customer appearance.")
    lines.extend(["", "## What the catalog cannot establish", "", "- The exact opening words, the first frame, or when the promised payoff appears.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--stability", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        sources = resolve_input(args.analysis), resolve_input(args.stability)
    except FileNotFoundError as missing:
        return missing_input(missing)
    text = compose_catalog_plus(load_json(sources[0]), load_json(sources[1]))
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
