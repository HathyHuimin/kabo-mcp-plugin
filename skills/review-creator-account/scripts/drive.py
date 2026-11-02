#!/usr/bin/env python3
"""Run a complete public account review between its judgments, so the model only judges.

A complete public review needs the model for one decision: the single synthesis. Everything before
it is mechanical — resolve the account, page its public history under the sample rules, place each
response, decide which coverage flags the Snapshot may carry, assemble, analyze, and print the
compact synthesis input — and so is everything after it: compose, both validators, the report.
Measured 2026-09-18 on Instagram reviews: 14 model requests for a small account and 21 for one
that paged four times, almost all of them that plumbing, and one of them a placement repaired by
hand after a new run directory had been reserved mid-run.

    fetch  --platform P --handle H     resolve -> history pages -> Snapshot -> Analyzer -> synthesis input
    fetch  --platform youtube --channel-id UC…   the same, from a channel id
    report                              Compose -> validate_artifacts -> validate_creator_report -> report

Retrieval follows SKILL.md exactly. Instagram: `resolve_profile`, then the first `list_posts` and
`list_reels` pages together; after that one page at a time from the format with fewer persisted
rows while both still have a next page and the combined count is under 100, the remaining format
alone once the other has none, and nothing more once the combined count reaches 100. YouTube:
`get_channel`, `list_channel_uploads` with 50 results and at most one cursor follow-up. TikTok:
`list_account_posts` once with `limit: 100`. Each response is placed as `snapshot/envelope-NN.json`
in fetch order with `persist_many.py`, from the complete copy `static_host.py stage` saved, so the
files are the bytes the manual workflow placed.

Connector calls are printed as `call:` lines naming public MCP tools (`connector_driver.py`) for the
model to make; the same command run again with `--placed` places those responses and goes on to the
next page.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import connector_driver as cd  # noqa: E402
from linked_post_snapshot import apply_link, bound_handle  # noqa: E402
from persist_envelope import (
    USABLE_STATUSES, account_of, drained_candidates, identity, in_order, nested_envelopes, row_count,
    truncated, unwrap_envelope,
)  # noqa: E402
from profile_contract import synthesis_schema  # noqa: E402
from synthesis_authoring import AuthoringError, validate_authoring  # noqa: E402

IG = "instagram-discovery"
YT = "youtube-public"
TT = "tiktok-public"
CONTENT_OPERATIONS = {
    "instagram": ("list_posts", "list_reels"),
    "youtube": ("list_channel_uploads",),
    "tiktok": ("list_account_posts",),
}

SNAPSHOT_DIR = Path("snapshot")
REJECTED_DIR = Path("rejected")
SNAPSHOT = "snapshot.json"
QUESTION = "question.txt"
SNAPSHOT_ANALYZER = "account_analyzers.py"
ANALYSIS = "account_analysis.json"
SYNTHESIS_INPUT = "synthesis_input.json"
SYNTHESIS = "account_review_synthesis.json"
# Only this name binds a growth scenario into the report; standalone calculations keep their own files.
GROWTH_INPUT = "review_growth_input.json"
OUTPUT_DIR = Path("output")
# 本 run 的 report 返回过 needs_revision 的标记（run 根目录的点文件）。只有留下这个标记的 run
# 还需要 verify-report 做最终复核；从未返回过的 run 由 report 直接作为最终步。
AUTHORING_CORRECTION_MARKER = Path(".authoring-correction-used")
# report 作为最终步时记下报告路径与 sha256；verify-report 见到同一份字节就不再重跑。
REPORT_FINAL_MARKER = Path(".report-final.json")

# The Instagram public sample: at most 100 persisted Feed + Reels rows combined.
COMBINED_CAP = 100
YOUTUBE_PAGE = 50
YOUTUBE_PAGES = 2
TIKTOK_LIMIT = 100
# A guard against a provider that repeats a cursor; the cap ends a real run long before this.
MAX_PAGES = 40
SEARCH_SHOWN = 10
# How a failed lookup is read, by the platform's error_code (kabo contracts `apiErrorCodeSchema`):
# - the account is not there: `not_found` (TikTok's list_account_posts answers a handle with no public
#   account `failed` / `not_found`, "No public TikTok account was found for that handle."), or a
#   handle the connector refuses as malformed (`validation_error`) — ask the user for the handle;
# - the capability is missing or spent for now: not offered, not set up, not allowed, quota used up —
#   say so and stop, a retry would get the same answer;
# - anything else (`service_unavailable`, `rate_limited`, `internal_error`, a timeout, no code) is a
#   temporary failure worth one more try, and says nothing about the account.
NOT_FOUND_CODES = {"not_found", "validation_error"}
CAPABILITY_STATUSES = {"unsupported", "setup_blocked", "not_implemented", "blocked"}
CAPABILITY_CODES = {"not_implemented", "forbidden", "authentication_required", "invalid_session",
                    "subscription_required", "quota_exceeded"}
SEARCH_QUERY = re.compile(r"^[A-Za-z0-9._]{2,30}$")


def say(text: str = "") -> None:
    cd.say(text)


def bare(handle: str | None) -> str:
    return str(handle or "").strip().lstrip("@").strip()


def safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text) or "account"


# --------------------------------------------------------------------------- placement


DRAINED_DIR = Path("snapshot/.drained")


def isolate_drained() -> None:
    """Move host records a runner drained into `snapshot/` aside, keeping this run's placements put.

    `--drained <dir>` makes every file under that directory count as a source rather than as a
    placement. This Skill places into `snapshot/envelope-NN.json`, the very names a runner drains
    into, so pointing the flag at `snapshot/` hands this run its own output back as a fresh host
    record. Moving the drained set into its own directory keeps the two apart.

    Two earlier attempts got the separator wrong:

    - 0.4.33 gated the flag on "this run has placed nothing yet". One round places several responses
      before that state is written, so later calls of the same round still carried it and re-read
      what the round had just written — `envelope-02.json` and `envelope-04.json` came back
      byte-identical and the Instagram cursor stopped advancing.
    - 0.4.34 moved the set aside once per run. But `drive.py fetch` runs first with `snapshot/` still
      empty (nothing is fetched yet) and only the `--placed` rerun goes through
      `kabo-run-pipeline --staging`, which drains *before* running its step. The one-shot marker was
      therefore set while there was nothing to move, and the real drained set never got isolated.

    Neither timing nor a one-shot flag separates the two. What does is whether **this run placed the
    file**, which `state["envelopes"]` records exactly. So this runs on every stage and moves only
    what this run did not place; a name that repeats across rounds is kept beside the earlier one
    rather than overwriting it.
    """
    placed = {str(entry.get("file")) for entry in (cd.state().get("envelopes") or [])}
    moved = []
    for path in sorted(Path("snapshot").glob("envelope-*.json")):
        if path.as_posix() in placed:
            continue
        DRAINED_DIR.mkdir(parents=True, exist_ok=True)
        # The shared placement reader excludes source records from its "already placed"
        # tally only for canonical envelope-NN.json names. A -2 collision suffix makes
        # the continuation page count as a placement of itself and disappear.
        match = re.fullmatch(r"envelope-(\d+)\.json", path.name)
        serial = int(match.group(1)) if match else 1
        target = DRAINED_DIR / f"envelope-{serial:02d}.json"
        while target.exists():
            serial += 1
            target = DRAINED_DIR / f"envelope-{serial:02d}.json"
        path.replace(target)
        moved.append(target.as_posix())
    if moved:
        cd.save_state(drained=[*(cd.state().get("drained") or []), *moved])
    archive_equivalent_drained()


def archive_equivalent_drained() -> None:
    """Keep equivalent host copies as evidence without presenting them as another response.

    The shared reader counts placements against responses, not physical source copies. A repeated
    host record would consume that count once and then offer the same page again. Only a complete
    Envelope with both the same existing identity and the same full JSON value is equivalent;
    batch responses sharing a request id, or changed response metadata, remain independent.
    """
    representatives: dict[tuple[str, str], Path] = {}
    archived: list[dict[str, str]] = []
    state = cd.state()
    previous = state.get("drained_equivalent_copies") or []
    restored = 0
    # The shared placement tally walks JSON objects, so a content.text wrapper's Envelope is
    # invisible after placement. Use only this driver's recorded, byte-preserved Snapshot files
    # as representatives for that shape; ordinary Envelopes keep the shared tally unchanged.
    for entry in state.get("envelopes") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            continue
        placed = Path(entry.get("file") or "")
        if (placed.parent != SNAPSHOT_DIR or placed.is_symlink()
                or placed.resolve().parent != Path.cwd().resolve() / SNAPSHOT_DIR
                or not re.fullmatch(r"envelope-\d+\.json", placed.name)):
            continue
        try:
            complete = json.loads(placed.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        envelope = unwrap_envelope(complete)
        if (envelope is None or envelope.get("operation") != entry.get("operation")
                or truncated(envelope) is not None or any(nested_envelopes(complete))):
            continue
        representatives[(identity(envelope), json.dumps(complete, sort_keys=True,
                                                        ensure_ascii=False))] = placed
    candidates = in_order(drained_candidates(DRAINED_DIR, None, None, [], None))
    # A re-mounted pathname can acquire an earlier mtime than its representative. Preserve the
    # representative already recorded for that response; this only orders copy comparison.
    preferred = {entry.get("representative") for entry in previous if isinstance(entry, dict)}
    candidates.sort(key=lambda candidate: candidate.source.removeprefix("drained:") not in preferred)
    for candidate in candidates:
        envelope = candidate.envelope
        if truncated(envelope) is not None:
            continue
        path = Path(candidate.source.removeprefix("drained:"))
        try:
            complete = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if unwrap_envelope(complete) != envelope:
            continue
        key = (identity(envelope), json.dumps(complete, sort_keys=True, ensure_ascii=False))
        representative = representatives.get(key)
        if representative is None:
            representatives[key] = path
            continue
        directory = DRAINED_DIR / "equivalent-copies"
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        # Some hosts re-mount the old pathname after absorbing a renamed run file. Remove only
        # that already archived view: the ledger, original archive bytes and current representative
        # must all agree. A new or changed response still gets its own preserved archive.
        restored_copy = False
        for entry in previous:
            if not isinstance(entry, dict) or entry.get("from") != path.as_posix():
                continue
            saved = Path(entry.get("to") or "")
            if (entry.get("sha256") != digest
                    or entry.get("representative") != representative.as_posix()
                    or saved.parent != directory
                    or not re.fullmatch(r"envelope-\d+\.json", saved.name)
                    or path.is_symlink() or saved.is_symlink()):
                continue
            try:
                restored_copy = saved.is_file() and saved.read_bytes() == body
            except OSError:
                restored_copy = False
            if restored_copy:
                path.unlink()
                restored += 1
                break
        if restored_copy:
            continue
        directory.mkdir(exist_ok=True)
        # Canonical names keep these archived bodies excluded from the shared reader's placement
        # tally, just like all source records under --drained. Never overwrite an earlier copy.
        match = re.fullmatch(r"envelope-(\d+)\.json", path.name)
        serial = int(match.group(1)) if match else 1
        target = directory / f"envelope-{serial:02d}.json"
        while target.exists() or target.is_symlink():
            serial += 1
            target = directory / f"envelope-{serial:02d}.json"
        path.replace(target)
        archived.append({"from": path.as_posix(), "to": target.as_posix(),
                         "representative": representative.as_posix(), "sha256": digest})
    if archived or restored:
        moved = {entry["from"]: entry["to"] for entry in archived}
        cd.save_state(
            drained=[moved.get(path, path) for path in (state.get("drained") or [])],
            drained_equivalent_copies=[*previous, *archived],
            drained_restored_aliases_removed=(state.get("drained_restored_aliases_removed") or 0) + restored,
        )


def drained_source() -> list[str]:
    """`--drained snapshot/.drained` while that set still holds a response this run has not placed.

    Safe on every call: the directory only ever holds what a runner drained, and
    `persist_envelope.py` skips a response this run already persisted, so each drained Envelope
    fills exactly one slot. On a host that stages nothing the directory never exists.
    """
    return ["--drained", DRAINED_DIR.as_posix()] if any(DRAINED_DIR.glob("*.json")) else []


def trace_placement(operation: str, output: str) -> None:
    """Name the host record this placement actually consumed.

    Which staged response a round takes is the one fact that decides whether pagination advances,
    and nothing used to record it: a round that silently re-took an earlier page looked exactly like
    one that advanced, until the cursor stopped moving several rounds later and the run died with
    "the provider is repeating a cursor" — pointing at the provider, which was fine. 2026-09-21 that
    cost a full investigation across the connector, the model's calls and the staging area before the
    placement itself came into view. One line per placement makes the next one a glance.
    """
    try:
        result = json.loads(output)
        placed = (result.get("results") or [{}])[0]
    except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
        return
    source = str(placed.get("source") or "")
    say(f"[drive] {operation} placed from {source.rsplit('/', 1)[-1] or '(unnamed)'}"
        f" request_id={placed.get('request_id') or '(none)'} rows={placed.get('rows')}")


def take(requests: list[dict[str, Any]], handle: str | None) -> list[tuple[dict[str, Any], Path, dict[str, Any]]]:
    """Place each response of the round just fetched into a holding file; return it with its Envelope.

    From the host's record of that request when the host named it — the file it staged, byte for
    byte, which is also what `persist_envelope.py` copies. Otherwise (the model made the calls,
    `--placed`) by `persist_many.py`, which takes the earliest unplaced response of that operation.
    """
    records = cd.LAST_RECORDS
    paired = records is not None and len(records) == len(requests)
    taken = []
    for index, request in enumerate(requests):
        operation = str(request["operation"])
        # Absolute: `persist_envelope.py` finds this run's tree by walking up from `--dest`, and a
        # relative path makes that answer depend on the caller's working directory. Resolved
        # elsewhere it sees no `snapshot/`, counts nothing as already persisted, and hands back the
        # page this run placed a round ago — pagination then repeats a cursor and the run dies
        # blaming the provider. Verified 2026-09-21: same staging, same run tree, a relative --dest
        # resolved outside the run directory returned page one, an absolute one returned page two.
        # The name carries the request, not just its position: a later page of the same operation
        # must not reuse the file the previous page is sitting in. `persist_many.py --dest` skips
        # the destination when it tallies what this run already placed — that is what makes
        # re-running one stage idempotent — so a holding file reused across rounds hides the page
        # it holds, and the next round takes that same page again. The run then sees a repeated
        # cursor and stops, blaming the provider. Verified 2026-09-22 on @vinny_creative: five
        # distinct staged responses, and the second `list_posts` round took page one a second time
        # because both pages were written to `.incoming-0-list_posts.json`. Same request, same
        # name — so running a stage again still lands on its own file and stays idempotent.
        page = hashlib.sha256(
            json.dumps(request.get("params") or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:12]
        holding = (Path.cwd() / f".incoming-{index}-{operation}-{page}.json").resolve()
        record = records[index][1] if paired else None
        if record is not None:
            holding.write_bytes(record.read_bytes())
            holding.chmod(0o600)
        else:
            identity = ["--handle", handle] if handle else []
            out = cd.script("persist_many.py", "--connector", str(request["connector_id"]), "--operation", operation,
                            "--any-status", "--dest", str(holding), *identity, *drained_source())
            trace_placement(operation, out)
        envelope = unwrap_envelope(holding.read_text(encoding="utf-8"))
        if envelope is None:
            raise cd.StageError(f"the {operation} response is not a connector Envelope; nothing was placed")
        owner = account_of(envelope)
        if record is not None and owner and handle and owner != bare(handle).lower():
            holding.unlink(missing_ok=True)
            raise cd.StageError(f"the {operation} response belongs to @{owner}, not @{bare(handle)}; "
                                "confirm the account with the user before running this again")
        taken.append((request, holding, envelope))
    return taken


def envelopes() -> list[dict[str, Any]]:
    return list(cd.state().get("envelopes") or [])


def keep(holding: Path, operation: str) -> str:
    """Move a usable response into the next `snapshot/envelope-NN.json`, in fetch order."""
    placed = envelopes()
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    target = SNAPSHOT_DIR / f"envelope-{len(placed) + 1:02d}.json"
    holding.replace(target)
    cd.save_state(envelopes=[*placed, {"file": target.as_posix(), "operation": operation}])
    return target.as_posix()


def reject(holding: Path, request: dict[str, Any], envelope: dict[str, Any]) -> None:
    """Keep an unusable response out of the Snapshot, but in the run, so it is never placed again."""
    REJECTED_DIR.mkdir(exist_ok=True)
    failed = list(cd.state().get("failed") or [])
    holding.replace(REJECTED_DIR / f"{request['operation']}-{len(failed) + 1:02d}.json")
    cd.save_state(failed=[*failed, {"operation": request["operation"], "params": request.get("params") or {},
                                    "status": envelope.get("status"), "error_code": envelope.get("error_code")}])
    say(f"[drive] {request['operation']} returned status={envelope.get('status')}"
        f"{' (' + str(envelope.get('error_code')) + ')' if envelope.get('error_code') else ''}; "
        "it is left out of the Snapshot and its history counts as incomplete")


def usable(envelope: dict[str, Any]) -> bool:
    return envelope.get("status") in USABLE_STATUSES


def not_found(envelope: dict[str, Any]) -> bool:
    """A failed lookup whose error_code says the account is not there (see NOT_FOUND_CODES)."""
    return not usable(envelope) and str(envelope.get("error_code") or "") in NOT_FOUND_CODES


def service_failed(holding: Path, operation: str, envelope: dict[str, Any], label: str) -> None:
    """A lookup that failed is no answer about the account: stop, and keep this round open.

    Only a completed response without the account, or a `not_found` answer, says it does not exist.
    A timed-out or upstream-error response used to be read that way too — the user was asked whether the handle was
    misspelled, and the round was recorded as done, so a rerun in the same run never asked again.
    The response is kept in the run (so no placement takes it again) and the round is not recorded.
    """
    REJECTED_DIR.mkdir(exist_ok=True)
    number = len(list(REJECTED_DIR.glob(f"{operation}-service-*.json"))) + 1
    holding.replace(REJECTED_DIR / f"{operation}-service-{number:02d}.json")
    code = f", error_code={envelope.get('error_code')}" if envelope.get("error_code") else ""
    if (str(envelope.get("status") or "") in CAPABILITY_STATUSES
            or str(envelope.get("error_code") or "") in CAPABILITY_CODES):
        raise cd.StageError(f"the data service cannot make {operation} here (status={envelope.get('status')}{code}): "
                            "the capability is missing, which says nothing about whether the account exists. Tell the "
                            f"user {label} cannot be reviewed here right now and stop; do not run this again, do not ask "
                            "whether the handle is misspelled and do not review another account in its place.")
    raise cd.StageError(f"the data service failed on {operation} for {label} (status={envelope.get('status')}{code}); "
                        "that says nothing about whether the account exists, and nothing was recorded for this call. "
                        "Run this same command again once, without --placed. If it fails the same way again, tell the "
                        f"user the data service could not retrieve {label} right now and stop; do not ask whether the "
                        "handle is misspelled and do not review another account in its place.")


def place_pages(requests: list[dict[str, Any]], handle: str | None) -> None:
    for request, holding, envelope in take(requests, handle):
        if usable(envelope):
            keep(holding, str(request["operation"]))
        else:
            reject(holding, request, envelope)


# --------------------------------------------------------------------------- pagination facts


def terminal(envelope: dict[str, Any]) -> bool:
    """The connector's own last-page signal, as build_public_snapshot.py reads it."""
    data = envelope.get("data")
    if not isinstance(data, dict):
        return False
    if data.get("more_available") is False:
        return True
    if envelope.get("operation") == "list_channel_uploads":
        coverage = data.get("coverage")
        return isinstance(coverage, dict) and "next_page_token" in coverage \
            and not str(coverage.get("next_page_token") or "").strip()
    return False


