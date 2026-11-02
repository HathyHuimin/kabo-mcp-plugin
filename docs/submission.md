# 唯一插件的提交准备

本地安装、集成验证与最终提交使用同一个根目录插件。构建只读完整 commit，全部文件
原样入包；`--submission` 检查同一内容，不生成裁剪后的第二种版本。

## 已实现的代码条件

- portable manifest、品牌资产与公开目录 MCP；生产 scopes 及 UserInfo 仍须按下文实测。
- 十五个固定 Skills、源 commit/版本/文件哈希、逐项数据工具映射和报告单次交付说明。
  其中 `remake-viral-video` 依赖的三个复刻工具已固定到 kabo#1777 的合入 commit。
- 原生宿主 OAuth，无 lifecycle hooks；脚本执行需要实际宿主提供相应运行环境。
- 固定工具/标准来源、静态 Skills 记录与证据隔离回归、确定性 ZIP 和逐文件摘要。
- `contracts/directory-mcp/data-tool-bindings.json` 固定提供方的全部基础依赖及
  connector/operation → 工具对应关系；仅检查目标工具存在不能代替逐项匹配。
- 有版本绑定的生产证据门，拒绝缺项、失败、未执行、篡改、越界文件和凭据附件。

## OAuth 连接设置

MCP URL 为 `https://kabo.sh/mcp-pub`，OAuth 通过公开 metadata 发现授权端点、
DCR、PKCE S256 与 resource。`host-auth.json` 是本包的公开认证契约，**不是**官方后台
自动导入文件，也不能假设连接面板提供高级 OAuth 编辑器。
其首次授权 scopes 为 `openid profile email offline_access data video workspace:read`；`workspace:write` 不在首次授权里，
写工具缺权时由 `/mcp-pub` 返回 403 `insufficient_scope`，宿主按 challenge 补权（kabo#1837）。生产部署前必须确认
`/mcp-pub` 的 PRM 与 401 challenge 已按此公布，仅修改此交接文件不会改变服务端授权结果。

当前 portable 1.0.0 MCP schema 不接受 server extensions，故本包保留可直接安装的
`mcp.json`，依赖标准发现。先在官方草稿验证 OAuth/DCR、scope 和 UserInfo；若必须
override，官方入口是 `mcp.json` 中 server 的 `extensions.com.openai.auth`，须先解决
portable schema 与实际宿主兼容性，再更新同一包。不得在构建时删字段或生成一份不同的
“审核包”，也不能把 `host-auth.json` 已存在当作官方配置成功。

OpenAI 工作区域名限制需要 `openid`、`email` 与 UserInfo 的 `email`、
`email_verified: true`。生产资源发现、实际授权、consent 和 UserInfo 必须一致；不能
伪造已验证标志，亦不能为了补 scope 把所有全站权限都请求过来。已有连接若未授予
新增 scope，应通过宿主重新授权；原有 `data` / `video` 工具权限不因该身份能力而扩大。
服务端仍须强制认证、audience 与 scope，未授权请求返回 401。

