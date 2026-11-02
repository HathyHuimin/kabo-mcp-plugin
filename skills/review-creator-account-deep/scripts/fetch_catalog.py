#!/usr/bin/env python3
"""Fetch a one-page catalog sample inside this deep run when no finished standard review exists.

State comes only from what this run placed under `catalog/`. Each run prints one JSON object:

- `needs_calls`: the next public MCP calls, each with the file to save its complete result in, the
  `static_host.py fetch` command for that file, and the `persist_many.py` commands that place the
  fetched responses as `catalog/NN-<operation>.json` in issue order. Run them, then run this same
  command again.
- `ok`: `account_analysis.json` is ready in this run.
- `stopped`: the account cannot be reviewed here; tell the user the message and stop.

The sample is one page per format: Instagram `resolve_profile` + the first Feed page + the first
Reels page, YouTube `get_channel` + the latest 50 uploads, TikTok one `list_account_posts` request.
A run makes at most `CATALOG_CALL_CAP` catalog calls, failures included. A failed page is retried
once within that cap; a failed Instagram Feed page is not (Feed rows are not watchable).

`--failed <operation>` records a call that returned no record as `catalog/NN-<operation>.failed`;
`--budget-exhausted` issues no further call and assembles the pages already placed. When this run
already holds `account_analysis.json`, nothing is fetched.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from persist_envelope import USABLE_STATUSES, row_count  # noqa: E402
from static_host import CONNECTOR_TOOLS  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
CATALOG = Path("catalog")
EXHAUSTED_MARK = CATALOG / "budget-exhausted"
# 上次打印的请求及其发出编号；--failed 按它把失败记到原编号，重复传同一个 --failed 不会多记一次。
ISSUED = CATALOG / "issued.json"
HOST_FAILED = "host_call_failed"
HOST_RESULTS = Path("host-results")
ANALYSIS = Path("account_analysis.json")
SNAPSHOT = Path("catalog_snapshot.json")
IG, YT, TT = "instagram-discovery", "youtube-public", "tiktok-public"
CONTENT = {"instagram": ("list_posts", "list_reels"), "youtube": ("list_channel_uploads",),
           "tiktok": ("list_account_posts",)}
YOUTUBE_PAGE, TIKTOK_LIMIT = 50, 100
# 每次运行目录阶段最多发几次调用（失败也算）：IG 3 次 + 1 次重试；YT 2 次；TT 1 次。其余调用留给看片。
CATALOG_CALL_CAP = 4
# 每页最多失败几次。Feed 行不可看片（深度版只看 Reels），失败一次就用 Reels 组装，不再为它花调用。
MAX_FAILURES = {"list_posts": 1}
NOT_FOUND = {"not_found", "validation_error"}
QUOTA = "quota_exceeded"
SAMPLE = {
    "instagram": "the first Feed page and the first Reels page",
    "youtube": f"the latest {YOUTUBE_PAGE} uploads (one page)",
    "tiktok": f"one list_account_posts request (limit {TIKTOK_LIMIT})",
}
QUOTA_MESSAGE = ("The data quota was used up before {what} was fetched, so there is no catalog to analyze. "
                 "Tell the user the retrieval quota is used up and to try again later; do not call it a data "
                 "service failure and do not guess whether the account exists.")
NOT_FOUND_MESSAGE = ("{label} did not resolve to a public account. Confirm the exact account with the user; do not "
                     "review any other account in its place.")


class Stop(Exception):
    """取数无法继续：账号不存在、额度用完或数据服务两次失败；告诉用户并停止，不换账号。"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def quote(text: str | Path) -> str:
    return "'" + str(text).replace("'", "'\\''") + "'"


def prefix() -> str:
    """SKILL.md 的命令形式：先 cd 到本 run，再带 KABO_STATIC_RUN_DIR。"""
    run = quote(Path.cwd().resolve())
    return f"cd {run} && KABO_STATIC_RUN_DIR={run} python3 "


def script(name: str) -> str:
    return prefix() + quote(SCRIPTS / name)


def numbered() -> list[Path]:
    """catalog/NN-<operation>.json 与 NN-<operation>.failed，按 NN（发出顺序）排列。"""
    if not CATALOG.is_dir():
        return []
    return sorted([*CATALOG.glob("[0-9][0-9]-*.json"), *CATALOG.glob("[0-9][0-9]-*.failed")], key=lambda p: p.name)


def placed() -> list[dict[str, Any]]:
    """已落盘的信封与宿主失败标记；标记是一条不可用的失败行，和失败信封一样计数。"""
    rows = []
    for path in numbered():
        envelope = json.loads(path.read_text(encoding="utf-8"))
        rows.append({"file": path, "operation": path.stem.split("-", 1)[1], "envelope": envelope,
                     "usable": path.suffix == ".json" and envelope.get("status") in USABLE_STATUSES})
    return rows


