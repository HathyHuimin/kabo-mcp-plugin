# MCP 元数据审计（本地注册结果）

本文件由真实 initialize / tools/list 生成；未执行工具，未访问生产，不是目录提交许可。
OAuth 通过 `securitySchemes` 与 `_meta.securitySchemes` 同值声明。下表的 scope=null 表示必须认证但无额外 scope。

| Tool | Scope | Read only | Destructive | Open world | 判定依据 |
| --- | --- | --- | --- | --- | --- |
| get_kabo_info | workspace:read | true | false | false | 只读已授权用户的 Kabo 账号与持久化资产；不同步、不生成、不扣 credits、不访问网页 agent。 |
| list_connected_accounts | workspace:read | true | false | false | 只读已授权用户的 Kabo 账号与持久化资产；不同步、不生成、不扣 credits、不访问网页 agent。 |
| get_creator_profile | workspace:read | true | false | false | 只读已授权用户的 Kabo 账号与持久化资产；不同步、不生成、不扣 credits、不访问网页 agent。 |
| get_social_connection | workspace:read | true | false | false | 只读已授权用户的 Kabo 账号与持久化资产；不同步、不生成、不扣 credits、不访问网页 agent。 |
| list_scripts | workspace:read | true | false | false | 只读已授权用户的 Kabo 账号与持久化资产；不同步、不生成、不扣 credits、不访问网页 agent。 |
| get_script | workspace:read | true | false | false | 只读已授权用户的 Kabo 账号与持久化资产；不同步、不生成、不扣 credits、不访问网页 agent。 |
| list_library_videos | workspace:read | true | false | false | 只读已授权用户的 Kabo 账号与持久化资产；不同步、不生成、不扣 credits、不访问网页 agent。 |
| get_library_video | workspace:read | true | false | false | 只读已授权用户的 Kabo 账号与持久化资产；不同步、不生成、不扣 credits、不访问网页 agent。 |
| save_script | workspace:write | false | false | false | 按当前用户保存已有正文，幂等回执与写入同事务；不启动生成或扣 credits。 |
| update_script | workspace:write | false | true | false | 替换用户已有正文；属主、乐观锁及幂等回执防止越权、重放与覆盖较新版本。 |
| connect_public_account | workspace:write | false | true | true | 使用当前用户取数额度核验公开身份并建立观察关系；换绑须携带当前账号与版本。不进行官方 OAuth、不投递分析或同步任务。 |
| update_content_preferences | workspace:write | false | true | false | 改本人对已绑定账号的画像偏好，remove / merge / reset 会去掉已有修改；必填 expected_version，改主打内容另带 basis。重放时版本已变必回 409、不会重复生效，所以不带幂等键（待目录面负责人确认）。不访问第三方、不启动生成、不扣 credits。 |
| start_skill_run | data | false | false | false | 宿主声明一次 skill 运行：按 (用户, run_id) 幂等记一笔 Regular credits（价格读价表）。额度用完先拦；开关关闭、内部或审核账号不记。不访问第三方、不改产品数据。 |
| get_credit_balances | workspace:read | true | false | false | 读取当前账号的 Regular 与 Video credits，与网页 GET /v1/credits 同一条 present()。用量已达标时会把当日任务奖励入账，这是网页打开额度时就会做的结算；不扣费、不发起充值。 |
| get_credit_tasks | workspace:read | true | false | false | 读取当前账号的任务墙。每日任务若今天已完成，这次读取会把奖励入账，与网页打开额度时同一条结算；不领取一次性任务、不扣费。 |
| claim_credit_task | workspace:write | false | false | false | 用户要求时领取一条一次性任务的 Regular credits；条件不满足或已领过则不发，账本唯一约束保证重放不重复入账。不领取每日任务。 |
| collect_instagram_trend_candidates | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| enrich_instagram_shortlist | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| collect_youtube_trend_candidates | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| enrich_youtube_shortlist | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| collect_account_history | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| collect_tiktok_video_candidates | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| collect_youtube_recent_videos | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| render_official_pages | data | true | false | true | 读取第三方公开数据；不占账号额度、不创建账号记录（服务端调用审计与证据副本不算）。 |
| collect_video_media | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| analyze_content_video | video | false | false | true | 创建账号拥有的视频分析记录（新分析需有效 Pro 订阅）；同一幂等键重放原分析，不发布或改动源视频。 |
| get_analysis_run | video | true | false | false | 仅查询当前用户持有的已存分析，不启动任务、不读取任意外部地址。 |
| start_remake_video | video | false | false | true | 取第三方公开视频（占取数额度）并创建账号拥有的复刻任务（占每日复刻名额）；idempotency_key 必填，同键同入参重放或续做同一任务，不发布或改动源视频。 |
| get_remake_video | video | false | false | false | 读当前用户经 /mcp-pub 建的复刻任务并签发短期下载链接；带 cancel 时终止该任务在跑的一步或准备（已花费照记），不访问第三方、不占额度。 |
| render_remake_video | video | false | false | true | 提交宿主写的稿件并启动付费生成（runner 调用口播、配音与图像供应商）；已受理的任务再调只回当前状态、不重复启动。 |
| start_video_project | video | false | false | true | 取第三方公开视频与公开图片（占取数额度）并创建账号拥有的原子项目，或带 job_id 往已有项目或已交付复刻追加素材；本身不生成、不扣生成费；idempotency_key 必填，同键同入参重放同一项目。 |
| make_video_step | video | false | false | true | 在账号自己的项目或已交付复刻的工作区里启动一步生成或剪辑（Runner 调视频、配音、图像供应商；op 封闭枚举、只收工作区句柄），按实际花费计费、超出任务预算拒收；同键同入参只回当前状态、不重复启动。 |
| edit_remake_video | video | false | false | true | 改账号自己已交付的视频（台词重配音并对口型、字幕、叠图、换背景、插镜），按实际花费计费，登记新版本、旧版本保留；同键同入参只回当前状态、不重复启动。 |
| finish_video_project | video | false | false | false | 把账号自己项目里的一个成片句柄做相似度检查后登记为新版本成片并签发下载链接；不访问第三方；同键同入参只回当前状态。 |
| get_research_capabilities | data | true | false | false | 只读 Kabo 自有状态（任务、制品、快照或目录），不访问第三方、不占额度。 |
| youtube_search_videos | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| youtube_search_channels | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| youtube_get_channel | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| youtube_get_video | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| youtube_list_comments | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| youtube_list_channel_uploads | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| instagram_resolve_profile | data | true | false | true | 读取第三方公开数据；不占账号额度、不创建账号记录（服务端调用审计与证据副本不算）。 |
| instagram_list_posts | data | true | false | true | 读取第三方公开数据；不占账号额度、不创建账号记录（服务端调用审计与证据副本不算）。 |
| instagram_list_reels | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| instagram_search_accounts | data | true | false | true | 读取第三方公开数据；不占账号额度、不创建账号记录（服务端调用审计与证据副本不算）。 |
| instagram_search_profiles | data | true | false | true | 读取第三方公开数据；不占账号额度、不创建账号记录（服务端调用审计与证据副本不算）。 |
| instagram_list_comments | data | true | false | true | 读取第三方公开数据；不占账号额度、不创建账号记录（服务端调用审计与证据副本不算）。 |
| instagram_get_transcript | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| instagram_baseline_search_reels | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| instagram_baseline_search_music | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_list_account_posts | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_discovery_search_videos | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_discovery_search_sounds | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_discovery_list_top_contents | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_discovery_get_industry_hashtags | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_discovery_get_trending_hashtag_detail | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_discovery_list_search_insights | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_discovery_list_comments | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_discovery_list_comment_replies | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_discovery_get_search_insight_trend | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_search_list_profile_videos | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_search_get_video | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| tiktok_search_get_transcript | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| creator_trends_get_instagram_audio_board | data | true | false | false | 只读 Kabo 自有状态（任务、制品、快照或目录），不访问第三方、不占额度。 |
| creator_trends_get_tiktok_sound_board | data | true | false | false | 只读 Kabo 自有状态（任务、制品、快照或目录），不访问第三方、不占额度。 |
| public_video_collect_public_metadata | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| public_video_fetch_transcript | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| public_video_extract_keyframes | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| public_video_extract_keyframes_hosted_media | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| public_video_extract_keyframes_hosted_stream | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| public_video_measure_media_signals | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| official_page_render_page | data | true | false | true | 读取第三方公开数据；不占账号额度、不创建账号记录（服务端调用审计与证据副本不算）。 |
| lookup_instagram_post | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| lookup_tiktok_video | data | false | false | true | 读取第三方公开数据，并占账号每日取数额度或创建账号拥有的任务/候选集；不修改上游内容。 |
| get_research_data_job | data | true | false | false | 只读当前用户且属于公开取数允许面的已有任务；不提交、重试或取消。 |
| get_research_data_download | data | true | false | false | 按当前用户与公开取数操作白名单读回已存 envelope 并签发短期链接；不访问第三方、不占额度、不改动调用记录。 |
| read_research_data_artifacts | data | true | false | false | 按当前用户、公开取数操作与任务登记的 object_ref 三重校验后读取已有证据。 |

## 未满足项

- 本地元数据不证明生产 OAuth、隔离、删除、Reviewer 账号或 5+3 案例通过。