def next_cursor(envelope: dict[str, Any]) -> str | None:
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    if envelope.get("operation") == "list_channel_uploads":
        coverage = data.get("coverage") if isinstance(data.get("coverage"), dict) else {}
        token = coverage.get("next_page_token")
        return str(token) if isinstance(token, str) and token.strip() else None
    cursor = data.get("cursor")
    if data.get("more_available") is True and isinstance(cursor, (str, int)) and str(cursor).strip():
        return str(cursor)
    return None


def chain(operation: str) -> dict[str, Any]:
    """Everything this run holds of one paginated operation."""
    pages = [cd.load(entry["file"]) for entry in envelopes() if entry["operation"] == operation]
    failed = any(entry["operation"] == operation for entry in cd.state().get("failed") or [])
    last = pages[-1] if pages else None
    return {
        "pages": len(pages),
        "rows": sum(row_count(page) or 0 for page in pages),
        "failed": failed,
        "next": None if failed or last is None else next_cursor(last),
        "terminal": bool(last is not None and not failed and terminal(last)),
    }


# --------------------------------------------------------------------------- identity


def remember_identity(handle: str, account_id: str | None, profile: str | None) -> None:
    cd.save_state(identity={"handle": handle, "account_id": account_id, "profile_envelope": profile})


def resolve_instagram(handle: str, args: argparse.Namespace) -> bool:
    """`resolve_profile` for the requested handle; False when it names no public profile."""
    params = {"handle": handle, "include_post_count": True}
    request = {"connector_id": IG, "operation": "resolve_profile", "params": params}

    def place() -> None:
        ((_, holding, envelope),) = take([request], handle)
        profile = (envelope.get("data") or {}).get("profile") if isinstance(envelope.get("data"), dict) else None
        if not usable(envelope) and not not_found(envelope):
            service_failed(holding, "resolve_profile", envelope, "@" + handle)
        if usable(envelope) and isinstance(profile, dict) and (profile.get("username") or profile.get("id")):
            target = keep(holding, "resolve_profile")
            account_id = profile.get("id") or profile.get("pk") or profile.get("user_id")
            remember_identity(str(profile.get("username") or handle), str(account_id) if account_id else None, target)
        else:
            holding.replace(Path(f"unresolved-{safe(handle)}.json"))
            cd.save_state(unresolved=[*(cd.state().get("unresolved") or []), handle.lower()])

    cd.fetch_round([cd.run_call(IG, "resolve_profile", params)], args, place)
    return handle.lower() not in (cd.state().get("unresolved") or [])


