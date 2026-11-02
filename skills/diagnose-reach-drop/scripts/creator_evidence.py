"""Reuse calculated evidence as tableless Markdown; fixed labels remain English."""
import copy
import re
import sys

sys.dont_write_bytecode = True
from analyze_traffic import require
from render_traffic import render


def evidence(finding, _zh=False, full=True):
    # These are model-authored display labels, not data selectors. Escape pipes
    # and flatten newlines before rendering the same facts through the one renderer.
    value = copy.deepcopy(finding)
    value["ask"] = "Evidence"
    for fact in value["facts"]:
        for group in fact.get("groups", []):
            group["label"] = group["label"].replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    body = render({"questions": [value]}).split("\n\n", 3)[3].rstrip()
    lines, headers = [], None
    for line in body.splitlines():
        if not line.startswith("|"):
            lines.append(line)
            if not line:
                headers = None
            continue
        cells = [cell.strip().replace("\\|", "|").replace("\\\\", "\\")
                 for cell in re.split(r"(?<!\\)\|", line)[1:-1]]
        if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        if headers is None:
            headers = cells
            continue
        require(len(cells) == len(headers), "rendered evidence table has inconsistent columns")
        pairs = [(key, cell) for key, cell in zip(headers[1:], cells[1:])
                 if full or key != "Peak"]
        lines.append("- " + cells[0] + ": " + "; ".join(key + " " + cell for key, cell in pairs))
    return "\n".join(lines)
