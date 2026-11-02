# 与候选版本绑定的生产验收

普通 `npm run verify` 验证源码和离线夹具，不会证明真实账号或生产宿主可用。
`submissionReady` 由公开工具契约和生产证据共同决定；不会固定写成 false，
也不会因为 ZIP 可构建而自动变成 true。它是本项目交付结论，不是 OpenAI 审核结果。

先提交代码，再为完整 40 位 commit 建候选包和验收索引：

```bash
npm run build -- --commit <完整commit> --output <新的候选目录>
npm run acceptance:init -- --commit <同一commit> --output <新的验收目录>
```

索引初始全部为 `not-run`，不会填造任何生产成功记录。实际执行后更新每项的
`status`（passed / failed / not-run）、UTC `performedAt`、脱敏证据相对路径
`artifact` 和该文件的 `sha256`。证据目录放在仓库与 ZIP 外；Reviewer 密码、
Cookie、access token、refresh token、client secret 不属于验收附件。

项目清单包括 ChatGPT/Codex 真实安装与交付、OAuth 授权/刷新/坏 token/错 audience/
缺 scope、run/artifact 跨账号隔离、账号删除完整性、旧 MCP 回归、生产 canary、
challenge 精确字节核验、Support/Legal、Reviewer 可用性与 Demo，以及 P-01～P-05、
N-01～N-03，以及执行独立性与报告单次交付。这 28 项是项目交付标准；其中首次 MCP
审核的 5 个正向、3 个负向案例也是当前[官方提交要求](https://developers.openai.com/plugins/deploy/submission#configure-onboarding-review-and-publication)，
不应将整套内部门禁或该案例数量套用到 skills-only 插件。
P-04 附件必须记录三个稳定频道 URL，P-05 附件记录稳定公开视频 URL。

`architecture.host-execution` 必须提供宿主的包内 Skill 选择/固定来源验证、公开数据工具调用、
本地脚本与报告验证轨迹，证明没有向 Kabo 网页端 agent 发起任务或读取其报告；通过测试
代理拒绝该路径，不能关闭共享生产网页服务来取证。缺宿主执行能力时
记失败/未执行，不转发到网页端 agent。`report.single-delivery` 比对验证后的报告
与最终宿主回复，确认正文只出现一次，没有第二份改写总结。离线夹具通过不算这两项
生产通过；更换包、MCP 契约或执行链后旧验收不能复用。

```bash
npm run check:submission -- --commit <同一commit> --evidence <验收目录>/acceptance.json
npm run build -- --commit <同一commit> --submission --evidence <验收目录>/acceptance.json --output <新的交付目录>
```

门禁逐项核对 commit、版本、确定性 ZIP SHA-256 和固定 tools/list SHA-256。
`mcp.tools-list` 的附件须为从生产返回结果提取并按固定契约格式规范化的 `{ tools }` JSON，
其摘要必须与本次固定 tools/list 完全相同；描述、权限或 schema 漂移时须重新导入和验收。
缺项、未执行、失败、换版本、附件篡改、越界路径或链接均不能通过；检查失败不输出
附件正文。候选与正式交付读取同一个插件根目录，没有裁剪后的第二套包。

证据哈希只能证明所审核的附件未改变，不能证明附件叙述属实。负责审核的维护者仍须复核实测
内容与最终后台扫描；Publish 确认仍属于发布流程，由发布负责人决定。测试中的合成证据只用于
验证门禁，不作为交付证据。

（旧动态运行时的本地缓存回收——`kabo-prepare`、`envelope-staging`、`skill-gc`——已随旧编号 3.0.0 退出；
固定 Skills 的运行目录在用户工作区的 `.kabo-runs/` 下，由用户自行管理。）

当前发布版本另需记录 Claude Code / Cursor 的实际安装与业务回归矩阵（每个 Skill 一项；含本次新增四个 Skill 后为十五项）；这些宿主的成功不能替代 OpenAI 上架候选本身的验收。网页宿主缺少执行条件时如实标记，不能删掉待验收项制造就绪。

## skills CLI 发布渠道

`stable` 分支是 `npx skills add kabo-sh/kabo-mcp-plugin#stable` 的更新渠道，只指向已发布的 tag。
候选验收通过、打 tag 并发布 GitHub Release 之后，由发布负责人手动把 `stable` fast-forward 到该 tag：

```bash
git push origin <tag>^{commit}:refs/heads/stable
```

不得推进到未发布的提交，也不用 workflow 自动推进。`stable` 受仓库规则保护，禁止删除与非 fast-forward 更新。

这条安装路径单独记录：skills CLI 安装、宿主识别、`/mcp-pub` 登录与至少一个 Skill 的端到端运行。
它不属于上面的提交门禁，插件安装成功不能代替。2026-10-27 已验证十个 Skills 安装完整、Codex 0.164 能识别；
登录与端到端运行未执行。