def search_instagram(handle: str, args: argparse.Namespace) -> None:
    """The requested handle named no profile: list who it could be, then stop for the model to choose."""
    if not SEARCH_QUERY.match(handle):
        say(f"@{handle} did not resolve to a public Instagram profile. Confirm the exact handle with the user, then run "
            "this fetch again with --handle <that handle>. Do not review any other account in its place.")
        return
    dest = Path(f"subject-search-{safe(handle)}.json")
    request = {"connector_id": IG, "operation": "search_accounts", "params": {"query": handle}}

    def place() -> None:
        ((_, holding, _),) = take([request], None)
        holding.replace(dest)

    cd.fetch_round([cd.run_call(IG, "search_accounts", {"query": handle})], args, place)
    envelope = cd.load(dest)
    rows = [row for row in ((envelope.get("data") or {}).get("rows") or []) if isinstance(row, dict)]
    say(f"@{handle} did not resolve to a public Instagram profile. Account search for {handle!r} "
        f"returned {len(rows)} account(s); the first {min(len(rows), SEARCH_SHOWN)}:")
    for row in rows[:SEARCH_SHOWN]:
        name = " ".join(str(row.get("full_name") or "").split())
        verified = " verified" if row.get("is_verified") else ""
        say(f"- @{row.get('username') or row.get('author') or '?'}{' | ' + name if name else ''}{verified}")
    say("Choose the account the user asked about only when one candidate clearly is that account (same name, "
        "an obvious spelling of the requested handle), then run this same fetch command again with --handle "
        "<that handle>. If none clearly is, tell the user @" + handle + " could not be found and ask for the exact "
        "handle; do not review a different account in its place.")


