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

## 工具发现治理与来源管理（W8.6/W8.10）

`/v1/admin/tool-discovery/sources`需要当前同租户platform_admin及有效登录会话，仅管理批准与目录元数据。来源别名为1–64位小写字母开头的字母/数字/下划线/短横线，不接受URL、命令或凭据。

| 方法与相对路径 | 请求与行为 |
| --- | --- |
| `GET`（无相对后缀） | 标准`page/page_size`分页，先按租户和来源选最新修订，再SQL计数/分页，source_key/id升序；越界页为空 |
| `GET /{source_key}` | 返回当前`tool_discovery_view_v1`，未配置404。最多50项完整绑定，是单项聚合而非分页列表 |
| `PUT /{source_key}` | `expected_revision`与`policy`；首次修订为0。策略固定`source_fingerprint`、`valid_until`和`allowed_tools: [{name, descriptor_digest}]`，`enabled`默认false；追加pending或disabled修订 |
| `POST /{source_key}/invalidate` | `expected_policy_digest`、`source_fingerprint`及`reason: list_changed/manual_refresh`；追加pending修订，旧绑定失效 |
| `POST /{source_key}/catalogs` | `expected_revision`、`expected_policy_digest`、`source_fingerprint`、`catalog_json`；仅当前pending接受，批准描述摘要必须逐项匹配 |
| `GET /{source_key}/tools/{tool_id}` | 查询`record_id`及`catalog_digest`，只返回当前可用固定描述；旧修订不能继续解析 |

`catalog_json`是适配器归一化后的封闭`tool_discovery_catalog_v1`，包含`tools`，每项为`name/description/input_schema/output_schema`；两侧Schema根必须为封闭object。它不是原生MCP协议帧，也不执行Schema或解析引用。目录最多128KiB、100工具、4096节点、16层；批准最多50工具。重复键/名称、非有限数、引用和未知字段拒绝，只保存获批描述，失败追加rejected及固定`TOOL_DISCOVERY_CATALOG_REJECTED`，不沿用旧快照。稳定别名为`mcp_`加租户/来源/远端名规范JSON的SHA-256，快照另固定全描述、输入/输出Schema及目录摘要。

失效必须先独立提交，未来MCP适配器再在事务外读取来源并以该pending修订提交；迟到旧结果返回409。成功响应及发现冲突均no-store，冲突码`TOOL_DISCOVERY_CONFLICT`；会话失效401 `AUTH_SESSION_INVALID`/Bearer。超时或提交结果未知只GET当前来源核对，不自动重发。自然过期隐藏绑定，`execution_enabled`恒false；没有MCP网络连接、Agent绑定或网关执行许可。依赖0051迁移，尚未执行迁移或接口验收；连接适配、管理UI和测试分别归8.5、8.10、8.11。

W8.10管理部分新增无尾斜杠的`GET /v1/admin/tool-discovery/sources`，继承公共分页响应、默认20/最大100，普通GET不写修订。单项和分页items的`tool_discovery_view_v1`新增完整`policy`，供编辑原批准集合，并单列`connection_status: not_connected`，不能从目录ready推断连接健康。API与Web需同步升级，旧契约被新页面识别后禁写。管理页`/agent-management/tool-sources`提供新建/编辑批准/启停、失效确认和当前固定定义查看；详情工具最多50项为完整配置集合，不额外分页。真实连接探测/熔断/恢复尚未实现，8.10保留未完成；未运行接口、类型、构建或浏览器验收。

## 分页升级与运行通知

### 本人知识导入任务

`/v1/admin/knowledge/ingestion-jobs`需要当前platform_admin且只操作本人申请，其他管理员没有代办或隐式查看权限。GET列表支持page/page_size（默认20、最大100）、固定status筛选和可选source_id；同一tenant/requested_by/业务过滤用于SQL count及created_at/id降序分页，越界页返回空items。GET `/{job_id}`使用相同本人/租户范围，隐藏资源统一404。

响应`knowledge_ingestion_job_view_v1`只包含任务ID、当前同租户来源名称/ID、状态/完整性、revision/代次/累计尝试/固定预算、原始时间/期限、检查点类型/页序号、已提交对象计数和当前取消/超时操作条件。没有正文、路径、owner许可ID、原始或秘密游标引用、批准scope或任意错误。完整性失败返回integrity_failed/unknown，范围/时间/计数及执行字段为空、操作false；GET不修复快照、不补造终态或当前时间。当前仅单页本地清单，automatic_publish_enabled恒false，来源创建/读取执行的默认关闭人工接口见下节，没有自动调度。

