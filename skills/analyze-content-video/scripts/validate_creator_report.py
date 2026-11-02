#!/usr/bin/env python3
"""Reject internal machine identifiers and billing metadata in Creator-facing Markdown.

Report structure is validated upstream. Heading wording alone cannot establish
billing or audit semantics; review those with the Skill delivery rules rather
than a localized phrase blacklist.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


FORBIDDEN = {
    "internal artifact name": re.compile(
        r"(?:VIDEO_ANALYSIS_FULL|video_analysis\.json|content_evidence\.json|\.schema\.json)",
        re.IGNORECASE,
    ),
    # `credits?` on its own matched the word anywhere, which is fine in a Chinese report and
    # wrong in an English one: "Always credit the original creator" is the correct instruction
    # for a third-party teardown, and it came back flagged as billing metadata. What is forbidden
    # is what a run cost, so the pattern asks for the billing sense.
    "provider or cost metadata": re.compile(
        r"(?:credits?_charged"
        r"|\bcredits?[ _-](?:charged|used|spent|cost|remaining|balance)\b"
        r"|\b(?:charged|used|spent|cost|costs|remaining)\s+(?:\d+\s+)?credits?\b"
        # The label form. A run's cost reaches a document as a table row or a field label far
        # more often than as a sentence: "Cost: 5 credits", "Charged: 5", "Credits: 5".
        r"|\b(?:cost|costs|charged|used|spent|remaining|balance)\s*:\s*\d*\s*credits?\b"
        r"|\bcredits?\s*:\s*\d"
        r"|token cost|connector_id|provider_id)",
        re.IGNORECASE
    ),
    "runtime status": re.compile(
        r"(?:all (?:five|\d+|the) outputs? (?:are |were )?written|validators?_passed|"
        r"validation (?:passed|status)|full detail, evidence and formulas)",
        re.IGNORECASE,
    ),
    "internal field name": re.compile(
        r"(?:must_not_assume|evidence_completeness|subject_state|causal_limits|media_coverage|"
        r"deterministic\.(?:hook|sustain)_stage|verdict\s*[=:]\s*unassessed)",
        re.IGNORECASE,
    ),
}


def validate(text: str) -> list[str]:
    return [label for label, pattern in FORBIDDEN.items() if pattern.search(text)]


def main() -> int:
    """Validate the report, optionally returning its original UTF-8 bytes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--emit-report", action="store_true",
                        help="Return the report body on stdout only after this final check succeeds.")
    args = parser.parse_args()
    raw = args.report.read_bytes()
    text = raw.decode("utf-8")
    errors = validate(text)
    if errors:
        for error in errors:
            print(f"ERROR: Creator-facing report contains {error}")
        return 1
    if args.emit_report:
        sys.stdout.buffer.write(raw)
        # 实测里短问题（「为什么火了」）的最终回复常被模型概括成几行：报告末尾直接写明怎么交付。
        print(("" if raw.endswith(b"\n") else "\n") + f"creator_report: {args.report.resolve()}")
        print('Your reply is this report, even when the question was short: at most one short sentence of your own, then the report above complete and unchanged (translated only where the Skill says), then this creator_report line.')
    else:
        print("OK: Creator-facing video report contains no forbidden internal identifiers or metadata")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
