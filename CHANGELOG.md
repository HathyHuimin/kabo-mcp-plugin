# 变更说明

版本号以 `plugin.json` 为准；`.claude-plugin/plugin.json`、`package.json` 与 `package-lock.json` 的根包版本与之同步。

## 未发布

- `analyze-content-video` 同步到 creator-skills 0.14.29、`create-script-and-packaging` 同步到 0.2.27（冻结提交 `a9514ba30`）：本地视频抽帧改用哈希文件名、720px、限 90 帧 / 12 MiB 且先写开头和结尾，`--measure-signals` 测剪辑点与停顿进 `media.probed`，宿主机器转写用 `--origin speech_recognition` 记为「听到的」；健康的 job 在本轮内等约 3 分钟；测量、长视频文字、图文帖、剪辑请求拿不到时的处理写进 `references/evidence-reading.md`；脚本的 `video_analysis_sources` 只认 `video_analysis.json`；续集先核实上一集。两个 SKILL.md 正文不增长。
## 1.0.6（2026-11-02；GitHub Release）

- 推荐交付增加统一兜底：已有入选视频不会因 synthesis 留空而丢失；正常推荐为空时，最多展示三个本轮采到的视频参考，明确标注匹配或资料不足，不填造指标。原 Gate 和名额不改写，也不追加数据或模型调用。YouTube 一次重搜后仍覆盖不足时继续利用已有候选，不再直接中止报告。

- 三个 trends Skill 修复 #1866 复测发现的偏题规则与边界漏洞：查询不再强制历史画像 seed，
  Gate 始终按本次 Request.niche 判断相关性；零主打分类仍保留可靠基础画像。
  Analysis 显式记录全部入选视频（含未匹配主打分类者），写报告时只展示入选证据，Composer 和 Validator 共用入选范围检查。
  删除历史 seed 配额检查及其无用参数；撤回无画像过滤扩展，作者去重仍由原有分配步骤负责。
  查询语义仍由宿主判断；不新增目标文件、模型阶段或推荐策略，不改变搜索预算、阈值、后端 API。
- 仓库规则进 CI：恢复 `scripts/repo-rules.test.mjs`（供应商名、宿主专属机制、用例编号、溯源文档、字节码），并新增两条：Skill 的 Markdown 不写版本代号与日期；带脚本的 Skill 在正文开头写明前台子 agent 执行（KS2.4）。`analyze-creator-competitors`、`research-instagram-trends`、`research-youtube-trends` 的 references 去掉测量 / 观察日期，结论与数字不变。CodeRabbit 补三条判断规则：description 不写执行规则、交付契约前置、能交给校验脚本的例外不再加进正文。
- 规范清理（不改行为）：`create-script-and-packaging`、`develop-content-ideas` 的标题去掉 V3/V4 版本来历；`analyze-content-video` 版式说明与 `review-creator-account-deep` 等待说明去掉来历与日期，只留结论。
- `analyze-creator-competitors` 同步到 creator-skills 0.5.88（`c414fac55`）：重复竞品保留第一条、不再整份报告失败，主页 tab 链接按账号接受；
  缺 `competitor-eligibility.json`（或写错文件名）、缺 intents 文件时返回 `needs_correction`、不关闭 run；长表 Gate 漏打不超过 1/20 行时补 0 分并注明；
  `popular_topic` 只给类目 lane；YouTube `@handle` 用 `subject --platform youtube --handle` 先经 `youtube_get_channel` 解析成频道 id，裸 `@handle` 不再静默当 Instagram；
  证据与回复逐账号给出发帖频率（下限写「至少 N 条」），YouTube 主体历史一次取 50 条，备用账号分类后 `content` 不沿用 `--placed`；
  回复写明用户要的账号数与每次 6 个的自动发现上限，无 pattern 时仍列重点内容；`content --comments` 一次取评论，`comments` 保留为别名，schema 先于大段证据紧凑打印。
- 用 `static_host.py` 的 Skill：`stage` 拒收「状态是完成、却没有 `data`」的结果（宿主保存下载结果时丢了 `download`、只剩摘要），指回保存完整结果后 `fetch`。以前这种摘要会被当成证据放置，到后面的脚本才报错，补下载的完整结果也不再被读到。
- `review-creator-account` 同步上游 0.5.49：首次 `report` 就通过（从未返回 needs_revision）即为最终步，不再跑
  `verify-report`；`verify-report` 在 `report` 之前运行会直接提示先跑 `report`；YouTube 频道简介与订阅数进入身份；
  被拒的 YouTube handle 改提示用 `--channel-id`；`fetch` 先打印紧凑 schema 再打印输入。`review-creator-account-deep`
  第 1 步同步认 `report` 为最终步时打印的 `creator_report:`。