POST `/{job_id}/cancel`与`/{job_id}/expire`只接受严格JSON整数expected_revision>=1，当前身份/会话/角色、本人和状态在事务中复检；取消清除许可并增加围栏，超时处理仅在截止或执行租约过期且预算耗尽后显式记failed。两者都保留原草稿/回执/审计，不重试、不读取来源、不审批或发布。成功响应与专属401/403/409为no-store；当前登录失效401 AUTH_SESSION_INVALID/Bearer，修订/状态/完整性冲突409 KNOWLEDGE_INGESTION_CONFLICT，不自动重放写请求。功能依赖未执行的0047迁移，接口/浏览器/类型检查/构建未验收。

### 获准来源人工创建与执行

同一前缀新增`GET /source-readiness`、`POST`、`GET /by-request?request_key=<UUID>`和`POST /{job_id}/execute`；需要当前管理员本人，静态路径先于`/{job_id}`注册。未配置时readiness返回available/create_available=false且范围为空，不读取知识文件；这是单一配置详情，不分页。可用时返回knowledge_ingestion_source_readiness_v1、来源ID/名称、scope_digest、object_count、valid_until、observed_at及automatic_publish_enabled=false，不含目录、文件名、完整策略或正文。来源当前数据库归属与身份复检，剩余批准不足600秒仅禁新建。

创建只接受严格expected_scope_digest与必填UUID Idempotency-Key；服务器当前配置提供来源及批准，不允许客户端/模型自批或调整预算。固定600秒/一次尝试，同键同范围复用、异范围409，不实际读取知识文件。创建提交不明返回409 KNOWLEDGE_INGESTION_EXECUTION_UNCERTAIN；按原request_key查询当前本人/租户记录，未观察到返回固定404而不证明在途未提交，不自动重放POST。Web发送前须按tenant/user将最小UUID请求键/来源ID存入sessionStorage并回读校验，失败不发送；关闭/刷新后恢复仅GET查询，来源未批准/撤销或readiness错误不阻止原请求查询，404保留定位且禁新键创建，回执匹配才清除。不保存令牌/正文/路径或恢复旧批准；本地记录不代表已发送/已创建，也不保证标签页关闭或浏览器清理后保留。

执行只接受严格expected_revision>=1；queued或明确retry_wait才允许，running（含过期租约）/终态拒绝。原任务范围必须匹配当前批准文件，当前身份/来源/目标映射/围栏和期限在边界复检；实际来源IO期间无活跃数据库事务。领取提交不明不启动来源读取；页面提交异常同样返回专属UNCERTAIN，仅查原任务/回执，不写失败或重放。只物化草稿，无自动发布或ACL变更。成功及专属401/403/409响应no-store。入口默认关闭且只允许隔离local/dev/test，staging/production拒绝启用；没有配置实际来源，0047迁移及接口/类型/构建/浏览器验收未执行。配置格式及隔离限制见[知识模块](modules/knowledge/overview.md)。

任务投影另提供可空`scope_digest`：仅完整性通过时按创建时的固定scope计算，不是当前批准事实。Web只在任务来源ID及范围摘要均匹配当前readiness时允许执行，确认发送前复检期限/预算，缺失或变化不发请求；执行回执须匹配原范围。字段不含路径/正文或完整scope，无需结构迁移；API/Web同步升级，旧响应缺少字段时新页面禁执行，历史查询与取消不受影响。

### 本人创建请求状态与显式封锁

完整性通过的Job视图可含`creation_request_key`，仅在原数据库键已经是规范UUID时投影；其他旧字符串和坏记录为空，不修改/转换原键。创建及状态回执核对原键与Job内键一致，非UUID字符串不透出。知识导入页提供独立“按请求标识查询”：即使本标签页定位或来源不可用，仍可输入原UUID只读核对三种服务器状态并打开原任务重新读详情；不新增接口、不创建/停止/执行或清除本地待确认定位。字段变更/关闭取消只读请求，迟到/登录变化/未知回执不用于展示原结果；unobserved不代表未提交，旧API缺少新键时保守不确认，需API/Web同步更新。

