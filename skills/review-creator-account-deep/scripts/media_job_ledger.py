"""Bind ordered initial batch receipts to the unchanged media plan, without copying job IDs.

This reads only files in one reserved run. It never calls a connector, reads host histories,
changes a receipt, or infers ownership from terminal-envelope arrival order.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid
import sys

sys.dont_write_bytecode = True

from media_payload import MEDIA_CONNECTOR, OPERATIONS, canonical_operation, load_envelope_files, operations_equivalent

SCHEMA = "media-job-ledger.v1"
BUSINESS_CONTEXT = "media-job-business-context.v1"
CONTEXT_FILE = "media-job-context.json"


def read(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def inside(path: Path, root: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("media inputs and receipts must be inside this reserved run")
    return path


def context(plan_path: Path, ledger_path: Path) -> tuple[dict, dict, dict]:
    root = ledger_path.parent.resolve()
    inside(ledger_path, root)
    plan_path = inside(plan_path, root)
    plan, digest = read(plan_path)
    validate_plan(plan)
    manifest_path = root / "run-manifest.json"
    if manifest_path.exists():
        manifest, _ = read(manifest_path)
        if manifest.get("run_id") != root.name or manifest.get("skill_id") != "review-creator-account-deep":
            raise ValueError("run manifest does not name this real run directory")
        if manifest.get("status") != "running":
            raise ValueError("cannot use media job ledger in a closed run")
        signature = {"run_directory": str(root), "run_id": manifest["run_id"],
                     "plan_path": str(plan_path.relative_to(root)), "plan_sha256": digest,
                     "started_at": manifest["started_at"]}
    else:
        reject_ancestor_manifest(root)
        native, context_hash = read(root / CONTEXT_FILE)
        if native.get("schema_version") != BUSINESS_CONTEXT or native.get("binding_kind") != "business-context" or \
                native.get("native_host") != "legacy-direct" or \
                native.get("run_directory") != str(root) or native.get("plan_sha256") != digest or \
                native.get("plan_path") != str(plan_path.relative_to(root)) or \
                not isinstance(native.get("context_id"), str):
            raise ValueError("business context belongs to another run or frozen plan")
        uuid.UUID(native["context_id"])
        timestamp(native["started_at"])
        source = native["analysis_source"]
        current, source_hash = read(inside(root / source["path"], root))
        if source_hash != source["sha256"] or current.get("analysis_id") != source["analysis_id"] or \
                plan.get("analysis_id") != source["analysis_id"]:
            raise ValueError("business context analysis source changed")
        signature = {"run_directory": str(root), "context_id": native["context_id"],
                     "context_sha256": context_hash, "plan_path": native["plan_path"],
                     "plan_sha256": digest, "started_at": native["started_at"]}
        manifest = {"status": "running", "binding_kind": "business-context"}
    if "analysis_id" in plan:
        signature["analysis_id"] = plan["analysis_id"]
    return signature, plan, manifest


def reject_ancestor_manifest(root: Path) -> None:
    if any((parent / "run-manifest.json").exists() for parent in root.parents):
        raise ValueError("a modern run ancestor cannot downgrade to business context")


def initialize_business_context(plan_path: Path, ledger_path: Path) -> dict:
    """Explicit legacy direct admission before media issuance; never a fallback for a modern run."""
    root = ledger_path.parent.resolve(strict=True)
    plan_path = inside(plan_path, root)
    if Path.cwd().resolve() != root or plan_path.parent != root:
        raise ValueError("native initialization requires the existing run cwd and its root plan")
    if (root / "run-manifest.json").exists():
        raise ValueError("modern manifests must use their existing run binding")
    reject_ancestor_manifest(root)
    if ledger_path.exists() or any(root.glob("*receipt*.json")) or \
            any(root.glob("*request*.json")) or (root / "watch_outcomes.json").exists():
        raise ValueError("business context must be initialized before media requests or receipts")
    for path in (root / "snapshot").glob("*.json"):
        envelope, _ = read(path)
        if isinstance(envelope, dict) and envelope.get("connector_id") == MEDIA_CONNECTOR:
            raise ValueError("cannot initialize business context from existing media results")
    plan, digest = read(plan_path)
    validate_plan(plan)
    source_path = inside(root / "watch_set.json", root)
    watch_set, source_hash = read(source_path)
    analysis_id = watch_set.get("analysis_id")
    if not isinstance(analysis_id, str) or not analysis_id.strip() or plan.get("analysis_id") != analysis_id:
        raise ValueError("native context requires this run's existing watch-set analysis source")
    native = {"schema_version": BUSINESS_CONTEXT, "binding_kind": "business-context",
              "native_host": "legacy-direct", "context_id": str(uuid.uuid4()),
              "run_directory": str(root), "plan_path": str(plan_path.relative_to(root)),
              "plan_sha256": digest, "started_at": datetime.now(timezone.utc).isoformat(),
              "analysis_source": {"path": source_path.name, "sha256": source_hash,
                                  "analysis_id": analysis_id}}
    descriptor = os.open(root / CONTEXT_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(native, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return native


def validate_plan(plan: dict) -> None:
    if plan.get("schema_version") != "media-job-plan.v1" or plan.get("connector_id") != MEDIA_CONNECTOR:
        raise ValueError("expected media-job-plan.v1")
    slots, urls = {}, {}
    for job in plan.get("jobs", []):
        if job["url"] in urls and urls[job["url"]] != job["content_id"]:
            raise ValueError("media plan maps one URL to ambiguous content ids")
        urls[job["url"]] = job["content_id"]
        for operation in job["operations"]:
            key = (job["content_id"], canonical_operation(operation["operation"]))
            request = {"connector_id": plan["connector_id"], "operation": operation["operation"],
                       "params": operation["params"]}
            if key[1] not in OPERATIONS or key in slots or request["params"].get("url") != job["url"]:
                raise ValueError("media plan has duplicate or inconsistent content/operation slots")
            slots[key] = request
    planned = []
    for number, batch in enumerate(plan.get("batches", []), 1):
        if batch.get("index") != number:
            raise ValueError("media plan batch indices must be contiguous issue order")
        for request in batch["requests"]:
            key = (request["content_id"], canonical_operation(request["operation"]))
            if request.get("url") != request["params"].get("url") or \
                    slots.get(key) != project(request):
                raise ValueError("media plan batch request differs from its content/operation slot")
            planned.append(key)
    if len(planned) != len(set(planned)) or set(planned) != set(slots):
        raise ValueError("media plan batches must cover every slot exactly once")


def project(request: dict) -> dict:
    return {name: request[name] for name in ("connector_id", "operation", "params")}


def results(receipt: dict) -> list[dict]:
    """Accept a full tool result or its unchanged structured body; reject conflicting copies."""
    candidates = []
    for body in (receipt, receipt.get("structuredContent"), receipt.get("structured_content")):
        if isinstance(body, dict) and isinstance(body.get("results"), list):
            candidates.append(body["results"])
        elif isinstance(body, dict) and {"connector_id", "operation", "status"} <= set(body):
            candidates.append([body])  # a single native data_connector_run retry, unwrapped unchanged
    for block in receipt.get("content", []):
        if block.get("type") == "text":
            try:
                body = json.loads(block.get("text", ""))
            except (ValueError, TypeError):
                continue
            if isinstance(body, dict) and isinstance(body.get("results"), list):
                candidates.append(body["results"])
            elif isinstance(body, dict) and {"connector_id", "operation", "status"} <= set(body):
                candidates.append([body])
    if receipt.get("isError") or not candidates or any(x != candidates[0] for x in candidates):
        raise ValueError("batch receipt lacks one unambiguous complete results array")
    return candidates[0]


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("receipt timestamps must name a timezone")
    return parsed


def derive(signature: dict, plan: dict, entry: dict) -> list[dict]:
    root = Path(signature["run_directory"])
    requests, request_hash = read(inside(root / entry["requests_path"], root))
    receipt, receipt_hash = read(inside(root / entry["receipt_path"], root))
    if request_hash != entry["requests_sha256"] or receipt_hash != entry["receipt_sha256"]:
        raise ValueError("a preserved media request or receipt changed after it was recorded")
    if isinstance(requests, dict) and set(requests) == {"requests"}:
        requests = requests["requests"]
    index = entry["batch_index"]
    if not isinstance(index, int) or not 1 <= index <= len(plan["batches"]):
        raise ValueError("receipt names an unknown batch index")
    planned = plan["batches"][index - 1]["requests"]
    if not isinstance(requests, list) or not requests:
        raise ValueError("actual batch requests must be a nonempty array")
    if entry.get("retry_of") is None:
        if requests != [project(row) for row in planned]:
            raise ValueError("actual batch requests differ from the frozen plan")
        selected = planned
    else:
        selected = [row for row in planned if project(row) in requests]
        if requests != [project(row) for row in selected]:
            raise ValueError("retry requests must be an ordered unchanged subset of the original batch")
    returned = results(receipt)
    if len(returned) != len(requests):
        raise ValueError("batch receipt count differs from issued request count")
    bound = []
    for original, request, result in zip(selected, requests, returned):
        if not isinstance(result, dict) or result.get("connector_id") != request["connector_id"] or \
                not operations_equivalent(request["operation"], result.get("operation")):
            raise ValueError("batch result connector/operation differs from its original request")
        job_id = result.get("job_id")
        if job_id is not None and (not isinstance(job_id, str) or not job_id.strip() or job_id != job_id.strip()):
            raise ValueError("returned job id must be a nonempty string")
        if result.get("status") in ("queued", "running") and not job_id:
            raise ValueError("deferred result has no returned job id")
        if result.get("status") not in {"queued", "running", "completed", "completed_partial", "partial",
                                        "blocked_setup", "unsupported", "failed", "cancelled"}:
            raise ValueError("unrecognized initial media result status")
        inner = result.get("envelope")
        if isinstance(inner, dict) and (inner.get("job_id") not in (None, job_id) or
                                       inner.get("connector_id") != request["connector_id"] or
                                       not operations_equivalent(request["operation"], inner.get("operation"))):
            raise ValueError("job receipt and its terminal envelope have inconsistent identities")
        # A prior run's job must not be relabelled as a newly issued operation.
        issued = result.get("created_at") or result.get("retrieved_at")
        if issued is None or timestamp(issued) < timestamp(signature["started_at"]):
            raise ValueError("media response predates this reserved run or lacks its issue timestamp")
        bound.append({"content_id": original["content_id"], "url": original["url"],
                      "operation": canonical_operation(request["operation"]), "job_id": job_id,
                      "status": result.get("status"), "issued_at": issued})
    return bound


def validate_entries(signature: dict, plan: dict, entries: list[dict], *, complete: bool) -> list[str]:
    next_batch, issued, seen_paths, seen_jobs, flags = 1, {}, set(), {}, []
    terminals = None
    for number, entry in enumerate(entries, 1):
        if entry.get("sequence") != number:
            raise ValueError("receipt issue sequence changed")
        for name in ("requests_path", "receipt_path"):
            if entry[name] in seen_paths:
                raise ValueError("an old request or receipt path cannot be reused or overwritten")
            seen_paths.add(entry[name])
        bound = derive(signature, plan, entry)
        retry = entry.get("retry_of")
        if retry is None:
            if entry["batch_index"] != next_batch:
                raise ValueError("initial media batches must be recorded in original issue order")
            next_batch += 1
        elif not isinstance(retry, int) or not 1 <= retry < number or \
                entries[retry - 1]["batch_index"] != entry["batch_index"]:
            raise ValueError("retry must name an earlier receipt for the same batch")
        for row in bound:
            slot = (row["content_id"], row["operation"])
            previous = issued.get(slot, [])
            if retry is not None:
                if len(previous) != 1 or previous[-1][0] != retry:
                    raise ValueError("retry is out of issue order or exceeds the one-retry limit")
                old = previous[-1][1]
                if timestamp(row["issued_at"]) < timestamp(old["issued_at"]):
                    raise ValueError("retry response predates the original issued job")
                if old["status"] != "failed":
                    if terminals is None:
                        terminals = load_envelope_files([], Path(signature["run_directory"]) / "snapshot")
                    if not old["job_id"] or not any(e.get("job_id") == old["job_id"] and
                                                   e.get("status") == "failed" for _, e in terminals):
                        raise ValueError("retry has no failed terminal result for its previous issued job")
            elif previous:
                raise ValueError("content/operation was already issued; declare its retry explicitly")
            job_id = row["job_id"]
            if job_id:
                if job_id in seen_jobs:
                    raise ValueError("returned job id is duplicated or reused across content/operation slots")
                seen_jobs[job_id] = slot
                flags.append(f"{row['content_id']}:{row['operation']}={job_id}")
            issued.setdefault(slot, []).append((number, row))
    if complete and next_batch != len(plan["batches"]) + 1:
        raise ValueError("job ledger is missing initial batch receipts")
    return flags


def load_job_ledger(plan_path: Path, ledger_path: Path, *, complete: bool = True) -> list[str]:
    signature, plan, _ = context(plan_path, ledger_path)
    ledger, _ = read(ledger_path)
    if ledger.get("schema_version") != SCHEMA or ledger.get("binding") != signature:
        raise ValueError("job ledger belongs to another run or a different frozen plan")
    return validate_entries(signature, plan, ledger["receipts"], complete=complete)


def record_batch(plan_path: Path, ledger_path: Path, batch_index: int,
                 requests_path: Path, receipt_path: Path, *, retry_of: int | None = None) -> dict:
    signature, plan, manifest = context(plan_path, ledger_path)
    if manifest.get("status") != "running":
        raise ValueError("cannot record new receipts in a closed run")
    root = Path(signature["run_directory"])
    requests_path, receipt_path = inside(requests_path, root), inside(receipt_path, root)
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    temporary = None
    try:
        if ledger_path.exists():
            load_job_ledger(plan_path, ledger_path, complete=False)
            ledger, _ = read(ledger_path)
        else:
            ledger = {"schema_version": SCHEMA, "binding": signature, "receipts": []}
        entry = {"sequence": len(ledger["receipts"]) + 1, "batch_index": batch_index,
                 "retry_of": retry_of, "requests_path": str(requests_path.relative_to(root)),
                 "requests_sha256": read(requests_path)[1], "receipt_path": str(receipt_path.relative_to(root)),
                 "receipt_sha256": read(receipt_path)[1]}
        ledger["receipts"].append(entry)
        validate_entries(signature, plan, ledger["receipts"], complete=False)
        temporary = ledger_path.with_name(f".{ledger_path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("x", encoding="utf-8") as output:
            os.chmod(temporary, 0o600)
            json.dump(ledger, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary, ledger_path)
        jobs = [{key: row[key] for key in ("content_id", "operation", "job_id", "status")}
                for row in derive(signature, plan, entry)]
        return {"status": "recorded", "sequence": entry["sequence"], "batch_index": batch_index,
                "ledger": str(ledger_path), "jobs": jobs}
    finally:
        os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock_path.unlink()