- TikTok 竞品（`analyze-tiktok-competitors`）同步 creator-skills 0.1.72：重复的竞品账号保留第一条、不再整份报告失败；展示译文按 id 匹配、只读 `text`，头字段可省略，单条译文不合规时退回原文并列在 `kept_source_text`（驱动会转出），不再整份失败；`search_query` 写明两到五个词。
- 用 `static_host.py fetch` 下载 Kabo 签名链接的 Skill：Python 默认信任库不存在时（macOS 上 python.org 装的 Python 没跑过 Install Certificates），`fetch` 改用 certifi 或系统自带的 CA 包，证书照常校验。以前这种机器上每次下载都报证书失败，模型只能自己用 curl 下载再 `stage`。
- 三个 trends Skill：`report` 阶段 Compose/Validate 拒收 synthesis 时，把 run 还原并返回 `needs_correction`（exit 0），在同一个 run 里修一次；第二次拒收才结束。同步自 creator-skills `4231cfa36`（#902，上游 IG 0.3.108 / TT 0.1.98 / YT 0.1.96）。只搬了这一处修复，`skills.lock.json` 的 `derivedFromVersion` 不变。
- 三个 trends Skill：Gate 打分表头在没指定地区和语言时加 `locale_fit_rule`，写明问句语言只决定回复语言，不要因为内容语言压低 `locale_fit`。同步自 creator-skills `e4b807f4`（#906，上游 IG 0.3.109 / TT 0.1.99 / YT 0.1.97）。
- 三个 trends Skill：`locale.region` / `locale.language` 只取用户明确点名的市场或内容语言，问句语言只决定回复语言，没点名时两项留空。同步自 creator-skills `77dd6179`（#909，上游 IG 0.3.110 / TT 0.1.100 / YT 0.1.98），写在各自的 references 里，SKILL.md 正文不增加。
- 三个 trends Skill：`discover` 新增 `--question-file`（`-` 从标准输入读用户原话）；`locale.region` / `locale.language` 不为空时必须在 `locale.named_in_ask` 里逐字引用原话中的片段，否则返回 `needs_correction`，只给一次修正机会。同步自 creator-skills `92824da5`（#919，上游 IG 0.3.111 / TT 0.1.101 / YT 0.1.99）。
- 三个 trends Skill：补齐 creator-skills `d2de2e4e` 到 `92824da5`（#919，IG 0.3.111 / TT 0.1.101 / YT 0.1.99）之间其余适用的改动（#879、#889、#891、#892、#894、#898）。没有 pattern 时报告在「Test this first」前列出排名前五条（Featured posts，同账号样本 ≥5 条才显示倍数）；TikTok 搜索热度小幅上涨不再显示成 1.0×；七天内问「哪些跑赢自身常态」或其他同账号倍数时留在 Workflow（`--policy user_named_span`）；IG 音频榜要逐首创意时按榜单顺序每首去重给一条；要的行数超过榜单时一句话说明榜单行数、不重拉（IG、TT）；TikTok 单次快照下 `current_high_performance` 要引用 ≥1.5× 自身常态或公开指标前四分之一的内容，否则用 `recurring_pattern`；IG 补充取数的 `content_key` 带 `instagram:` 前缀，拒收时原样重发一次再 `rank --placed`；Gate 打分说明写明没点名市场或内容语言时不因语言压低 `locale_fit`；IG description 写明题材内跑赢自身常态归本 Skill。规则写在 references（TikTok 的开场规则拆到新增的 `references/synthesis-openings.md`），IG SKILL.md 正文不增加。上游之后的账号画像系列（#914、#915、#918、#920）已由 #32、#33 移植进 main，本条不重复；cwd 措辞、manifest 等宿主相关改动不移植。

## 1.0.5（2026-10-30）