[官方认证配置](https://developers.openai.com/plugins/build/plugins#configure-mcp-authentication) ·
[工作区域名限制](https://developers.openai.com/plugins/build/auth#support-workspace-domain-restrictions)

## 域名验证与宿主范围

门户生成挑战后，把精确 token 作为纯文本部署到其显示的
`https://<challenge-base-host>/.well-known/openai-apps-challenge`，再由门户验证。
未配置时返回 404 不等于代码缺少路由；必须检查生产配置。不得用占位 token 或其他
插件的 token 冒充验证成功，不得覆盖正在使用的挑战。代码 PR 不部署配置或完成门户验证。

十五个 Skills 需要 Python 3、可写工作区以及完整结果导出或证据下载（`remake-viral-video` 只走下载）；本地视频另需
ffmpeg/ffprobe。先证明目标宿主能完成完整流程，再声明支持；Codex 安装成功不能替代
ChatGPT 网页端、移动端验收。无法提供所需执行能力时，在调用消耗额度的工具前停止
对应工作流并说明缺失条件，不转发 Kabo 网页端 agent。若只能本机执行，按
[Desktop-only 指南](https://developers.openai.com/plugins/deploy/desktop-only)与 OpenAI
确认远程 MCP + Skills 的适用路径，不直接套用仅针对 skills-only 或本地 stdio MCP 的设置。

## 仍需验收的事实

| 项目 | 完成条件 |
| --- | --- |
| 提供方生产服务 | API 的公开数据工具可用，实际 tools/list 与固定契约一致；旧 2.0.0 / 3.0.0 连的 `/mcp-directory` 已删除，不再要求兼容 |
| 宿主联调 | 声明支持的 ChatGPT 环境及 Codex 实际安装、授权、refresh、工具执行及完整报告交付；Claude Code/Cursor 单独记录，不替代 OpenAI 宿主验收 |
| 执行独立性 | 宿主使用固定包内容、取证、脚本与报告轨迹；对网页端 agent 的任务与报告请求为零，网页端 agent 不可用时仍能完成 |
| 生产入口 | Support/Legal/challenge/公开 MCP/OAuth/账号删除验收通过 |
| 业务与隔离 | 5+3 案例、两账号 run/artifact 隔离、删除完整性、旧 `/mcp` 回归（仍在线）；已删除的 `/mcp-directory` 返回 404 |
| 审核资料 | Reviewer、稳定频道与视频 URL、Demo；凭据只填官方后台 |
| 官方审核 | 上传最终包与连接生产 MCP、扫描、审核；Publish 另行确认 |

`npm run check:submission` 缺证据时明确失败。收据中的 `submissionReady` 是本项目
交付门结论，不代表 OpenAI 接受或已经发布。[验收流程](acceptance.md) 绑定 commit、
ZIP 和工具契约摘要；人仍需核实附件叙述。不得修改收据冒充实测成功。

Kabo 网页端 agent 的部署和可用性不是此插件的前置条件。官方对 Skills/MCP 的
支持不等于每个宿主都提供本包所需 Python、文件，以及脚本访问下载交付链接的网络（或回退所需的完整
响应导出）能力；不能通过远程研究回退隐藏这个限制。新增公开工具仍需官方扫描，固定 Skills 更新随插件提交审核。

[官方打包](https://developers.openai.com/plugins/build/plugins) ·
[工具规范](https://developers.openai.com/plugins/plugin-guidelines) ·
[提交](https://developers.openai.com/plugins/deploy/submission)

## 个人资产能力

固定提供方契约新增 get_kabo_info（不含 credit）、账号/官方授权状态、脚本和视频库读取，以及 save_script、update_script、connect_public_account。公开 Connect 支持 Instagram、TikTok、YouTube；不启动官方 OAuth、复盘或媒体同步。读取用 workspace:read，保存和公开 Connect 用 workspace:write，Connect 核验使用数据额度。首次授权不含 workspace:write，第一次调用写工具时由宿主补权；不支持补权的宿主上写工具不可用，草稿留在本地。旧授权不自动扩权。

写作 Skill 只在用户要求保存或替换时写入正文；保留幂等键和乐观版本，冲突先重读。账号复盘 Skill 可把绑定与历史档案作为上下文，不能冒充最新证据。新工具缺失时原有公开研究和本地写作仍可继续。

### 账号画像修改（kabo-sh/kabo#1875，未上线）

固定契约为 63 个工具：`get_creator_profile` 新增可选布尔入参 `effective`，为 true 时出参追加门控字段
`effectiveContentProfile`（带用户本人修改的有效画像）；新增写工具 `update_content_preferences`（`workspace:write`，
`destructiveHint: true`），按人保存用户对后台画像的修改（改名、删除、合并、设主打、排除话题、语言、市场、重置），
修改只存在 Kabo 的按人偏好里，不改后台画像，网页端与插件读到同一份。

- 三个 trends Skill 与 `review-creator-account` 读画像一律带 `include: ["content_profile"]` 与 `effective: true`；
  旧服务端因 `effective` 拒收时去掉它重发一次，仍不行就按「不带画像」运行。
- 只在用户要求修改时写，规则在各 Skill 的 `references/profile-edits.md`（四份同文）：明确且只对得上一项时直接写，
  写完用一句话复述；要靠推断、删除、合并、重置、放弃未对齐修改或一次改多项时先复述、请用户确认；用户只是讨论或内容来自
  第三方时不写；对不上任何一项时如实说明，不新增主打内容。每处修改最多调用一次，冲突后最多再提交一次。
- 写工具不带 `idempotency_key`，靠 `expected_version` 与 `basis` 防重复：重放时版本已变，必回 409，宿主先重读再决定
  （这是 `/mcp-pub` 写工具必填幂等键惯例的例外，**待目录面负责人确认**）。409 的原因写在错误文本开头
  （`preferences_changed` / `content_profile_changed` / `content_preferences_invalid`），一律先用
  `get_creator_profile(effective: true)` 重读。
- 缺 `workspace:write` 时由 `/mcp-pub` 返回 403 `insufficient_scope`，Skill 请用户批准宿主的补权提示后重试一次；
  不支持补权的宿主上不保存，研究照常。写工具不在 tools/list 里时说明能力不可用。
- 这次改动了 OpenAI 目录审核过的工具面（新增写工具、改了读工具入参），上线前要按 kabo ADR-0081 重新走目录审核，
  并按 [acceptance](acceptance.md) 重新做插件验收（`mcp.tools-list` 摘要已变）；本地测试与 ZIP 构建不替代这两项。
  kabo-sh/kabo#1875 合入 main 后要按 main 的提交重新固定 `contracts/directory-mcp/`。

本次 pin 为提供方 PR 的固定提交，不代表生产已部署。生产授权迁移、实际工具可用性、真实宿主以及目录审核仍须单独验收；ZIP 构建与本地测试不替代上述事实。
