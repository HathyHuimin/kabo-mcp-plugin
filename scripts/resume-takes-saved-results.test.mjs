// 交接与续跑之间只剩「存文件 → 跑一条续跑命令」（docs/skills-spec.md KS8）：交接给每条调用点名一个
// 运行目录 host-results/ 下的绝对路径，续跑（--placed）自己对这些文件跑 static_host 的 fetch（没有就绪下载时
// stage），再放置。实测里模型在两步之间走偏过：把组合结果的 calls[] 逐个拿去 get_research_data_download、
// 用命令行参数写文件、在错的目录里跑 stage。这里对每个驱动 Skill 的 connector_driver.py 副本（连同它自己的
// static_host.py）在进程内走一遍：下载路径（假 HTTPS，不联网）、内联 stage 回退、点名文件缺失时的提示、
// 同一文件不重复取。
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const SIGNED = "https://storage.googleapis.com/kabo-media/_temporary/research-export/v1/resume.json?X-Goog-Signature=SIGNATURE";

// 每个场景一个进程：导入该 Skill 的 connector_driver / static_host，替换 urllib 的 HTTPS 收发，
// 先让 fetch_round 交接（打印并 SystemExit 0），再按场景把结果存进点名的文件、带 --placed 续跑。
const HARNESS = String.raw`
import argparse, base64, contextlib, http.client, io, json, os, sys, urllib.request, urllib.response

scripts, spec_file = sys.argv[1], sys.argv[2]
spec = json.load(open(spec_file))
sys.path.insert(0, scripts)
requests = []

class FakeHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        requests.append(req.full_url)
        body = base64.b64decode(spec["download"])
        headers = http.client.parse_headers(io.BytesIO(b"Content-Type: application/json\r\n\r\n"))
        response = urllib.response.addinfourl(io.BytesIO(body), headers, req.full_url, 200)
        response.msg = "fake"
        return response

import static_host
static_host.build_opener = lambda: urllib.request.build_opener(static_host.NoRedirect(), FakeHTTPS())
run = static_host.init(spec["workspace"])
os.environ["KABO_STATIC_RUN_DIR"] = str(run)
os.chdir(run)
import connector_driver as cd

def place():
    print("PLACED", json.dumps(sorted(p.name for p in (run / "snapshot/.host-evidence").glob("*.meta"))))

def step(placed):
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out):
        try:
            cd.fetch_round(spec["calls"], argparse.Namespace(placed=placed), place)
        except SystemExit as stop:
            code = stop.code or 0
        except cd.StageError as error:
            print("STOPPED", error)
            code = 2
    return {"code": code, "out": out.getvalue()}

report = {"run": str(run), "handed": step(False)}
saved = [line[len("  save: "):] for line in report["handed"]["out"].splitlines() if line.startswith("  save: ")]
report["saved"] = saved
if spec["result"] is not None:
    for name in saved:
        with open(name, "w") as stream:
            json.dump(spec["result"], stream)
report["resumed"] = step(True)
report["again"] = step(True) if spec.get("again") else None
report["requests"] = requests
print(json.dumps(report))
`;

const envelope = (connector_id, operation, n) => ({
  connector_id, operation, status: "completed", summary: `Found ${n} rows`, limitations: [],
  evidence_ref: `ev-${operation}-${n}`, raw_sha256: "a".repeat(64), request_id: `req-${operation}-${n}`,
  data: { rows: Array.from({ length: n }, (_, i) => ({ id: `${operation}-${i}`, caption: `Neutral row ${i}` })) },
});
const composite = (tool, calls) => ({
  schema_version: "kabo-composite-fetch.v1", tool, status: "completed",
  calls: calls.map(([callTool, connector, operation, n], index) => ({
    tool: callTool, group: `g-${index}`, parameters: { n }, attempts: 1, result: envelope(connector, operation, n) })),
  summary: {}, coverage: { provider_calls: calls.length }, limitations: [],
});
const history = (platformCalls) => ({ composite: "collect_account_history", calls: platformCalls,
  arguments: { platform: "instagram", accounts: [{ handle: "neutral.creator" }], delivery: "download" } });