def mark_failed(operations: list[str]) -> list[str]:
    """把失败且没有记录可落盘的调用记成 catalog/NN-<operation>.failed，返回实际新记的文件。

    编号取上次打印的发出编号（与 persist 的 --dest 相同）；该编号已有信封或标记时不再记，所以
    重跑时照抄同一个 --failed 不会把一次失败算成两次。没有打印记录时取最小的空编号。"""
    if not operations:
        return []
    CATALOG.mkdir(exist_ok=True)
    issued = json.loads(ISSUED.read_text(encoding="utf-8")) if ISSUED.exists() else []
    taken = {int(path.name[:2]) for path in numbered()}
    written = []
    for operation in operations:
        slots = [item["number"] for item in issued if item["operation"] == operation]
        if issued and not slots:
            # 上次打印的请求里没有它（多半是重跑时照抄了旧的 --failed）：不记，免得多算一次失败。
            print(f"--failed {operation} ignored: the last printed requests did not include it", file=sys.stderr)
            continue
        if slots and all(number in taken for number in slots):
            continue  # 这次发出已有信封或标记：重复的 --failed，不再计数
        number = next(n for n in slots if n not in taken) if slots else \
            next(n for n in range(1, 100) if n not in taken)
        target = CATALOG / f"{number:02d}-{operation}.failed"
        target.write_text(json.dumps({"operation": operation, "status": "failed", "error_code": HOST_FAILED,
                                      "note": "the call failed without returning a record"},
                                     ensure_ascii=False) + "\n", encoding="utf-8")
        taken.add(number)
        written.append(str(target))
    return written


def terminal(envelope: dict[str, Any]) -> bool:
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    if data.get("more_available") is False:
        return True
    coverage = data.get("coverage")
    return envelope.get("operation") == "list_channel_uploads" and isinstance(coverage, dict) \
        and "next_page_token" in coverage and not str(coverage.get("next_page_token") or "").strip()


def state(rows: list[dict[str, Any]], operation: str) -> dict[str, Any]:
    """一个 operation 的唯一一页：成功信封、失败次数、是否额度用完或账号不存在。"""
    mine = [row for row in rows if row["operation"] == operation]
    good = next((row["envelope"] for row in mine if row["usable"]), None)
    codes = [row["envelope"].get("error_code") for row in mine if not row["usable"]]
    return {"page": good, "failures": len(codes), "quota": QUOTA in codes,
            "not_found": good is None and any(code in NOT_FOUND for code in codes)}


def request(platform: str, operation: str, handle: str, channel_id: str | None,
            identity: dict[str, Any] | None) -> dict[str, Any]:
    if platform == "instagram":
        params: dict[str, Any] = {"handle": handle}
        if operation == "resolve_profile":
            params["include_post_count"] = True
        elif operation == "list_reels" and identity and identity.get("id"):
            params["user_id"] = str(identity["id"])
        return {"connector_id": IG, "operation": operation, "params": params}
    if platform == "youtube":
        if operation == "get_channel":
            return {"connector_id": YT, "operation": operation,
                    "params": {"channel_id": channel_id} if channel_id else {"handle": "@" + handle}}
        return {"connector_id": YT, "operation": operation,
                "params": {"channel_id": str((identity or {}).get("id")), "max_results": YOUTUBE_PAGE}}
    return {"connector_id": TT, "operation": operation, "params": {"handle": "@" + handle, "limit": TIKTOK_LIMIT}}


def resolved(platform: str, page: dict[str, Any]) -> dict[str, Any] | None:
    data = page.get("data") if isinstance(page.get("data"), dict) else {}
    if platform == "instagram":
        profile = data.get("profile")
        if isinstance(profile, dict) and (profile.get("username") or profile.get("id")):
            return {"id": profile.get("id") or profile.get("pk") or profile.get("user_id")}
        return None
    channels = data.get("rows") or []
    channel = channels[0] if channels and isinstance(channels[0], dict) else {}
    return {"id": channel["id"]} if channel.get("id") else None