实际创建、执行与封锁要求PostgreSQL及READ COMMITTED；配置开启时拒绝非PostgreSQL，服务在实际事务内再检查后端与隔离级别。其他数据库/隔离级别返回固定409 KNOWLEDGE_INGESTION_CONFLICT，不保存停止事实或读取来源；历史查询、取消与显式到期处置继续按原契约。目标读取前后变化通过内部元数据摘要检查，不接受客户端提供目标摘要或恢复许可。

同一前缀新增单项`GET /creation-requests/{request_key}`和`POST /creation-requests/{request_key}/stop`，路径UUID，当前管理员仅限本人/租户，无来源读取批准依赖，即使实际导入已关闭或批准撤销也能查询/停止自己的意图。返回`knowledge_ingestion_creation_status_v1`，含原request_key、status、可空job/stopped_at和真实observed_at；unobserved不补造事实、不证明在途请求未提交，recorded只带原Job视图，stopped只带实际停止时间。数据损坏/互相冲突409且不自动修复；无正文、路径、策略或执行许可，非列表不分页。

POST只接受显式JSON布尔`confirm_stop=true`，缺失/false/数字/额外字段拒绝。当前本人锁与PG READ COMMITTED约束下：已有Job返回recorded，不取消、改写或执行它；已有停止只返回stopped，不重复审计；二者都未观察到才追加停止事实和最小审计，之后同键迟到创建永久拒绝。整个API事务提交后才返回，提交不明409 KNOWLEDGE_INGESTION_EXECUTION_UNCERTAIN，客户端保留原定位并查询，不自动重发。成功及专属401/403/409 no-store。新0048表/触发器及并发行为尚未执行验收，非READ COMMITTED写入由PG触发器拒绝；SQLite不能代替PG并发证明。

Web恢复改用该状态接口；未知版本、目标或回执不清本地定位，unobserved保持等待并给出显式封锁选项，需第二次危险确认。stopped或匹配的原任务才清本地定位；任务已存在时跳到原详情，不把停止意图变成取消任务。旧`GET /by-request`仍保留原Job/404语义，404不表示停止事实，也不为旧客户端补造Job状态。接口、Web、ORM与0048须同步发布，当前默认未启用来源，未运行迁移/测试/类型检查/构建/浏览器验收。

### 本人导入草稿比较

导入草稿的`GET /v1/admin/knowledge/ingestion-jobs/{job_id}/drafts?page=1&page_size=20`只列本任务首次创建的物化ID/时间，当前管理员本人/租户/来源/任务先于SQL count及created_at/id降序分页，最大100；复用旧版本仅保留在任务计数。`GET .../drafts/{materialization_id}/comparison`为单项有界聚合详情，不分页，返回`knowledge_ingestion_comparison_v1`。两接口均no-store，非本人/跨范围固定404；损坏任务冲突409，不修复历史。

比较详情验证页回执/物化/对象/版本摘要和当前目标，文档锁下复检当前知识ACL及有效发布版；本人任务或管理员均不自动获取正文权限。available时返回当前文档/负责人ID/已配置复核时间、候选状态/审核决定、候选及读取时当前发布版本摘要，以及共同首尾行数和中间完整纯文本替换区间。两侧UTF-8总量64KiB、每侧4000行，超限too_large不返回部分差异；这不是最小编辑或语义冲突判断。content_unavailable（无有效发布或ACL不命中）、target_unavailable、newer_candidate、scope_changed、integrity_failed、too_large均隐藏所有目标范围/正文。首次草稿没有有效发布/ACL时也不例外，不配置权限、不审核、不发布；该比较接口不负责分配或到期提示，独立治理接口见下节。无新表，0047及验收仍未执行。

### 本人导入候选复核安排

`GET /v1/admin/knowledge/ingestion-jobs/{job_id}/drafts/{materialization_id}/governance`为单项治理元数据，当前管理员仅本人任务。复用回执/对象/候选最新性校验，返回`knowledge_ingestion_governance_v1`：available时有文档ID、负责人ID/名称/当前有效性、复核时间、候选状态/审核决定、治理摘要、服务端observed_at及review_timing=unscheduled|scheduled|due；无正文/标题/联系方式。首次未发布且无ACL的草稿也能安排元数据，正文比较权限保持独立。target_unavailable/newer_candidate/integrity_failed隐藏范围、update_available=false；跨范围固定404。