def resolve_youtube(handle: str | None, channel_id: str | None, args: argparse.Namespace) -> bool:
    # `get_channel` takes exactly one identity parameter.
    params = {"channel_id": channel_id} if channel_id else {"handle": "@" + bare(handle)}
    request = {"connector_id": YT, "operation": "get_channel", "params": params}
    label = channel_id or bare(handle)

    def place() -> None:
        ((_, holding, envelope),) = take([request], None)
        rows = (envelope.get("data") or {}).get("rows") if isinstance(envelope.get("data"), dict) else None
        channel = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
        if not usable(envelope) and not not_found(envelope):
            service_failed(holding, "get_channel", envelope, label)
        if usable(envelope) and channel.get("id"):
            target = keep(holding, "get_channel")
            remember_identity(str(channel.get("custom_url") or bare(handle) or channel["id"]), str(channel["id"]), target)
        else:
            holding.replace(Path(f"unresolved-{safe(label)}.json"))
            cd.save_state(unresolved=[*(cd.state().get("unresolved") or []), label.lower()])

    try:
        cd.fetch_round([cd.run_call(YT, "get_channel", params)], args, place)
    except cd.StageError as error:
        # 数据源拒绝的 handle 每次重试都会同样失败：改为指向 channel id，而不是停在拒绝上。
        if channel_id or not str(error).startswith("connector call refused:"):
            raise
        cd.save_state(unresolved=[*(cd.state().get("unresolved") or []), label.lower()])
        say(f"YouTube refused the handle {label!r} ({str(error)[:200]}). Do not send this --handle again: take the "
            "channel's UC… id from its channel URL or a channel search, then run this fetch again with "
            "--channel-id <UC… id>.")
        return False
    if label.lower() in (cd.state().get("unresolved") or []):
        say(f"{label} did not resolve to a public YouTube channel. Confirm the channel with the user (its @handle or "
            "its channel URL), then run this fetch again with --handle <@handle> or --channel-id <UC… id>. Do not "
            "review any other channel in its place.")
        return False
    return True


# --------------------------------------------------------------------------- history


def instagram_history(handle: str, args: argparse.Namespace) -> None:
    user_id = (cd.state().get("identity") or {}).get("account_id")

    def posts(cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"handle": handle}
        if cursor:
            params["max_id"] = cursor
        return {"connector_id": IG, "operation": "list_posts", "params": params}

    def reels(cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"handle": handle}
        if user_id:
            params["user_id"] = user_id
        if cursor:
            params["max_id"] = cursor
        return {"connector_id": IG, "operation": "list_reels", "params": params}

    first = [posts(), reels()]
    cd.fetch_round(cd.batch_calls(first), args, lambda: place_pages(first, handle))
    for _ in range(MAX_PAGES):
        feed, clips = chain("list_posts"), chain("list_reels")
        combined = feed["rows"] + clips["rows"]
        if combined >= COMBINED_CAP:
            break
        if feed["next"] and clips["next"]:
            # Both still have a page: the format with fewer persisted rows goes next (Feed on a tie,
            # the order the first round fetched them in). Never both at once — that spends the cap
            # on whichever format pages faster and changes the Feed/Reels mix of the sample.
            request = posts(feed["next"]) if feed["rows"] <= clips["rows"] else reels(clips["next"])
        elif feed["next"]:
            request = posts(feed["next"])
        elif clips["next"]:
            request = reels(clips["next"])
        else:
            break
        page = [request]
        cd.fetch_round(cd.batch_calls(page), args, lambda: place_pages(page, handle))
    else:
        raise cd.StageError(f"Instagram pagination did not end after {MAX_PAGES} pages; the provider is repeating "
                            "a cursor. Tell the user the history could not be retrieved completely.")


