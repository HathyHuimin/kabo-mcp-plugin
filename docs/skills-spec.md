# Kabo Skills 规范 v1

适用范围：本仓 `skills/`。这些 skill 是从 creator-skills 特化出的固定子集，随插件发布。
Kabo 网页端 agent 使用的 Skills 与其分发机制不受本规范约束。

本规范以 [Agent Skills 开放规范](https://agentskills.io/specification) 为基础，只补 Kabo 需要的部分。
开放规范已有的规则（frontmatter、目录结构）不重复解释。

校验：`npm run skills:lint`，规则编号与 `scripts/skills-lint.mjs` 一一对应。

## 默认前提

一份 skill 应能在任何支持 skills 与 MCP 的模型或宿主（harness）中运行，例如 Codex、Claude Code、Cursor。
skill 不与某个宿主或模型直接绑定。ChatGPT 没有 shell，依赖脚本的 skill 在那里跑不了，暂不作为目标宿主。

skill 内容（`SKILL.md`、`references/`、脚本输出）一律用英文。本文档面向协作者，用中文。

## 级别与门禁

| 级别 | 含义 | 是否阻止 `npm run verify` |
| --- | --- | --- |
| 错误 | 违反规范 | 不阻止（门禁未启用，见下） |
| 警告 | 建议修改 | 不阻止 |
| 审计 | 需要人工确认，PR review 时逐项看 | 不阻止 |

**门禁不启用**：`skills-spec.json` 的 `enforced` 保持为空（2026-10-20 决定）。
`verify` 只输出各 skill 的摘要，规范问题靠 PR review 与 `npm run skills:lint` 发现。脚本仍保留
按 skill 启用的机制；是否启用、对哪个 skill 启用，由用户决定，不在改造 PR 里顺手加。

存量 skill 在启用前只在 `verify` 中输出摘要。看全部明细用 `npm run skills:lint`，按全部启用来检查用 `--strict`。

## 规则

### KS1 frontmatter（与开放规范一致）

这一组规则与官方校验器 `skills-ref` 0.1.1（`agentskills validate`）一致。本仓用 Node 实现，CI 不需要 Python。

- **KS1.1**：frontmatter 必须是合法 YAML。`name` 不超过 64 字符，只含小写字母、数字和连字符，不以连字符开头或结尾，不含连续连字符，并且与目录名一致。
  - 例：description 未加引号时，其中的 `: ` 会被解析成映射，这种情况判为非法。
- **KS1.2**：`description` 非空，不超过 1024 字符。
  - 超过 500 字符给警告。原因：Codex 的 skill 列表最多占上下文的 2%，所有已装 skill 共用这份预算，超出时先缩短 description。
  - description 只写「做什么、什么时候用」，执行规则写进正文。这一点由人工判断。
- **KS1.3**：frontmatter 只能使用规范字段：`name`、`description`、`license`、`compatibility`、`metadata`、`allowed-tools`。
- **KS1.4**：`compatibility` 是不超过 500 字符的字符串。`metadata` 是字符串到字符串的映射。
  - 来源业务版本如果要写，写在 `metadata.version`。版本与发布规则暂不纳入本规范。
  - 许可写在 `license`，须与 `plugin.json` 一致（包检查强制）。skills CLI 只复制单个 Skill 目录，
    不带仓库根目录的 LICENSE，许可声明须随 Skill 一起分发。

### KS2 依赖与宿主无关

- **KS2.1**：`SKILL.md` 与 `references/` 下的文本文件中不得出现宿主私有运行时。
  - 包括 `kabo-run-pipeline`、`PLUGIN_ROOT` / `CLAUDE_PLUGIN_ROOT`，以及 `mcp__*` 这类宿主给工具加的命名空间名。
  - 工具一律用 `/mcp-pub` 上的名称。
- **KS2.2**：运行依赖只有 `/mcp-pub` 的工具，用 `allowed-tools`（空格分隔）列出全部依赖的工具名。
  - 每项都必须出现在 `contracts/directory-mcp/tools-list.json` 中。
  - `SKILL.md` 与 `references/` 里提到的 `/mcp-pub` 工具，必须全部列在 `allowed-tools` 中。
  - 已放弃的注册机制工具 `list_research_skills` / `get_research_skill` 已于 2026-10-22 从 `/mcp-pub` 删除，不在工具清单中，按上一条即报错。
  - 注意：`allowed-tools` 在开放规范中是实验字段，有的宿主会把它当作预授权工具清单。在 Kabo 这里它表示「依赖清单」。各宿主的实际处理方式需要逐个实测。
  - 旧 `/mcp` 的工具名不属于本规范；旧接口在线上照常保留。
- **KS2.3**（警告）：超出默认前提的依赖，在 `compatibility` 中逐项写明。
  - 例如运行 `scripts/` 需要 Python 3 与文件读写，分析本地视频需要 ffmpeg。
  - 宿主缺少某项依赖时，skill 要如实说明哪一步做不了，不要假装完成。
  - 本地文件功能（未发布的视频、本地字幕）保留，按本条声明依赖。
  - 依赖 `/mcp-pub` 工具的 skill，写明需要连接的 Kabo MCP：`https://kabo.sh/mcp-pub`。
  - 写明 `allowed-tools` 需要的 Kabo OAuth 权限，格式为 `Kabo permission: data.`；可选的权限写明用途与触发条件，
    例如 `workspace:write` 只在用户要求保存或绑定时使用。权限由工具清单的 `securitySchemes` 反推，lint 给警告。
    这些是 Kabo 签发给宿主连接的权限，不是宿主本机权限；宿主按连接授权、不按 skill 授权，用户只能从这里看出每个 skill 要哪一项。
  - 脚本会下载 Kabo 工具结果给出的签名链接时，写明需要从脚本访问 `storage.googleapis.com`（Codex 默认沙箱可能不放行）；
    宿主导出加 `stage` 是回退，不能代替这条声明。
- **KS2.4**（人工）：正文不按宿主写分支，不写 `subagent` / `inline` 这类执行概念，也不区分「主 agent」与「executor」的职责。
  - 耗时长的 skill 在正文开头写：建议在前台子 agent 中执行，主线程等它返回最终结果后再回复；不得放到后台
    再告诉用户稍后交付（无交互宿主如 `claude -p` 会在主线程回复后退出，后台子 agent 随之终止，报告丢失，
    2026-10-22 Claude Code 2.1.308 实测）；宿主不支持前台子 agent 时，在主线程按同样步骤执行。
- **KS2.5**（警告）：正文含中文字符时给警告。

### KS3 长度

`SKILL.md` 正文在激活时整篇加载。Claude Code 做上下文压缩后，每个 skill 只重新附上前 5,000 token。

- **KS3.1**：`SKILL.md` 正文不超过 500 行，也不超过 21,000 字符（英文约 5,250 token）。
  - 2026-10-29 由 20,000 放宽到 21,000。压缩后重新附上的前 5,000 token 约覆盖前 20,000 字符，
    超出的尾部可能不随压缩保留，所以关键规则仍按 KS3.3 写在前部。
- **KS3.2**：`references/` 下单个文本文件不超过 500 行、20,000 字符（`*.schema.json` 除外）。
  - 只由脚本读取的数据文件也不例外。超长时先删去脚本和正文都用不到的字段，不要为它开例外。
- **KS3.3**（人工）：关键规则写在正文前部。分支流程、校验细则、错误处理移到 `references/`，正文只保留主流程和跳转说明。

长度按字符数计量，不按 token 计量。不同模型的分词器不同，用 token 计量就把规则绑定到了某个模型，违反默认前提。

### KS4 不嵌套

- **KS4.1**：skill 目录里只能有根部一个 `SKILL.md`。
  - 第三方语料自带的子 skill（例如 OpenMontage 的各类 director 与 pipeline）不得入包。
- **KS4.2**：不读取另一个 skill 的文件，也不把另一个 skill 当作本 skill 的一个步骤来调用。
  - 允许在结尾向用户建议下一步可以用哪个 skill，写 skill 名称，不写路径。
  - 共享代码按副本放进各自的 `scripts/`。
  - 脚本里出现另一个 skill 的名称只列为审计项，例如共享脚本按 skill 名区分平台。

### KS5 许可证与第三方内容

- **KS5.1**：`license` 如果填写，必须在白名单内：`MIT`、`Apache-2.0`、`BSD-2-Clause`、`BSD-3-Clause`、`ISC`、`0BSD`、`CC0-1.0`、`CC-BY-4.0`。不填写视为 Kabo 自有内容。
- **KS5.2**：skill 内的 `LICENSE` / `COPYING` / `NOTICE` 文件以及 SPDX 标识，不得出现白名单之外的许可证，包括 GPL、AGPL、LGPL 与 NC 类。
  - 每个 SPDX 标识都要在白名单内。许可证文件认不出是白名单内的许可证时，列为审计项，由人工确认。
  - 用白名单而不是「禁止商业用途」来判断：AGPL-3.0 允许商用，但有传染性条款，并且要求通过网络提供服务时也公开源码。
- **KS5.3**（人工）：「第三方」包括库、语料、样本数据和外部程序。引入第三方内容的 PR 要写明来源与许可证。改写自受限许可证内容的文件也可能被视为衍生作品，应按我们的需求重写。

### KS6 非标准文件

- **KS6.1**：不得有 `manifest.json`。各字段的去处：

  | 原字段 | 去处 |
  | --- | --- |
  | `version` | `metadata.version` |
  | `required.tools` / `required.connectors` | `allowed-tools` |
  | `permissions` | `compatibility` |
  | 许可证 | `license` |
  | 产品配置 | 归使用方，不放在 skill 中 |
  | `execution`、`pipeline`、`min_plugin_version` 等 | 删除 |

- **KS6.2**：不得有 `references/mcp-bindings.json`。逻辑 connector 到工具的映射，改为由 `allowed-tools` 声明、脚本和正文直接使用 `/mcp-pub` 工具名。
- **KS6.3**：不得有 `agents/` 目录，包括 Codex 的 `agents/openai.yaml`。
  - `policy.allow_implicit_invocation` 不写时默认为 `true`，与插件需要的行为一致。
  - `dependencies` 里的 MCP 与插件 `mcp.json` 声明的是同一个 `kabo`，属于重复声明。
  - 不通过插件单独安装 skill 的用户，按 KS2.3 在 `compatibility` 中看到需要连接的 MCP 地址。
  - 代价只有 Codex 界面上的显示名改为 skill 名本身。

### KS7 脚本

脚本放在 `scripts/`，不设严格约束，但纳入审计。

- **KS7.1**（错误）：不读宿主的会话记录或内部状态。
  - 包括 `CODEX_HOME`、`.codex/sessions`、rollout 文件、`.claude/projects`、桌面宿主的 userData、宿主的 skill 缓存。
  - 宿主刻意对命令面隐藏 `CODEX_HOME` 时，脚本不得绕过。
- **KS7.2**（审计）：运行时不安装软件。缺少依赖时提示用户，并按 KS2.3 如实说明。
- **KS7.3**（审计）：网络访问只到 Kabo 自己的域名，以及 Kabo 工具结果给出的 `storage.googleapis.com`
  签名下载链接（只 GET、主机精确匹配、不跟随重定向）。
  - 后半句是服务端的设计决定：GCS 签名链接零新增 IAM、沿用生产已有的签名写法；
    改走 Kabo 域名链接需要新密钥与网关变更，换来的只是网络访问留在 Kabo 域名内。
  - 网络访问只有两处，都在各 skill `scripts/static_host.py`：
    - `fetch --input`：签名链接只从工具结果读（文件或 stdin，不进 argv），协议与主机逐字匹配，3xx 按失败处理，
      按声明的 `bytes` 设读取上限并流式校验 sha256，对不上就什么都不写；报错与输出不复述链接、签名或对象名。
    - `fetch --id` 与交接续跑的 `--id NN=<id>`：按结果编号 GET `https://kabo.sh/v1/research-downloads/<编号>`
      （kabo ADR-0084「2026-11-04」）。主机逐字匹配、不跟随重定向，必须有 `Content-Length`（不超过上限）与
      `X-Content-SHA256`，读到的字节数与 sha256 都对上才写，否则什么都不写。编号与签名链接是同一张 10 分钟
      不记名票，允许出现在 argv 和打印的续跑命令里；交接只让模型交编号，不交签名链接（误贴整条链接时脚本
      只取其中的编号，不用链接本身发请求）。
  - 链接过期（`expires_at` 已过，或 GCS 回 400/401/403）时 `fetch` 不重试下载，只打印下一步：同步数据
    结果用信封里的 `evidence_ref` 调 `get_research_data_download` 重签，新链接
    照样走上面这套校验，按新返回的 sha256 / bytes 核对；重签本身失败（`not_found` / `service_unavailable`）时
    才退回带 `"delivery": "download"` 再调一次原工具（会重跑）。重签不是第二处网络访问，仍是 Kabo 工具结果给出的签名链接。
  - lint 只按「出现网络库」列审计项，不判断主机；PR review 逐项确认没有上面两处之外的网络访问。
- **KS7.4**（审计）：可执行脚本放在 `scripts/` 下。

### KS8 数据读取（说明，不由脚本校验）

脚本需要完整数据，用于计算指标、按 sha256 绑定证据。不得让模型把工具结果重新抄写进文件：经过模型上下文的结果会被截断，曾出现 100 条帖子保存后 `rows` 为空的情况。

- **主路径：下载交付**：数据工具、`get_research_data_job` 与
  `read_research_data_artifacts` 调用时传 `delivery: "download"`。结果里给出 `download`
  （`status / kind / url / expires_at / sha256 / bytes / content_type`），同步 envelope 省略 `data`，
  产物逐项给 `artifacts[i].download`。模型只保存这份很小的完整结果，运行
  `scripts/static_host.py fetch --tool <工具名> --input <文件>`；脚本下载、核对大小与 sha256，再走与
  `stage` 同一套校验写入 `snapshot/.host-evidence/`。链接 10 分钟过期，所以拿到就 fetch。
  - 为什么是主路径：大结果经过模型上下文会被截断，而宿主的结果导出能力各家不一；下载交付让完整
    字节不经模型、只经脚本，校验也在脚本里。
  - 这份结果只有链接与摘要、不含数据，模型原样写进文件（宿主导出或 heredoc）不算抄写证据：
    抄错一个字符会在主机或 sha256 校验上失败，不会落下错的数据。
  - **结果编号（2026-11-04 起优先）**：连这份小结果也不让模型存。envelope 下载链接路径里
    `research-export/v1/<编号>.json` 的 22 字符就是结果编号（kabo ADR-0084「2026-11-04」）；模型只把
    编号交给脚本——驱动交接的续跑命令 `--id NN=<编号>`，或 `static_host.py fetch --tool <工具名>
    --id <编号>`——脚本向 `https://kabo.sh/v1/research-downloads/<编号>` 取同一份字节，按响应头的
    `Content-Length` 与 `X-Content-SHA256` 校验后照 `fetch` 落盘。原因：1.0.10 的 Codex 回归里，模型
    存这份小结果仍是交接的头号故障（patch 写坏、签名链接抄漏、改写 `calls[].tool`、存错目录；程序从
    返回值写文件 37/40 成功，模型手抄 3/10），而插件不注册生命周期钩子、只装 Skills 的安装也没有
    钩子，宿主替模型落盘的路走不通。没有下载链接的结果（下载不可用、未带 delivery）仍按上面存文件。
    产物（帧、转写）没有编号，仍按下面逐项下载。
  - **帧与图片同样走下载**：`read_research_data_artifacts` 读帧也带 `delivery: "download"`，
    `fetch` 把图片落成与 `stage` 相同的 `<id>-<i>.<jpg|png|webp>` + `.meta`，输出里逐项给出绝对 `path`，
    模型再用宿主自己的读文件 / 看图工具打开它才算看过。原因：Claude Code 2.1.308 把内联图片另存进
    它自己的会话目录，可导出的工具结果里没有图片，`stage` 拿不到完整结果（2026-10-22 端到端实测连续
    失败 5 次，模型最后去宿主会话目录按 sha256 拼回结果——那是宿主内部目录，不可依赖）。Codex 0.163
    会把图片留在工具结果里，内联 + `stage` 在那边仍可用，所以保留为回退。
    - 下载校验：只收 `https://storage.googleapis.com/`、不跟随重定向、按声明 bytes 设上限并流式
      校验 sha256、类型只收闭集（`image/jpeg|png|webp`、`text/vtt`、`application/json`）且须与产物记录
      一致，响应带 `Content-Type` 时也须一致（缺头不拦：字节已由大小与 sha256 钉住）；任何一项不符
      整次不落盘。
    - 回退：某项 `download` 不可用（`not_found` 记为缺口，其他原因内联重读），或宿主不让脚本联网时，
      对那些 `object_refs` 不带 `delivery` 再读一次，经宿主导出 + `stage` 保存。正文明确禁止去读宿主
      自己的会话或 tool-results 目录拼结果。
  - 驱动脚本交给模型的调用（`public_calls`）：数据工具、`get_research_data_job`、
    `read_research_data_artifacts` 与四个聚合工具（`collect_*` / `enrich_*`）都带上
    `delivery: "download"`。就绪查询没有这个入参，照旧。
  - **聚合工具的下载只是副本**：它们的 `items` 在已发布的出参里是必填，服务端下载就绪时 items 照旧
    完整内联。模型挑候选仍读内联 items；脚本凭下载副本拿完整数据。下载字节是
    去掉 `download` 的整份出参，`fetch` 按 `schema_version` 判形状；`stage` 也接受它，落盘前去掉
    含签名链接的 `download`。
  - **组合取数工具（`kabo-composite-fetch.v1`，KS9）**：`collect_account_history`、
    `collect_tiktok_video_candidates`、`collect_youtube_recent_videos`、`render_official_pages` 一次调用做完
    一段多轮取数，出参 `{schema_version, tool, status, calls:[{tool, parameters, group, result}], summary, coverage, limitations}`，
    `calls[i].result` 与单个数据工具的结果信封完全相同。`stage` / `fetch --tool <组合工具>`（下载字节是去掉
    `download` 的整份出参）把 `calls[]` 逐项拆开，每项按 `calls[i].tool` 走单工具 `stage` 的同一套校验，落成
    同样的 `<id>.json` + `<id>.meta`（正文与单独 stage 逐字节相同，下游解析不变），旁注另记 `composite_tool`、
    `composite_id`、`composite_index`、`composite_group`、`composite_parameters`；再写一份整体记录
    `<composite_id>.json` + `.meta`（旁注 `tool` 为组合工具、`schema_version` 为 `kabo-composite-fetch.record.v1`），
    正文是 `status / summary / coverage / limitations` 与按调用顺序的 `calls[{index, tool, group, status, evidence_id}]`。
    驱动用 `static_host.composite_records(tool)` 读它（每项带证据文件的绝对 `path`）。任何一项校验不过、
    调用的工具不是本 Skill 声明的数据工具、或组合工具名不在上面四个之内，整份拒收、什么都不写。
    同一份组合再落一次时逐项与记录都按 sha256 去重，指回上次那份。
  - 链接过期：对同一只读工具（`get_research_data_job` / `read_research_data_artifacts`）再调一次即重签；
    同步数据工具的结果用信封里的 `evidence_ref` 调 `get_research_data_download`（不重跑、
    不占额度；新链接的 sha256 与 bytes 可能和旧的不同，只按新返回的值校验）；重签本身失败时退回带
    `"delivery": "download"` 再调一次原工具（会重跑，这是已收费结果丢失前的最后一条路）。业务级 lookup 工具
    （`lookup_*`）例外：重签拿回的是作答路线记下的原始信封、不是投影，只能再调一次 lookup。聚合工具
    的内联结果本来完整，改走导出 + `stage`，不重调。
- **回退：宿主导出 + `stage`**：`fetch` 报告没有就绪的下载（服务端给 `unavailable` 并退回内联、
  旧服务端、内联模式）时，通过宿主的结果导出能力加 `scripts/static_host.py stage` 保存完整结果。
  这条路径不删：Codex 默认沙箱是否放行脚本联网要在宿主实测后再定；产物在下载模式下
  `stage` 不收，回退是不带 `delivery` 重读后再 `stage`（见上）。
- **延迟媒体调用的幂等键**：`public_video_*` 的入参由 `static_host.py job-call` 生成，
  顶层带 `idempotency_key`（`<skill>:<运行目录名>:<工具名>:<入参摘要>:a<尝试序号>`）。没拿到响应而
  重发时原样复用，服务端交回原 job、不再计费；拿到错误结果、改了入参或要重跑已终局的任务时换下一个
  尝试序号。`analyze-content-video` 与 `review-creator-account-deep` 的抽帧调用帧数上限为 60（服务端
  上限 120）：缺省时由 `job-call` 补上 `max_frames: 60`，只摊薄开头之后的采样，开头密度不变；
  显式传入超过 60 的值会被拒绝。deep 的计划值在 `plan_media_jobs.py`。
- **有限等待（已落地）**：延迟任务先返回 job_id，再用 `get_research_data_job` 的 `wait_s`（上限 30 秒）
  等待，不另做首次调用时的服务端等待（2026-10-20 决定）。排队中的任务不重新发起。
- **长任务**：视频分析、采集 / 补全的 p90 在 60–100 秒。skill 不得假设工具会同步返回。

### KS9 取数编排在后台闭环（CI 硬闸门）

一段多轮取数（搜索 → 筛选 → 拉历史 → 补详情）由后台一个接口做完，skill 只引导发起这一次调用，
所有宿主调同一个接口。参照 `collect_*_trend_candidates` / `enrich_*_shortlist`。

- **KS9.1**（错误）：不新增「驱动打印 `call:` → 宿主模型逐个调用 → 落盘 → 续跑」的交接轮次。每轮多花两三次
  模型调用：竞品一条要 14–16 步，Web Agent 每回合 12 步的上限内跑不完，插件端也慢。存量驱动是待迁移的存量，
  轮次数由 `scripts/fetch-orchestration.test.mjs` 按 skill 记录，只减不增。
- **KS9.2**（错误）：脚本不按宿主探测取数路径（connector-outbox、会话线程 id、回合令牌）。按宿主分支等于把端差异
  藏进脚本，各宿主行为分叉；同一测试记录存量文件，只减不增。
- **KS9.3**（人工）：两轮取数之间是确定性规则（打分、门槛、取前 N），规则一起搬进后台。需要模型判断时：
  只有一两个判断点，可在判断点切开，宿主模型判断后再调一次后台；判断点多，把那段判断的引导抽成独立 prompt，
  后台在固定节点自己调模型，不借宿主模型。取数结束后的分析与写作仍由宿主模型完成。

## 暂不纳入

- 版本与发布规则
- 脚本的完整安全边界
- 如实报告缺口的统一写法
- 跨模型验收