- 账号画像的有效画像与按人修改（三个 trends Skill 与 `review-creator-account`）：
  - `contracts/directory-mcp/` 重新固定到 kabo-sh/kabo#1875 的 main 合并提交 `927b75c47`，工具数 62 → 63：
    `get_creator_profile` 加可选布尔入参 `effective` 与门控出参 `effectiveContentProfile`，新增写工具
    `update_content_preferences`（`workspace:write`，不带 `idempotency_key`）；`runtime-contract.json`
    的 `requiredTools` 加上写工具。tools/list 与该提交的 golden 逐字节一致。
  - 提供方已用 `maxItems: 0` 修复 `effectiveContentProfile.formats` 的空数组 schema；撤掉元模式校验的临时绕行，
    全部钉住的工具入参、出参均通过 draft-07 元模式校验。
  - 手工移植 creator-skills `feat/reco2-trends-effective-profile`（kabo-sh/creator-skills#920，`12b91ffef`）：画像文件里有
    `effectiveContentProfile` 且字面量认得时读它，否则退回 `contentProfile` v1；Request 新增 `excluded_topics`、
    `market_region`、`own_content_ids`、`profile_basis`；计划校验、资格过滤与 Gate 都执行排除话题；主打项排第一；
    市场只作缺省检索地区（TikTok 搜索、YouTube collect，Instagram 不用）；同一运行中途换画像文件报错。本仓的 MCP 外壳拆读、
    不打印受众数与 `--without-include` 交接保持不变。
  - 读画像带 `effective: true`，被拒时去掉重发一次。四个 Skill 的 `allowed-tools` 加 `update_content_preferences`、
    `compatibility` 加 `workspace:write`；写入规则在新增的 `references/profile-edits.md`（四份同文），按 kabo CONTRACT §16.6
    （kabo-sh/kabo#1869）的确认规则执行，409 先重读，`insufficient_scope` 请用户批准补权后重试一次。
  - 新增有效画像夹具（TikTok 三份取自 kabo `effective-content-profile-v1` golden 的向量）与相应测试；README、
    `docs/submission.md` 补说明。上线前要重新走目录审核并重新做插件验收。
- 三个 trends Skill（`research-tiktok-trends`、`research-instagram-trends`、`research-youtube-trends`）接收后台账号画像：
  手工移植 creator-skills #918（`feat/reco-trends-allocation`，`e6fd8cfe7`）的 S1–S3。用户问自己已绑定账号的趋势时，
  先 `list_connected_accounts`、再 `get_creator_profile`（`include: ["content_profile"]`），完整结果原样存成文件交给
  `drive.py start --profile`；`--profile` 与 `--video` / `--post` 互斥。带画像时检索意图带上主打内容的种子词，
  TikTok 搜索带 `include: ["content_signals"]`、Instagram collect 带 `include: ["media_signals"]`，候选先过资格过滤，
  Gate 多一列主打内容编号，名额按主打内容权重分配，报告每类主打内容至少引用一条。规则在各自的
  `references/signature-content.md`。语言只是检索标签，不做硬过滤，不写进 `locale`。
  - 与 creator-skills 读法的差别：画像文件可以是完整的 `get_creator_profile` 结果（先拆 MCP 外壳，`isError` 拒收）或其
    `structuredContent`；`start` 不再打印受众数（只留在 Request 里给资格过滤用）；本宿主不替脚本取数，`include`
    被拒时由交接提示模型用 `--without-include` 重跑同一 stage，只把那一轮带 `include` 的调用去掉 `include` 再交一次。
  - 读画像被拒、工具不可用、缺 `workspace:read`、没有对应账号或 `contentProfile` 为 null 时不带画像，交出的调用与之前逐字节相同
    （`scripts/mcp-pub-sync.test.mjs` 钉住）。新增 `scripts/trends-profile.test.mjs` 与合成夹具 `scripts/fixtures/creator-profile/`。
  - 依赖 kabo #1849（连接器 `include`）与 #1851（`get_creator_profile` 的 `include`）。`contracts/directory-mcp/` 重新固定到
    kabo main 的提交 `48d5ee773`（工具数仍是 62，与该提交的 tools/list golden 逐字节一致；`annotations.md` 与
    `data-tool-bindings.json` 字节不变）。服务端还没上线这两项时，读画像会被拒或拿到 null，
    Skill 按「不带画像」运行。
- 复刻（`remake-viral-video`）支持旁白类参考视频（没人出镜、配音配画面）：移植网页端 creator-remake-web 0.1.30
  （creator-skills PR #896，runner V2-r63）。带字的画面由 agent 写动效（composition，里面要的图片用
  `make.images` 生成），写稿后先 `render_remake_video` + `preview` 渲出画面分页、用 `fetch-preview` 存到本地看图，
  改到满意再出片；成片前 runner 停一次给逐句对照图，`fetch` 存下后写 `beat_review` 用 `--beat-review` 返工，
  只重做不行的句子。旁白没有口播模型可选。
  依赖 kabo `mark/remake-preview-review`（`/mcp-pub` 放行 narration、`render_remake_video` 加 `preview` 与
  `beat_review`、preview 结果与逐句对照图放在 brief 信封里），`contracts/directory-mcp/` 已重新固定到其 commit
  `cf290a6cf`（只有两个复刻工具的入参和说明变了，出参不变）；合入后需按 kabo main 的 commit 重新固定。
