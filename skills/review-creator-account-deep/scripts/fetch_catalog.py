#!/usr/bin/env python3
"""Fetch a one-page catalog sample inside this deep run when no finished standard review exists.

The whole sample is one backend call, `collect_account_history` (KS9): Instagram `resolve_profile` +
the first Feed page + the first Reels page, YouTube `get_channel` + the latest 50 uploads, TikTok one
`list_account_posts` request (at most `CATALOG_CALL_CAP` catalog calls; the backend retries a briefly
failed page once). Each run prints one JSON object:

- `needs_calls`: the one call to make and `rerun` (this command with `--placed --id <id>`), which
  fetches the result by its result id (or, for a result with no download url, from the fallback
  file `save_as`), places each call as
  `catalog/NN-<operation>.json` in issue order, assembles the analysis and then runs `watch_media.py`
  on it, so its output ends with the watch hand-over (workflow step 2).
- `ok`: `account_analysis.json` is ready in this run.
- `stopped`: the account cannot be reviewed here; tell the user the message and stop.

`--workspace <dir>` reserves this request's run directory first (the same as `static_host.py init`),
so reserving the run and handing over the catalog call are one command. A run this Skill reserved in
the same workspace within the last 30 minutes and still empty is taken instead of reserving another. `--call-failed` records that
the call itself failed twice without returning a result. When this run already holds
`account_analysis.json`, nothing is fetched.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from persist_envelope import USABLE_STATUSES, row_count  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
CATALOG = Path("catalog")
HOST_RESULTS = Path("host-results")
ANALYSIS = Path("account_analysis.json")
SNAPSHOT = Path("catalog_snapshot.json")
IG, YT, TT = "instagram-discovery", "youtube-public", "tiktok-public"
COMPOSITE_TOOL = "collect_account_history"
CONTENT = {"instagram": ("list_posts", "list_reels"), "youtube": ("list_channel_uploads",),
           "tiktok": ("list_account_posts",)}
YOUTUBE_PAGE, TIKTOK_LIMIT = 50, 100
# 目录阶段的组合调用在后台最多发几次底层调用：IG 3 次 + 1 次重试；YT 2 次；TT 1 次。其余额度留给看片。
CATALOG_CALL_CAP = 4
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


def history_call(platform: str, handle: str, channel_id: str | None) -> dict[str, Any]:
    """一页样本的组合调用：IG 资料页 + Feed 首页 + Reels 首页，YT 频道 + 最新 50 条，TT 一次 100 条。"""
    if platform == "instagram":
        history: dict[str, Any] = {"max_items": 100, "max_pages": len(CONTENT["instagram"]),
                                   "instagram_formats": {"posts": True, "reels": True}}
        account: dict[str, Any] = {"handle": handle}
    elif platform == "youtube":
        history = {"max_items": YOUTUBE_PAGE, "max_pages": 1, "page_size": YOUTUBE_PAGE}
        account = {"channel_id": channel_id} if channel_id else {"handle": "@" + handle}
    else:
        history = {"max_items": TIKTOK_LIMIT}
        account = {"handle": handle}
    return {"tool": COMPOSITE_TOOL, "arguments": {"platform": platform, "accounts": [account], "profile": True,
                                                   "history": history, "on_identity_miss": "gap",
                                                   "delivery": "download"}}


def take_saved(path: Path, result_id: str | None = None) -> None:
    """按结果编号取组合结果（没有编号时读退路文件，没有就绪下载时 stage）；宿主已自己落过盘时照用已落的那份。"""
    import static_host
    if result_id and not static_host.composite_records(COMPOSITE_TOOL):
        try:
            static_host.fetch_id(COMPOSITE_TOOL, result_id)
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise ValueError(f"--id {result_id} gave no usable {COMPOSITE_TOOL} result ({error}). Run rerun again "
                             "with the id from that call's download url") from None
        return
    if not path.is_file():
        if static_host.composite_records(COMPOSITE_TOOL):
            return
        raise ValueError(f"no {COMPOSITE_TOOL} result: run rerun again with --id <id>, the id from that call's "
                         f"download url (only for a result with no download url, save it whole at {path} and "
                         "leave --id out)")
    raw = path.read_bytes()
    result = static_host.fetch(COMPOSITE_TOOL, raw)
    if result.get("fetched") == 0:
        static_host.stage(COMPOSITE_TOOL, raw)


def place_composite() -> None:
    """把最近一份组合结果按调用顺序逐字节写成 catalog/NN-<operation>.json；已放过就不再放。"""
    import static_host
    if numbered():
        return
    records = static_host.composite_records(COMPOSITE_TOOL)
    if not records:
        raise ValueError(f"no {COMPOSITE_TOOL} result was saved in this run; make the printed call first")
    CATALOG.mkdir(exist_ok=True)
    for number, call in enumerate(records[-1]["calls"], start=1):
        body = Path(call["path"]).read_bytes()
        operation = json.loads(body).get("operation") or str(call["tool"]).split("_", 1)[-1]
        (CATALOG / f"{number:02d}-{operation}.json").write_bytes(body)


def profile_check(platform: str, label: str, rows: list[dict[str, Any]]) -> None:
    """资料页（IG / YT）拿不到：不存在如实说；其余说明不了账号在不在。"""
    if platform == "tiktok":
        current = state(rows, "list_account_posts")
        if current["page"] is None and current["not_found"]:
            raise Stop("account_not_found", NOT_FOUND_MESSAGE.format(label=label))
        return
    operation = "resolve_profile" if platform == "instagram" else "get_channel"
    current = state(rows, operation)
    if current["page"] is None or resolved(platform, current["page"]) is None:
        if current["quota"]:
            raise Stop("quota_exhausted", QUOTA_MESSAGE.format(what="the account profile"))
        if current["page"] is not None or current["not_found"]:
            raise Stop("account_not_found", NOT_FOUND_MESSAGE.format(label=label))
        raise Stop("service_failed", f"The data service could not return {operation}. Tell the user the account "
                                     "cannot be fetched right now and stop; do not guess whether it exists.")


# 实测（1.0.14 回归）：模型先跑了 static_host.py init、记了一次 start_skill_run，又跑 --workspace 再预留
# 一个 run，于是同一个请求按两个 run_id 记了两次用量。--workspace 先认领本 workspace 里刚预留、
# 还没放进任何东西的本 Skill run，没有才新建；一个请求始终只有一个 run。
FRESH_RUN_SECONDS = 30 * 60


def untouched_run(workspace: str) -> Path | None:
    import time
    base = Path(workspace).resolve() / ".kabo-runs"
    if not base.is_dir():
        return None
    for run in sorted(base.iterdir(), reverse=True):
        marker = run / ".static-run.json"
        if run.is_symlink() or not marker.is_file() or time.time() - marker.stat().st_mtime > FRESH_RUN_SECONDS:
            continue
        try:
            if json.loads(marker.read_text(encoding="utf-8")).get("skill") != SCRIPTS.parent.name:
                continue
        except (OSError, ValueError):
            continue
        # 只有 init 建的空目录与标记文件：任何已落盘的文件都说明这个 run 已经开始，不能认领。
        if any(path.is_file() and path != marker for path in run.rglob("*")):
            return None
        return run
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--platform", choices=sorted(CONTENT), required=True)
    parser.add_argument("--account", default="", help="the account the user named (with or without @)")
    parser.add_argument("--channel-id", help="YouTube only: the UC… channel id, instead of --account")
    parser.add_argument("--workspace", help="reserve this request's run directory under this workspace first")
    parser.add_argument("--placed", action="store_true", help="the printed call was made")
    parser.add_argument("--id", dest="result_id", help="the result id of the printed call")
    parser.add_argument("--call-failed", action="store_true",
                        help="the printed call failed twice without returning any result")
    # 目录就绪后直接接着跑 watch_media.py（中间没有判断），把看片的交接一并打印。
    parser.add_argument("--output-language", choices=("en", "zh-CN"), default="en")
    parser.add_argument("--cap", type=int, default=10)
    parser.add_argument("--named-content-id", action="append", default=[])
    args = parser.parse_args(argv)
    handle = args.account.strip().lstrip("@")
    if not handle and not (args.platform == "youtube" and args.channel_id):
        parser.error("--account is required (YouTube also accepts --channel-id)")
    try:
        if args.workspace and not os.environ.get("KABO_STATIC_RUN_DIR"):
            import static_host
            run_directory = untouched_run(args.workspace) or static_host.init(args.workspace)
            os.chdir(run_directory)
            os.environ["KABO_STATIC_RUN_DIR"] = str(run_directory)
        if ANALYSIS.exists():
            print(json.dumps({"status": "ok", "account_analysis": str(ANALYSIS.resolve()), "reused": True,
                              "next_action": "This run already holds account_analysis.json; nothing was fetched."},
                             ensure_ascii=False))
            return 0
        label = args.channel_id or ("@" + handle)
        saved = (Path.cwd() / HOST_RESULTS / f"catalog-01-{COMPOSITE_TOOL}.json").resolve()
        if args.placed or args.call_failed:
            if args.call_failed and not numbered():
                import static_host
                if not static_host.composite_records(COMPOSITE_TOOL):
                    raise Stop("service_failed", "The data service did not answer the catalog call. Tell the user "
                                                 "the account cannot be fetched right now and stop; do not guess "
                                                 "whether it exists.")
            if not args.call_failed:
                take_saved(saved, args.result_id)
            place_composite()
            rows = placed()
            profile_check(args.platform, label, rows)
            print(json.dumps(assemble(args.platform, handle, rows, False), ensure_ascii=False, indent=2))
            sys.stdout.flush()
            watch = [sys.executable, "-B", str(SCRIPTS / "watch_media.py"), str(ANALYSIS), "--cap", str(args.cap),
                     "--output-language", args.output_language,
                     *[part for content_id in args.named_content_id for part in ("--named-content-id", content_id)]]
            return subprocess.run(watch).returncode
    except Stop as stop:
        print(json.dumps({"status": "stopped", "reason": stop.reason, "message": str(stop)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"catalog fetch failed: {error}", file=sys.stderr)
        return 1
    HOST_RESULTS.mkdir(exist_ok=True)
    rerun = [script("fetch_catalog.py"), "--platform", args.platform, "--output-language", args.output_language,
             "--cap", str(args.cap)]
    rerun += ["--channel-id", quote(args.channel_id)] if args.channel_id else []
    rerun += ["--account", quote(args.account.strip())] if args.account.strip() else []
    rerun += [part for content_id in args.named_content_id for part in ("--named-content-id", quote(content_id))]
    print(json.dumps({"status": "needs_calls", "run_directory": str(Path.cwd().resolve()),
                      "call": history_call(args.platform, handle, args.channel_id),
                      "save_as": str(saved),
                      "rerun": " ".join([*rerun, "--placed", "--id", "<id>"]),
                      "if_the_call_failed": " ".join([*rerun, "--call-failed"]),
                      "next_action": "Make this one call with exactly its arguments. Do not save its result: its "
                                     "download url holds its result id, the 22 characters between "
                                     "`research-export/v1/` and `.json`; run rerun with <id> replaced by that id. "
                                     "Only if the result has no download url, save it whole as save_as (with your "
                                     "file-write tool) and drop `--id <id>` from rerun. Never download anything "
                                     "yourself. "
                                     "If the call itself fails without returning a result, make it once more; if it "
                                     "fails again, run if_the_call_failed instead. Never write or edit catalog/ files. "
                                     "Every later command in this run uses run_directory."},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