`PATCH`同一路径完整提交`expected_governance_digest`、`owner_user_id`与`review_due_at`三个必填字段，拒绝额外字段。日期要求带时区ISO或显式null（清除）；数字时间戳、无时区或缺字段不接受。当前用户/目标负责人按UUID加锁后复检本人/管理员/会话、同租户active负责人、当前候选及文档/ACL/发布/负责人/日期/updated_at/审核范围摘要，变化409 KNOWLEDGE_INGESTION_CONFLICT并要求刷新。同值不追加变更，实际更新复用知识文档服务和最小审计，在API事务内提交；修改不授予ACL/来源执行/审核/发布许可，不改变正式索引。页面到期提示仅按服务端本次观察时间判断，不自动失效知识或发送通知。成功及专属401/403/409均no-store；客户端不自动重放PATCH，失败只查询确认。无新表或迁移，0047与验收仍未执行。

`GET .../governance/owners?keywords=&page=1&page_size=20`限定本人最新有效候选，tenant/active/转义姓名筛选先于SQL count和display_name/id稳定分页，最大100；只返回用户ID/显示名称，不加载凭据或携带邮箱/电话/角色。来源或目标权限不会因出现在候选列表而改变。

### 本人撤回未发布导入候选

`POST /v1/admin/knowledge/ingestion-jobs/{job_id}/drafts/{materialization_id}/withdraw`严格接受当前`expected_governance_digest`和`reason_code=source_scope_changed|manual_resolution_required|no_longer_needed`；无正文、自由说明或批准字段。治理视图增加`withdraw_available`提示条件，提交仍复检当前管理员/会话、本人Job/回执/稳定绑定/创建人/最新候选和完整治理范围；已发布/已被取代/已删除/已归档或范围变化409，不放宽原失效/发布API。

只将本人未发布draft/in_review候选置retired、递增review_revision并更新文档updated_at，保留内容/审核历史和页回执；当前正式版本指针、知识ACL、有效期及索引均不改，无知识Outbox。知识操作与Job/物化/原因/前置摘要/审核修订审计同事务；原因是本人处置意见，不代表系统确认语义冲突或独立审核拒绝。旧批准不能复活退役候选，后续须创建新版本独立审核。

响应仍为`knowledge_ingestion_governance_v1`，新状态`candidate_withdrawn`只在未发布退役事实及唯一同tenant/本人/任务/物化/version/当前整数审核修订的撤回审计匹配后返回，其他范围字段空，update_available/withdraw_available=false；比较详情同步识别且隐藏正文。旧退役无证明不补造。提交前证明不匹配抛错，整笔回滚；重复请求不会重新执行，客户端未知响应只GET确认、不自动重放POST。成功及专属401/403/409均no-store，跨范围固定404；无新表或迁移，运行验收未执行。

### 发布、结果与运行通知

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
| 凭据访问审计 | MODEL_CREDENTIAL_AUDIT_UNAVAILABLE | 503/no-store，审计结果未确认，不自动重放解封或请求；其他凭据拒绝保持403/no-store |
| 凭据密封 | MODEL_CREDENTIAL_SEAL_FAILED | 503/no-store，密封异常或结果不可用，不自动重试或使用旧密钥完成写入 |
| 运行通知/尝试 | RUN_NOTIFICATION_CONFLICT / RUN_RECOVERY_CONFLICT | 409，重新读取 revision/generation，次数保留 |
| 动作/审批 | ACTION_STATE_CONFLICT / APPROVAL_SCOPE_INVALID | 409，重新读取冻结范围，不自动改参/重放/借批准扩权 |
| 连接器/工具 | ACTION_CONNECTOR_UNAVAILABLE / TOOL_* | 503 或 403，保持停用/拒绝，不静默替换实现 |
| 检索 | KNOWLEDGE_EVIDENCE_INVALID / KNOWLEDGE_* | evidence 404 隐藏，索引失败记录持久化 error_code；返回前/提交/展开均复检 |
| 配额 | TENANT_*QUOTA_EXCEEDED / TOOL_CALL_QUOTA_EXCEEDED | 429，额度事实不会因重试/新 attempt 清零 |