def youtube_history(args: argparse.Namespace) -> None:
    channel_id = str((cd.state().get("identity") or {}).get("account_id"))
    params: dict[str, Any] = {"channel_id": channel_id, "max_results": YOUTUBE_PAGE}
    first = [{"connector_id": YT, "operation": "list_channel_uploads", "params": params}]
    cd.fetch_round(cd.batch_calls(first), args, lambda: place_pages(first, None))
    uploads = chain("list_channel_uploads")
    if uploads["next"] and uploads["pages"] < YOUTUBE_PAGES:
        follow = [{"connector_id": YT, "operation": "list_channel_uploads",
                   "params": {**params, "cursor": uploads["next"]}}]
        cd.fetch_round(cd.batch_calls(follow), args, lambda: place_pages(follow, None))


def tiktok_history(handle: str, args: argparse.Namespace) -> bool:
    """`list_account_posts` once; the Connector pages internally. False when the account did not resolve."""
    params = {"handle": "@" + handle, "limit": TIKTOK_LIMIT}
    request = {"connector_id": TT, "operation": "list_account_posts", "params": params}

    def place() -> None:
        ((_, holding, envelope),) = take([request], handle)
        if not_found(envelope):
            holding.replace(Path(f"unresolved-{safe(handle)}.json"))
            cd.save_state(unresolved=[*(cd.state().get("unresolved") or []), handle.lower()])
            return
        if not usable(envelope):
            service_failed(holding, "list_account_posts", envelope, "@" + handle)
        keep(holding, "list_account_posts")
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        remember_identity(str(data.get("handle") or handle), None, None)

    cd.fetch_round([cd.run_call(TT, "list_account_posts", params)], args, place)
    if handle.lower() in (cd.state().get("unresolved") or []):
        say(f"@{handle} is not a public TikTok account. Confirm the exact handle with the user, then run this "
            "fetch again with --handle <that handle>. Do not review any other account in its place.")
        return False
    return True


# --------------------------------------------------------------------------- assemble


def coverage_flags(platform: str) -> list[str]:
    """`--complete-operation` only for an operation whose last persisted page is the connector's terminal
    page (and, on Instagram, while the combined sample stayed at or under 100 rows); every other case
    keeps coverage incomplete."""
    chains = {operation: chain(operation) for operation in CONTENT_OPERATIONS[platform]}
    combined = sum(c["rows"] for c in chains.values())
    complete = [operation for operation, c in chains.items()
                if c["pages"] and c["terminal"] and (platform != "instagram" or combined <= COMBINED_CAP)]
    flags = [] if len(complete) == len(chains) else ["--coverage-incomplete"]
    return flags + [arg for operation in complete for arg in ("--complete-operation", operation)]


def focus_handle(identity: dict[str, Any], requested: str) -> str:
    """The Snapshot creator whose platform account id is the resolved account; its handle as normalized there."""
    creators = [c for c in cd.load(SNAPSHOT).get("creators") or [] if isinstance(c, dict) and c.get("handle")]
    account_id = identity.get("account_id")
    for creator in creators:
        if account_id and str(creator.get("creator_id")) == str(account_id):
            return str(creator["handle"])
    names = {bare(identity.get("handle")).lower(), bare(requested).lower()} - {""}
    for creator in creators:
        if bare(creator["handle"]).lower() in names:
            return str(creator["handle"])
    if len(creators) == 1:
        return str(creators[0]["handle"])
    return bare(identity.get("handle") or requested)


def assemble(platform: str, requested: str, question: str, window: str) -> None:
    placed = envelopes()
    content = [entry for entry in placed if entry["operation"] in CONTENT_OPERATIONS[platform]]
    rows = sum(row_count(cd.load(entry["file"])) or 0 for entry in content)
    if not rows:
        raise cd.StageError("the account returned no public content rows (private, empty, or unavailable); tell the "
                            "user no public history could be retrieved for this account, and stop")
    flags = coverage_flags(platform)
    identity = cd.state().get("identity") or {}
    envelope_args = [arg for entry in placed for arg in ("--envelope", entry["file"])]
    cd.script("build_public_snapshot.py", "--platform", platform, "--window", window, *envelope_args, *flags,
                     "--output", SNAPSHOT)
    linked_file = cd.state().get("linked_evidence")
    if linked_file:
        try:
            snapshot = apply_link(cd.load(SNAPSHOT), cd.load(linked_file), question)
        except ValueError as error:
            raise cd.StageError(f"linked post evidence was rejected: {error}") from error
        temporary = Path(SNAPSHOT + ".linked.tmp")
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(SNAPSHOT)
    focus = focus_handle(identity, requested)
    pages = ", ".join(f"{operation} {chain(operation)['pages']} page(s)/{chain(operation)['rows']} rows"
                      for operation in CONTENT_OPERATIONS[platform])
    say(f"[drive] {pages}; snapshot flags: {' '.join(flags) or '(none)'}; focus handle: {focus}")
    cd.script(SNAPSHOT_ANALYZER, SNAPSHOT, "--operation", "all", "--focus-handle", focus, "--output", ANALYSIS)
    profile = identity.get("profile_envelope")
    profile_args = ["--profile-envelope", str(profile)] if profile else []
    # schema 先于 compact input 打印，并用紧凑 JSON：宿主截断的是输出尾部，截掉的只会是已落盘的
    # synthesis_input.json，而不是 schema。
    say(json.dumps(synthesis_schema(), ensure_ascii=False, separators=(",", ":")))
    cd.script("build_synthesis_input.py", "--analysis", ANALYSIS, "--snapshot", SNAPSHOT, *profile_args,
              "--user-question", question,
              "--output", SYNTHESIS_INPUT, "--print", show=True)


# --------------------------------------------------------------------------- stages


def standard_input(wait: bool) -> str:
    """What standard input holds: all of it when `wait`, else only what is already there."""
    stream = sys.stdin
    if stream is None or stream.isatty():
        return ""
    if not wait:
        try:
            import select

            ready, _, _ = select.select([stream], [], [], 0)
        except (OSError, ValueError, TypeError, io.UnsupportedOperation):
            ready = [stream]  # an in-memory stream (tests): reading it never blocks
        if not ready:
            return ""
    return stream.read().rstrip("\r\n")


def question_of(args: argparse.Namespace) -> str:
    """The user's request this fetch answers: the one given now, else the stored one on a --placed rerun.

    A request given on standard input always wins, even in a run directory that holds another one:
    the same account asked a different question is a different review. Only a `--placed` rerun with
    nothing on standard input keeps the question its run was started with.
    """
    placed = bool(getattr(args, "placed", False))
    stored = cd.state().get("question") if placed else None
    if getattr(args, "question_file", None) == "-":
        # The request arrives on standard input (a heredoc on the same command), so nothing is written
        # before the run directory exists: a file written first lands in whatever directory the shell
        # is in — on 2026-09-19 that was this skill's own cache, which then failed its signature check.
        # A terminal on standard input means the heredoc was left out: never wait on it. A --placed rerun
        # without a heredoc must not wait either: it reads only what is already there, else keeps the
        # question its run was started with.
        text = standard_input(wait=not placed)
        if text.strip():
            return text
    elif getattr(args, "question_file", None) and (Path(args.question_file).is_file() or not stored):
        # A heredoc ends the file with a newline the user never typed.
        return Path(args.question_file).read_text(encoding="utf-8").rstrip("\r\n")
    if getattr(args, "question", None):
        return str(args.question)
    if stored:
        # A --placed rerun from the run directory, where the question file written before the run
        # directory existed is not: the question the first run read is the one this run keeps.
        return str(stored)
    raise cd.StageError("pass the user's original request verbatim on standard input with --question-file - "
                        "and a heredoc on the same command")


