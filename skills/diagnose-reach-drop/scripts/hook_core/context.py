"""Prepare the method-only opening context this Skill consumes; no media is read or measured."""
from __future__ import annotations

import json

from .common import HookError, digest, envelope, implementation_digest
from .knowledge import ROOT, load_knowledge, select_patterns


def provenance() -> dict:
    return {"implementation_sha256": implementation_digest(),
            "knowledge_sha256": digest(ROOT / "references/hook-core/knowledge.json")}


def prepare_context(request: dict) -> dict:
    """Method choices only: this Skill ships no media diagnosis, so observed-media fields are refused."""
    media_fields = {"expressions", "observations", "pattern_ids", "reference_query"}
    if (request.get("evidence_file") is not None or "evidence_sha256" in request or "evidence_dir" in request
            or media_fields & request.keys()):
        raise HookError("invalid_request", "observed-media fields are not supported by this Skill's opening context")
    patterns = select_patterns(request.get("materials", []), route=request.get("route"))
    material_basis = "declared" if "materials" in request else "unconfirmed"
    if material_basis == "unconfirmed":
        # Method inventory is conditional: never declare reference assets available
        # to a creator merely because the current business brief omits materials.
        patterns["patterns"] = [p for p in load_knowledge()["patterns"]
                                if request.get("route") is None or p["route"] in (request["route"], "overlay")]
    # Historical sample links are not shipped here: this Skill never shows them to the creator.
    examples: list[dict] = []
    result = envelope("HookContext", status="method_reference", basis="method_only", provenance=provenance(),
                      source=None, pattern_candidates=patterns["patterns"],
                      reference_examples=examples,
                      material_basis=material_basis,
                      selection_status=patterns["selection_status"], measurements=None,
                      recognition=None, diagnosis=None,
                      use_scope="Shared input to existing business synthesis; each owner chooses its report and workflow.",
                      execution_scope="No retrieval, decoding, OCR, ASR, repair or model invocation in this action.")
    if len((json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()) > 8 * 1024 * 1024:
        raise HookError("context_budget_exceeded", "prepared consumption JSON exceeds 8 MiB; no successful artifact produced")
    return result
