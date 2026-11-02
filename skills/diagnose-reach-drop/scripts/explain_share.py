#!/usr/bin/env python3
"""Explain a source-share change without inventing absolute traffic."""
import argparse
from pathlib import Path
import math
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from correction import Parser, language  # noqa: E402


def share(value):
    raw = value.strip()
    percent_sign = raw.endswith("%")
    try:
        number = float(raw.rstrip("%").strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"pass a fraction such as 0.62 or a percentage such as 62%, not {value!r}")
    number = number / 100 if percent_sign else number
    if not math.isfinite(number) or not 0 <= number <= 1:
        hint = f"; {raw} as a percentage is {raw}% → {number / 100:g}" if 1 < number <= 100 else ""
        raise argparse.ArgumentTypeError(f"share must be a fraction from 0 to 1 or a percentage up to 100%{hint}")
    return number


def percent(value):
    return f"{value * 100:g}%"


def explain(before, after, language):
    points = (after - before) * 100
    ratio = after / before if before else None
    if language == "zh-CN":
        ratio_text = (f"占比之比是 {ratio:.2f}×；只有两个时期总播放相同，来源绝对播放量才也会是 {ratio:.2f}×。"
                      if ratio is not None else "此前占比为零，因此占比之比未定义。")
        return (f"不是。For You 占比是 For You 播放量 ÷ 总播放量。\n\n"
                f"- 占比从 {percent(before)} 变为 {percent(after)}，变化 {points:+g} 个百分点。\n"
                f"- {ratio_text}\n"
                "- 仅凭占比不能判断总播放或 For You 绝对播放量变化；还需要两个时期各自的总播放量。")
    ratio_text = (f"The share ratio is {ratio:.2f}×. The absolute source count has the same ratio only if total views are equal in both periods."
                  if ratio is not None else "The earlier share is zero, so the share ratio is undefined.")
    return (f"No. For You share is For You views divided by total views.\n\n"
            f"- The share changed from {percent(before)} to {percent(after)}, a {points:+g}-percentage-point change.\n"
            f"- {ratio_text}\n"
            "- Share alone cannot establish the change in total views or absolute For You views; both periods' total views are needed.")


def main():
    parser = Parser(description=__doc__)
    parser.add_argument("--before", required=True, type=share)
    parser.add_argument("--after", required=True, type=share)
    parser.add_argument("--language", type=language, metavar="{en,zh-CN}", default="zh-CN")
    parser.add_argument("--output", help="Also write the explanation to this file (a wrapper's final --report-file)")
    args = parser.parse_args()
    result = explain(args.before, args.after, args.language)
    if args.output:
        Path(args.output).write_text(result + "\n", encoding="utf-8")
    print(result)


if __name__ == "__main__":
    main()
