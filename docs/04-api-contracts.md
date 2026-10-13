# API 契约约定

## 分层

| 前缀 | 用途 | 典型权限 |
| --- | --- | --- |
| `/v1/auth` | 登录和当前身份 | 公开登录 / 已登录 |
| `/v1/admin/*` | 控制面、知识和 Dashboard | `platform_admin` |
| `/v1/agents` | 员工可用 Agent 目录 | 显式授权 |
| `/v1/conversations` | 会话、消息和历史 | 会话主体授权 |
| `/v1/runs` | Run 状态、取消和事件流 | 会话主体授权 |

## 通用规则

- 使用 JWT 建立主体、租户和角色上下文。
- 跨租户或无权限资源统一返回不可枚举的 404 语义。
- 消息写入必须携带 `Idempotency-Key`。
- 普通列表统一使用 `page >= 1`、`page_size` 默认 20/最大 100，返回 `items/total/page/page_size/pages`，在数据库 count/offset/limit 并稳定排序。越界页返回空 items，保留请求页码；部门完整树、整体替换集合和 SSE 保留完整语义。
- 错误响应包含稳定错误码和 correlation ID，不返回密钥、Prompt、内部域名或完整敏感正文。
- SSE 支持 `Last-Event-ID` 和 `cursor`，终态后关闭流。

## 分页升级与运行通知

- 工具目录与知识来源在原 items 对象上增加分页元数据；来源支持 `keywords` 名称搜索。文档响应增加 `source_name/source_type/source_environment`，仅批量读取该页关联来源。
- 知识版本历史由数组改为标准分页对象，前端 `getKnowledgeVersions` 同步读取 items；API 与 Web 一起发布/回滚，旧客户端不能直接调用新版数组契约。
- `GET /v1/admin/run-notifications` 仅管理员可按本租户查询，支持 `status=pending/delivered/dead_letter/obsolete` 和统一分页。
- `POST /v1/admin/run-notifications/{id}/retry` 要求 `expected_revision`，仅恢复未执行的本租户死信；跨租户 ID 为 404，状态/revision 冲突为 409 `RUN_NOTIFICATION_CONFLICT`。重试保留原消息/Run/固定版本，不复活已取消/已执行 Run。
- `run.dispatch_failed` 与 `run.dispatch_retried` 是附加事件，原六种 Run 状态保持不变；死信的 Run 仍 queued，等待明确重试或取消，不伪造业务执行终态。

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
