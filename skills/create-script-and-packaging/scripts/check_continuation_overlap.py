#!/usr/bin/env python3
"""Detect substantial copied passages in append-only fictional continuations."""
import argparse
import difflib
import json
from pathlib import Path
import re


def check_overlap(source, continuation):
    # 只检查连续复制片段，不声称判断人物状态或语义连续性。
    old = re.findall(r"\w+", source.casefold())
    new = re.findall(r"\w+", continuation.casefold())
    if max(len(old), len(new)) > 8_000:
        raise ValueError("Compare the selected section, up to 8,000 words per file.")
    blocks = [block for block in difflib.SequenceMatcher(None, old, new, autojunk=False).get_matching_blocks() if block.size >= 12]
    copied = sum(block.size for block in blocks)
    new_ratio = copied / len(new) if new else 0
    old_ratio = copied / len(old) if old else 0
    # 追加很多新文字不能掩盖已复制的大段前篇；两边分母都要检查。
    repeated = copied >= 60 and max(new_ratio, old_ratio) >= 0.20
    return {
        "status": "empty" if not old or not new else "repeated_source" if repeated else "no_large_literal_replay",
        "source_words": len(old),
        "continuation_words": len(new),
        "copied_passage_words": copied,
        "copied_fraction_of_source": round(old_ratio, 4),
        "copied_fraction_of_continuation": round(new_ratio, 4),
        "limitation": "Checks substantial literal reuse only; an Agent must still check scope, paraphrased repetition, established facts and character states.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--continuation", type=Path, required=True)
    args = parser.parse_args()
    try:
        if any(path.stat().st_size > 120_000 for path in (args.source, args.continuation)):
            raise ValueError("A text exceeds the 120 KB input limit.")
        result = check_overlap(args.source.read_text(encoding="utf-8"), args.continuation.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(2, f"Cannot check the supplied text files: {error}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "no_large_literal_replay" else 1


if __name__ == "__main__":
    raise SystemExit(main())
