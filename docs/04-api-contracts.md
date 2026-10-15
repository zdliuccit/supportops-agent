# API 契约约定

## 分层

| 前缀 | 用途 | 典型权限 |
| --- | --- | --- |
| `/v1/auth` | 密码登录、当前身份、本人会话及永久撤销 | 公开登录 / 已登录 |
| `/v1/admin/*` | 控制面、知识和 Dashboard | `platform_admin` |
| `/v1/agents` | 员工可用 Agent 目录 | 显式授权 |
| `/v1/conversations` | 会话、消息和历史 | 会话主体授权 |
| `/v1/runs` | Run 状态、取消和事件流 | 会话主体授权 |

## 通用规则

- 使用 JWT 建立主体、租户和角色上下文。
- JWT 必须绑定持久化 jti/用户/租户/许可代次与期限；缺少会话引用的旧令牌拒绝，默认 60 分钟，无刷新。身份、会话列表及撤销响应使用 no-store。
- `GET /v1/auth/sessions?page=1&page_size=20` 仅查询本人会话，租户/用户先于 SQL count/分页，按 created_at/id 降序；返回 id/创建/期限/撤销时间/固定原因/is_current/is_active，不返回令牌/IP/设备指纹。
- `POST /v1/auth/logout` 撤销当前会话，`POST /v1/auth/sessions/{id}/revoke` 撤销本人指定会话，`POST /v1/auth/sessions/revoke-all` 撤销全部并原子递增用户许可代次。其他人的会话与不存在统一 404；已撤销会话记录不覆盖第一次撤销事实。请求自身失效返回 401，客户端不自动重放写操作。
- SSE 每轮复检登录会话；失效发送无 id 的 `auth.expired` 控制帧并结束连接，不能用业务 cursor 过滤该帧，不写 RunEvent 或改变业务终态。前端清理对应会话并重新登录；网络错误可重试认证，迟到 401 不清除新登录。前后端与 0040 同步升级，新契约尚未测试。
- 跨租户或无权限资源统一返回不可枚举的 404 语义。
- 消息写入必须携带 `Idempotency-Key`。
- 普通列表统一使用 `page >= 1`、`page_size` 默认 20/最大 100，返回 `items/total/page/page_size/pages`，在数据库 count/offset/limit 并稳定排序。越界页返回空 items，保留请求页码；部门完整树、整体替换集合和 SSE 保留完整语义。
- 错误响应包含稳定错误码和 correlation ID，不返回密钥、Prompt、内部域名或完整敏感正文。
- SSE 支持 `Last-Event-ID` 和 `cursor`，终态后关闭流。

## 分页升级与运行通知

RunAttempt 的内部 ContextSnapshot 新增 history_sources（消息/角色/序号/digest）及 extractive_history_v1 的有限历史摘录，不新增产品记忆或业务确认事实。管理 attempts 响应保留来源元数据但将 history_excerpts 清为空，不复制用户历史正文/答案 summary 到管理诊断；正文/历史仍按既有会话权限读取。模型调用、正文提交和最终产品提交复检历史源，失效使用 HISTORY_CONTEXT_AUTHORIZATION_CHANGED，当前输入及本轮工具轨迹不因预算裁剪丢失。

Agent 发布及版本分页响应增加 nullable `release_manifest_digest`/`release_manifest` 和 `release_manifest_status=recorded|legacy_unrecorded|integrity_failed`。仅当前租户管理员读取；清单由服务器生成，不接受客户端填写。recorded 包含 agent_release_manifest_v1，记录发布版本/源码/策略摘要，未记录历史为 null；完整性失败隐藏清单/摘要且拒绝激活。evaluation 保持发布时 not_assessed，后续追加评测单独读取。知识 corpus_version 为 null，当前授权版本在每次检索生成 Run evidence，不能据清单读取历史正文或获得执行权限。0041/0042 未执行，前后端同步升级。

`GET /v1/admin/agents/{agent_id}/versions/{version_id}/release-readiness` 是有限聚合详情，返回当前环境/stage/enforcement、固定版本/当前范围/策略摘要、最新评测ID与完成/到期时间，以及每项 passed/failed/missing/stale、实测/阈值/样本。只读，不运行模型/评测，也不保存或批准结果。`GET .../evaluations?page=1&page_size=20` 是服务器标准分页元数据历史（最大100），先租户/Agent/版本过滤，按 recorded_at/id 降序；不返回报告正文，不开放上传/改写/人工放行入口。

