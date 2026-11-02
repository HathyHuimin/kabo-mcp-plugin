import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";

// lookup_instagram_post 的固定投影：信封记实际作答的路线，
// data 是固定投影 kabo.instagram_post.v1。这里钉住插件这一侧的迁移：ACV、ACC 的 static_host 两条路线
// 都收、ACC 驱动按顶层 post + delivery 交接、读帖子的脚本按投影分支且旧形状照读。
// RIT 在服务端给投影补上 include（评论预览、标记用户、地点、合作者与付费合作标记）之后迁入。
const MIGRATED = ["analyze-content-video", "analyze-creator-competitors", "research-instagram-trends"];

const root = fileURLToPath(new URL("..", import.meta.url));
const scripts = (id) => path.join(root, "skills", id, "scripts");
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const POST_URL = "https://www.instagram.com/creator/reel/DeZrE2phzDx/?igsh=abc";
const CANONICAL = "https://www.instagram.com/reel/DeZrE2phzDx/";

function python(args, { cwd = root, run, input } = {}) {
  return execFileSync("python3", ["-B", ...args], {
    cwd, input, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", ...(run ? { KABO_STATIC_RUN_DIR: run } : {}) },
  });
}

function attempt(args, options) {
  try {
    return { exit: 0, stdout: python(args, options), stderr: "" };
  } catch (error) {
    return { exit: error.status, stdout: String(error.stdout), stderr: String(error.stderr) };
  }
}

/** 一份投影信封；route 决定 connector_id 与回退时的 completed_partial。 */
function lookupEnvelope({ route = "primary", post = {}, metrics = {}, audio, unavailable = [] } = {}) {
  const fallback = route === "fallback";
  const connector = fallback ? "instagram-detail-proxy" : "instagram-discovery";
  const provider = fallback ? "provider-b" : "provider-a";
  return {
    connector_id: connector, operation: "get_post", provider,
    status: fallback ? "completed_partial" : "completed",
    summary: fallback ? "Instagram post returned through the fallback route." : "Found 1 post",
    limitations: fallback
      ? ["Instagram discovery refused this post (forbidden), so Kabo answered through the Instagram detail route. Fields that route does not return (music attribution) are null and listed in unavailable_fields."]
      : [],
    evidence_ref: `ev-${route}`, raw_sha256: sha256(route), request_id: `req-${route}`, retrieved_at: "2026-10-21T07:00:00Z",
    data: {
      projection: "kabo.instagram_post.v1",
      post: {
        shortcode: "DeZrE2phzDx", id: "4006422793079435505", url: "https://www.instagram.com/p/DeZrE2phzDx/",
        is_video: true, product_type: "clips", published_at: "2026-10-01T12:34:30.000Z",
        caption: "Monsoon trip in 20 seconds #travel #monsoon",
        owner: { id: "123", username: "creator", full_name: null, is_verified: false, is_private: false },
        metrics: { plays_instagram: 31290541, plays_combined: null, likes: 3050591, likes_hidden: null, comments: 4234, ...metrics },
        duration_s: 21.4, thumbnail_url: "https://scontent.cdninstagram.com/v/t51/x.jpg?oe=ABC",
        audio: audio === undefined
          ? (fallback ? null : { uses_original_audio: true, audio_id: "987", artist_name: "creator", song_name: "Original audio" })
          : audio,
        ...post,
      },
      source: { route, connector_id: connector, operation: "get_post", provider, retrieved_at: "2026-10-21T07:00:00Z", fallback_reason: fallback ? "forbidden" : null },
      unavailable_fields: unavailable,
    },
  };
}

