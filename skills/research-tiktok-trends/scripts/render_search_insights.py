#!/usr/bin/env python3
"""Render Creator Search Insights without changing list order or measurements."""
import argparse
import json
import math
from pathlib import Path


def cell(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\n", " ").replace("\r", " ")


def format_ratio(value):
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("Search insight rise_ratio is invalid")
    if value == int(value):
        return f"{int(value)}×"
    text = f"{value:.1f}"
    # 73/70 printed as "1.0×" beside a "rising" label; two decimals keep such a rise visible.
    return f"{value:.2f}×" if text in ("1.0", "0.0") else f"{text}×"


def expected_rise(seq):
    if len(seq) < 2 or seq[0] <= 0:
        return None
    return seq[-1] / seq[0]


def window_text(seq):
    if not seq:
        return None
    if len(seq) == 1:
        return str(seq[0])
    return f"{seq[0]}→{seq[-1]}"


def render(envelope, language=None, keep_category_substr=None, selection=None):
    """`language` is accepted from older callers and does not change the output."""
    if envelope.get("status") != "completed":
        raise ValueError("Search insight retrieval did not complete")
    data = envelope.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("Search insight data is missing")
    if not data.get("fetched_at") or not data.get("tab"):
        raise ValueError("Search insight measurement basis is missing")
    lines = [
        "Rising TikTok search terms",
        "",
        "Tab: " + cell(data["tab"]),
        "Fetched at: " + cell(data["fetched_at"]),
    ]
    if data.get("language_filters"):
        lines.append("Language: " + cell(data["language_filters"]))
    if data.get("category_filters"):
        lines.append("Category: " + cell(data["category_filters"]))
    if keep_category_substr:
        lines.append("Keep category_path containing: " + cell(str(keep_category_substr).strip()))
    lines += [
        "",
        "Rise is last/first in the returned window; missing or ≤1 is not rising. Popularity is not search volume.",
        "",
    ]
    items = data["items"]
    follow_ids = set()
    if selection is None:
        raise ValueError("Search insight selection is required")
    if selection is not None:
        if not isinstance(selection, dict):
            raise ValueError("Search insight selection must be an object")
        known = {row.get("query_id") for row in items if isinstance(row, dict)}
        for field in ("keep_query_ids", "recommend_query_ids"):
            ids = selection.get(field)
            if (not isinstance(ids, list) or any(not isinstance(v, str) or not v for v in ids)
                    or len(ids) != len(set(ids)) or not set(ids) <= known):
                raise ValueError(f"Search insight {field} must contain unique retrieved IDs")
        kept = set(selection["keep_query_ids"])
        follow_ids = set(selection["recommend_query_ids"])
        if not follow_ids <= kept:
            raise ValueError("Recommended search terms must belong to the kept niche list")
        items = [row for row in items if isinstance(row, dict) and row.get("query_id") in kept]
    if keep_category_substr:
        needle = str(keep_category_substr).strip()
        if not needle:
            raise ValueError("keep_category_substr is empty")
        items = [
            row
            for row in items
            if isinstance(row, dict)
            and needle.lower() in str(row.get("category_path") or "").lower()
        ]
    follow = []
    for index, row in enumerate(items, start=1):
        if not isinstance(row, dict) or not all(k in row for k in ("query_text", "query_id", "trending_seq", "rise_ratio")):
            raise ValueError("Search insight row is incomplete")
        if not isinstance(row["query_text"], str) or not row["query_text"].strip():
            raise ValueError("Search insight query_text is invalid")
        seq = row["trending_seq"]
        if not isinstance(seq, list) or any(type(point) is not int or point < 0 for point in seq):
            raise ValueError("Search insight trending_seq is invalid")
        rise = row["rise_ratio"]
        if rise is not None and (isinstance(rise, bool) or not isinstance(rise, (int, float))):
            raise ValueError("Search insight rise_ratio is invalid")
        expected = expected_rise(seq)
        if expected is None:
            if rise is not None:
                raise ValueError("Search insight rise_ratio must be null when the window cannot rise")
        elif rise is None or not math.isclose(rise, expected, rel_tol=0, abs_tol=1e-9):
            raise ValueError("Search insight rise_ratio does not match the window")
        labels = row.get("labels") or []
        if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
            raise ValueError("Search insight labels are invalid")
        window = window_text(seq)
        ratio = format_ratio(rise)
        line = f'{index}. {cell(row["query_text"])}'
        if window:
            line += f' · window {window}'
        if ratio is not None:
            line += f' · rise {ratio}'
        elif not window:
            line += " · no window"
        if row.get("popularity") is not None:
            line += f' · popularity {row["popularity"]}'
        if row.get("category_path"):
            line += f' · {cell(row["category_path"])}'
        if labels:
            line += f' · labels {", ".join(cell(label) for label in labels)}'
        if rise is not None and rise <= 1 and "content_gap" not in labels:
            line += " · related/stable, not rising"
        lines.append(line)
        if row["query_id"] in follow_ids and ((rise is not None and rise > 1) or "content_gap" in labels):
            follow.append((row["query_text"], rise, labels))
    if not items:
        if (keep_category_substr or selection is not None) and data["items"]:
            lines.append("No search terms matched the requested niche path.")
        else:
            lines.append("The list returned no search terms.")
    lines += ["", "Consider following now:", ""]
    if not follow:
        lines.append("No terms passed niche relevance, reusability and rise/gap checks together.")
    else:
        for text, rise, labels in follow:
            extra = " · rising" if rise is not None and rise > 1 else " · content gap, not rising"
            if "content_gap" in labels and rise is not None and rise > 1:
                extra = " · rising and content gap"
            lines.append(f'- {cell(text)}{extra}')
        lines.append("Follow only reusable mechanisms; skip one-off names, places and news.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("envelope", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--language", help="accepted from older callers; fixed labels are English")
    parser.add_argument("--keep-category-substr")
    parser.add_argument("--selection", type=Path, required=True, help="JSON containing keep_query_ids and recommend_query_ids")
    args = parser.parse_args()
    text = render(
        json.loads(args.envelope.read_text(encoding="utf-8")),
        keep_category_substr=args.keep_category_substr,
        selection=json.loads(args.selection.read_text(encoding="utf-8")) if args.selection else None,
    )
    args.output.write_text(text, encoding="utf-8")