def require_open_reservation(directory: Path) -> None:
    """A driver command must not reopen a wrapper's terminal attempt, even outside that wrapper."""
    for name in ("run-manifest.json", ".pipeline-stages.json"):
        path = directory / name
        if path.is_symlink():
            raise cd.StageError("host reservation records must not be symlinks; preserve artifacts and stop")
        if path.exists():
            record = cd.load(path)
            if not isinstance(record, dict) or record.get("status") != "running":
                raise cd.StageError("this host reservation is closed; preserve artifacts and stop")


def reserve_review_directory(placed: bool = False, args: Any = None, question: str = "") -> None:
    """Work in the run static_host.py reserved for this request (`KABO_STATIC_RUN_DIR`)."""
    require_open_reservation(Path.cwd())
    cd.reserve_run_directory(placed, stage="fetch", reuse=False, args=args, extra=question)


def stage_fetch(args: argparse.Namespace) -> None:
    for name in ("build_public_snapshot.py", SNAPSHOT_ANALYZER):
        if not (cd.SCRIPTS / name).is_file():
            raise cd.StageError(f"This skill package is incomplete: scripts/{name} is missing. "
                                "Reload a complete verified package before fetching account content.")
    platform = args.platform
    handle = bare(args.handle)
    channel_id = (getattr(args, "channel_id", None) or "").strip() or None
    if not handle and not (platform == "youtube" and channel_id):
        raise cd.StageError("--handle is required (YouTube also accepts --channel-id)")
    if channel_id and platform != "youtube":
        raise cd.StageError("--channel-id is YouTube only")
    question = question_of(args)
    linked_bytes = None
    linked_origin = None
    if getattr(args, "linked_evidence", None):
        if platform != "tiktok":
            raise cd.StageError("--linked-evidence currently supports TikTok post evidence only")
        linked_path = Path(args.linked_evidence).resolve()
        linked_origin = str(linked_path)
        stored = cd.state().get("linked_evidence") if args.placed else None
        if stored and not linked_path.exists() and cd.state().get("linked_evidence_origin") == linked_origin:
            linked_path = Path(stored).resolve()
        linked_bytes = linked_path.read_bytes()
        try:
            handle = bound_handle(json.loads(linked_bytes), question, handle)
        except ValueError as error:
            raise cd.StageError(f"--linked-evidence was rejected: {error}") from error
    before = Path.cwd()
    reserve_review_directory(bool(args.placed), args=args, question=question)
    if Path.cwd() != before:
        say("a --placed rerun and drive.py report run from that directory too: cd into it first")
    isolate_drained()
    current = cd.state()
    # Only a --placed rerun reaches a run directory that holds a review: it must be the same request.
    if current.get("question") is not None and current["question"] != question:
        raise cd.StageError("this run holds a different original request; start a new fetch without --placed")
    if current.get("platform") and current["platform"] != platform:
        raise cd.StageError(f"this run directory holds a {current['platform']} review; --placed continues that review "
                            "only. For this request, start a new run with static_host.py init and run the fetch there")
    requested = (channel_id or handle).lower()
    if current.get("requested") and current["requested"] != requested:
        raise cd.StageError(f"this run directory already holds the review of {current.get('requested')}; --placed "
                            "continues that review only. For another account, start a new run with static_host.py init and "
                            "run the fetch there")
    window = args.window
    if current.get("window") and window != current["window"]:
        if window == "auto":
            window = str(current["window"])  # a --placed rerun that left --window out keeps the run's own
        else:
            raise cd.StageError(f"this run was started for --window {current['window']}; --placed continues that "
                                "review only. For another window, start a new run with static_host.py init and run the "
                                "fetch there")
    args.window = window
    question_path = Path(QUESTION)
    if question_path.exists():
        if question_path.read_text(encoding="utf-8").rstrip("\r\n") != question.rstrip("\r\n"):
            raise cd.StageError(f"{QUESTION} differs from this run's original request; use a new run")
    else:
        question_path.write_text(question + "\n", encoding="utf-8")
    say(f"original request: {question_path.resolve()}")
    say(f"public snapshot after fetch completes: {Path(SNAPSHOT).resolve()}")
    if linked_bytes is not None:
        linked_path = SNAPSHOT_DIR / "linked-post-evidence.json"
        linked_path.parent.mkdir(mode=0o700, exist_ok=True)
        if linked_path.exists() and linked_path.read_bytes() != linked_bytes:
            raise cd.StageError("this run already holds different linked post evidence")
        if not linked_path.exists():
            linked_path.write_bytes(linked_bytes)
        cd.save_state(linked_evidence=linked_path.as_posix(), linked_evidence_origin=linked_origin)
    cd.save_state(platform=platform, requested=requested, question=question, window=window)
    if platform == "instagram":
        if not resolve_instagram(handle, args):
            search_instagram(handle, args)
            return
        instagram_history(handle, args)
    elif platform == "youtube":
        if not resolve_youtube(handle or None, channel_id, args):
            return
        youtube_history(args)
    else:
        if not tiktok_history(handle, args):
            return
    assemble(platform, channel_id or handle, question, args.window)


def growth_choices(analysis: dict, snapshot: dict, question: str, metric: str, days: float) -> str:
    """The assessed choices for this run, each as the input file that binds it.

    The model only states the user's plan; scope, cohort and the verbatim question come from the run,
    so no second request schema, snapshot path or hand-copied field stands between assessment and binding.
    """
    from estimate_growth import render_evidence, review_evidence
    scope = {"platform": analysis["platform"], "account": analysis["scope"]["account"]}
    evidence = review_evidence({"schema_version": "growth-review.v1", "scope": scope, "horizon_days": days,
                                "metrics": [metric]}, snapshot, SNAPSHOT)
    lines = [render_evidence(evidence).rstrip(), "",
             f"To bind a choice, save the one that fits the request as {GROWTH_INPUT} in this run directory, "
             "replace the plan placeholders with the user's own number as written in the question, "
             "and run drive.py growth again before writing the synthesis:"]
    for entry in evidence["metrics"]:
        for candidate in entry["candidates"]:
            ages = candidate["basis"]["observed_age_days"] if candidate.get("basis") else None
            cohort = {"format": candidate["cohort"]["format"], "age_min_days": 0,
                      "age_max_days": int(ages["max"]) + 1 if ages else candidate["cohort"]["age_max_days"]}
            choice = {"schema_version": "growth-input.v1", "mode": "content_scenario",
                      "scope": {**scope, "metric": metric, "semantics": "per_content_cumulative"},
                      "horizon_days": days, "cohort": cohort,
                      "plan": {"posts": "<number>", "origin": "user", "quote": question.strip(),
                               "binding": {"quantity_kind": "total", "quantity_quote": "<number as written>"}}}
            lines.append(json.dumps(choice, ensure_ascii=False))
    lines += ["For a count per day or week, add \"period\": \"day\"|\"week\" to plan and use binding "
              "{\"quantity_kind\": \"cadence\", \"quantity_quote\": \"<number as written>\", \"period\": <same>, "
              "\"period_quote\": \"<the period words as written>\"}. Without a user plan, use plan {\"origin\": \"observed_cadence\"}. "
              "Set horizon_days to the user's publication window."]
    return "\n".join(lines)