const igHistory = history([["instagram_resolve_profile", "instagram-discovery", "resolve_profile", 1],
  ["instagram_list_reels", "instagram-discovery", "list_reels", 3]]);
const ttCandidates = { composite: "collect_tiktok_video_candidates",
  calls: [["tiktok_discovery_search_videos", "tiktok-discovery", "search_videos", 2], ["tiktok_discovery_search_videos", "tiktok-discovery", "search_videos", 1]],
  arguments: { intents: [{ keyword: "bead craft ideas", publish_time: 30 }], sorts: [0, 1], count: 30, delivery: "download" } };

// 每个驱动 Skill 交出的那种调用：组合工具，或交接成单个具名数据工具的 connector 请求。
const DRIVERS = {
  "analyze-creator-competitors": igHistory,
  "analyze-tiktok-competitors": ttCandidates,
  "diagnose-reach-drop": igHistory,
  "research-instagram-trends": { single: ["instagram-baseline-proxy", "search_reels", "instagram_baseline_search_reels"] },
  "research-tiktok-trends": ttCandidates,
  "research-youtube-trends": { single: ["youtube-public", "get_video", "youtube_get_video"] },
  "review-creator-account": igHistory,
  "verify-platform-rule": { composite: "render_official_pages",
    calls: [["official_page_render_page", "official-page-render", "render_page", 1]],
    arguments: { pages: [{ url: "https://help.example.com/rules" }], delivery: "download" } },
};

function scenario(driver, { download, saved = true }) {
  let calls; let full;
  if (driver.composite) {
    calls = [{ tool: driver.composite, arguments: driver.arguments }];
    full = composite(driver.composite, driver.calls);
  } else {
    const [connector, operation] = driver.single;
    calls = [{ tool: "data_connector_run", arguments: { connector_id: connector, operation, params: { n: 2 } } }];
    full = envelope(connector, operation, 2);
  }
  const bytes = Buffer.from(JSON.stringify(full));
  let result = full;
  if (download) {
    const { calls: _calls, data: _data, ...rest } = full;
    result = { ...rest, download: { status: "ready", kind: "envelope", url: SIGNED, expires_at: "2099-01-01T00:10:00Z",
      sha256: sha256(bytes), bytes: bytes.length, content_type: "application/json" } };
  }
  return { calls, result: saved ? { content: [{ type: "text", text: JSON.stringify(result) }], structuredContent: result } : null,
    download: bytes.toString("base64"), again: true };
}