Job/索引持久化状态中的失败码不是 HTTP 响应码：模型 job `queued/running/succeeded/failed/cancelled` 与 TestRun `queued/running/passed/failed/cancelled` 分开，知识 Outbox dead/error 只说明索引事实。Run 仅 `queued/running/cancelling/waiting_approval/waiting_action/completed/failed/cancelled`，动作的 `unknown/reconciling/needs_attention` 不移植为 Run 状态。审批等待释放模型 Worker、批准后仅动作阶段继续，submitted 不代表业务成功。

凭据已解封但访问审计会话/写入/提交/退出失败或超过2秒工作等待时，停止返回密钥并使用MODEL_CREDENTIAL_AUDIT_UNAVAILABLE。PG独立审计事务语句1秒/锁250毫秒，返回密钥前继续固定修订和当前状态复核。该码不证明未解封或审计未提交，不自动再次解封、换修订或重放审计；异步Run/探测保留原固定引用和失败码，不作为自动依赖恢复条件。外部取消传播，回滚/关闭不承诺硬截止。字段契约不变，Web通用错误处理和OpenAPI全局503适用；本轮未执行API/数据库或故障验收。

创建模型端点、保存连接配置的新密钥及显式轮换的api_key在去除首尾空白后须为8至20000个字符；API/Core/本地密封共用边界。配置编辑api_key=null/未提供保持原密钥，显式空白或短Key返回422；Schema校验为VALIDATION_ERROR，核心校验为MODEL_ENDPOINT_INVALID，均不返回输入材料。provider密封异常或非字符串/空白/原样输入结果使用MODEL_CREDENTIAL_SEAL_FAILED（503/no-store），不自动重试或回退旧Key，API原事务回滚。响应字段及URL不变，配置表单补充长度提示，模型发现的仅本次请求Key不持久化，原契约不变；未运行接口/页面/数据库验收。

聊天恢复已识别 recovering/recovery_blocked/dispatch_failed/dispatch_retried 及两个 waiting 事件，终态只用服务端事实。取消响应若仍 cancelling 或 waiting，保留 Run/订阅，不能提前显示 cancelled。客户端在快照、发送回执、Run 详情/取消与带 status 的 Run SSE 检查已知状态；未知状态/非法事件 JSON 使用本地 `CLIENT_CONTRACT_UNSUPPORTED` 阻止当前会话的新提交并要求刷新，不映射为失败/完成或自动再次发送。无状态的新增诊断事件可忽略，auth.expired 不参与业务游标过滤。测试与浏览器验收仍暂缓。

- 工具目录与知识来源在原 items 对象上增加分页元数据；来源支持 `keywords` 名称搜索。文档响应增加 `source_name/source_type/source_environment`，仅批量读取该页关联来源。
- 知识版本历史由数组改为标准分页对象，前端 `getKnowledgeVersions` 同步读取 items；API 与 Web 一起发布/回滚，旧客户端不能直接调用新版数组契约。
- `GET /v1/admin/run-notifications` 仅管理员可按本租户查询，支持 `status=pending/delivered/dead_letter/obsolete` 和统一分页。
- `POST /v1/admin/run-notifications/{id}/retry` 要求 `expected_revision`，仅恢复未执行的本租户死信；跨租户 ID 为 404，状态/revision 冲突为 409 `RUN_NOTIFICATION_CONFLICT`。重试保留原消息/Run/固定版本，不复活已取消/已执行 Run。
- `run.dispatch_failed` 与 `run.dispatch_retried` 是附加事件；死信的 Run 仍 queued，等待明确重试或取消，不伪造业务执行终态。新 Run 支持 waiting_approval/waiting_action，API/Worker/Web 须同步升级。

## Mock 审批、工单与升级包

等待审批/动作期间，API 可继续幂等接收同会话输入为 queued；最早 input_sequence 在前轮终态前阻止后轮模型/工具执行，Web 当前保持输入禁用并展示等待。只读详情不启动新 Run，仍使用当前主体/来源/知识 ACL；不能把追问插到冻结动作内。改参必须显式 amend，创建新动作和审批并使旧批准失效；已有执行/消费或未知副作用拒绝改参换键。

本人 evidence/动作/回执/升级包详情由当前认证、租户/原主体/获准来源 Run 和当前资源/工具策略限制；列表先授权再 SQL count/分页。升级包列表仅含 ID/Run/attempt/时间，详细读取重新校验 payload digest 与 Run/attempt 一致，服务入口先拒绝跨租户。客服另需显式 ticket_handle + support_context_read，来源主体仍需有效授权；知识正文还需读取者与原主体双方当前 ACL/有效快照。失效来源投影隐藏问题事实/候选/引用，保留最小不可执行的失效说明；不存在/隐藏 ID 使用 404。历史元数据与未执行建议不能当作执行许可。