// 旧的单操作工具信封（instagram_get_post 的 GraphQL 原样）：该工具已从 /mcp-pub 删除，
// 但删除前落下的快照里可能还有，读帖子的脚本要照读。
const GRAPHQL_ENVELOPE = {
  connector_id: "instagram-discovery", operation: "get_post", status: "completed", summary: "Found 1 post",
  limitations: [], raw_sha256: sha256("graphql"), request_id: "req-graphql",
  data: {
    post: {
      shortcode: "DeZrE2phzDx", product_type: "clips", is_video: true, video_duration: 21.4,
      edge_media_to_caption: { edges: [{ node: { text: "Old caption #legacy" } }] },
      video_play_count: 1200, edge_media_preview_like: { count: 80 }, comment_count: 9,
      clips_music_attribution_info: { uses_original_audio: false, audio_id: "55", artist_name: "Band", song_name: "Song" },
      owner: { username: "creator", full_name: "The Creator", is_verified: true },
    },
  },
};

const result = (envelope) => ({ structuredContent: { result: envelope } });

async function newRun(t, skill) {
  const temp = await mkdtemp(path.join(os.tmpdir(), "kabo-lookup-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const run = python([path.join(scripts(skill), "static_host.py"), "init", "--workspace", temp]).trim();
  return { temp, run };
}

async function stage(skill, run, tool, value, command = "stage") {
  // 输入放在运行目录之外：放置脚本会把运行目录里已有的同一份回答当成「已放置」。
  const file = path.join(path.dirname(path.dirname(run)), `input-${Math.random().toString(16).slice(2)}.json`);
  await writeFile(file, JSON.stringify(value));
  return attempt([path.join(scripts(skill), "static_host.py"), command, "--tool", tool, "--input", file], { run });
}

async function metas(run) {
  const directory = path.join(run, "snapshot/.host-evidence");
  const names = (await readdir(directory)).filter((name) => name.endsWith(".meta")).sort();
  return Promise.all(names.map(async (name) => JSON.parse(await readFile(path.join(directory, name), "utf8"))));
}

test("static_host：lookup_instagram_post 两条路线的信封都可 stage，旁注记作答路线；别的操作、缺投影与旧工具名被拒", async (t) => {
  for (const skill of MIGRATED) {
    const { run } = await newRun(t, skill);
    const primary = await stage(skill, run, "lookup_instagram_post", result(lookupEnvelope()));
    assert.equal(primary.exit, 0, `${skill}：${primary.stderr}`);
    const fallback = await stage(skill, run, "lookup_instagram_post", result(lookupEnvelope({ route: "fallback" })));
    assert.equal(fallback.exit, 0, `${skill}：${fallback.stderr}`);
    // 失败信封不带 data、标 isError：照样是那条路线的证据。
    const { data: _data, ...failedBody } = { ...lookupEnvelope(), status: "failed", error_code: "not_found", limitations: ["No public post."] };
    const failed = await stage(skill, run, "lookup_instagram_post", { isError: true, ...result(failedBody) });
    assert.equal(failed.exit, 0, `${skill}：${failed.stderr}`);
    assert.deepEqual((await metas(run)).map((meta) => `${meta.tool}:${meta.connector_id}/${meta.operation}`).sort(), [
      "lookup_instagram_post:instagram-detail-proxy/get_post",
      "lookup_instagram_post:instagram-discovery/get_post",
      "lookup_instagram_post:instagram-discovery/get_post",
    ]);

    const wrongRoute = await stage(skill, run, "lookup_instagram_post", result({ ...lookupEnvelope(), operation: "get_transcript" }));
    assert.equal(wrongRoute.exit, 1);
    assert.match(wrongRoute.stderr, /does not match its declared operation/);
    const unprojected = await stage(skill, run, "lookup_instagram_post", result(GRAPHQL_ENVELOPE));
    assert.equal(unprojected.exit, 1);
    assert.match(unprojected.stderr, /declared projection/);
    // 旧工具名已从服务端删除，不是本 Skill 的证据工具，也不在 allowed-tools 里；
    // 留着这条断言是为了确认旧结果冒充证据时仍被拒收。
    for (const tool of ["instagram_get_post", "instagram_detail_get_post"]) {
      const legacy = await stage(skill, run, tool, result(GRAPHQL_ENVELOPE));
      assert.equal(legacy.exit, 1);
      assert.match(legacy.stderr, /Not a declared evidence tool/);
    }
    const body = await readFile(path.join(root, "skills", skill, "SKILL.md"), "utf8");
    const allowed = body.match(/^allowed-tools: (.*)$/m)[1].split(" ");
    assert.ok(allowed.includes("lookup_instagram_post"));
    assert.equal(allowed.includes("instagram_get_post") || allowed.includes("instagram_detail_get_post"), false);
  }
});

// 与 static-host-fetch.test.mjs 同法：进程内替换 urllib 的 opener，只有 HTTPS 收发被假处理器接管。
const FETCH_HARNESS = String.raw`
import base64, contextlib, http.client, io, json, os, runpy, sys, urllib.request, urllib.response
script, url, body, source = sys.argv[1:5]
class FakeHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        assert req.full_url == url and req.get_method() == "GET"
        message = http.client.parse_headers(io.BytesIO(b"Content-Type: application/json\r\n\r\n"))
        response = urllib.response.addinfourl(io.BytesIO(base64.b64decode(body)), message, req.full_url, 200)
        response.msg = "fake"
        return response
original = urllib.request.build_opener
urllib.request.build_opener = lambda *handlers: original(*handlers, FakeHTTPS())
sys.argv = [script, "fetch", "--tool", "lookup_instagram_post", "--input", source]
code = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as exit_:
    code = exit_.code or 0
sys.exit(code)
`;

test("static_host fetch：详情路线作答的 lookup 信封经下载核对后落盘，身份字段与投影都校验", async (t) => {
  const url = "https://storage.googleapis.com/kabo-media/_temporary/research-export/v1/lookup.json?X-Goog-Signature=S";
  for (const skill of MIGRATED) {
    const { run } = await newRun(t, skill);
    const envelope = lookupEnvelope({ route: "fallback" });
    const bytes = Buffer.from(JSON.stringify(envelope));
    const { data: _data, ...inline } = envelope;
    const structured = {
      download: { status: "ready", kind: "envelope", url, expires_at: "2099-01-01T00:10:00Z", sha256: sha256(bytes), bytes: bytes.length, content_type: "application/json" },
      result: inline,
    };
    const source = path.join(path.dirname(path.dirname(run)), "lookup-download.json");
    await writeFile(source, JSON.stringify({ structuredContent: structured }));
    const output = python(["-c", FETCH_HARNESS, path.join(scripts(skill), "static_host.py"), url, bytes.toString("base64"), source], { run });
    assert.equal(JSON.parse(output).staged, 1);
    const [meta] = await metas(run);
    assert.equal(meta.connector_id, "instagram-detail-proxy");
    assert.equal(meta.delivery, "download");
    // 下载内联模式下 stage 拒绝只带链接的结果，指向 fetch。
    const refused = await stage(skill, run, "lookup_instagram_post", { structuredContent: structured });
    assert.equal(refused.exit, 1);
    assert.match(refused.stderr, /run static_host\.py fetch/);
  }
});

test("驱动交接：ACC 把种子帖交成顶层 post + delivery 的 lookup_instagram_post，详情路线的回答也能放置", async (t) => {
  const { run } = await newRun(t, "analyze-creator-competitors");
  const drive = path.join(scripts("analyze-creator-competitors"), "drive.py");
  const first = python([drive, "start", "--platform", "instagram", "--post", POST_URL], { cwd: run, run });
  const calls = first.split("\n").filter((line) => line.startsWith("call: ")).map((line) => JSON.parse(line.slice(6)));
  assert.deepEqual(calls, [{ tool: "lookup_instagram_post", arguments: { post: POST_URL, delivery: "download" } }]);

  const staged = await stage("analyze-creator-competitors", run, "lookup_instagram_post", result(lookupEnvelope({
    route: "fallback", metrics: { plays_instagram: null, plays_combined: 41290541, likes: null, likes_hidden: true },
  })));
  assert.equal(staged.exit, 0, staged.stderr);
  const placed = python([drive, "start", "--platform", "instagram", "--post", POST_URL, "--placed"], { cwd: run, run });
  assert.match(placed, /seed post: DeZrE2phzDx by @creator/);
  const facts = JSON.parse(placed.split("\n").find((line) => line.startsWith("seed post (the video the user gave): ")).split(": ").slice(1).join(": "));
  // 合并播放数单列，不冒充 IG 播放数；投影里的 null 不打印。
  assert.deepEqual(facts, {
    format: "reel", duration_seconds: 21.4, plays_instagram_and_facebook: 41290541, comments: 4234,
    caption: "Monsoon trip in 20 seconds #travel #monsoon",
  });
  const seed = JSON.parse(await readFile(path.join(run, "seed-envelope.json"), "utf8"));
  assert.equal(seed.connector_id, "instagram-detail-proxy");
  // 种子帖的投影 caption 是字符串，Gate 的 seed_caption 照读。
  const caption = python(["-c", "import json,sys; sys.path.insert(0, sys.argv[1]); from project_gate_inputs import seed_caption; "
    + "print(seed_caption(json.load(open(sys.argv[2]))))", scripts("analyze-creator-competitors"), path.join(run, "seed-envelope.json")]);
  assert.equal(caption.trim(), "Monsoon trip in 20 seconds #travel #monsoon");
});

// RIT 源 Reel 读的那些字段在旧 GraphQL 原样里的位置：评论预览、标记用户、地点、合作者、付费合作标记。
const RIT_GRAPHQL_ENVELOPE = {
  ...GRAPHQL_ENVELOPE,
  data: {
    post: {
      ...GRAPHQL_ENVELOPE.data.post,
      is_paid_partnership: true,
      edge_media_to_parent_comment: { edges: [{ node: { text: "First!" } }, { node: { text: "Where is this?" } }] },
      edge_media_to_tagged_user: { edges: [{ node: { user: { username: "friend" } } }] },
      coauthor_producers: [{ username: "partner" }],
      location: { name: "Goa" },
    },
  },
};

// 带 include 四组的投影：请求了的组键恒在；空列表是 []，拿不到是 null 并进 unavailable_fields。
const RIT_INCLUDE = ["comments_preview", "tagged_users", "location", "collaboration"];
const INCLUDED = {
  comments_preview: [
    { text: "First!", likes: 3, published_at: "2026-10-01T13:00:00Z" },
    { text: "Where is this?", likes: 1, published_at: null },
    { text: "Saved", likes: 0, published_at: null },
    { text: "Fourth is cut", likes: 0, published_at: null },
  ],
  tagged_users: [{ id: "9", username: "friend", is_verified: false }],
  coauthors: [{ id: "8", username: "partner", is_verified: true }],
  location: { id: "77", name: "Goa" },
  is_paid_partnership: true,
};

test("驱动交接：RIT 把源 Reel 交成带 include 的 lookup_instagram_post，投影的五项补充字段照旧打印", async (t) => {
  const { run } = await newRun(t, "research-instagram-trends");
  const drive = path.join(scripts("research-instagram-trends"), "drive.py");
  const first = python([drive, "start", "--post", POST_URL], { cwd: run, run });
  const calls = first.split("\n").filter((line) => line.startsWith("call: ")).map((line) => JSON.parse(line.slice(6)));
  // 逐个具名调用；lookup 入参在顶层，转写照旧套 parameters。
  assert.deepEqual(calls, [
    { tool: "lookup_instagram_post", arguments: { post: CANONICAL, include: RIT_INCLUDE, delivery: "download" } },
    { tool: "instagram_get_transcript", arguments: { parameters: { url: CANONICAL }, delivery: "download" } },
  ]);
  // 补充字段只有主路线给得出（详情回退时五个键恒为 null，见下一条用例）。
  for (const [tool, envelope] of [
    ["lookup_instagram_post", lookupEnvelope({ post: INCLUDED, audio: null, unavailable: ["audio"] })],
    ["instagram_get_transcript", { connector_id: "instagram-discovery", operation: "get_transcript", status: "completed", summary: "ok", limitations: [], raw_sha256: sha256("t"), data: { text: "Monsoon in twenty seconds.", language: "en" } }],
  ]) {
    const staged = await stage("research-instagram-trends", run, tool, result(envelope));
    assert.equal(staged.exit, 0, staged.stderr);
  }
  const placed = python([drive, "start", "--post", POST_URL, "--placed"], { cwd: run, run });
  const line = placed.split("\n").find((text) => text.startsWith("source Reel (the Creator is its author"));
  assert.ok(line, placed);
  assert.match(line, /creator_profile\.handle is @creator/);
  assert.deepEqual(JSON.parse(line.slice(line.indexOf("{"))), {
    shortcode: "DeZrE2phzDx",
    author: { username: "creator", is_verified: false },
    caption: "Monsoon trip in 20 seconds #travel #monsoon",
    published_at: "2026-10-01T12:34:30.000Z",
    product_type: "clips",
    video_duration: 21.4,
    play_count: 31290541,
    like_count: 3050591,
    comment_count: 4234,
    is_paid_partnership: true,
    tagged_users: ["friend"],
    coauthors: ["partner"],
    location: "Goa",
    top_comments: ["First!", "Where is this?", "Saved"],
    unavailable: ["audio"],
  });
  const source = JSON.parse(await readFile(path.join(run, "source-post.json"), "utf8"));
  assert.equal(source.data.projection, "kabo.instagram_post.v1");
  assert.match(placed, /source Reel transcript \(en\): Monsoon in twenty seconds\./);
});

test("驱动续跑：详情路线（instagram-detail-proxy）作答的 lookup，不带 --placed 再跑照样放置，不再交一次（ACC、RIT）", async (t) => {
  // 交接时请求的是 instagram-discovery/get_post，回退时信封记 instagram-detail-proxy；轮次登记只认请求那条时，
  // 再跑 start 会判定「没有回答」，把已收费的 lookup 再交一次。
  const transcript = { connector_id: "instagram-discovery", operation: "get_transcript", status: "completed", summary: "ok", limitations: [], raw_sha256: sha256("t"), data: { text: "Monsoon.", language: "en" } };
  for (const [skill, args, responses, printed] of [
    ["analyze-creator-competitors", ["--platform", "instagram", "--post", POST_URL], [["lookup_instagram_post", lookupEnvelope({ route: "fallback" })]], /seed post: DeZrE2phzDx by @creator/],
    ["research-instagram-trends", ["--post", POST_URL], [["lookup_instagram_post", lookupEnvelope({ route: "fallback" })], ["instagram_get_transcript", transcript]], /^source Reel \(the Creator is its author/m],
  ]) {
    const { run } = await newRun(t, skill);
    const drive = path.join(scripts(skill), "drive.py");
    python([drive, "start", ...args], { cwd: run, run });
    for (const [tool, envelope] of responses) {
      const staged = await stage(skill, run, tool, result(envelope));
      assert.equal(staged.exit, 0, `${skill}：${staged.stderr}`);
    }
    const again = attempt([drive, "start", ...args], { cwd: run, run });
    const output = again.stdout + again.stderr;
    assert.match(output, /placing their responses/, `${skill}：${output}`);
    assert.doesNotMatch(output, /^call: \{"tool": "lookup_instagram_post"/m, skill);
    assert.match(output, printed, skill);
  }
});

test("RIT 读帖函数：投影按 data.projection 分支读五项补充字段，null 省略并列入 unavailable；旧 GraphQL 照读", () => {
  const probe = String.raw`
import json, sys
sys.path.insert(0, sys.argv[1])
import drive
cases = json.loads(sys.argv[2])
print(json.dumps([drive.projected_source_fields(cases[0]["data"]), drive.projected_source_fields(cases[1]["data"]),
                  drive.source_fields(cases[2]["data"]["post"])]))
`;
  const included = lookupEnvelope({ post: INCLUDED });
  // 详情回退作答：补充字段恒为 null 并列入 unavailable_fields（不放宽回退白名单）。
  const missing = lookupEnvelope({
    route: "fallback",
    post: { comments_preview: null, tagged_users: [], coauthors: null, location: null, is_paid_partnership: null },
    metrics: { likes: null, likes_hidden: true, plays_combined: 5000 },
    unavailable: ["comments_preview", "coauthors", "location", "is_paid_partnership"],
  });
  const [withAll, withNulls, legacy] = JSON.parse(python(["-c", probe, scripts("research-instagram-trends"),
    JSON.stringify([included, missing, RIT_GRAPHQL_ENVELOPE])]));
  assert.deepEqual(withAll.top_comments, ["First!", "Where is this?", "Saved"]);
  assert.deepEqual([withAll.tagged_users, withAll.coauthors, withAll.location, withAll.is_paid_partnership],
    [["friend"], ["partner"], "Goa", true]);
  // 空列表与 null 都不打印；null 的那几项列进 unavailable，不当 0 或 false；点赞隐藏单列。
  for (const key of ["top_comments", "tagged_users", "coauthors", "location", "is_paid_partnership", "like_count"]) {
    assert.equal(key in withNulls, false, key);
  }
  assert.equal(withNulls.likes_hidden, true);
  assert.equal(withNulls.play_count_instagram_and_facebook, 5000);
  assert.deepEqual(withNulls.unavailable, ["comments_preview", "coauthors", "location", "is_paid_partnership"]);
  assert.deepEqual([legacy.tagged_users, legacy.coauthors, legacy.location, legacy.is_paid_partnership, legacy.top_comments],
    [["friend"], ["partner"], "Goa", true, ["First!", "Where is this?"]]);
});

test("驱动的读帖函数：ACC 投影的 IG 与合并播放数分列，旧 GraphQL 信封照旧读", () => {
  const probe = String.raw`
import json, sys
sys.path.insert(0, sys.argv[1])
import drive
print(json.dumps([drive.seed_facts(case) for case in json.loads(sys.argv[2])]))
`;
  const both = lookupEnvelope({ route: "fallback", metrics: { plays_instagram: 900, plays_combined: 1500 } });
  const [accBoth, accOld] = JSON.parse(python(["-c", probe, scripts("analyze-creator-competitors"), JSON.stringify([both, GRAPHQL_ENVELOPE])]));
  assert.equal(accBoth.plays, 900);
  assert.equal(accBoth.plays_instagram_and_facebook, 1500);
  assert.deepEqual(accOld, { format: "reel", duration_seconds: 21.4, plays: 1200, comments: 9, caption: "Old caption #legacy" });
});

test("ACV build_content_evidence：投影两条路线、播放数分列、点赞隐藏与 null 字段；旧 GraphQL 照旧；证据过 schema", async (t) => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "kabo-lookup-evidence-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  await mkdir(path.join(temp, "analysis"));
  const request = path.join(temp, "analysis/video-analysis-request.json");
  await writeFile(request, JSON.stringify({
    schema_version: "video-analysis-request.v1", request_id: "req-1",
    source: { acquisition: "public_url", subject_state: "third_party", platform: "instagram", url: CANONICAL, content_id: "DeZrE2phzDx", declared_format: "video", owner_data: null },
    acquire: { media: "best_effort", transcript: "best_effort", frames: "best_effort", owner_metrics: "skip" },
    budget: { max_connector_calls: 10, max_provider_credits: null, confirmed_by_user: true, per_call: { timeout_seconds: 60, max_response_bytes: 50000000 } },
    failure_policy: { on_missing_capability: "return_partial", fatal_capabilities: [] },
  }));
  const ajv = new Ajv2020({ allErrors: true, strict: false, logger: false });
  const schema = ajv.compile(JSON.parse(await readFile(path.join(root, "skills/analyze-content-video/references/content-evidence.schema.json"), "utf8")));
  async function build(name, envelope) {
    const input = path.join(temp, `${name}.json`);
    await writeFile(input, JSON.stringify(envelope));
    const output = path.join(temp, "analysis", `${name}-evidence.json`);
    python([path.join(scripts("analyze-content-video"), "build_content_evidence.py"), "--request", request, "--envelope", input, "--output", output]);
    const evidence = JSON.parse(await readFile(output, "utf8"));
    assert.ok(schema(evidence), `${name}：${JSON.stringify(schema.errors)}`);
    return evidence;
  }
  const metrics = (evidence) => Object.fromEntries((evidence.public_metrics ?? []).map((item) => [item.native_name, item.value]));

  const primary = await build("primary", lookupEnvelope());
  assert.deepEqual(metrics(primary), { "metrics.plays_instagram": 31290541, "metrics.likes": 3050591, "metrics.comments": 4234 });
  assert.equal(primary.content_metadata.caption, "Monsoon trip in 20 seconds #travel #monsoon");
  assert.deepEqual(primary.content_metadata.tags, ["travel", "monsoon"]);
  assert.deepEqual(primary.audio_attribution, { uses_original_audio: true, audio_id: "987", artist_name: "creator", song_name: "Original audio", trend_metrics_available: false });
  // metadata 没给发布时间时由投影补上；会过期的 thumbnail_url 不当封面写进证据。
  assert.equal(primary.content_ref.published_at, "2026-10-01T12:34:30.000Z");
  assert.deepEqual(primary.content_metadata.thumbnails, []);
  assert.equal(JSON.stringify(primary).includes("cdninstagram"), false);

  const fallback = await build("fallback", lookupEnvelope({ route: "fallback", metrics: { plays_instagram: 900, plays_combined: 1500, likes: null, likes_hidden: true } }));
  assert.deepEqual(metrics(fallback), { "metrics.plays_instagram": 900, "metrics.plays_combined": 1500, "metrics.comments": 4234 });
  assert.match(fallback.public_metrics.find((item) => item.native_name === "metrics.plays_combined").note, /not an Instagram-only count/);
  assert.equal(fallback.audio_attribution ?? null, null);
  assert.ok(fallback.limitations.includes("The post hides its like count, so likes are unavailable, not zero."));
  assert.ok(fallback.limitations.some((text) => /answered through the Instagram detail route/.test(text)));
  assert.equal(fallback.acquisition[0].connector_id, "instagram-detail-proxy");

  // 合并值不冒充 IG 播放数：只有合并值时没有 plays_instagram 这一项。
  const combinedOnly = await build("combined", lookupEnvelope({ route: "fallback", metrics: { plays_instagram: null, plays_combined: 1500 } }));
  assert.deepEqual(metrics(combinedOnly), { "metrics.plays_combined": 1500, "metrics.likes": 3050591, "metrics.comments": 4234 });

  const empty = await build("nulls", lookupEnvelope({
    post: { caption: null, published_at: null }, audio: null,
    metrics: { plays_instagram: null, plays_combined: null, likes: null, likes_hidden: null, comments: null },
  }));
  assert.equal(empty.public_metrics, undefined);
  assert.equal(empty.content_metadata.caption, null);
  assert.equal(empty.content_ref.published_at, "unknown");
  assert.equal(empty.limitations.some((text) => /hides its like count/.test(text)), false);

  const legacy = await build("graphql", GRAPHQL_ENVELOPE);
  assert.deepEqual(metrics(legacy), { video_play_count: 1200, "edge_media_preview_like.count": 80, comment_count: 9 });
  assert.equal(legacy.content_metadata.caption, "Old caption #legacy");
  assert.equal(legacy.audio_attribution.audio_id, "55");
  assert.equal(legacy.content_ref.published_at, "unknown");

  const probe = String.raw`
import json, sys
sys.path.insert(0, sys.argv[1])
from compose_video_analysis import entailed_assumptions
print(json.dumps([entailed_assumptions(json.load(open(path)))[0] for path in sys.argv[2:]]))
`;
  const lines = JSON.parse(python(["-c", probe, scripts("analyze-content-video"),
    ...["primary", "combined", "graphql"].map((name) => path.join(temp, "analysis", `${name}-evidence.json`))]));
  assert.deepEqual(lines, [
    "A public play count is not reach and not an audience; 3050591 likes over 31290541 plays is not an engagement rate.",
    "A public play count is not reach and not an audience; 3050591 likes over 1500 plays is not an engagement rate.",
    "A public play count is not reach and not an audience; 80 likes over 1200 plays is not an engagement rate.",
  ]);
});
