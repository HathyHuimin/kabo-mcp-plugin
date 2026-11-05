#!/usr/bin/env python3
"""Reserve one local run directory per request for this Skill; no network or model loop."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
SKILL = Path(__file__).resolve().parent.parent
# 本 Skill 不调用任何 /mcp-pub 数据工具，没有需要 stage 的工具结果，所以只保留 init；
# 目录布局与其他 Skill 的 init 保持一致，便于在同一工作区里辨认各次运行。


def init(workspace):
    base = Path(workspace).resolve(strict=True) / ".kabo-runs"
    if SKILL == base or SKILL in base.parents:
        raise ValueError("Runs must be outside the installed Skill")
    base.mkdir(mode=0o700, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(6)
    run = base / name
    run.mkdir(mode=0o700)
    for name in ("snapshot", "analysis", "report", "owner"):
        (run / name).mkdir(mode=0o700)
    (run / "snapshot/.host-evidence").mkdir(mode=0o700)
    write_new(run / ".static-run.json", json.dumps({"format": "kabo.static-run.v1", "skill": SKILL.name}).encode())
    return run


def write_new(path, body):
    # 独占写入，重试和并发不能覆盖已有证据。
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init").add_argument("--workspace", required=True)
    args = parser.parse_args()
    print(init(args.workspace))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print("Static run operation failed: " + str(error), file=sys.stderr)
        sys.exit(1)
