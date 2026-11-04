import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";

// 2026-11-04 回归：YouTube 频道声明英国，模型在 request 里写了 "region": "GB" 却没写 region_source，
// 合并时没被频道声明覆盖，评估又把「无来源的国家」降成 UNKNOWN，报告写「注册国家未知」、差距项全缺。
const scripts = path.join(import.meta.dirname, "..", "skills", "plan-platform-monetization", "scripts");

function probe(code) {
  return JSON.parse(execFileSync("python3", ["-I", "-B", "-c", code, scripts], { encoding: "utf8" }));
}

test("频道声明的国家补上没有来源的 region，创作者自己说的国家保留", () => {
  const out = probe(`
import json, sys
sys.path.insert(0, sys.argv[1])
from assess_monetization import merge_observed
derived = {"schema_version": "observed-metrics.v1", "platform": "youtube", "observed_metrics": [],
           "region": {"region": "GB", "region_source": "account_declared"}}
def merged(**request):
    out = merge_observed({"platform": "youtube", **request}, derived)
    return [out.get("region"), out.get("region_source")]
print(json.dumps({
    "no_source": merged(region="GB"),
    "unknown_source": merged(region="US", region_source="unknown"),
    "absent": merged(),
    "declared": merged(region="GB", region_source="account_declared"),
    "stated": merged(region="US", region_source="user_stated"),
    "nothing_derived": [merge_observed({"platform": "youtube", "region": "GB"},
                                       {**derived, "region": None}).get("region_source")],
}))
`);
  assert.deepEqual(out.no_source, ["GB", "account_declared"]);
  assert.deepEqual(out.unknown_source, ["GB", "account_declared"]);
  assert.deepEqual(out.absent, ["GB", "account_declared"]);
  assert.deepEqual(out.declared, ["GB", "account_declared"]);
  assert.deepEqual(out.stated, ["US", "user_stated"]);
  assert.deepEqual(out.nothing_derived, [null]);
});

test("只有 get_channel 时也从 channel_profile 读到声明的国家；uploads 有国家时以它为准", () => {
  const out = probe(`
import json, sys
from datetime import date
sys.path.insert(0, sys.argv[1])
from derive_monetization_metrics import derive
def channel(country):
    profile = {"country": country} if country else {}
    return {"connector_id": "youtube-public", "operation": "get_channel", "status": "completed",
            "retrieved_at": "2026-11-04T05:00:00Z",
            "data": {"rows": [{"metrics": {"subscribers": 6000000}, "channel_profile": profile}]}}
def uploads(country):
    return {"connector_id": "youtube-public", "operation": "list_channel_uploads", "status": "completed",
            "retrieved_at": "2026-11-04T05:00:01Z",
            "data": {"rows": [], "coverage": {"next_page_token": ""}, "channel": {"country": country}}}
as_of = date(2026, 11, 4)
print(json.dumps({
    "channel_only": derive("youtube", [channel("gb")], as_of, {})["region"],
    "uploads_wins": derive("youtube", [channel("GB"), uploads("US")], as_of, {})["region"],
    "none": derive("youtube", [channel(None)], as_of, {})["region"],
}))
`);
  assert.deepEqual(out.channel_only, { region: "GB", region_source: "account_declared" });
  assert.deepEqual(out.uploads_wins, { region: "US", region_source: "account_declared" });
  assert.equal(out.none, null);
});