激活事务重新检查当前固定模型能力/凭据、工具实现/目录/风险及评测适用性，拒绝使用409 `AGENT_RELEASE_GATE_BLOCKED`，错误 envelope 额外携带 `release_readiness`，OpenAPI明确此结构。staging/production 强制全部门禁；local/dev/test 允许明确标记未验收的开发激活，已知功能/安全/完整性失败仍拒绝。正式创建新会话再次检查，员工只收到固定冲突提示，不暴露评测详情。旧会话版本不改写；这是发布门禁，不替代每次调用的实时授权。

Web 发布改为 activate=false 保存候选，再经历史版本显式激活。兼容旧 API activate=true 时保存与激活仍是同一事务，门禁拒绝整体回滚，不留下客户端未知候选；拒绝审计在回滚后独立追加，新增候选未落库时不使用其版本外键。W19实际执行器/实测仍待，灰度控制见下；配置摘要不会创建通过结果。

灰度接口均为当前租户platform_admin控制面，资源不存在或跨租户统一404；普通员工不能读取主体范围/评测元数据：

| 方法与路径（前缀 `/v1/admin/agents/{agent_id}`） | 契约 |
| --- | --- |
| GET `/rollouts` | SQL先过滤租户/Agent，再count与稳定created_at/id分页，默认20最大100 |
| POST `/rollouts` | ReleaseRolloutCreate：candidate_version_id、initial_candidate_bps（1–1000）、subject_ids（最多200，不授权）、严格policy；只保存draft，不启动 |
| GET `/rollouts/{id}` | 固定范围/策略摘要、双方readiness、当前轮次两组元数据/checks、巡检及逐项禁用原因；只读投影不批准 |
| POST `/rollouts/{id}/commands` | expected_revision及start/pause/resume/expand/stop；仅expand接受candidate_bps，最多增加1000且≤9000；陈旧修订/门禁/策略/观察不符409 AGENT_ROLLOUT_CONFLICT |
| GET `/rollouts/{id}/events` | 同租户固定灰度的追加审计，SQL count/稳定created_at/id分页，系统事件actor_kind=system且actor_user_id=null |

policy为release_rollout_policy_v1：必须显式给定观察秒数300–86400、每组最少终态样本3–5000、失败率≤0.1、正USD费用上限、API首正文P95≤3000ms、完整响应P95≤10000ms及费用/耗时相对倍数1–2；固定不自动扩大、最大比例9000/步进1000、单组最多5000、授权拒绝零容忍。服务器SUPPORTOPS_RELEASE_ROLLOUT_POLICY_DIGEST默认空，只有规范化策略摘要匹配才允许启动/恢复/扩大，两版本在所有环境均须完整passed。支持保存未批准草稿，不支持人工填写通过结果。

创建会话在当前Agent授权及Agent锁后按固定主体哈希选择，保存选中版本/灰度轮次；旧会话和后续Run不重新选版。巡检过期/策略未批准/固定引用或门禁失效时关闭候选流量，正式基线仍需当前门禁。观察只使用本轮新会话Run的有界服务器元数据，缺失不填零；等待、取消、样本/计量不足不能扩大。结构化结果存在不证明语义正确，API可见上界不证明客户端延迟；客户端上报不批准扩大。停止曾启动灰度撤回候选新写许可，激活同事务关闭灰度并按需撤回，历史和对账事实保留。0044未执行，API/Worker/Web须同步升级。

工具SDK输入边界新增model_tool_data_v1不可信数据契约（不是新的HTTP工具执行接口），旧工具消息没有来源信封时不补造，Worker须同步升级。RunAttempt的context_snapshot新增used_tool_sources有界元数据，管理诊断仍不返回历史摘录正文，并清空工单定位/版本字段；该只读投影不能用于执行重放。异步Run可能返回TOOL_RESULT_INVALID、TOOL_OUTPUT_SCHEMA_UNSUPPORTED、TOOL_CONTEXT_AUTHORIZATION_CHANGED或TOOL_SOURCE_CONFLICT稳定错误码；分别表示输出契约不符、未登记输出Schema、已用来源失效及声明事实冲突。冲突/失效不能伪称已升级、已建单或确认根因；重新发起有当前证据的新轮次，工具/模型累计许可不在原Run内重置。

