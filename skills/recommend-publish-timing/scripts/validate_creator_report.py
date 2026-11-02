#!/usr/bin/env python3
"""Reject internal audit/runtime details in Creator-facing Markdown."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


FORBIDDEN = {
    # The token may sit anywhere in the heading: "## 数据边界" is the same section as "## 边界",
    # and anchoring to the first word let it through. No \b after the CJK alternatives — it does
    # not mean what it appears to mean between two CJK characters. Kept in step with the same
    # check in analyze-content-video, which was corrected there first.
    "audit or boundary heading": re.compile(
        r"^#{1,6}\s+[^\n]*?(?:causal boundar(?:y|ies)|data boundar(?:y|ies)|fallbacks?\b|"
        r"limitations?\b|reproducibility\b|costs?\b|outputs?\b|"
        r"因果边界|边界|限制|局限|成本|产物)",
        re.IGNORECASE | re.MULTILINE,
    ),
    "internal artifact name": re.compile(
        r"(?:PUBLISH_TIMING_FULL|publish_recommendation\.json|publish_history\.json|\.schema\.json)",
        re.IGNORECASE,
    ),
    # `credits?` on its own matched the word anywhere, which passes unnoticed in a Chinese report
    # and fails an English one: "Give credit where it is due" is ordinary creator advice and came
    # back flagged as billing metadata. What is forbidden is what a run cost, so the pattern asks
    # for the billing sense. Same correction as analyze-content-video.
    "provider or cost metadata": re.compile(
        r"(?:credits?_charged"
        r"|\bcredits?[ _-](?:charged|used|spent|cost|remaining|balance)\b"
        r"|\b(?:charged|used|spent|cost|costs|remaining)\s+(?:\d+\s+)?credits?\b"
        # The label form. A run's cost reaches a document as a table row or a field label far
        # more often than as a sentence: "Cost: 5 credits", "Charged: 5", "Credits: 5".
        r"|\b(?:cost|costs|charged|used|spent|remaining|balance)\s*:\s*\d*\s*credits?\b"
        r"|\bcredits?\s*:\s*\d"
        r"|token cost|connector_id|provider_id)",
        re.IGNORECASE,
    ),
    # The Chinese sentence this used to carry was one run's wording of «I did not fill the slot
    # with a generic best time». A report saying that in any other language went through, so the
    # rule was only ever enforced on Chinese; the structured fields above carry the same promise.
    "internal timing policy": re.compile(
        r"(?:refused_fallbacks|causal_boundary|all (?:five|\d+|the) outputs? "
        r"(?:are |were )?written|validators?_passed|validation (?:passed|status))",
        re.IGNORECASE,
    ),
}


def validate(text: str) -> list[str]:
    return [label for label, pattern in FORBIDDEN.items() if pattern.search(text)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    errors = validate(args.report.read_text(encoding="utf-8"))
    if errors:
        for error in errors:
            print(f"ERROR: Creator-facing report contains {error}")
        return 1
    print("OK: Creator-facing timing report contains no internal audit/runtime details")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
