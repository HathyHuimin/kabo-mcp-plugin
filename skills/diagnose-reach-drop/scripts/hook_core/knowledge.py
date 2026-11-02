"""The fifteen short-video opening patterns, read from references/hook-core/knowledge.json."""
from __future__ import annotations

from pathlib import Path

from . import CORE_VERSION
from .common import HookError, read_json

ROOT = Path(__file__).resolve().parents[2]


def load_knowledge() -> dict:
    knowledge = read_json(ROOT / "references/hook-core/knowledge.json")
    if knowledge["core_version"] != CORE_VERSION:
        raise HookError("version_mismatch", "knowledge and executable core versions differ")
    return knowledge


def select_patterns(materials: list[str], *, route: str | None = None) -> dict:
    if route not in (None, "A", "B"):
        raise HookError("invalid_request", "route must be A or B")
    if not isinstance(materials, list) or any(not isinstance(x, str) for x in materials):
        raise HookError("invalid_request", "materials must be named material capabilities")
    candidates = [p for p in load_knowledge()["patterns"]
                  if set(p["material_requirements"]) <= set(materials)
                  and (route is None or p["route"] in (route, "overlay"))]
    return {"patterns": candidates, "basis": "declared_material_capabilities",
            "selection_status": "creator_or_business_skill_decides",
            "limitations": ["Material availability must be confirmed; a reference link is not reuse permission."]}


