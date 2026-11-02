# 多轮取数迁到后台：接口设计（2026-11-02）

规则见 `docs/skills-spec.md` KS9：一段多轮取数由后台一个接口做完，所有宿主调同一个接口。本文是把现有 8 个
带驱动的 Skill 迁过去的接口设计与顺序。后台实现在 kabo 仓，按 ADR-0021（服务端编排研究管线）的四件套范式做：
领域函数一份，`/mcp-pub`、旧 `/mcp`（按需）和 Web agent 内部接口三个入口共用。

## 现状

每轮取数是「驱动打印 `call:` → 宿主模型逐个调 → 逐个落盘 → 带 `--placed` 续跑」。按每次落盘算一步：

| Skill | 取数轮次 | 两轮之间的模型判断 | 典型步数 |
|---|---|---|---|
| review-creator-account | IG 2–9，YT 2–3，TikTok 1 | 无 | IG 大号约 31，小号约 10 |
| diagnose-reach-drop | IG/YT 2，TikTok 1 | 无 | 9–13 |
| verify-platform-rule | 页数 / 4 波 | 无 | 12–24 |
| research-youtube-trends | 长窗口 2–4，短窗口 2 | 长窗口：计划、Gate；短窗口：无 | 12–22 |
| research-instagram-trends | 1–3 | 计划、Gate | 11–17 |
| research-tiktok-trends | 3–4 | 计划、Gate | 22–28 |
| analyze-tiktok-competitors | 2–3 | Gate | 约 22 |
| analyze-creator-competitors | IG 5–10，YT 4 | 身份、意图、Gate、eligibility、条件补位分类 | 26–47 |

## 新接口

四个后台组合工具，只做确定性的多轮取数；轮次之间需要模型判断的地方仍留在宿主（判断点都不多）。

| 工具 | 做什么 | 服务的 Skill |
|---|---|---|
| `collect_account_history` | 1–12 个账号（并发 4）：主页 → 按规则翻页到上限；空历史重取一次；可带候补名单补位；身份解析不到返回候选 | 账号复盘、流量诊断、TikTok 竞品第二段、跨平台竞品（主体 / 选账号 / 内容）、TikTok 热门作者历史 |
| `collect_tiktok_video_candidates` | 每个意图按给定排序各搜一次，可选加日榜（为空往前补一天），结果按 query 与排序对回 | TikTok 热门、TikTok 竞品第一段 |
| `collect_youtube_recent_videos` | 2–4 次搜索 → 去重 → 详情按 50 个一块补全 | YouTube 热门短窗口 |
| `render_official_pages` | 1–32 个官方页面一次渲染（并发 4） | 平台规则核验 |

### 共同出参（`kabo-composite-fetch.v1`）

```json
{
  "schema_version": "kabo-composite-fetch.v1",
  "tool": "collect_account_history",
  "status": "completed | partial | failed",
  "calls": [
    { "tool": "instagram_list_reels", "parameters": { "...": "..." }, "group": "acct-0", "result": { "...": "与单个 /mcp-pub 数据工具的 result 完全相同的信封" } }
  ],
  "summary": { "...": "按工具不同，见下" },
  "coverage": { "...": "..." },
  "limitations": ["..."]
}
```

- `calls[]` 是后台实际发出的每一次单操作调用：`tool` 用 `/mcp-pub` 单数据工具名，`parameters` 是那次调用的完整入参（含后台算出的游标），`result` 是 `runConnectorOperation` 产出的信封原样（含 `connector_id`、`operation`、`status`、`evidence_ref`、`raw_sha256`、`request_id`、`data`）。脚本把每一项当作一次单工具调用落盘，下游解析代码不变。
- `group` 把调用归到输入里的某个账号 / 意图 / 页面（`acct-<i>`、`intent-<i>`、`page-<i>`），按输入顺序编号。
- 失败与缺口收进 `status: partial` + `limitations` + `coverage`，不抛错；领域函数抛异常才是工具失败。
- 预算：入参钉住扇出与页数上限；墙钟预算 90 秒，到点停止新请求、已拿到的照常返回并在 `coverage` 标出未完成项。
- 只在 `/mcp-pub` 与 Web 内部接口 `POST /internal/agent/research/<tool>` 注册（旧 `/mcp` 不注册）；`/mcp-pub` 首发带 `delivery: "download"`，下载字节是去掉 `download` 的整份出参。
- 重试：`service_unavailable` 同一请求原样重试 1 次；`rate_limited` 等 2 秒整组重试 1 次，仍失败记缺口；准入拒绝按 `runWithCapacityRetry`。

### `collect_account_history`

入参：

