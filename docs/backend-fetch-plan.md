# 多轮取数迁到后台：接口设计（草案，2026-11-02）

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

| 接口 | 做什么 | 服务的 Skill |
|---|---|---|
| `collect_account_history` | 一到多个账号（上限 12，并发 4）：解析身份 → 主页 → 按规则翻页到条数上限。IG 每轮取行数少的一侧，YT 上传列表每页 50；空历史重取一次；身份解析不到时返回候选列表（`needs_identity`），不报错 | 账号复盘（上限 100）、流量诊断（上限 30，IG 每种格式 15）、TikTok 竞品第二段、跨平台竞品选账号与内容、TikTok 热门作者基线 |
| `render_official_pages` | 一组官方页面（上限 32，并发 4）一次渲染，逐页给出可达性、重定向出域、`retrieved_at` | 平台规则核验 |
| `collect_tiktok_trend_candidates` | 每个意图两种排序的搜索 + 前一天榜单（为空补前两天），按 query 对回关键词，返回 `candidate_set_id` | TikTok 热门、TikTok 竞品第一段 |
| `enrich_tiktok_shortlist` | 按 `candidate_set_id` + 入选 id 拉作者历史、算基线 | TikTok 热门 |
| YouTube 短窗口 | 扩 `collect_youtube_trend_candidates`：≤7 天窗口时搜索 + 详情分块一次做完 | YouTube 热门短窗口 |
| `run_creator_competitor_research`（异步任务） | 跨平台竞品整段：发现 → 后台 prompt 节点打 Gate 分 → 选账号与补位 → 后台 prompt 节点判 eligibility → 内容与评论。宿主只做身份确认（可能要问用户）和最后的分析写作 | 跨平台竞品 |

共同约定（照 collect/enrich）：
- 失败与缺口收进 `status: partial` + `limitations` + `coverage`，不抛错；重试规则以现有 4 份 `connector_driver.py` 里最全的一份为准（`retry_lost`、限流整轮再交一次只做一次）。
- 预算由调用方给的上限钉住，额度仍按每次 provider 调用记到用户名下。
- `/mcp-pub` 首发就带 `delivery: "download"`；Web 走 `POST /internal/agent/research/<tool>`，agent-service 的 `RESEARCH_TOOLS` 加一项即可，不需要宿主专用分支。
- 超过同步上限（约 120 秒）的走异步任务（start/get + `idempotency_key`），照 `analyze_content_video` / remake 的做法。多账号历史在账号多时可能超时，先量再定是否异步。

后台 prompt 节点（只在跨平台竞品用）：
- 把 `gate-and-eligibility.md` 里打分与 eligibility 的引导抽成独立 prompt，版本号写进结果（`prompt_version`），照 video-review 的做法。
- 用产品 LLM key（`KABO_LLM_*`，经 LiteLLM），在 composition 里注入 provider，不在领域包里读环境变量。
- ADR-0021 规定两段之间的语义判断归模型（宿主），这里改成「判断点多时归后台 prompt 节点」，需要新写一份 ADR。

## 迁移后

每个 Skill 的驱动只剩「准备参数 → 宿主调一次接口 → 落盘续跑 → 算分出报告」。判断点只有一两个的 Skill（热门三件、TikTok 竞品）在判断点切开，最多两三次后台调用。

| Skill | 后台调用次数 |
|---|---|
| 账号复盘、流量诊断、规则核验、YouTube 短窗口 | 1 |
| IG / YouTube 长窗口热门 | 2（已是） |
| TikTok 热门、TikTok 竞品 | 2 |
| 跨平台竞品 | 1 个异步任务 |

迁完一个 Skill，就在同一 PR 里把 `scripts/fetch-orchestration.test.mjs` 的轮次数改小；删掉 outbox 残留时把文件从名单里拿掉。

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