版本响应新增 nullable `withdrawn_at/withdrawn_by`。激活较旧版本时，实际退出版本在同事务中追加撤回/回滚审计；已撤回版本所有环境均不可激活，readiness提供 `RELEASE_NOT_WITHDRAWN` 失败原因。新写动作/审批/执行遇到提案或执行来源撤回返回409 `ACTION_RELEASE_WITHDRAWN`；取消、读取历史回执和未知结果只读对账仍按当前原有授权处理，不重置参数/业务键或次数。0043未执行；目标事务串行保证只适用于隔离Mock，不外推真实API。

`GET /v1/admin/agents/{agent_id}/versions/{version_id}/comparison` 比较两个同 Agent/租户不可变版本，可选 baseline_version_id，未提供时取读取到的活动版本；跨范围/不存在 404，无活动版本为 no_baseline。响应 agent_release_comparison_v1 包含基线/查看版本、生成时间、清单状态和有限元数据 differences（equal/changed/added/removed/unknown）、变化/未知数量；这是完整两个版本的聚合详情，不分页、不请求全量版本库存。只返回摘要/ID/版本标识，不返回 Prompt、模型 URL、凭据或知识正文。未知记录不判相同，比较不作为发布批准/当前运行许可，GET 不修改版本或评测事实。

业务错误统一为 `{error:{code,message,correlation_id}}`，OpenAPI 共享声明 ErrorResponse（401/403/404/409/422/429/500/503）。健康 ready 的 503 单独保留 HealthResponse。资源不存在、跨租户或资源被隐藏均使用 404 `NOT_FOUND` 和固定“资源不存在或当前不可访问”；知识 evidence 同样隐藏标题/正文，返回 404 `KNOWLEDGE_EVIDENCE_INVALID`。操作权限拒绝使用 403，不能根据返回文案探测隐藏 ID；合法范围内的状态/版本冲突使用 409。

| 范围 | 典型稳定错误码 | HTTP 与处理 |
| --- | --- | --- |
| 登录/会话 | AUTH_LOGIN_FAILED / AUTH_SESSION_INVALID | 401，no-store / WWW-Authenticate，重新登录，不自动重放写请求 |
| 模型 job | MODEL_PROBE_LEASE_LOST / MODEL_PROBE_AUTHORIZATION_DENIED | 409 / 403，重新读取当前任务；不能接管旧 owner 或绕过当前角色 |
| 运行通知/尝试 | RUN_NOTIFICATION_CONFLICT / RUN_RECOVERY_CONFLICT | 409，重新读取 revision/generation，次数保留 |
| 动作/审批 | ACTION_STATE_CONFLICT / APPROVAL_SCOPE_INVALID | 409，重新读取冻结范围，不自动改参/重放/借批准扩权 |
| 连接器/工具 | ACTION_CONNECTOR_UNAVAILABLE / TOOL_* | 503 或 403，保持停用/拒绝，不静默替换实现 |
| 检索 | KNOWLEDGE_EVIDENCE_INVALID / KNOWLEDGE_* | evidence 404 隐藏，索引失败记录持久化 error_code；返回前/提交/展开均复检 |
| 配额 | TENANT_*QUOTA_EXCEEDED / TOOL_CALL_QUOTA_EXCEEDED | 429，额度事实不会因重试/新 attempt 清零 |

Job/索引持久化状态中的失败码不是 HTTP 响应码：模型 job `queued/running/succeeded/failed/cancelled` 与 TestRun `queued/running/passed/failed/cancelled` 分开，知识 Outbox dead/error 只说明索引事实。Run 仅 `queued/running/cancelling/waiting_approval/waiting_action/completed/failed/cancelled`，动作的 `unknown/reconciling/needs_attention` 不移植为 Run 状态。审批等待释放模型 Worker、批准后仅动作阶段继续，submitted 不代表业务成功。

聊天恢复已识别 recovering/recovery_blocked/dispatch_failed/dispatch_retried 及两个 waiting 事件，终态只用服务端事实。取消响应若仍 cancelling 或 waiting，保留 Run/订阅，不能提前显示 cancelled。客户端在快照、发送回执、Run 详情/取消与带 status 的 Run SSE 检查已知状态；未知状态/非法事件 JSON 使用本地 `CLIENT_CONTRACT_UNSUPPORTED` 阻止当前会话的新提交并要求刷新，不映射为失败/完成或自动再次发送。无状态的新增诊断事件可忽略，auth.expired 不参与业务游标过滤。测试与浏览器验收仍暂缓。

