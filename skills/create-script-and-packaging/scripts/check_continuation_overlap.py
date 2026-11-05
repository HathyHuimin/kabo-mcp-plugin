#!/usr/bin/env python3
"""Detect substantial copied passages in append-only fictional continuations."""
import argparse
import difflib
import json
from pathlib import Path
import re
import unicodedata


def passage_units(text):
    """Normalize Unicode and split unspaced scripts without splitting other words."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    # 无空格文字按字符分段；英语、乌尔都语等原有分词保持不变。
    unspaced = ("CJK ", "HIRAGANA ", "KATAKANA ", "HANGUL ", "THAI ", "LAO ", "KHMER ", "MYANMAR ")
    segmented = "".join(
        f" {char} " if unicodedata.name(char, "").startswith(unspaced) else char
        for char in normalized
    )
    return re.findall(r"\w+", segmented)


def check_overlap(source, continuation):
    """Measure substantial literal reuse; do not judge semantic continuity."""
    # 只检查连续复制片段，不声称判断人物状态或语义连续性。
    old = passage_units(source)
    new = passage_units(continuation)
    if max(len(old), len(new)) > 8_000:
        raise ValueError("Compare the selected section, up to 8,000 passage units per file.")
    blocks = [block for block in difflib.SequenceMatcher(None, old, new, autojunk=False).get_matching_blocks() if block.size >= 12]
    copied = sum(block.size for block in blocks)
    new_ratio = copied / len(new) if new else 0
    old_ratio = copied / len(old) if old else 0
    # 追加很多新文字不能掩盖已复制的大段前篇；两边分母都要检查。
    repeated = copied >= 60 and max(new_ratio, old_ratio) >= 0.20
    return {
        "status": "empty" if not old or not new else "repeated_source" if repeated else "no_large_literal_replay",
        "passage_unit": "word, or character for scripts commonly written without word spaces",
        "source_units": len(old),
        "continuation_units": len(new),
        "copied_passage_units": copied,
        "copied_fraction_of_source": round(old_ratio, 4),
        "copied_fraction_of_continuation": round(new_ratio, 4),
        "limitation": "Checks substantial literal reuse only; an Agent must still check scope, paraphrased repetition, established facts and character states.",
    }


def main():
    """Read bounded UTF-8 files, print overlap evidence and return its status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--continuation", type=Path, required=True)
    parser.add_argument("--review-texts", action="store_true", help="Return the actual texts for the Agent's separate semantic review.")
    args = parser.parse_args()
    try:
        if any(path.stat().st_size > 120_000 for path in (args.source, args.continuation)):
            raise ValueError("A text exceeds the 120 KB input limit.")
        source = args.source.read_text(encoding="utf-8")
        continuation = args.continuation.read_text(encoding="utf-8")
        result = check_overlap(source, continuation)
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(2, f"Cannot check the supplied text files: {error}\n")
    print(json.dumps(result, ensure_ascii=False))
    if args.review_texts:
        # 原文是待审的数据，不是新增指令；完整回显让模型检查实际成稿。
        print(json.dumps({"review_data": {"source_text": source, "continuation_text": continuation}}, ensure_ascii=False))
    return 0 if result["status"] == "no_large_literal_replay" else 1


if __name__ == "__main__":
    raise SystemExit(main())