```json
{
  "platform": "instagram | youtube | tiktok",
  "accounts": [{ "handle": "...", "channel_id": "...", "user_id": "..." }],
  "reserves": [{ "handle": "..." }],
  "target_count": 6,
  "profile": true,
  "history": {
    "max_items": 100,
    "max_pages": 40,
    "page_size": 50,
    "since": "2026-08-01T00:00:00Z",
    "min_items_since": 6,
    "instagram_formats": { "posts": true, "reels": true, "per_format_max": 15, "balance": "fewer_rows_first" }
  },
  "on_identity_miss": "candidates | gap"
}
```

- IG：`instagram_resolve_profile` → 首轮 `instagram_list_posts` + `instagram_list_reels` → `balance: fewer_rows_first` 时每轮只给行数少的一侧翻一页，直到合计 `max_items`、两侧都没有下一页或到 `max_pages`；只要 reels 时只翻 reels（竞品内容段）。`per_format_max` 截断每侧（流量诊断）。
- YouTube：`youtube_get_channel`（handle 或 channel_id）→ `youtube_list_channel_uploads` 每页 `page_size`，到 `max_items` / `max_pages`。
- TikTok：`tiktok_list_account_posts`，`limit = max_items`。
- `since` + `min_items_since`：窗口内条数够了或翻到窗口外就停（竞品内容段的 `MIN_WINDOW_REELS` / `REEL_PAGES_MAX`）。
- 历史为空时重取一次。
- `reserves` + `target_count`：主页不可用的账号按顺序用候补补足到 `target_count`，补位只做一轮。
- 身份解析不到：`candidates` 时 IG 调 `instagram_search_accounts` 返回最多 10 个候选，账号状态 `needs_identity`；`gap` 时记缺口。
- `summary.accounts[]`：`{ group, input, resolved: {handle, id}, status: completed|partial|unavailable|needs_identity|replaced, replaced_by?, items_total, pages, candidates? }`。

### `collect_tiktok_video_candidates`

入参：`{ "intents": [{ "keyword": "...", "publish_time": 7 }], "sorts": [0, 1], "count": 30, "region": "US", "top_contents": { "date": "2026-11-01", "fallback_days": 1 } }`（`top_contents` 可省）。每个意图 × 每种排序一次 `tiktok_discovery_search_videos`；`top_contents` 给了就调 `tiktok_discovery_list_top_contents`，为空往前补 `fallback_days` 天。`group` 为 `intent-<i>-sort-<s>` 与 `top-<date>`。

### `collect_youtube_recent_videos`

入参：`{ "queries": [{ "q": "...", "published_after": "...", "order": "viewCount" }], "max_results": 50, "details": true }`。每个 query 一次 `youtube_search_videos`，去重后按 50 个一块调 `youtube_get_video`。

### `render_official_pages`

入参：`{ "pages": [{ "url": "...", "source_id": "..." }] }`，上限 32。每页一次 `official_page_render_page`，并发 4；`group` 为 `page-<i>`，`summary.pages[]` 给 `{group, url, source_id, status, final_url}`。

### 插件与 Web 怎么接

- `static_host.py` 认 `kabo-composite-fetch.v1`：`stage` / `fetch` 把 `calls[]` 拆成逐个单工具结果，按各自 `tool` 走现有校验落盘，另存一份整体摘要供驱动读 `summary` / `coverage`。
- 驱动每段只交出一个 `call:`（组合工具），续跑时从拆好的单工具结果里取数，后续解析不变。
- Web：agent-service 的 `RESEARCH_TOOLS` 加四项，`run_research_tool` 直接调；结果经 `evidence_refs` + `stage` 落盘，与插件同一条拆分逻辑。

## 顺序

1. 无判断点的：`collect_account_history`、`render_official_pages`、YouTube 短窗口。覆盖账号复盘、流量诊断、规则核验，也给后面两批打底。
2. 判断点少的：`collect_tiktok_trend_candidates`、`enrich_tiktok_shortlist`；TikTok 竞品复用它们和 `collect_account_history`。
3. 跨平台竞品异步任务与后台 prompt 节点，连同新 ADR。

每批：kabo 分支 → pre 验收 → 合 main（kabo 合入即上生产，需用户授权）→ 插件 Skill 切到新接口（R3，负责人回归）→ Web 适配本地验证。接口只增不改，旧插件和 Web 现网不受影响。

## 待定

- kabo 合入 main 即上生产，每批上线前由用户授权。
- 后台 LLM 调用目前只走 LiteLLM 虚拟 key 预算，不进用户额度；跨平台竞品上线前要定是否按用户计量。
- 旧 `/mcp` 是否也注册新接口（只在旧插件仍需要时）。
- 同步上限与网关超时的实际配置未核实。