- 工具目录与知识来源在原 items 对象上增加分页元数据；来源支持 `keywords` 名称搜索。文档响应增加 `source_name/source_type/source_environment`，仅批量读取该页关联来源。
- 知识版本历史由数组改为标准分页对象，前端 `getKnowledgeVersions` 同步读取 items；API 与 Web 一起发布/回滚，旧客户端不能直接调用新版数组契约。
- `GET /v1/admin/run-notifications` 仅管理员可按本租户查询，支持 `status=pending/delivered/dead_letter/obsolete` 和统一分页。
- `POST /v1/admin/run-notifications/{id}/retry` 要求 `expected_revision`，仅恢复未执行的本租户死信；跨租户 ID 为 404，状态/revision 冲突为 409 `RUN_NOTIFICATION_CONFLICT`。重试保留原消息/Run/固定版本，不复活已取消/已执行 Run。
- `run.dispatch_failed` 与 `run.dispatch_retried` 是附加事件；死信的 Run 仍 queued，等待明确重试或取消，不伪造业务执行终态。新 Run 支持 waiting_approval/waiting_action，API/Worker/Web 须同步升级。

## Mock 审批、工单与升级包

等待审批/动作期间，API 可继续幂等接收同会话输入为 queued；最早 input_sequence 在前轮终态前阻止后轮模型/工具执行，Web 当前保持输入禁用并展示等待。只读详情不启动新 Run，仍使用当前主体/来源/知识 ACL；不能把追问插到冻结动作内。改参必须显式 amend，创建新动作和审批并使旧批准失效；已有执行/消费或未知副作用拒绝改参换键。

本人 evidence/动作/回执/升级包详情由当前认证、租户/原主体/获准来源 Run 和当前资源/工具策略限制；列表先授权再 SQL count/分页。升级包列表仅含 ID/Run/attempt/时间，详细读取重新校验 payload digest 与 Run/attempt 一致，服务入口先拒绝跨租户。客服另需显式 ticket_handle + support_context_read，来源主体仍需有效授权；知识正文还需读取者与原主体双方当前 ACL/有效快照。失效来源投影隐藏问题事实/候选/引用，保留最小不可执行的失效说明；不存在/隐藏 ID 使用 404。历史元数据与未执行建议不能当作执行许可。

模型控制面新增 `GET /v1/admin/model-endpoints/{id}/credential-revisions`、`/credential-audits`，仅当前租户管理员、标准 SQL 权限过滤/稳定分页；前者只选修订元数据列，后者严格白名单，不输出明文、密文或原始解封错误。`POST /{id}/credential-revisions/{revision}/revoke` 要求正整数 revision、expected_current_revision 和非空限长说明，永久撤销指定修订且决定不可覆盖。ModelEndpointResponse 返回 credential_status/provider/revoked_at，历史通过的探测不覆盖撤销；轮换只创建新修订，旧固定引用不替换。当前仅本地密封 provider，0037 未执行。

`POST /v1/runs/{id}/client-first-text` 只接受 event_sequence 和 0–86400000 的 elapsed_ms 整数，必须关联当前主体获准 Run 的非空正文事件；重复上报不改写首次测量。数据为未信任遥测，不写 Run/动作终态。Trace 的 first_output_at 区分 SDK 输出与 API 首次看到已提交正文；API 值是含轮询/重连的提交可见性上界。客户端单调时钟只计算本页提交到 React 正文提交，不当作精确屏幕绘制或与服务器时间相减。观测失败允许 recorded=false，不中断产品响应。

`GET /v1/admin/runtime-alerts` 仅当前租户平台管理员可读取。标准 SQL 分页，`state=unacknowledged|acknowledged|resolved|all`（默认 unacknowledged）、可选 `severity`/固定 `rule_code`；响应包含 `scan_observed_at`、`scan_stale` 和 `convention_version=runtime_alerts_v1`，逐条提供 `is_stale`、来源 generation、告警 revision 及白名单脱敏诊断元数据。共享扫描仅显示成功批次时间，不泄露其他租户游标、来源 ID 或计数。`POST /v1/admin/runtime-alerts/{id}/acknowledge` 接受严格整数 `expected_revision>=1`，越租户 404、版本/状态冲突 409；同一人员已阅重试幂等，同事务追加审计。已阅不改 Run/动作终态，也不是修复证明。扫描规则、阈值和留存边界见 [可观测性](modules/observability/overview.md)。