两个升级包详情入口在5秒读取事务内完成响应DTO投影（PostgreSQL语句4秒/锁1秒），成功响应`Cache-Control: no-store`；实际引用正文SQL同时限定原主体与读取者的当前知识ACL，多份引用先按文档ID统一加锁。任一引用不可用沿用隐藏上下文的成功投影，数据库异常/读取超时使用既有`409 KNOWLEDGE_CONFLICT`，不伪装成撤权或恢复旧内容。该事务不是数据库只读模式，不承诺驱动清理硬截止，也未扩展知识快照/本人引用专属拒绝审计的覆盖范围。

模型控制面新增 `GET /v1/admin/model-endpoints/{id}/credential-revisions`、`/credential-audits`，仅当前租户管理员、标准 SQL 权限过滤/稳定分页；前者只选修订元数据列，后者严格白名单，不输出明文、密文或原始解封错误。`POST /{id}/credential-revisions/{revision}/revoke` 要求正整数 revision、expected_current_revision 和非空限长说明，永久撤销指定修订且决定不可覆盖。ModelEndpointResponse 返回 credential_status/provider/revoked_at，历史通过的探测不覆盖撤销；轮换只创建新修订，旧固定引用不替换。当前仅本地密封 provider，0037 未执行。

模型详情、凭据修订和凭据审计GET及撤销成功响应使用`Cache-Control: no-store`，Web四个对应请求均禁缓存。撤销确认固定目标ID/revision、打开时的expected_current_revision和登录会话，已知变化须重新确认；回执须对应原目标及撤销时间/人员才显示成功。请求失败或回执未知关闭旧确认并只读刷新，不自动重发POST，不因读取缺失声称原写入未发生。字段、URL和服务端并发/权限契约不变；未运行接口/浏览器故障验收。

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
- 新报告的credentials类别按本人截止时间内会话/Run统计固定Agent/模型版本及对应凭据；ModelCredentialRevision只计Run实际固定的credential_id/revision组合，包含软删除历史，不把同端点其他修订纳入。knowledge追加本人Run证据引用的快照/分块集合计数，重复引用不重复计实体；分块无创建时间，按证据检索截止限定。既有tables.reason_code新增六个固定说明：FIXED_MODEL_CREDENTIAL_REFERENCE、FIXED_RUN_CREDENTIAL_REVISION_REFERENCE、FIXED_CONVERSATION_AGENT_REFERENCE、FIXED_MODEL_VERSION_REFERENCE、FIXED_EVIDENCE_SNAPSHOT_REFERENCE、FIXED_EVIDENCE_CHUNK_REFERENCE；已知非零引用另加类别阻塞FIXED_REFERENCES_PRESENT。计数只有metadata，不读正文/密文或返回资源ID，不证明当前可用/完整盘点/保护部署/擦除许可，原未知和法律保留阻塞保留；旧报告缺少说明保持原文和摘要，不回填。无新增字段/路由/迁移，API/Web同步，未执行数据库或接口/浏览器验收。

## 执行、索引和证据契约

知识快照/引用读取补充：`GET /v1/admin/knowledge/documents/{id}/snapshot`及`GET /v1/runs/{id}/evidence/{evidence_id}`在共同文档锁内复检当前ACL并完成响应投影，成功读取响应no-store；知识专属403/引用404也no-store，既有隐藏404不变。引用ACL前只加载定位元数据，实际正文SQL仍核对当前版本/有效期/ACL。整段读取等待5秒，PG语句4秒/锁1秒；数据库失败/超时为既有409 KNOWLEDGE_CONFLICT，不返回旧正文或记为权限拒绝。已认证的明确拒绝在原事务结束后，另用2秒等待（PG语句1秒/锁250毫秒）追加既有knowledge_access_denied审计；metadata只含固定操作/结果/requested_only及租户域内请求目标/Run/关联值摘要，actor/tenant为可信principal，不保存原始目标、标题、正文、查询、令牌或异常，不推断目标存在。审计失败不改变原拒绝、没有必达/自动重试承诺；无新增请求/响应字段或路由，当前测试/数据库/并发验收未执行。

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
