// diagnose-reach-drop 的流量来源对比：两组各是一个日历周期（上周对这周）时，每组带自己的日历窗口，
// 不能被改写成“发布后 168 小时”。窗口要两组都给、都是日历周期、时长相同、按时间先后且不重叠；
// 时长不同、重叠或倒序时不出对比结论，只记限制。夹具是中性的合成账号汇总数据。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const workflow = path.join(root, "skills", "diagnose-reach-drop", "scripts", "run_traffic_workflow.py");

const week = (start, end) => ({ kind: "calendar", start, end });
const W1 = week("2026-10-01T00:00:00Z", "2026-10-08T00:00:00Z");
const W2 = week("2026-10-08T00:00:00Z", "2026-10-15T00:00:00Z");

function brief(edit = () => {}) {
  const question = {
    id: "source_change", kind: "traffic_sources", intent: "diagnose",
    ask: "Compare the recommended-source share between two calendar weeks.",
    selection_reason: "The user supplied account totals for two calendar weeks.",
    total_metric: "views", population: "account calendar-period views", source_name: "For You",
    groups: [
      { label: "Week 1", totals: [3000], counts: [1800], content_ids: ["account-week-1"], window: { ...W1 } },
      { label: "Week 2", totals: [3000], shares: [0.3], content_ids: ["account-week-2"], window: { ...W2 } },
    ],
  };
  edit(question);
  return {
    schema_version: "traffic-workflow-brief.v1", platform: "tiktok", account: "user-supplied-account",
    source: { kind: "user_supplied", locator: "numbers in the user's message" },
    questions: [question], coverage: [],
    user_request: "Week 1 total views 3000, For You 1800; week 2 total views 3000, For You share 30%. Why did it drop?",
  };
}

async function prepare(payload) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "reach-calendar-"));
  try {
    const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
    delete env.KABO_STATIC_RUN_DIR;
    const result = spawnSync("python3", ["-B", workflow, "prepare", "-", "--output-dir", dir, "--language", "en"],
      { input: JSON.stringify(payload), env, encoding: "utf8" });
    const out = result.stdout + result.stderr;
    if (out.includes("needs_correction")) return { out, assessment: null };
    const assessment = JSON.parse(await readFile(path.join(dir, "reach_drop_assessment.json"), "utf8"));
    const evidence = await readFile(path.join(dir, "TRAFFIC_EVIDENCE.md"), "utf8");
    const verdicts = spawnSync("python3", ["-B", "-c",
      "import json,sys; sys.path.insert(0, sys.argv[1]); from compose_traffic import verdict; "
      + "a = json.load(open(sys.argv[2])); print('\\n'.join(verdict(q, z) for q in a['questions'] for z in (True, False)))",
      path.dirname(workflow), path.join(dir, "reach_drop_assessment.json")], { env, encoding: "utf8" });
    return { out, assessment, evidence, verdict: verdicts.stdout + verdicts.stderr };
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

const sourceFacts = (assessment) => JSON.stringify(assessment).match(/"type":"traffic_sources"/g) ?? [];

test("two calendar weeks keep their own windows and compare", async () => {
  const { out, assessment, evidence } = await prepare(brief());
  assert.ok(assessment, out);
  assert.equal(sourceFacts(assessment).length, 1);
  assert.match(evidence, /2026-10-01T00:00:00Z → 2026-10-08T00:00:00Z/);
  assert.match(evidence, /2026-10-08T00:00:00Z → 2026-10-15T00:00:00Z/);
  assert.match(evidence, /60\.00%/);
  assert.match(evidence, /30\.00%/);
  assert.doesNotMatch(evidence, /Post-publication observation age/);
});

test("direct counts with an unchanged share say so, without an estimate note", async () => {
  const { out, verdict } = await prepare(brief((q) => {
    q.groups[0] = { ...q.groups[0], totals: [1000], counts: [300] };
    q.groups[1] = { label: "Week 2", totals: [2000], counts: [600], content_ids: ["account-week-2"], window: { ...W2 } };
  }));
  assert.match(verdict, /in the supplied data, this source's share was unchanged and its amount rose, which describes the supplied data, not a cause or platform enforcement\.$/m, out + verdict);
  assert.doesNotMatch(verdict, /estimated/);
});

test("two calendar weeks get a period verdict, not 'missing matching totals'", async () => {
  const { out, verdict } = await prepare(brief());
  assert.match(verdict, /Between the two calendar periods in the supplied data, this source's share fell and its amount, at least partly estimated from the reported shares, fell, which describes the supplied data, not a cause or platform enforcement\.$/m, out + verdict);
  assert.doesNotMatch(verdict, /两个日历周期|account's source mix/);
  assert.doesNotMatch(verdict, /缺少匹配的总量|without matching totals/);
});

for (const [name, edit, limit] of [
  ["overlapping weeks", (q) => { q.groups[1].window = week("2026-10-05T00:00:00Z", "2026-10-12T00:00:00Z"); },
    "overlapping_or_reversed_calendar_windows"],
  ["reversed weeks", (q) => { q.groups.reverse(); }, "overlapping_or_reversed_calendar_windows"],
  ["unequal durations", (q) => { q.groups[1].window = week("2026-10-08T00:00:00Z", "2026-10-12T00:00:00Z"); },
    "incompatible_metric_population_source_or_window"],
]) {
  test(`${name} give no comparison, only a limit`, async () => {
    const { out, assessment } = await prepare(brief(edit));
    assert.ok(assessment, out);
    assert.equal(sourceFacts(assessment).length, 0);
    assert.ok(JSON.stringify(assessment).includes(limit), JSON.stringify(assessment));
  });
}

for (const [name, edit, message] of [
  ["a window on one group only", (q) => { delete q.groups[1].window; }, "must be given on both groups"],
  ["a post-age window on a group", (q) => { q.groups[0].window = { kind: "post_age", hours: 168 }; },
    "group windows must be calendar periods"],
  ["group windows plus a common post age", (q) => { q.post_age_hours = 168; },
    "or a calendar window on each group"],
]) {
  test(`${name} is sent back for correction`, async () => {
    const { out, assessment } = await prepare(brief(edit));
    assert.equal(assessment, null);
    assert.ok(out.includes(message), out);
  });
}