`GET /v1/admin/system/dashboard/agents/status` 仅管理员读取当前租户标准分页快照，不再通过 GET 重算健康。`computed_at`/`observed_at` 缺失时为空，`health_stale` 表示缺失/超过配置的默认 90 秒或时间超前超过 5 秒；stale 时健康 unknown，错误率/P95 为空，活跃计数仍来自当前执行许可。生命周期、执行态、历史时间与健康快照分别展示；`pending_publish` 按当前草稿与活动版本摘要计算，解析或版本引用缺失保守提示，不沿用旧快照差异。Web 状态页展示真实时间、过期原因和发布差异，使用服务端分页及可重试错误，刷新不会制造成功观测时间。

Dashboard summary/timeseries/runs/errors 聚合、系统排行与 Agent 状态现在共用查询范围：`days=1..90`、可选 `start/end`（须成对、带时区、递增且跨度不超过 90 天，覆盖滚动 days）、`agent_id/user_id/organization_unit_id/agent_version_id/model_endpoint_version_id`、`status`（含 queued/cancelling/waiting_approval/waiting_action）及当前 `lifecycle/health`。普通列表的 total/items 共用 SQL 条件，跨租户关联行不可进入；历史软删除仍按管理员分析边界保留。非法字段返回 422，选定的外租户/不存在标识不扩大全租户范围。

`/v1/admin/analytics/errors` 保留 `status` 作为事件 resolution_status，新增 `run_status` 约束关联 Run，其他共用维度/区间适用；事件按 occurred_at，调用维度通过同范围获准 Run 关联筛选，无法关联的历史事件不冒充已满足新维度。错误聚合/排行是完整分组报告（排行固定 Top 10），不是普通明细分页列表。timeseries 每点追加 `bucket_end`，首尾桶裁切至准确区间；前端图表点击/桶选择、排行和状态下钻保留有效范围并重置页码。

当前 Agent 库存不随时间窗口自动删除“无活动” Agent；用户/部门/版本/模型/Run 状态存在时，仅返回区间内有匹配调用的 Agent。生命周期和健康为当前事实，health_* 全范围 SQL 聚合不从状态第一页推算；状态表的错误率/P95 仍是健康快照固定 24 小时。用户总数未设调用维度时为租户用户（Agent scope 为历史参与者），设维度后为窗口内匹配用户；新增会话按创建时间并在适用时要求有匹配调用。共享 Worker/队列状态是全局基础设施事实，不按这些历史维度拆分。

系统 `rankings` 增加 `active_users/failure_rate/p95_latency_ms/tool_calls`，保留 `agent_runs/tokens/cost_microusd/errors`，各自 Top 10，已知值降序、未知在后、Agent UUID 稳定破平局。调用按业务 Run 去重，活跃用户为原会话用户；失败率只用 completed+failed 分母，取消/等待不入分母。P95 取这些终态的非负 end_to_end_latency_ms，至少 3 个有效样本，SQL row_number/count 与 ceil(0.95*N) 实现 nearest-rank；缺少耗时的 Run 不伪造耗时。Token/费用任一匹配 Run 缺字段时整个该指标为空；工具次数是 ToolInvocation 去重许可（denied 排除，失败/取消/未知许可仍消费），若 Run 汇总缺失或与账本不一致则为空。查询仅使用元数据/SQL 聚合，不读取完整正文/参数，历史会话范围沿用管理分析边界；新指标不代表业务动作成功或生产质量验收。

摘要和趋势桶共用 dashboard_run_metrics SQL 聚合；不加载完整回答，也不把 usage 已知子集作为完整总量。空集合的计数/usage/估算费用为已知 0；存在匹配 Run 且任一输入/输出/费用字段缺失时对应总量 null。DashboardMetric 补 `source/sample_count/missing_count`，Web 单独说明样本/缺失。增加 P50/P99，与 P95 一样仅使用 completed/failed 的非负端到端耗时，nearest-rank、至少 3 个样本；终态端到端耗时包含先前排队/审批等待，不能当作模型执行时间。没有可算终态时成功率/延迟为空。区间末 5 分钟活跃取 [max(start,end-5min),end)，固定历史区间不代表当前在线。

