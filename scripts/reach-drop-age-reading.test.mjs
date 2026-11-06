// diagnose-reach-drop 的近期掉量对比：一次公开快照里早发的帖子总是更“老”，累计播放天然偏向前期。
// 近期更年轻却不低于前期，就直接说没有下降；近期更低时，只拿发布满近似成熟时长的帖子比
// （短内容 7 天、YouTube 长视频 28 天），近期都太新就说暂时看不出。夹具是中性的合成账号数据。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const workflow = path.join(root, "skills", "diagnose-reach-drop", "scripts", "run_traffic_workflow.py");
const OBSERVED = "2026-11-01T00:00:00Z";
const daysAgo = (days) => new Date(Date.parse(OBSERVED) - days * 86400000).toISOString().replace(".000", "");

function group(label, rows) {
  return {
    label, values: rows.map(([, value]) => value), content_ids: rows.map(([days]) => `${label}-${days}`),
    published_at: rows.map(([days]) => daysAgo(days)), observed_at: rows.map(() => OBSERVED),
  };
}

function brief(format, earlier, recent, extra = {}) {
  return {
    schema_version: "traffic-workflow-brief.v1", platform: "youtube", account: "user-supplied-account",
    source: { kind: "user_supplied", locator: "numbers in the user's message" }, coverage: [],
    user_request: "Did views on my recent videos drop compared with earlier ones?",
    questions: [{ id: "drop", kind: "recent_drop", intent: "diagnose", metric: "views", format,
      ask: "Compare recent and earlier videos.", selection_reason: "The user supplied per-video views.",
      groups: [group("earlier", earlier), group("recent", recent)], ...extra }],
  };
}

async function run(payload) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "reach-age-"));
  try {
    const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
    delete env.KABO_STATIC_RUN_DIR;
    const result = spawnSync("python3", ["-B", workflow, "prepare", "-", "--output-dir", dir, "--language", "en"],
      { input: JSON.stringify(payload), env, encoding: "utf8" });
    const out = result.stdout + result.stderr;
    const assessment = JSON.parse(await readFile(path.join(dir, "reach_drop_assessment.json"), "utf8"));
    const evidence = await readFile(path.join(dir, "TRAFFIC_EVIDENCE.md"), "utf8");
    const verdicts = spawnSync("python3", ["-B", "-c",
      "import json,sys; sys.path.insert(0, sys.argv[1]); from compose_traffic import verdict; "
      + "a = json.load(open(sys.argv[2])); print('\\n'.join(verdict(q, z) for q in a['questions'] for z in (True, False)))",
      path.dirname(workflow), path.join(dir, "reach_drop_assessment.json")], { env, encoding: "utf8" });
    const fact = assessment.questions[0].facts.find((item) => item.type === "performance");
    return { out, fact, evidence, verdict: verdicts.stdout + verdicts.stderr };
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

const EARLIER = [[40, 9000], [36, 10000], [32, 11000]];

test("younger recent posts that are not lower mean no decline", async () => {
  const { out, fact, verdict } = await run(brief("short_video", EARLIER, [[10, 12000], [6, 13000], [2, 9500]]));
  assert.equal(fact?.age_reading?.basis, "recent_younger_not_lower", out);
  assert.match(verdict, /^The recent posts are younger, yet their current cumulative median is not lower, so there is no sign of a decline/m);
  assert.match(verdict, /^近期内容发布时间更短，当前累计中位数却不低于前期，所以没有下降迹象/m);
});

test("a lower recent median is compared on matured short posts only", async () => {
  const { out, fact, evidence, verdict } = await run(brief("short_video", EARLIER, [[12, 4000], [9, 5000], [3, 800]]));
  assert.equal(fact?.age_reading?.basis, "matured_only", out);
  assert.equal(fact.age_reading.min_age_hours, 168);
  assert.deepEqual(fact.age_reading.groups.map((item) => item.n), [3, 2]);
  assert.equal(fact.age_reading.change.after, 4500);
  assert.match(evidence, /Posts at least 7 days old only \(approximation\)/);
  assert.match(verdict, /^Comparing only posts at least 7 days old, an approximation .* the recent median is still lower, which is a decline signal/m);
  assert.match(verdict, /^只比较发布满 7 天的内容（近似比较.*近期中位数仍低于前期，构成下降信号/m);
});

test("long YouTube videos use 28 days, and young matured-out posts say so", async () => {
  const lower = await run(brief("youtube_long", [[90, 9000], [70, 10000]], [[30, 12000], [5, 1000], [2, 500]]));
  assert.equal(lower.fact?.age_reading?.min_age_hours, 672, lower.out);
  assert.match(lower.verdict, /^Comparing only posts at least 28 days old, .* the recent median is not lower, so the lower overall figure may reflect newer posts that have not finished accruing/m);
  const young = await run(brief("youtube_long", [[90, 9000], [70, 10000]], [[20, 3000], [5, 1000]]));
  assert.equal(young.fact?.age_reading?.basis, "recent_too_young", young.out);
  assert.match(young.verdict, /^The recent posts are all under 28 days old, short of the comparison age and possibly still accruing, so it is too early to tell/m);
});

test("same-age, explicit-age and non-cumulative comparisons keep their existing verdicts", async () => {
  const sameAge = brief("short_video", EARLIER, [[12, 4000], [9, 5000]], { post_age_hours: 168 });
  for (const item of sameAge.questions[0].groups) { delete item.published_at; delete item.observed_at; }
  const same = await run(sameAge);
  assert.equal(same.fact?.age_reading, undefined, same.out);
  const policy = await run(brief("short_video", EARLIER, [[12, 4000], [9, 5000], [3, 800]],
    { minimum_age_hours: 72, age_policy_reason: "The user only counts posts older than three days." }));
  assert.equal(policy.fact?.age_reading, undefined, policy.out);
  const rate = brief("short_video", EARLIER, [[12, 4000], [9, 5000], [3, 800]]);
  rate.questions[0].metric = "average_view_duration_seconds";
  const average = await run(rate);
  assert.equal(average.fact?.age_reading, undefined, average.out);
});

test("a generic video format gets no maturity age, so no lower-median reading", async () => {
  const generic = await run(brief("video", EARLIER, [[12, 4000], [9, 5000], [3, 800]]));
  assert.equal(generic.fact?.age_reading, undefined, generic.out);
  const youtube = await run(brief("youtube_video", EARLIER, [[12, 4000], [9, 5000], [3, 800]]));
  assert.equal(youtube.fact?.age_reading, undefined, youtube.out);
  const chinese = await run(brief("短视频", EARLIER, [[12, 4000], [9, 5000], [3, 800]]));
  assert.equal(chinese.fact?.age_reading?.min_age_hours, 168, chinese.out);
});