def plan(platform: str, handle: str, channel_id: str | None, rows: list[dict[str, Any]],
         exhausted: bool) -> list[dict[str, Any]]:
    """下一轮要发的请求；空列表表示该组装了（取满，或额度/上限已到）。"""
    exhausted = exhausted or len(rows) >= CATALOG_CALL_CAP or any(
        row["envelope"].get("error_code") == QUOTA for row in rows)
    room = CATALOG_CALL_CAP - len(rows)
    label = channel_id or ("@" + handle)
    identity = None
    if platform != "tiktok":
        operation = "resolve_profile" if platform == "instagram" else "get_channel"
        current = state(rows, operation)
        if current["page"] is None:
            if current["not_found"]:
                raise Stop("account_not_found", NOT_FOUND_MESSAGE.format(label=label))
            if exhausted:
                raise Stop("quota_exhausted", QUOTA_MESSAGE.format(what="the account profile"))
            if current["failures"] >= 2:
                raise Stop("service_failed", f"The data service failed {operation} twice. Tell the user the account "
                                             "cannot be fetched right now and stop; do not guess whether it exists.")
            return [request(platform, operation, handle, channel_id, None)]
        identity = resolved(platform, current["page"])
        if identity is None:
            raise Stop("account_not_found", NOT_FOUND_MESSAGE.format(label=label))
    pending = []
    for operation in CONTENT[platform]:
        current = state(rows, operation)
        if current["not_found"] and platform == "tiktok":
            raise Stop("account_not_found", NOT_FOUND_MESSAGE.format(label=label))
        if current["page"] is None and current["failures"] < MAX_FAILURES.get(operation, 2) \
                and not current["not_found"]:
            pending.append(request(platform, operation, handle, channel_id, identity))
    return [] if exhausted else pending[:max(room, 0)]


def assemble(platform: str, handle: str, rows: list[dict[str, Any]], exhausted: bool) -> dict[str, Any]:
    """用随包的 build_public_snapshot.py 与 account_analyzers.py 组装；缺页照组装，覆盖标记如实为不完整。"""
    pages = {op: state(rows, op) for op in CONTENT[platform]}
    kept = [row for row in rows if row["usable"]]
    combined = sum(row_count(p["page"]) or 0 for p in pages.values() if p["page"] is not None)
    quota = exhausted or any(row["envelope"].get("error_code") == QUOTA for row in rows)
    missing = [op for op, p in pages.items() if p["page"] is None]
    if not combined:
        if quota:
            raise Stop("quota_exhausted", QUOTA_MESSAGE.format(what="any catalog page"))
        if any(p["failures"] for p in pages.values()):
            raise Stop("service_failed", "The data service returned no usable catalog page. Tell the user the "
                                         "account's catalog cannot be fetched right now and stop; do not guess "
                                         "whether the account exists.")
        raise Stop("no_public_rows", "The account returned no public posts (private, empty or unavailable). Tell "
                                     "the user no public posts could be fetched and stop.")
    complete = [op for op, p in pages.items() if p["page"] is not None and terminal(p["page"])]
    flags = ([] if len(complete) == len(pages) else ["--coverage-incomplete"]) + \
        [arg for op in complete for arg in ("--complete-operation", op)]
    note = (f"Standalone deep catalog: only {SAMPLE[platform]} were fetched, at most {CATALOG_CALL_CAP} catalog "
            f"calls in this run ({combined} rows), a smaller sample than the standard review's up-to-100-row catalog.")
    if missing:
        note += f" Not retrieved: {', '.join(missing)} ({'retrieval budget used up' if quota else 'service failed'})."
    envelopes = [arg for row in kept for arg in ("--envelope", str(row["file"]))]
    run([str(SCRIPTS / "build_public_snapshot.py"), "--platform", platform, "--window", "auto", *envelopes, *flags,
         "--output", str(SNAPSHOT)])
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    # 样本缩小写进 source.limitations，由 account_analyzers 带到分析的 source_coverage.limitations。
    snapshot.setdefault("source", {}).setdefault("limitations", []).append(note)
    SNAPSHOT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    creators = [c for c in snapshot.get("creators") or [] if c.get("handle")]
    focus = next((str(c["handle"]) for c in creators if str(c["handle"]).lstrip("@").lower() == handle.lower()),
                 str(creators[0]["handle"]) if len(creators) == 1 else handle)
    run([str(SCRIPTS / "account_analyzers.py"), str(SNAPSHOT), "--operation", "all", "--focus-handle", focus,
         "--output", str(ANALYSIS)])
    return {"status": "ok", "account_analysis": str(ANALYSIS.resolve()), "rows": combined, "catalog_calls": len(rows),
            "catalog_call_cap": CATALOG_CALL_CAP, "snapshot_flags": flags, "sample_note": note,
            "next_action": "The catalog is ready: continue SKILL.md step 1 with this run's account_analysis.json. "
                           "There is no standard report on this path: quote only the scripted catalog numbers, "
                           "and put sample_note verbatim in other_limitations."}