服务摘要 `instances/available_instances` 为当前有效 healthy/degraded 租约，`observed_instances` 另计历史已登记实例；degraded 仍存活，不能当作全部离线。Worker 心跳元数据新增内部 queue_name；`queue_depth` 是 API 所配置共享队列的最新有效 LLEN 样本，同一队列多个 Worker 不相加。旧无队列名、Redis 不可测、负值、时钟异常或 stale 样本保持未知；该深度可能含重复通知，不代表唯一业务 Run 数，也不按租户历史筛选拆分。

管理员 Run Trace 增加 business_trace（business_phases_v1）：Run 根 Span、父子阶段、来源时间/耗时/关联 attempt/action/approval/receipt。它是租户隔离的有界聚合详情，各类来源最多 100 条并显式 truncated，非普通扁平分页接口。直接从持久化阶段/审批/恢复/动作/回执事实投影，不含提案正文、审批理由、知识或原始响应；未结束与未知耗时为空，不把父子耗时或费用相加。原 observations 同时返回 span_id/parent_observation_id/first_output_at。

`GET /v1/admin/runs/{id}/security-events` 仅当前租户管理员可读，权限先于稳定 SQL 分页，返回授权拒绝、审批和动作的追加 RunEvent 白名单元数据。新的动作结果事件关联 generation/action_attempt/receipt_id/receipt_status/冻结摘要和实际版本，旧缺失字段不补造；不返回原始正文、审批理由、回执内容或目标编号。控制面工具策略编辑/撤销审计继续保存在 AgentAuditEvent。

- `GET/POST /v1/admin/mock-resource-grants` 管理明确人员和原主体隔离项目的 approve/ticket_handle/support_context_read；只有管理范围的管理员可配置，角色不自动授予业务读取或处理权。
- `GET /v1/actions/{id}/approvers` 分页列出当前可指定非本人审批者；`POST /v1/actions/{id}/approval` 冻结动作/参数/版本/资源修订/期限。`GET /v1/approvals` 与 `/{id}` 只允许申请者或当前获准指定审批者；decision/cancel 分别追加不可覆盖决定、取消尚未发出意图，不伪称撤回已执行动作。
- `POST /v1/actions/{id}/amend` 校验原摘要/版本，只创建新参数提案和新审批；原决定与回执保留。动作 `/{id}/escalations` 及 `/{id}/escalations/{package_id}` 提供本人分页升级历史和当前权限详情。
- 预算失败的 `POST /v1/actions/mock-ticket-proposals` 仍仅冻结当前获准提案/升级来源，不赋予失败 Run 执行或审批许可。Worker 保存点内可尝试同一入口，失败追加 escalation.unavailable；当前用户请求 ID 的解析与诊断共用，不依赖模型完成。升级包来源变化追加历史，重复同源读取/保存复用；后续写动作需新的有效授权上下文，不能从此接口推断已建单。
- `GET /v1/mock-tickets` 支持 state/keywords 与标准分页，读取本人或显式 ticket_handle 项目工单；`/{external_id}` 返回当前业务状态、处理者、can_claim/can_update/can_read_context，不把历史回执当作当前状态。
- `GET/POST /v1/mock-tickets/{external_id}/commands` 读取分页处理记录或提交 claim/note/reply/close/escalate/release。提交固定 expected_version/idempotency_key/body，同键不同参数拒绝；私有 note 仅当前获准客服可读，认领后才允许后续写操作。
- `GET /v1/mock-tickets/{external_id}/escalations` 及 `/{package_id}` 从目标事实绑定原动作，客服须同时具备 ticket_handle 与 support_context_read，权限先于 count/分页；详情再检查原主体来源及客服/原主体知识 ACL，失效隐藏问题/事实/步骤/引用。
- `GET /v1/conversations/{id}/support-takeover` 返回本人当前获准会话的接管摘要，不含客服私有备注。聊天有限轮询、刷新恢复当前接管状态。全部接口限非生产 Mock；迁移和测试未执行。

## 受控补偿提案与数据保留申请

受控补偿提案接口：

