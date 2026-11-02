"""Load the bundled monetization rule library.

The library ships split into small files (`references/rules/platform-monetization-rules.v1/`):
`library.json` holds the metrics, benchmarks and niche rates and lists one `program-*.json` file
per programme in its `program_files`. This loader puts them back together into the single
`monetization-rules` object the assessment and the rules schema expect. A path to one complete
library JSON file is still read as is.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_LIBRARY = Path(__file__).resolve().parent.parent / "references" / "rules" / "platform-monetization-rules.v1"


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_rule_library(path: Path = DEFAULT_LIBRARY) -> dict[str, Any]:
    if not path.is_dir():
        return _object(path)
    header = _object(path / "library.json")
    names = header.pop("program_files", None)
    if not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names):
        raise ValueError(f"{path / 'library.json'} must list its programme files in program_files")
    programs = []
    for name in names:
        # 只读本目录里的文件名，不接受路径：库是随包分发的固定内容。
        if Path(name).name != name or not name.startswith("program-") or not name.endswith(".json"):
            raise ValueError(f"program_files entry {name!r} is not a programme file in {path}")
        programs.append(_object(path / name))
    library: dict[str, Any] = {}
    for key, value in header.items():
        library[key] = value
        if key == "metrics":
            library["programs"] = programs
    library.setdefault("programs", programs)
    return library