def stage_growth(args: argparse.Namespace) -> None:
    """List this run's growth choices, or print the result the report will bind from the saved one.

    It writes nothing, so a rejected input is the model's to correct in this same run: it is printed
    as a finding with a zero exit, never as a stage failure that would close a wrapper's run.
    """
    require_open_reservation(Path.cwd())
    if not cd.state().get("envelopes") or not Path(ANALYSIS).exists():
        raise cd.StageError("run drive.py fetch first, from this run directory")
    from compose_review import growth_for_review, render_growth  # the report's own binding, not a copy
    analysis, snapshot = cd.load(ANALYSIS), cd.load(SNAPSHOT)
    question = Path(QUESTION).read_text(encoding="utf-8")
    if not Path(GROWTH_INPUT).exists():
        say(growth_choices(analysis, snapshot, question, args.metric, args.horizon_days))
        return
    try:
        growth = growth_for_review(analysis, cd.load(GROWTH_INPUT), snapshot, question)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        detail = f"{type(error).__name__}: {error}"
        say(f"growth input rejected, nothing bound: {detail}. Correct {GROWTH_INPUT} from one of the choices below "
            f"and run drive.py growth again in this run before writing the synthesis; drive.py report fails on the same input.")
        say(growth_choices(analysis, snapshot, question, args.metric, args.horizon_days))
        return
    say("\n".join(render_growth(growth, args.language)).rstrip())
    say(f"Any growth figure in the synthesis is one of these. The report recomputes them from {GROWTH_INPUT} "
        "and rejects a synthesis that states the total, item count or quartile totals of another assessed choice.")


def preflight_report_sources(analysis: dict[str, Any], snapshot: dict[str, Any],
                             profile: str | None, stored: list[str]) -> None:
    """Finish the final path's source-only checks before softening any author error."""
    from compose_review import growth_for_review, load, load_complete_envelope
    from public_identity import identity_from_envelope
    from validate_creator_report import envelope_identity_values

    source_paths = [Path(name) for name in stored]
    if profile:
        source_paths.append(Path(profile))
    growth = Path(GROWTH_INPUT)
    has_growth = growth.exists() or growth.is_symlink()
    if has_growth:
        source_paths.extend((growth, Path(QUESTION)))
    for path in source_paths:
        if path.is_symlink():
            raise cd.StageError(f"report source must not be a symlink: {path}")
    if profile:
        identity_from_envelope(load_complete_envelope(Path(profile)))
    # Same object loader and source scan as the unchanged Markdown validator;
    # this does not fabricate a report or weaken source/lineage checks.
    envelope_identity_values([load(Path(name)) for name in stored])
    if has_growth:
        growth_for_review(analysis, load(growth), snapshot,
                          Path(QUESTION).read_text(encoding="utf-8"))


def stage_report(args: argparse.Namespace) -> None:
    require_open_reservation(Path.cwd())
    current = cd.state()
    if not current.get("envelopes") or not Path(ANALYSIS).exists():
        raise cd.StageError("run drive.py fetch first, from this run directory")
    if not Path(SYNTHESIS).exists():
        raise cd.StageError(f"write {SYNTHESIS} with a heredoc and chain this command after it")
    output_dir = Path(getattr(args, "output_dir", None) or OUTPUT_DIR)
    if not output_dir.resolve().is_relative_to(Path.cwd().resolve()):
        raise cd.StageError("report output directory must stay inside this run directory")
    profile = (current.get("identity") or {}).get("profile_envelope")
    stored = [entry["file"] for entry in current.get("envelopes") or []]
    stored += sorted(str(path) for pattern in ("unresolved-*.json", "subject-search-*.json")
                     for path in Path.cwd().glob(pattern))
    if getattr(args, "allow_authoring_correction", False) is True:
        # This optional managed non-final stage is not a successful report receipt. Source,
        # provenance, filesystem and host failures remain fatal; only authored fields recover.
        for name in (ANALYSIS, SYNTHESIS, SNAPSHOT):
            if Path(name).is_symlink():
                raise cd.StageError(f"authoring input must not be a symlink: {name}")
        analysis, synthesis, snapshot = cd.load(ANALYSIS), cd.load(SYNTHESIS), cd.load(SNAPSHOT)
        if (snapshot.get("schema_version") != "public-content-snapshot.v1"
                or snapshot.get("snapshot_id") != analysis.get("snapshot_id")
                or not isinstance(snapshot.get("items"), list)):
            raise cd.StageError("deterministic snapshot schema/binding is invalid")
        if any(not isinstance(row, dict) or not isinstance(row.get("content_id"), str)
               or not row["content_id"] for row in snapshot["items"]):
            raise cd.StageError("deterministic snapshot contains an invalid content_id")
        source_ids = {row["content_id"] for row in snapshot["items"]}
        for row in analysis.get("rankings", []):
            if not isinstance(row, dict) or row.get("content_id") not in source_ids:
                raise cd.StageError("deterministic analysis content_id is absent from this snapshot")
        if any((output_dir / name).exists() or (output_dir / name).is_symlink() for name in
               ("account_review.json", "creator_profile.json", "ACCOUNT_REVIEW.md")):
            raise cd.StageError("a report already exists; verify it without replacing it")
        preflight_report_sources(analysis, snapshot, profile, stored)
        try:
            validate_authoring(analysis, synthesis)
        except AuthoringError as error:
            # 返回过 needs_revision 的 run，最终仍由 verify-report 复核。
            AUTHORING_CORRECTION_MARKER.write_text("needs_revision\n", encoding="utf-8")
            say(json.dumps({"status": "needs_revision", "report_written": False,
                            "issues": error.issues,
                            "next_action": "Correct only these authored fields in this same open run; then repeat this non-final report stage. Do not change source IDs or deterministic inputs."}, ensure_ascii=False))
            return
    profile_args = ["--profile-envelope", str(profile)] if profile else []
    growth_args = (["--growth-input", GROWTH_INPUT, "--growth-snapshot", SNAPSHOT,
                    "--growth-question-file", QUESTION] if Path(GROWTH_INPUT).exists() else [])
    check_args = ["--check-existing"] if getattr(args, "verify_existing", False) is True else []
    cd.script("compose_review.py", "--analysis", ANALYSIS, "--synthesis", SYNTHESIS, *profile_args,
              *growth_args, "--output-dir", str(output_dir), *check_args)
    cd.script("validate_artifacts.py", "--analysis", ANALYSIS, "--synthesis", SYNTHESIS,
              "--report", str(output_dir / "account_review.json"), "--profile", str(output_dir / "creator_profile.json"))
    cd.script("validate_creator_report.py", "--report", str(output_dir / "account_review.json"),
              "--synthesis", SYNTHESIS, "--markdown", str(output_dir / "ACCOUNT_REVIEW.md"),
              "--analysis", ANALYSIS, *growth_args,
              *[arg for path in stored for arg in ("--envelope", str(path))])
    report = (output_dir / "ACCOUNT_REVIEW.md").resolve()
    # 本 run 从未返回 needs_revision 时，这次 report 就是最终步：两个校验器刚在同一份字节上通过，
    # verify-report 再跑一遍只会重复 compose 和两次校验。
    final = (getattr(args, "allow_authoring_correction", False) is True
             and not AUTHORING_CORRECTION_MARKER.exists())
    if final:
        REPORT_FINAL_MARKER.write_text(json.dumps({"report": str(report), "sha256": hashlib.sha256(
            report.read_bytes()).hexdigest()}) + "\n", encoding="utf-8")
    say(f"creator_report: {report}")
    if final:
        say("[drive] this report is final: both validators passed. "
            "verify-report is not needed; deliver ACCOUNT_REVIEW.md now.")
    elif AUTHORING_CORRECTION_MARKER.exists() and getattr(args, "verify_existing", False) is not True:
        # 不含 needs_revision 字样：这里报告已通过校验，只是还差最终复核。
        say("[drive] an earlier report in this run asked for author corrections, so this one is "
            "non-final: run drive.py verify-report as the final step.")
    say(report.read_text(encoding="utf-8").rstrip())


