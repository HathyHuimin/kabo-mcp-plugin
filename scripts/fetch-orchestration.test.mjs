// 取数编排在后台闭环（docs/skills-spec.md KS9）的机械闸门，两条都是只减不增的棘轮：
// 1. 交接轮次：驱动每处 `fetch_round` / `fetch_or_hand_over` / `hand_over` 调用，都是一轮
//    「打印 call: → 宿主模型逐个调用 → 落盘 → 续跑」，每轮多花两三次模型调用。存量按 skill 记在
//    HANDOVER_SITES 里；新 skill 不许有，存量 skill 不许变多。迁到后台接口后数目变少，同一 PR 里把
//    这里的数字改小（数字对不上也会失败，免得棘轮松掉）。
// 2. 宿主探测：脚本不许按宿主走不同的取数路径（connector-outbox、会话线程 id、回合令牌）。存量
//    文件记在 HOST_PROBE_FILES；删掉后同一 PR 里把它从名单里拿掉。
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const scripts = () =>
  execFileSync("git", ["ls-files", "-z", "--", "skills"], { cwd: root, encoding: "utf8" })
    .split("\0")
    .filter((file) => /^skills\/[^/]+\/scripts\/[^/]+\.py$/.test(file));
const text = (file) => readFileSync(path.join(root, file), "utf8");

export const HANDOVER_CALL = /\b(?:fetch_round|fetch_or_hand_over|hand_over)\(/;
export const HOST_PROBE = /connector-outbox|\boutbox_dir\b|\bCODEX_THREAD_ID\b|\bturn_token\b/;

const HANDOVER_SITES = {
  "analyze-creator-competitors": 12,
  "analyze-tiktok-competitors": 3,
  "diagnose-reach-drop": 5,
  "research-instagram-trends": 5,
  "research-tiktok-trends": 3,
  "research-youtube-trends": 3,
  "review-creator-account": 8,
  "verify-platform-rule": 1,
};

const HOST_PROBE_FILES = [
  "skills/diagnose-reach-drop/scripts/connector_driver.py",
  "skills/research-instagram-trends/scripts/connector_driver.py",
  "skills/review-creator-account/scripts/connector_driver.py",
  "skills/verify-platform-rule/scripts/connector_driver.py",
];

export function handoverSites(files = scripts()) {
  const counts = {};
  for (const file of files) {
    if (file.endsWith("/connector_driver.py")) continue; // 定义处，不是调用处
    const skill = file.split("/")[1];
    for (const line of text(file).split("\n")) {
      if (HANDOVER_CALL.test(line) && !/^\s*def\s/.test(line)) counts[skill] = (counts[skill] ?? 0) + 1;
    }
  }
  return counts;
}

export function hostProbeFiles(files = scripts()) {
  return files.filter((file) => HOST_PROBE.test(text(file))).sort();
}

test("交接轮次只减不增：新增多轮取数要做成一次后台调用（KS9）", () => {
  assert.deepEqual(
    handoverSites(),
    HANDOVER_SITES,
    "drivers hand a fetch round to the host model more (or fewer) times than recorded. A new round belongs in a " +
      "backend endpoint that runs the whole fetch (docs/skills-spec.md KS9); after removing rounds, lower the numbers here.",
  );
});

test("脚本不按宿主探测取数路径（KS9）", () => {
  assert.deepEqual(
    hostProbeFiles(),
    [...HOST_PROBE_FILES].sort(),
    "a script probes for a host-only fetch channel. Fetching must not branch on the host (docs/skills-spec.md KS9); " +
      "after deleting a probe, drop the file from HOST_PROBE_FILES.",
  );
});

test("检查本身能抓到违例", () => {
  assert.ok(HANDOVER_CALL.test("    cd.fetch_round(calls, args, place)"));
  assert.ok(!HANDOVER_CALL.test("original_hand_over(printed, why)"));
  assert.ok(HOST_PROBE.test('root / "connector-outbox-host"'));
});