- `GET /v1/actions/{id}/compensation-readiness?context_run_id=...` 只读当前本人原动作及当前已完成且关联的授权上下文。必须有当前generation的独立verify/reconcile且结束的attempt、摘要/主体/业务键一致的verified或conflict回执、同租户本人项目当前Mock工单；unknown/submitted、不明确目标或已关闭不生成范围。当前工具/模型/凭据/grant/版本撤回仍复检。proposal_supported只表示可保存意向，执行器/新非本人审批/重新授权和目标检查始终列为执行阻塞。
- `POST /v1/actions/{id}/compensation-proposals` 接受context_run_id/expected_scope_digest，重读并在会话→Run→动作锁下冻结当前回执/目标版本/参数摘要/上下文和目录；范围变化409 ACTION_STATE_CONFLICT，当前来源撤回拒绝。仅close_mock_ticket意向，最低R3，原或当前策略R4不降低；同tenant/action/scope复用，不接受任意补偿工具/参数、回执上传或成功标记。保存追加控制面审计和action.compensation_proposed事件，不改原动作/批准/工单。
- `GET /v1/actions/{id}/compensation-proposals` 为本人SQL分页，过滤后count，created_at/id稳定倒序；`GET /{proposal_id}` 分开integrity_status和applicability（current/changed/unavailable/integrity_failed）。坏记录隐藏scope，当前不可用隐藏目标范围；不重算覆盖历史。GET/成功响应no-store。所有生成仅显式隔离Mock/非production；没有批准、排队或执行接口，原建单批准不能授权关闭。0046未执行，API/Web须同步升级。

本人数据预览接口：

- `GET/POST /v1/admin/data-governance/policies` 和 `GET /{policy_id}` 仅限当前租户管理员；列表使用标准SQL分页/唯一排序，创建不可变草稿。输入name、rules（category/可空retain_days/可空legal_hold_required/backup_handling）、同租户候选reviewer_user_ids；候选配置不授予复核权限。仅draft，无批准/编辑/删除接口。
- `GET/POST /v1/account/erasure-requests`、`GET /{request_id}` 仅限本人，管理员不绕过。创建只接受categories和固定reason_code，拒绝目标用户/租户或自由个人理由；必需Idempotency-Key（1–200字符，字母数字下划线短横线），同键同类别集合/原因返回原申请，不同409。服务器固定创建时草稿ID/摘要或null、本人/租户和截止时间；不重绑后续草稿。列表权限过滤先于SQL count/分页，page默认1、page_size默认20最大100，created_at/id倒序，越界返回空页。
- `POST /{request_id}/preview` 与 `/withdraw` 均要求expected_revision。仅requested可预览一次，requested/previewed可撤回，撤回不可复活；报告不能覆盖，撤回保留历史。预览整体10秒/PG单语句2.5秒及锁等待1秒，超时/数据库失败返回503 DATA_GOVERNANCE_PREVIEW_UNAVAILABLE并回滚，不能保存半份报告；状态/范围冲突409 DATA_GOVERNANCE_CONFLICT，不存在或他人申请统一404 NOT_FOUND。成功读取及治理冲突响应no-store。
- 报告绑定申请/范围/策略/截止时间和SHA256，记录observed_at/finished_at；跨表计数不是执行一致快照。16类固定目录只返回本人/参与/共享关联行数，包含软删除历史，无正文/资源ID/秘密；未覆盖依赖row_count=null。检查点/长期记忆/备份/外部副本缺适配时标明未知，不当作不存在。额外当前在途Run/动作不受创建截止限制。complete_inventory/eligible_for_erasure/snapshot_is_execution_permit始终false。
- GET只校验已有报告，不重算或修复；摘要/绑定损坏时report_status=integrity_failed并隐藏report/review_scope。未来review_scope须绑定scope_digest/preview_digest/policy_digest，明确独立非本人/显式主体范围、独立维护身份及重新盘点，approval_granted/execution_enabled=false。0045仅提供DeletionTombstone结构，无实际清理/批准/擦除/tombstone写入/恢复重施入口，迁移未执行。

## 执行、索引和证据契约