def close_unwrapped_reservation() -> None:
    """A verified report run outside the wrapper still has to become a parent Deep can read.

    The wrapper writes this manifest when the final step goes through it, and a stage file means
    that close is still its job. A bare verify-report never creates one, so the reservation would
    stay running after a report that already passed.
    """
    directory = Path.cwd()
    manifest_path = directory / "run-manifest.json"
    if (directory / ".pipeline-stages.json").exists() or not manifest_path.is_file() or manifest_path.is_symlink():
        return
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("status") != "running" or record.get("finished_at") is not None:
        return
    artifacts = []
    for folder, kind in (("analysis", "analysis"), ("report", "report")):
        path = directory / folder
        if path.is_dir() and not path.is_symlink():
            for child in sorted(path.iterdir()):
                if child.is_file() and not child.is_symlink():
                    artifacts.append({"kind": kind, "path_or_uri": f"{folder}/{child.name}",
                                      "sha256": hashlib.sha256(child.read_bytes()).hexdigest()})
    for child in sorted(directory.iterdir()):
        if not child.is_file() or child.is_symlink() or child.name.startswith(".") or child.name == "run-manifest.json":
            continue
        payload = None
        if child.suffix == ".json":
            try:
                payload = json.loads(child.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                payload = None
        kind = "snapshot" if isinstance(payload, dict) and payload.get("connector_id") else "analysis"
        artifacts.append({"kind": kind, "path_or_uri": child.name,
                          "sha256": hashlib.sha256(child.read_bytes()).hexdigest()})
    finished = datetime.now(timezone.utc)
    try:
        started = datetime.fromisoformat(str(record["started_at"]).replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        started = finished
    record["status"] = "ok"
    record["finished_at"] = finished.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    record["duration_seconds"] = max(0, (finished - started).total_seconds())
    record["artifacts"] = artifacts
    manifest_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stage_verify_report(args: argparse.Namespace) -> None:
    """Final host close: recompute existing bytes and run both unchanged strict validators."""
    args.verify_existing = True
    args.allow_authoring_correction = False
    # report 尚未生成时先停下，不把可修正的作者错误变成最终步骤失败；什么都不读不写。
    output_dir = Path(getattr(args, "output_dir", None) or OUTPUT_DIR)
    if not (output_dir / "ACCOUNT_REVIEW.md").is_file():
        raise cd.StageError(
            f"verify-report found no composed report in {output_dir}/; nothing was read or written. "
            "Run the non-final `drive.py report --allow-authoring-correction` stage in this same run "
            "first, and verify only after it prints the validated report")
    # 幂等：report 已作为最终步（从未返回 needs_revision），且报告字节未变时不重跑 compose 和校验器。
    # 标记缺失、不是普通文件或哈希不符时走下面的严格路径。
    report = (output_dir / "ACCOUNT_REVIEW.md").resolve()
    try:
        record = (json.loads(REPORT_FINAL_MARKER.read_text(encoding="utf-8"))
                  if REPORT_FINAL_MARKER.is_file() and not REPORT_FINAL_MARKER.is_symlink() else None)
    except (OSError, UnicodeError, ValueError):
        record = None
    if (isinstance(record, dict) and record.get("report") == str(report) and not report.is_symlink()
            and record.get("sha256") == hashlib.sha256(report.read_bytes()).hexdigest()):
        say(f"creator_report: {report}")
        say("[drive] report already finalized this run; nothing was re-run. "
            "Deliver ACCOUNT_REVIEW.md as printed by report.")
        return
    stage_report(args)
    close_unwrapped_reservation()


STAGES = {"fetch": stage_fetch, "growth": stage_growth, "report": stage_report,
          "verify-report": stage_verify_report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="stage", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--platform", choices=["instagram", "youtube", "tiktok"], required=True)
    fetch.add_argument("--handle", help="the account's public handle, as verified for the user's request")
    fetch.add_argument("--channel-id", help="YouTube only: the UC… channel id, instead of --handle")
    fetch.add_argument("--window", default="auto",
                       help="<start>/<end> when the user asked for a window; auto otherwise")
    fetch.add_argument("--question-file", help="- to read the user's original request, verbatim, from standard input; or a file holding it")
    fetch.add_argument("--question", help="the user's original request, verbatim (prefer --question-file)")
    fetch.add_argument("--linked-evidence", help="exact host-produced linked-media-evidence.v1 file for the requested TikTok post")
    growth = sub.add_parser("growth")
    growth.add_argument("--language", default="en", help="output language of the synthesis, as a BCP-47 tag")
    growth.add_argument("--metric", choices=["views", "likes", "comments"], default="views",
                        help="the content metric the user asked about")
    growth.add_argument("--horizon-days", type=float, default=30, help="the user's publication window in days")
    report = sub.add_parser("report")
    report.add_argument("--output-dir", type=Path, default=OUTPUT_DIR,
                        help="validated report directory inside this run (default: output)")
    report.add_argument("--allow-authoring-correction", action="store_true",
                        help="non-final stage only: return needs_revision without a report for author-shape/ID errors")
    verify = sub.add_parser("verify-report")
    verify.add_argument("--output-dir", type=Path, default=OUTPUT_DIR,
                        help="read-only verification of the existing validated report directory")
    for command in (fetch, growth, report, verify):
        command.add_argument("--placed", action="store_true",
                             help="the connector calls were already made by hand; place their responses")
    return cd.run_stage(STAGES, parser.parse_args(), first=("fetch",), model_files=(SYNTHESIS, GROWTH_INPUT, "question.txt"))


if __name__ == "__main__":
    raise SystemExit(main())
