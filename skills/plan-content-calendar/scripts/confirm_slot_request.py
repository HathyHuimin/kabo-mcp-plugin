#!/usr/bin/env python3
"""Build the confirmation-stage Calendar Request from the pending Request and its proposal.

When the Creator confirms a pending slot choice, the confirmation Request must equal the pending
Request in every field and array order except the new request_id, the host's created_at and the
confirmation fields, and it must pin both reviewed artifacts by id and SHA-256. The model used to
reproduce the whole pending Request by hand and type both hashes; one changed field or reordered
array fails the validator, and a retyped hash pins nothing.

  python3 scripts/confirm_slot_request.py --pending-request pending-request.json \\
    --proposal pending-calendar.json --request-id <new id> --created-at <host time> \\
    --confirmed-at <host time> --item-id <item the Creator confirmed> --output confirm-request.json

The confirmed item must be the one the proposal selected: confirming a different item is a new
proposal, not a confirmation. Standard library only.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from calendar_common import file_sha256, load_json, safe_artifact_path  # noqa: E402

REQUEST_SCHEMA = "content-calendar-request.v1"
CALENDAR_SCHEMA = "content-calendar.v1"


def ref(path: Path, output: Path, run_root: Path, artifact_id: Any, schema_version: str) -> dict[str, Any]:
    relative = os.path.relpath(path.resolve(), output.parent.resolve())
    safe_artifact_path(output, run_root, relative)
    return {"path": relative, "artifact_id": artifact_id, "schema_version": schema_version, "sha256": file_sha256(path)}


def confirm(pending: dict[str, Any], proposal: dict[str, Any], *, request_id: str, created_at: str,
            confirmed_at: str, item_id: str | None) -> dict[str, Any]:
    single = pending.get("single_slot_decision")
    if pending.get("schema_version") != REQUEST_SCHEMA or pending.get("intent") != "choose_for_slot" \
            or not isinstance(single, dict) or single.get("confirmation_status") != "pending":
        raise ValueError("--pending-request must be a pending choose_for_slot Request")
    decision = proposal.get("slot_decision") or {}
    if proposal.get("schema_version") != CALENDAR_SCHEMA or proposal.get("projection_mode") != "focus_slot_patch" \
            or decision.get("confirmation_status") != "pending":
        raise ValueError("--proposal must be the pending focus-slot Calendar built from that Request")
    if proposal.get("request_id") != pending.get("request_id"):
        raise ValueError(f"the proposal answers Request {proposal.get('request_id')!r}, not {pending.get('request_id')!r}")
    selected = decision.get("selected_item_id")
    if not selected:
        raise ValueError("the proposal selected no item, so there is nothing to confirm: ask the Creator to choose")
    if item_id is not None and item_id != selected:
        raise ValueError(f"the Creator confirmed {item_id!r} but the proposal selected {selected!r}: record that as "
                         "creator_preferred_item_id in a new pending Request instead")
    if request_id == pending.get("request_id"):
        raise ValueError("the confirmation needs a new request_id")
    confirmation = copy.deepcopy(pending)
    confirmation["request_id"] = request_id
    confirmation["created_at"] = created_at
    confirmation["single_slot_decision"].update(
        confirmation_status="confirmed", confirmed_item_id=selected, confirmed_at=confirmed_at)
    return confirmation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pending-request", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, required=True, help="the validated pending content-calendar.v1")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--created-at", required=True, help="host-supplied confirmation time")
    parser.add_argument("--confirmed-at", required=True, help="host-supplied time of the Creator's confirmation")
    parser.add_argument("--item-id", help="the item the Creator confirmed; must be the proposed one")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, default=None)
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        run_root = (args.run_root or output.parent).resolve()
        if output in (args.pending_request.resolve(), args.proposal.resolve()):
            raise ValueError("--output would overwrite a reviewed artifact; both stay immutable")
        pending, proposal = load_json(args.pending_request), load_json(args.proposal)
        confirmation = confirm(pending, proposal, request_id=args.request_id, created_at=args.created_at,
                               confirmed_at=args.confirmed_at, item_id=args.item_id)
        confirmation["single_slot_decision"]["pending_request_ref"] = ref(
            args.pending_request, output, run_root, pending.get("request_id"), REQUEST_SCHEMA)
        confirmation["single_slot_decision"]["proposal_ref"] = ref(
            args.proposal, output, run_root, proposal.get("calendar_id"), CALENDAR_SCHEMA)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(confirmation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", "output": str(args.output),
                      "confirmed_item_id": confirmation["single_slot_decision"]["confirmed_item_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