async function drive(t, skill, spec) {
  const temp = await mkdtemp(path.join(os.tmpdir(), "kabo-resume-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const file = path.join(temp, "spec.json");
  await writeFile(file, JSON.stringify({ ...spec, workspace: temp }));
  const output = execFileSync("python3", ["-I", "-B", "-c", HARNESS, path.join(root, "skills", skill, "scripts"), file],
    { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
  return JSON.parse(output.trim().split("\n").at(-1));
}

for (const [skill, driver] of Object.entries(DRIVERS)) {
  const tool = driver.composite ?? driver.single[2];

  test(`${skill}：交接只点名绝对路径与一条续跑命令；续跑自己下载（假 HTTPS）、校验并放置，再跑不重复取`, async (t) => {
    const report = await drive(t, skill, scenario(driver, { download: true }));
    const handed = report.handed.out;
    assert.equal(report.handed.code, 0, handed);
    assert.deepEqual(report.saved, [path.join(report.run, "host-results", `01-${tool}.json`)]);
    assert.match(handed, /^call: /m);
    assert.match(handed, /never call get_research_data_download/);
    assert.match(handed, /never by passing the result as a command-line argument/);
    assert.doesNotMatch(handed, /static_host\.py/, "the model runs no fetch or stage command itself");
    const resume = handed.split("\n").filter((line) => line.startsWith("Then run this one command"));
    assert.equal(resume.length, 1, handed);
    assert.ok(resume[0].includes(`cd '${report.run}' && `) && resume[0].endsWith(" --placed"), resume[0]);
    assert.equal(report.resumed.code, 0, report.resumed.out);
    assert.match(report.resumed.out, new RegExp(`01-${tool}\\.json: fetched ${tool}`));
    assert.match(report.resumed.out, /^PLACED \[.+\]$/m);
    assert.deepEqual(report.requests, [SIGNED], "one download, made by the resume");
    // 同一命令再跑：这一轮已放置，不再下载。
    assert.equal(report.again.code, 0, report.again.out);
    assert.doesNotMatch(report.again.out, /fetched|staged|PLACED/);
  });

  test(`${skill}：结果没有就绪下载时续跑退回 stage；${driver.composite ? "模型存下的整份组合结果照收" : "内联信封照收"}`, async (t) => {
    const report = await drive(t, skill, scenario(driver, { download: false }));
    assert.equal(report.resumed.code, 0, report.resumed.out);
    assert.match(report.resumed.out, new RegExp(`01-${tool}\\.json: staged ${tool}`));
    const placed = JSON.parse(/^PLACED (.+)$/m.exec(report.resumed.out)[1]);
    assert.equal(placed.length, driver.composite ? driver.calls.length + 1 : 1, report.resumed.out);
    assert.deepEqual(report.requests, []);
  });

  test(`${skill}：续跑时点名的文件不存在，一句话点名文件与该存什么，什么都不放`, async (t) => {
    const report = await drive(t, skill, { ...scenario(driver, { download: false, saved: false }), again: false });
    assert.equal(report.resumed.code, 2, report.resumed.out);
    assert.ok(report.resumed.out.includes(
      `STOPPED no saved result at ${report.saved[0]} (save the complete ${tool} result there)`), report.resumed.out);
    assert.doesNotMatch(report.resumed.out, /PLACED/);
  });
}

// research-instagram-trends 的精简补全交接自己打印（不走 call: 行），同样点名绝对路径、记下待取文件，续跑照样取。
test("research-instagram-trends：精简补全交接点名绝对路径与一条续跑命令；文件缺失时续跑点名它", async (t) => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "kabo-resume-lean-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const script = String.raw`
import argparse, contextlib, io, json, os, sys
sys.path.insert(0, sys.argv[1])
import static_host
run = static_host.init(sys.argv[2])
os.environ["KABO_STATIC_RUN_DIR"] = str(run)
os.chdir(run)
import drive, connector_driver as cd
sent = {"items": [{"url": "https://www.instagram.com/reel/NEUTRAL1/"}], "window": {"since": "2026-10-01T00:00:00Z"}}
calls = [{"tool": "enrich_instagram_shortlist", "arguments": sent}]
def step(placed):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            cd.fetch_round(calls, argparse.Namespace(placed=placed), lambda: print("PLACED"),
                           hand_over_calls=lambda: drive.hand_over_lean(sent))
        except SystemExit:
            pass
        except cd.StageError as error:
            print("STOPPED", error)
    return out.getvalue()
print(json.dumps({"run": str(run), "handed": step(False), "resumed": step(True)}))
`;
  const report = JSON.parse(execFileSync("python3", ["-I", "-B", "-c", script,
    path.join(root, "skills", "research-instagram-trends", "scripts"), temp], { encoding: "utf8" }).trim().split("\n").at(-1));
  const file = path.join(report.run, "host-results", "01-enrich_instagram_shortlist.json");
  assert.ok(report.handed.includes(`  save: ${file}`), report.handed);
  assert.match(report.handed, /never call get_research_data_download/);
  assert.doesNotMatch(report.handed, /static_host\.py/);
  assert.match(report.handed, /^Then run this one command right away .* --placed$/m);
  assert.ok(report.resumed.includes(`STOPPED no saved result at ${file} (save the complete enrich_instagram_shortlist result there)`), report.resumed);
});