def run(command: list[str]) -> None:
    done = subprocess.run([sys.executable, "-B", *command], capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed: {(done.stderr or done.stdout).strip()[-600:]}")


def public_call(item: dict[str, Any], number: int) -> dict[str, Any]:
    """一条请求对应一次具名 /mcp-pub 调用，完整结果存进 host-results/，再由 static_host.py fetch 落进暂存区。"""
    tool = CONNECTOR_TOOLS[f"{item['connector_id']}/{item['operation']}"]
    # catalog- 前缀：看片阶段的 host-results/NN-<tool>.json 从 01 起编号，两套编号不会撞名。
    save_as = HOST_RESULTS / f"catalog-{number:02d}-{tool}.json"
    return {"operation": item["operation"], "tool": tool,
            "arguments": {"parameters": item["params"], "delivery": "download"},
            "save_as": save_as.as_posix(),
            "fetch": f"{script('static_host.py')} fetch --tool {tool} --input {quote(save_as.as_posix())}"}


def persist_commands(requests: list[dict[str, Any]], start: int, handle: str, platform: str) -> list[str]:
    """每种 operation 一条 persist_many.py；--dest 编号即发出顺序，不按返回先后。"""
    commands, number = [], start
    for operation in dict.fromkeys(item["operation"] for item in requests):
        dests = []
        for item in requests:
            if item["operation"] == operation:
                dests += ["--dest", f"{CATALOG}/{number:02d}-{operation}.json"]
                number += 1
        bound = ["--handle", handle] * (len(dests) // 2) if platform != "youtube" else []
        commands.append(" ".join([script("persist_many.py"), "--connector", requests[0]["connector_id"],
                                  "--operation", operation, "--any-status", *dests, *bound]))
    return commands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--platform", choices=sorted(CONTENT), required=True)
    parser.add_argument("--account", default="", help="the account the user named (with or without @)")
    parser.add_argument("--channel-id", help="YouTube only: the UC… channel id, instead of --account")
    parser.add_argument("--budget-exhausted", action="store_true",
                        help="a call was refused because the data quota is used up and returned no record: issue "
                             "no further call and assemble the pages already placed")
    parser.add_argument("--failed", action="append", default=[], metavar="OPERATION",
                        help="this call failed without returning any record: skip its persist and rerun with this "
                             "flag (repeatable)")
    args = parser.parse_args(argv)
    handle = args.account.strip().lstrip("@")
    if not handle and not (args.platform == "youtube" and args.channel_id):
        parser.error("--account is required (YouTube also accepts --channel-id)")
    if ANALYSIS.exists():
        print(json.dumps({"status": "ok", "account_analysis": str(ANALYSIS.resolve()), "reused": True,
                          "next_action": "This run already holds account_analysis.json; nothing was fetched."},
                         ensure_ascii=False))
        return 0
    try:
        if args.budget_exhausted:
            CATALOG.mkdir(exist_ok=True)
            EXHAUSTED_MARK.touch()
        mark_failed(args.failed)
        exhausted = EXHAUSTED_MARK.exists()
        rows = placed()
        requests = plan(args.platform, handle, args.channel_id, rows, exhausted)
        if not requests:
            print(json.dumps(assemble(args.platform, handle, rows, exhausted), ensure_ascii=False, indent=2))
            return 0
    except Stop as stop:
        print(json.dumps({"status": "stopped", "reason": stop.reason, "message": str(stop)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"catalog fetch failed: {error}", file=sys.stderr)
        return 1
    start = len(rows) + 1
    ISSUED.parent.mkdir(exist_ok=True)
    ISSUED.write_text(json.dumps([{"number": start + i, "operation": item["operation"]}
                                  for i, item in enumerate(requests)]) + "\n", encoding="utf-8")
    HOST_RESULTS.mkdir(exist_ok=True)
    rerun = [script("fetch_catalog.py"), "--platform", args.platform]
    rerun += ["--channel-id", quote(args.channel_id)] if args.channel_id else []
    rerun += ["--account", quote(args.account.strip())] if args.account.strip() else []
    print(json.dumps({"status": "needs_calls", "catalog_calls": len(rows), "catalog_call_cap": CATALOG_CALL_CAP,
                      "requests": [public_call(item, start + i) for i, item in enumerate(requests)],
                      "persist": persist_commands(requests, start, handle, args.platform),
                      "rerun": " ".join(rerun),
                      "if_a_call_failed": " ".join([*rerun, "--failed", "<operation>"]),
                      "if_quota_used_up": " ".join([*rerun, "--budget-exhausted"]),
                      "next_action": "Make each request as its own individual call to its tool with exactly its "
                                     "arguments, save each complete result as its save_as file (it is a small download "
                                     "result: write the text the call returned with a heredoc; every call counts toward "
                                     "catalog_call_cap, so never call a tool again just to save its result) and run its fetch "
                                     "command (static_host.py stage on the same file when fetch reports no ready "
                                     "download). Then run every persist command in order and run rerun. If a call "
                                     "failed without returning any record (fetch and stage refuse it as an error with "
                                     "no terminal evidence), do not retry it yourself and skip its persist command: "
                                     "run if_a_call_failed (--failed <operation>) instead of rerun; this script decides "
                                     "any retry. If a call was refused because the data quota is used up and nothing "
                                     "could be saved, run if_quota_used_up (--budget-exhausted) instead. Never write or edit catalog/ files."},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