- 第 8 步「没跑完」的细则移到 `references/when-it-does-not-finish.md`（SKILL.md 正文回到 2 万字符以内）。
- 新增 `scripts/remake-narration.test.mjs`：旁白画面检查、preview / review 参数、分页下载（不联网）。

## 1.0.4（2026-10-28；GitHub Release）

- `remake-viral-video`：参考视频被判为纯口播以外的类型时，告诉用户判成了哪一类（口播时摆弄实物、录屏/画中画、旁白），
  并说明网页端支持（#16；服务端原因见 kabo#1819）。
- `remake-viral-video`：两个输入齐了就直接开始，不再要求查已绑定账号、OAuth 或 workspace 数据（#17）。
- `remake-viral-video`：交稿生成时若同一账号已有另一条复刻在跑，服务端返回 `quota_exceeded`（`reason: in_flight`，
  kabo#1822），技能等那条结束后用同样的参数重新提交（#17）。
- 视频分析（#14）：单词后面的点（如 `obs.14`）不再被当作以点开头的小数。
- `remake-viral-video`：被拒时同时告诉用户判成了哪一类、网页端可以复刻这一类；第一次复刻的开场说明在第一条消息已带齐两个输入时也要先说，
  且逐条列出（复刻什么、哪些会在生成前被拒且不收费、边缘情况可能效果不好）。
  生产回归（Codex `gpt-5.6-luna`，OAuth 连 `kabo.sh/mcp-pub`，kabo-api 镜像 `bd4c6798`，runner `sha256:103d39d8…`）：
  正常口播交付（job `fe1d0da5`，约 21 分钟、$0.55，8 帧逐帧检查通过；27 秒处字幕压到抬起的手，runner 质检未报）；
  实物参考视频三次均在生成前以 `unsupported_type` 拒绝（`e04da234`、`b5d74607`、`cd8d2432`），原因写明「口播时展示或摆弄实物」
  并说明网页端支持，未渲染、无花费；修复后的版本会把「网页端支持」转告用户。已知：开场说明里「被拒不收费」一句 GPT 仍常略去。
  未在生产覆盖：稿件被退回一次后的第二次提交、#1822 的 in_flight 拒绝。

## 1.0.3（2026-10-28；GitHub Release）

- 新增第十一个 Skill `remake-viral-video`：把 60 秒以内的爆款纯口播视频复刻成创作者本人出镜的成片（MP4）。
  移植自网页端 creator-remake-web 0.1.23（creator-skills `2206478d`）的写稿关口（读音校对、四个改写位、
  画面文字与继承台账、来源规则、四份文档），只做纯口播；录屏/画中画、实物、旁白与网页端专有机制未移植。
  依赖 `/mcp-pub` 新增的 `start_remake_video`、`get_remake_video`、`render_remake_video`（kabo#1777），
  三个工具已随 kabo#1777 重新固定进 `contracts/directory-mcp/`（62 个工具，另含 kabo main 上新增的两个 TikTok 话题工具与十一个个人资产工具），
  `runtime-contract.json` 的 `requiredTools` 已补上。固定来源是 kabo#1777 的合入 commit `005fcbc6d`。
  第一次提出复刻时先用两三句话说明支持与会被拒的参考视频（同网页端 0.1.26）。
  生产验收：Codex 0.164 经 OAuth 安装本包、连 `kabo.sh/mcp-pub` 跑通一条纯口播（job `ed76cc05`，runner r56，
  约 19 分钟、$0.53，成片抽帧检查通过）；Claude Code 与其他宿主未实测。
  生产 runner 换成 r58q（开头快速粗筛）后回归：正常口播在开头粗筛被放行并交付（job `b04218cd`）；两人同框的参考视频
  在开头粗筛 30 秒内以 `out_of_scope` 被拒、未做付费生成（job `0d1575bb`）。交付结果不含终审结论，
  技能不再声称成片「通过终审」，改为建议用户发布前完整看一遍。
- 视频分析（#12）：来源本身写成「27B / 27 billion」这类带量级词的数字，不再被当作编造而中断诊断。
- 个人资产工具（#11）：授权 workspace 权限后可读已绑定账号、创作者画像、社媒连接状态、脚本与视频库，
  保存或更新脚本，绑定公开账号；`review-creator-account` 与 `create-script-and-packaging` 按需使用。

## 1.0.2（2026-10-27；GitHub Release）

