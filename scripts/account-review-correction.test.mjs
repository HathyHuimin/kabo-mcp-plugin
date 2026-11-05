// review-creator-account 的改稿补救路径（1.0.14 生产回归暴露）：`drive.py report --allow-authoring-correction`
// 里 compose 已写出报告、校验器才拒收（例：Markdown 露出内部实现词）。旧行为把未过校验的报告留在
// report/，改完重跑撞上「a report already exists」，verify-report 又因 synthesis 已改对不上，run 只能失败。
// 现在：拒收时删掉未过校验的报告、记下改稿标记，同一条命令改完重跑即可出报告（最终仍走 verify-report）。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const scripts = path.join(root, "skills", "review-creator-account", "scripts");

// 只替换 report 阶段之外的部分（预留检查、来源预检、子脚本），report 阶段本身原样运行。
const harness = String.raw`
import json, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import drive, connector_driver as cd

Path(drive.ANALYSIS).write_text(json.dumps({"snapshot_id": "s1", "rankings": [{"content_id": "c1"}]}))
Path(drive.SNAPSHOT).write_text(json.dumps({"schema_version": "public-content-snapshot.v1", "snapshot_id": "s1",
                                            "items": [{"content_id": "c1"}]}))
Path(drive.SYNTHESIS).write_text("{}")
drive.require_open_reservation = lambda directory: None
drive.preflight_report_sources = lambda *a: None
drive.validate_authoring = lambda *a: None
cd.state = lambda: {"envelopes": [{"file": "snapshot/envelope-01.json"}]}
attempts = {"validator": 0}

def script(name, *args, show=False):
    out = Path(args[args.index("--output-dir") + 1]) if "--output-dir" in args else Path("report")
    if name == "compose_review.py":
        out.mkdir(exist_ok=True)
        for file in ("account_review.json", "creator_profile.json", "ACCOUNT_REVIEW.md"):
            (out / file).write_text("draft")
    if name == "validate_creator_report.py":
        attempts["validator"] += 1
        if attempts["validator"] == 1:
            raise cd.StageError("validate_creator_report.py failed:\nexposes internal implementation details: ['provider']")
    return ""
cd.script = script

args = SimpleNamespace(output_dir="report", allow_authoring_correction=True)
try:
    drive.stage_report(args)
    print("FIRST: no error")
except cd.StageError as error:
    print("FIRST:", str(error).replace("\n", " "))
print("LEFT:", sorted(p.name for p in Path("report").glob("*")))
print("MARKER:", drive.AUTHORING_CORRECTION_MARKER.exists())
drive.stage_report(args)
print("FINAL:", drive.REPORT_FINAL_MARKER.exists())
`;

test("改稿后同一条 report 命令能出报告：未过校验的报告被删掉，最终仍由 verify-report 复核", async (t) => {
  const run = await mkdtemp(path.join(os.tmpdir(), "kabo-acct-correction-"));
  t.after(() => rm(run, { recursive: true, force: true }));
  const result = spawnSync("python3", ["-c", harness, scripts], {
    cwd: run, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", KABO_STATIC_RUN_DIR: run },
  });
  const out = result.stdout + result.stderr;
  assert.equal(result.status, 0, out);
  assert.match(out, /FIRST: .*internal implementation details.*The unvalidated report was removed/);
  assert.match(out, /LEFT: \[\]/);
  assert.match(out, /MARKER: True/);
  assert.match(out, /creator_report: /);
  assert.match(out, /run drive\.py verify-report as the final step/);
  assert.match(out, /FINAL: False/);
});
