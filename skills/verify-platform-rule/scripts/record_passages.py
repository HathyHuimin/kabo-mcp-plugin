#!/usr/bin/env python3
"""Record the passages a rule check rests on, copied from the page this run persisted.

The snapshot file used to be written by the model: the URL, the fetched URL, the retrieval date and
the passages, all retyped from a fenced rendering of the page. Two things went wrong with that. A
passage that was paraphrased, re-spaced or had its line breaks joined still looked like a quote and
was no longer a substring of the page it cites. And on Kabo Desktop the model reached for
apply_patch to write the file, which the kernel turns into a file-edit approval that stops an
unattended run until someone clicks it.

Here the model supplies only which passages matter. Everything else is copied from the persisted
render_page envelope, and every passage must appear in that page's text character for character.

  python3 scripts/record_passages.py --envelope <run>/snapshot/<source_id>.envelope.json \\
    --source-id <source_id> --output <run>/snapshot/<source_id>.json --quotes-from - <<'EOF'
  ["first passage, verbatim", "second passage,\\nline breaks kept"]
  EOF

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

CONNECTOR = "official-page-render"
OPERATION = "render_page"
USABLE = {"completed", "completed_partial"}
SPACE = re.compile(r"\s+")
# render_page returns the painted page as Markdown. A reader sees link text, not its target; words,
# not the ** around them; a list item, not its bullet. Those marks are set aside when comparing, and
# nothing else is.
LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
EMPHASIS = re.compile(r"(\*\*|__|\*|`)(?=\S)(.+?)(?<=\S)\1")
LIST_MARK = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+", re.M)
HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.M)
FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                      "–": "-", "—": "-", " ": " "})


def plain_text(markdown: str) -> str:
    """The page as a reader sees it: Markdown links, emphasis, bullets and heading marks set aside."""
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    text = LINK.sub(r"\1", text)
    for _ in range(2):  # nested emphasis, e.g. ***bold italic***
        text = EMPHASIS.sub(r"\2", text)
    text = HEADING.sub("", LIST_MARK.sub("", text))
    return "\n".join(line.rstrip() for line in text.split("\n"))


def quoted(passage: str, page: str) -> bool:
    """Whether the passage is the page's own characters, as written or as a reader sees them."""
    return passage in page or passage.replace("\r\n", "\n") in plain_text(page)


def folded(value: str) -> str:
    return SPACE.sub(" ", unicodedata.normalize("NFKC", value).translate(FOLD)).strip()


def load_page(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict) or envelope.get("connector_id") != CONNECTOR or envelope.get("operation") != OPERATION:
        raise ValueError(f"{path} is not a persisted {CONNECTOR}/{OPERATION} envelope")
    if envelope.get("status") not in USABLE:
        raise ValueError(f"{path} records status {envelope.get('status')!r}: an unread page cannot be quoted; "
                         "record the source as unreachable instead")
    data = envelope.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("text"), str) or not data["text"].strip():
        raise ValueError(f"{path} carries no page text to quote from")
    return envelope, data


def check_passages(passages: list[str], text: str) -> list[str]:
    errors = []
    # For the hint only: the page folded both as written and as read, so a passage that joined the
    # page's bullet lines, or straightened its curly quotes, is told what differs rather than
    # "these words do not appear".
    flat = (folded(plain_text(text)), folded(text))
    for index, passage in enumerate(passages):
        if not isinstance(passage, str) or not passage.strip():
            errors.append(f"passage {index} is empty")
        elif not quoted(passage, text):
            hint = ("its punctuation, spacing or line breaks differ from the page (curly quotes and dashes "
                    "included): copy them as the page shows them" if any(folded(passage) in f for f in flat)
                    else "these words do not appear in the page")
            errors.append(f"passage {index} is not verbatim ({hint}): {passage[:80]!r}")
    return errors


def record(envelope_path: Path, source_id: str, passages: list[str]) -> dict[str, Any]:
    if not passages:
        raise ValueError("give at least one passage: a source with nothing quoted is not evidence")
    envelope, data = load_page(envelope_path)
    errors = check_passages(passages, data["text"])
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "source_id": source_id,
        "url": data.get("url"),
        "fetched_url": data.get("fetched_url") or data.get("url"),
        "retrieved_at": data.get("retrieved_at") or envelope.get("retrieved_at"),
        "retrieval_method": data.get("retrieval_method"),
        "title": data.get("title"),
        "quote_source": "raw_document",
        "request_id": envelope.get("request_id"),
        "passages": passages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--envelope", type=Path, required=True, help="the persisted render_page envelope")
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quote", action="append", default=[], help="one passage, verbatim (repeatable)")
    parser.add_argument("--quotes-from", help="a JSON array of passages, from a file or '-' for stdin")
    args = parser.parse_args()
    try:
        passages = list(args.quote)
        if args.quotes_from:
            raw = sys.stdin.read() if args.quotes_from == "-" else Path(args.quotes_from).read_text(encoding="utf-8")
            # strict=False: a passage keeps the page's line breaks, and a heredoc carries them literally.
            loaded = json.loads(raw, strict=False)
            if not isinstance(loaded, list):
                raise ValueError("--quotes-from must hold a JSON array of strings")
            passages.extend(loaded)
        snapshot = record(args.envelope, args.source_id, passages)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", "output": str(args.output), "passages": len(snapshot["passages"]),
                      "retrieved_at": snapshot["retrieved_at"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