- `GET /v1/admin/runs/{id}/attempts`、`GET /v1/admin/runs/{id}/model-calls` 为本租户管理员标准分页元数据；不返回原始问题、模型输入或凭据。
- `POST /v1/admin/runs/{id}/recover` 要求 expected_generation；有效租约/未知副作用/终态不能重跑。旧无 owner 执行需要明确确认已停止。
- `POST /v1/admin/tools/{tool_id}/revocation` 要求 revoked/expected_version；普通启用不清除紧急撤销。is_enabled 同时作用于历史绑定的后续调用。
- `GET /v1/admin/knowledge/documents/{id}/index` 返回关键词索引代次与任务合计；`GET /v1/admin/knowledge/index-jobs` 标准分页；rebuild/retry 请求持久化任务，提交不表示已构建成功，retry 保留原死信/次数。
- `GET /v1/runs/{id}/diagnostic` 返回本人 Run 的完整有界 Mock 诊断报告（聚合非列表）；读取复检当前身份、Agent/工具策略和知识引用，失效来源隐藏事实/候选/接管包。会话最新 Run 携带 diagnostic_stages，完整 SupportResult 携带 diagnostic；SSE 阶段事件不包含事实正文。
- `POST /v1/admin/knowledge/versions/{id}/review` 接受 decision、expected_review_revision、expected_content_digest、comment；拒绝需说明，创建人/提交人不能自审。submit-review/publish 可携带 expected_review_revision，重新送审已批准版本必须提供；发布需要独立批准及当前范围/审核人权限一致，submit-review 不代表批准。
- 逐模型 `POST /v1/admin/model-endpoints/{endpoint_id}/models/{model_id}/tests` 同事务保存 TestRun/ModelProbeJob 返回 202，Worker 执行；TestRun 响应增加 nullable probe_job（状态/次数/generation/退避/安全错误/有界尝试摘要），旧历史保持 null。`POST /v1/admin/model-endpoints/test-runs/{id}/cancel` 为当前租户管理员原子取消/fencing，迟到写入不能覆盖终态；刷新 GET 不触发新任务。
- `POST /v1/actions/mock-ticket-proposals` 仅接受本人 run_id；参数和资源由服务端冻结。`GET /v1/actions?run_id=...` 与 `GET /v1/actions/{id}/receipts` 标准数据库分页，`GET /v1/actions/{id}` 返回当前授权详情。context_run_id 必须已有可信提案关联，新 Run 不替换原业务键。
- `POST /v1/actions/{id}/execute` 接收参数摘要/generation/当前目录版本，保存独立执行意图；submitted/queued/unknown 不代表业务成功，需独立 verified 回执。`POST /v1/actions/{id}/reconcile` 只追加一次有界查询许可，累计次数保留，不接受用户输入成功证明。
- `GET /v1/admin/dashboard/runs/{id}/actions` 为租户管理员分页控制面审计，只返回状态/风险/次数/原因等元数据，不返回他人业务参数/目标编号/回执正文，也不授予业务数据越权能力。
- `GET /v1/runs/{id}/evidence/{evidence_id}` 限本人 Run，复查当前 ACL/有效版本；失效/无权统一为 404 KNOWLEDGE_EVIDENCE_INVALID，不泄露标题或快照。
- MessageResponse 新增 nullable support_result，版本 1 含 Run/attempt、答案/摘要、获准 citations、proposed 建议和 recommended 升级意向。引用用 evidence ID，无模型自造 URL；旧正文不回填结果。撤权返回 unavailable 引用，不带旧元数据。
- SSE 附加 result.ready 只含消息标识/版本；原 text.delta/text.replace 和游标保持连续。最终引用不合法会清除临时正文并失败；成功检索无证据由服务端替换为未确认/追问/升级意向。
- ConversationRunState 返回 error_code/error_message；Trace 返回物理账本预算合计与 attempt 标识，缺 usage/费用为 null，错误说明为安全中文。

AgentVersionResponse 增加 resolved_tool_versions，历史配置 digest 不变；当前不可用的固定实现拒绝执行。ModelCallResponse 增加固定模型版本/policy_version，模型停用以 MODEL_RESOURCE_UNAVAILABLE 终止后续调用。System Agent execution_status 可为 idle/queued/running/cancelling/needs_attention，不把排队或过期尝试显示成正在调用模型；健康和活动口径共用核心服务。SupportResult 增加 evidence_conflicts 与可选 diagnostic_data_environment=mock；撤权隐藏冲突原因，模拟业务结果不证明真实生产状态。