- 视频分析：服务端抽帧少于采样计划（如请求 120 帧、返回 119 帧）时不再中断报告生成，`data_note` 写明
  「119 extracted frames of 120 requested」；帧记录多于计划仍拒绝。
- 视频分析与深度复盘的抽帧请求封顶 60 帧（此前默认顶到服务端上限 120）：`static_host.py job-call` 按各 Skill 的
  `FRAME_CAPS` 补上 `max_frames`，显式超过上限的值被拒绝；开头三秒密度不变，只摊薄正文采样。
  未在真实宿主或生产环境实测，提速幅度未计时。
- README 补充 MCP 与 Skills 的安装、更新说明。

## 1.0.1（2026-10-27；GitHub Release）

- 支持用 skills CLI 单独安装十个 Skills（`npx skills add kabo-sh/kabo-mcp-plugin#stable`）：README 说明安装、
  另行添加 `/mcp-pub` 连接、版本与更新方式；`stable` 分支只指向已发布 tag，作为 `npx skills update` 的更新渠道。
  每个 SKILL.md 新增 `license: MIT`，许可随单独安装的 Skill 分发，包检查要求与 `plugin.json` 一致。
  已验证安装完整与 Codex 0.164 识别；登录与端到端运行待验收。
- 仓库协作约定（AGENTS.md）改为英文，提交说明与 PR 使用英文。

## 1.0.0（2026-10-23 首发；2026-10-27 随公开仓库重新发布）

- 2026-10-27 重新发布：仓库转为公开，开发历史不随公开仓库发布；ZIP 从新提交重新构建，旧 ZIP 作废。
- 插件采用 MIT 许可：新增根目录 `LICENSE` 并随 ZIP 打包，`plugin.json`、`.claude-plugin/plugin.json` 与 `package.json` 声明 `MIT`；
  `.claude-plugin/plugin.json` 显式声明隐私政策链接，`claude plugin validate --profile directory` 不再报目录提交问题。
  许可只覆盖本仓内容，kabo.sh 服务仍受其服务条款约束。
- 公开仓库前的清理：删除 1.x/2.x 历史迁移记录，文档与代码注释不再引用非公开仓库的 issue、PR 与设计文档，
  README 去掉私有仓读权限的安装要求。
- 对外发布版本统一为 `1.0.0`，替代此前开发阶段的包版本编号；不是回退到旧代码。
- 保留最新主分支的十个固定 Skills、`/mcp-pub` 连接、完整证据下载、宿主适配与打包修复；
  deep 只使用本对话中已完成的普通账号复盘校验输出。
- 同步 portable / Claude Code 清单、npm 根包及锁文件、解包测试、发布文案和安装迁移说明；构建产物为 `kabo-1.0.0.zip`。
- 统一英文体验：深度复盘默认英文，目录摘要与评论证据的固定说明改为英文，修复报告单复数；README 提供英文安装与排错说明，保留显式中文输出及来源原文。
- `1.0.0` 的数值低于此前 `3.x`，旧安装需刷新市场并确认实际加载版本；不假设宿主会自动降号更新。
- 来源 Skill 业务版本、依赖与 schema 版本独立维护。
- GitHub Release 发布不改变生产验收或市场审核状态，仍需绑定本次 commit 与 ZIP 重新验收。

## 旧编号（3.x 及更早，开发阶段）

以下只保留升级时需要知道的事实，不作为当前发布版本。

- 3.1.1（2026-10-22）：读帧改走 `delivery: "download"` 下载交付，脚本把图片落到运行目录，不再依赖宿主
  会话目录；十个 Skill 要求子 agent 在前台执行，避免 `claude -p` 退出时丢失报告。从 3.0.1 及以后更新不需要重新授权。
- 3.1.0（2026-10-22）：新增 Claude Code 市场清单 `.claude-plugin/marketplace.json`（插件 ID `kabo@kabo-mcp-plugin`），
  Codex 市场名仍是 `kabo-plugin-openai`；图标改为 K Fold 圆角图标；十个 Skill 按 `docs/skills-spec.md` 改造；
  数据工具默认走 10 分钟有效的 GCS 签名链接下载，`static_host.py fetch` 核对大小与 sha256 后落盘。
  `/mcp-directory` 已从服务端删除，从 3.0.0 及更早版本更新须为 `/mcp-pub` 重新授权。
- 3.0.1：连接与 OAuth resource 统一迁到 `/mcp-pub`，从旧版更新后需要为新地址重新授权。
- 3.0.0：改为十个固定 Skills 随包分发、在宿主执行；动态 Skill 下载、签名包缓存与 lifecycle hooks 退出。
