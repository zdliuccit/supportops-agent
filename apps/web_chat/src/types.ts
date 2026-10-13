/** Agent Run 的完整生命周期状态。 */
export type RunStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "completed"
  | "failed"
  | "cancelled";

/** 审批等待/决定/失效/一次性消费状态。 */
export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired" | "cancelled" | "invalidated" | "consumed";

/** 权限过滤后的审批摘要，不含冻结正文。 */
export interface ApprovalSummary {
  /** 申请 ID。 */ id: string;
  /** 原逻辑动作。 */ action_id: string;
  /** 授权上下文。 */ run_id: string;
  /** 原资源主体。 */ applicant_id: string;
  /** 明确指定的审批者。 */ approver_id: string;
  /** 固定工具。 */ tool_id: string;
  /** 固定实现版本。 */ implementation_version: number;
  /** 冻结目录策略版本。 */ catalog_version: number;
  /** 冻结参数摘要。 */ parameters_digest: string;
  /** 当前审批事实。 */ status: ApprovalStatus;
  /** 并发版本。 */ revision: number;
  /** 服务器限定期限。 */ expires_at: string;
  /** 申请时间。 */ created_at: string;
  /** 决定/终止时间。 */ finished_at: string | null;
  /** 仅隔离数据。 */ data_environment: "mock";
}

/** 再次授权后读取的冻结审批详情。 */
export interface ApprovalDetail extends ApprovalSummary {
  /** 严格服务端模板参数。 */ parameters: BusinessActionDetail["parameters"];
  /** 固定风险。 */ risk_level: "R2" | "R3" | "R4";
  /** 独立动作执行状态。 */ action_status: BusinessActionStatus;
  /** 脱敏决定理由。 */ decision_reason: string | null;
  /** 当前指定非本人审批者可决定。 */ can_decide: boolean;
  /** 当前申请者可取消未消费申请。 */ can_cancel: boolean;
}

/** 当前项目显式获准审批人员。 */
export interface ApprovalApprover {
  /** 人员 ID。 */ user_id: string;
  /** 当前人员名称。 */ display_name: string;
}

/** 隔离项目的显式审批/客服授权。 */
export interface MockResourceGrant {
  /** 授权 ID。 */ id: string;
  /** 资源主体。 */ owner_user_id: string;
  /** 获准人员。 */ user_id: string;
  /** 服务器派生项目。 */ project_id: string;
  /** 独立权限。 */ permission: "approve" | "ticket_handle";
  /** 当前启停状态。 */ is_enabled: boolean;
  /** 并发更新版本。 */ revision: number;
  /** 变更时间。 */ updated_at: string;
  /** 隔离数据。 */ data_environment: "mock";
}

/** 用户可见的单条会话消息。 */
export interface Message {
  /** 消息 UUID。 */
  id: string;
  /** 消息发送方；system 消息由平台生成。 */
  role: "user" | "assistant" | "system";
  /** 用户可见消息正文。 */
  content: string;
  /** ISO 8601 格式的创建时间。 */
  created_at: string;
  /** 所属输入轮次；旧无法关联的消息为空。 */
  turn_sequence?: number | null;
  /** 服务端校验的完整结果；旧消息及流中消息可为空。 */
  support_result?: SupportResult | null;
}

/** 当前权限投影后的合法引用，失效时不返回标题和快照元数据。 */
export interface ResultCitation {
  /** 服务端生成的证据 UUID。 */ evidence_id: string;
  /** 当前有效且可读，或失效/撤权后不可读。 */ status: "available" | "unavailable";
  /** 获准的知识快照 UUID。 */ snapshot_id: string | null;
  /** 不可变内容版本 UUID。 */ version_id: string | null;
  /** 服务端确认的知识标题。 */ title: string | null;
  /** 规范化原文章节锚点。 */ section_id: string | null;
  /** 服务端确认的章节标题。 */ section_title: string | null;
  /** 检索分块 SHA-256。 */ content_digest: string | null;
  /** 实际检索时间。 */ retrieved_at: string | null;
}

/** 刷新后可重建的产品结果；建议和升级意向不表示已经执行。 */
export interface SupportResult {
  /** 当前授权的实际业务提案/动作；工具成功不冒充执行成功。 */ business_actions?: {
    /** 稳定动作 ID。 */ action_id: string;
    /** 当前动作状态；撤权时隐藏。 */ status: BusinessActionStatus | null;
    /** 当前权限投影结果。 */ availability: "available" | "unavailable";
    /** 隔离数据。 */ data_environment: "mock";
    /** 实际最低风险。 */ risk_level: string | null;
  }[];
  /** 结果契约版本。 */ schema_version: "1";
  /** 关联业务 Run UUID。 */ run_id: string;
  /** 最终提交的执行尝试 UUID。 */ attempt_id: string;
  /** 用户可见答案正文。 */ answer: string;
  /** 模型返回的简短摘要。 */ summary: string | null;
  /** 当前权限允许展示的引用集合。 */ citations: ResultCitation[];
  /** 仅为待考虑建议，没有执行回执。 */ suggested_actions: {
    /** 建议说明。 */ description: string;
    /** 建议尚未执行。 */ status: "proposed";
  }[];
  /** 是否建议升级；未自动建立工单。 */ escalation_status: "recommended" | "not_requested";
  /** 经证据身份校验的不一致来源；撤权后隐藏原因。 */ evidence_conflicts?: {
    /** 本次获准检索的不同证据 UUID。 */ evidence_ids: string[];
    /** 当前引用是否仍可读取。 */ status: "available" | "unavailable";
    /** 模型标记的待复核原因，不作为确定根因。 */ reason: string | null;
  }[];
  /** 隔离模拟业务数据，不能当作真实生产诊断。 */ diagnostic_data_environment?: "mock" | null;
  /** 服务端有界诊断事实与升级包，读取时重新检查来源权限。 */ diagnostic?: DiagnosticReport | null;
}

/** 业务动作独立于聊天 Run 的生命周期。 */
export type BusinessActionStatus = "proposed" | "waiting_approval" | "queued" | "running" | "unknown" | "reconciling" | "succeeded" | "failed" | "needs_attention" | "cancelled";

/** 当前主体可读的冻结动作与执行事实。 */
export interface BusinessActionDetail {
  /** 稳定动作 ID。 */ id: string;
  /** 原始提案 Run。 */ run_id: string;
  /** 注册工具标识。 */ tool_id: string;
  /** 实际动作状态。 */ status: BusinessActionStatus;
  /** 隔离模拟环境。 */ data_environment: "mock";
  /** 固定最低风险。 */ risk_level: "R2" | "R3" | "R4";
  /** 是否仍需审批。 */ approval_required: boolean;
  /** 固定实现版本。 */ implementation_version: number;
  /** 原提案目录版本。 */ catalog_version: number;
  /** 当前复检的目录版本。 */ effective_catalog_version: number | null;
  /** 已提交执行意图时的目录版本。 */ execution_catalog_version: number | null;
  /** 当前授权的执行 Run，保留原出处。 */ execution_run_id: string | null;
  /** 服务端派生的冻结参数。 */ parameters: {
    /** 参数契约版本。 */ schema_version: "1";
    /** 安全标题。 */ title: string;
    /** 最小支持摘要。 */ summary: string;
    /** 服务器解析的业务意图。 */ issue_key: string;
  };
  /** 参数 SHA-256。 */ parameters_digest: string;
  /** 当前执行 generation。 */ generation: number;
  /** 累计独立对账次数，不因恢复清零。 */ reconciliation_count: number;
  /** 当前有限对账许可。 */ reconciliation_limit: number;
  /** 复核成功后才展示的外部 ID。 */ external_id: string | null;
  /** 稳定状态原因码。 */ error_code: string | null;
  /** 原创建时间。 */ created_at: string;
  /** 确认终态时间。 */ finished_at: string | null;
}

/** 独立回执摘要；submitted 不能当作业务执行成功。 */
export interface ActionReceipt {
  /** 回执 UUID。 */ id: string;
  /** 提供证明的实际动作尝试。 */ attempt_id: string;
  /** 连接器版本。 */ connector_version: string;
  /** 已知模拟目标编号。 */ external_id: string | null;
  /** 实际回执类型。 */ status: "submitted" | "verified" | "conflict" | "not_executed" | "unknown";
  /** 白名单回执摘要。 */ result_digest: string;
  /** 实际观察时间。 */ observed_at: string;
}

/** 管理台脱敏动作审计；不包含他人参数/目标业务编号/回执正文。 */
export interface ActionAuditSummary {
  /** 动作标识。 */ id: string;
  /** 实际生命周期。 */ status: BusinessActionStatus;
  /** 注册工具。 */ tool_id: string;
  /** 记录的最低风险。 */ risk_level: string;
  /** 当前 generation。 */ generation: number;
  /** 累计对账次数。 */ reconciliation_count: number;
  /** 稳定原因。 */ error_code: string | null;
  /** 记录创建时间。 */ created_at: string;
  /** 确认终态时间。 */ finished_at: string | null;
}

/** 服务端固定诊断步骤，刷新后从产品快照恢复。 */
export interface DiagnosticStage {
  /** 版本化节点名称。 */ name: "classify" | "collect_information" | "query_logs" | "query_profile" | "query_route" | "query_incidents" | "retrieve_rules" | "evaluate" | "escalate" | "complete";
  /** 实际节点状态。 */ status: "running" | "completed" | "skipped" | "failed";
  /** 节点开始时间。 */ started_at: string;
  /** 收敛时间。 */ finished_at: string | null;
  /** 受限错误码。 */ error_code: string | null;
}

/** 只含 Mock 白名单字段的诊断报告；查询事实不等同于根因。 */
export interface DiagnosticReport {
  /** 契约版本。 */ schema_version: "1";
  /** 固定工作流版本。 */ workflow_version: "mock_429_workflow_v1";
  /** 明确标注隔离数据。 */ data_environment: "mock";
  /** 业务 Run。 */ run_id: string;
  /** 报告执行尝试。 */ attempt_id: string;
  /** 已确认仅指模拟环境。 */ status: "running" | "needs_information" | "confirmed_mock" | "unconfirmed" | "conflict" | "unavailable";
  /** 获准模拟标识或未配置标记。 */ request_id: string | null;
  /** 固定步骤上限。 */ max_steps: 10;
  /** 固定查询上限。 */ max_queries: 5;
  /** 持久化节点状态。 */ stages: DiagnosticStage[];
  /** 校验后的调用来源摘要。 */ sources: {
    /** 工具账本 ID。 */ invocation_id: string;
    /** 稳定工具 ID。 */ tool_id: string;
    /** 固定实现版本。 */ implementation_version: number;
    /** 执行时策略版本。 */ catalog_version: number;
    /** 白名单结果摘要。 */ result_digest: string;
    /** 事实查询时间。 */ observed_at: string;
    /** 是否通过实时校验复用了成功只读结果。 */ reused: boolean;
  }[];
  /** 查询已观察的事实；禁止当作真实生产数据。 */ facts: {
    /** 来源工具。 */ tool_id: string;
    /** 白名单字段。 */ field: string;
    /** 获准标量值。 */ value: string | number | boolean | null;
    /** 调用回执。 */ invocation_id: string;
    /** 实际查询时间。 */ observed_at: string;
    /** 仅表明来源提供该事实。 */ confirmation: "source_observed";
  }[];
  /** 事实支持的根因候选。 */ candidates: {
    /** 固定规则代码。 */ code: string;
    /** 服务端描述。 */ description: string;
    /** 模拟根因确认状态。 */ confirmation: "confirmed_mock" | "unconfirmed";
    /** 支撑调用。 */ source_ids: string[];
    /** 支撑知识。 */ evidence_ids: string[];
  }[];
  /** 当前获准知识规则 ID。 */ evidence_ids: string[];
  /** 未确认事项。 */ unresolved_items: string[];
  /** 稳定原因代码。 */ reason_codes: string[];
  /** 接管意向；尚未创建工单。 */ escalation: {
    /** 尚需人工处理。 */ status: "recommended";
    /** 升级原因代码。 */ reason_codes: string[];
    /** 已观察事实数。 */ confirmed_fact_count: number;
    /** 事实来源。 */ source_ids: string[];
    /** 合法知识引用。 */ evidence_ids: string[];
    /** 实际完成的排查节点。 */ completed_steps: DiagnosticStage["name"][];
    /** 需人工补充的事项。 */ unresolved_items: string[];
  } | null;
  /** 工作流完成时间。 */ finished_at: string | null;
}

/** 通过当前权限检查后读取的单份证据正文。 */
export interface KnowledgeEvidenceDetail {
  /** 安全证据标识。 */ evidence_id: string;
  /** 不可变快照 UUID。 */ snapshot_id: string;
  /** 内容版本 UUID。 */ version_id: string;
  /** 文档标题。 */ title: string;
  /** 章节锚点。 */ section_id: string;
  /** 章节标题。 */ section_title: string;
  /** 分块原文摘要。 */ content_digest: string;
  /** 实际检索时间。 */ retrieved_at: string;
  /** 以纯文本展示的分块原文，不执行 HTML 或外链。 */ content: string;
}

/** 知识详情的关键词索引状态。 */
export interface KnowledgeIndexState {
  /** 稳定文档 UUID。 */ document_id: string;
  /** 关键词算法版本。 */ strategy: string;
  /** 当前不启用向量能力。 */ vector_enabled: boolean;
  /** 最新构建代次；尚无索引时为空。 */ generation: {
    /** 代次 UUID。 */ id: string;
    /** 发布快照 UUID。 */ snapshot_id: string;
    /** 文档内递增代次。 */ generation_number: number;
    /** 分块策略版本。 */ algorithm_version: string;
    /** 构建状态，检索仍复查当前权限和有效期。 */ status: string;
    /** 分块总数。 */ chunk_count: number;
    /** 脱敏错误码。 */ error_code: string | null;
    /** 创建时间。 */ created_at: string;
    /** 成功构建时间。 */ ready_at: string | null;
  } | null;
  /** 等待消费的任务数。 */ pending_jobs: number;
  /** 耗尽重试的任务数。 */ dead_jobs: number;
}

/** 关键词索引事件元数据，不包含知识正文。 */
export interface KnowledgeIndexJob {
  /** 任务 UUID。 */ id: string;
  /** 文档 UUID。 */ document_id: string;
  /** 生命周期版本 UUID。 */ version_id: string | null;
  /** 生命周期事件类型。 */ event_type: string;
  /** 历史消费次数。 */ attempts: number;
  /** 下次消费时间。 */ available_at: string;
  /** 已处理时间。 */ processed_at: string | null;
  /** 死信时间。 */ dead_at: string | null;
  /** 脱敏稳定错误码。 */ error_code: string | null;
  /** 任务创建时间。 */ created_at: string;
  /** 仅未创建恢复任务的死信可恢复。 */ retry_available: boolean;
}

/** 最新执行的可续传快照，正文与事件游标来自同一服务端状态。 */
export interface ConversationRunState {
  /** 当前尝试已持久化的诊断阶段，刷新不重新执行查询。 */ diagnostic_stages?: DiagnosticStage[];
  /** 执行 UUID。 */
  id: string;
  /** 最终失败的稳定错误码。 */
  error_code?: string | null;
  /** 服务端返回的可执行中文说明。 */
  error_message?: string | null;
  /** 本次执行对应的用户输入消息 UUID。 */
  input_message_id: string;
  /** 执行生命周期状态。 */
  status: RunStatus;
  /** 快照已包含的最后一个事件序号。 */
  last_event_id: number;
  /** 已持久化的累计回答正文。 */
  content: string;
  /** 本轮固定输入序号。 */
  input_sequence?: number | null;
  /** 当前获准执行的尝试 UUID。 */
  current_attempt_id?: string | null;
  /** 当前执行 fencing generation。 */
  execution_generation?: number;
  /** 已使用的执行恢复次数。 */
  recovery_count?: number;
  /** 稳定恢复/人工诊断原因码。 */
  recovery_reason?: string | null;
}

/** 会话详情；创建时固定 Agent 和模型版本。 */
export interface Conversation {
  /** 会话 UUID。 */
  id: string;
  /** 会话使用的稳定 Agent UUID。 */
  agent_id: string;
  /** 会话创建时固定的不可变 AgentVersion UUID。 */
  agent_version_id: string;
  /** Agent 当前活动版本；停用时或无活动版本时为空。 */
  current_agent_version_id: string | null;
  /** 会话固定 AgentVersion 的租户内版本号。 */
  agent_version_number: number;
  /** AgentVersion 固定引用的 ModelEndpointVersion UUID。 */
  model_endpoint_version_id: string;
  /** 服务端权限过滤后的 Agent 展示资料。 */
  agent: Agent;
  /** 可修改的会话标题。 */
  title: string | null;
  /** 是否在当前 Agent 的最近会话中置顶。 */
  is_pinned: boolean;
  /** 会话生命周期状态。 */
  status: "active" | "closed";
  /** ISO 8601 格式的创建时间。 */
  created_at: string;
  /** ISO 8601 格式的最近活动时间。 */
  updated_at: string;
  /** 按输入轮次及用户/助手顺序返回的产品消息；旧无法关联的消息保留历史。 */
  messages: Message[];
  /** 最新执行及流式正文；旧服务端无此字段时按普通历史处理。 */
  latest_run?: ConversationRunState | null;
}

/** 左侧最近会话列表使用的轻量摘要。 */
export interface ConversationSummary {
  /** 会话 UUID。 */
  id: string;
  /** 会话所属的稳定 Agent UUID。 */
  agent_id: string;
  /** 会话固定的 AgentVersion UUID。 */
  agent_version_id: string;
  /** 会话标题。 */
  title: string | null;
  /** 是否置顶。 */
  is_pinned: boolean;
  /** 会话生命周期状态。 */
  status: "active" | "closed";
  /** ISO 8601 格式的创建时间。 */
  created_at: string;
  /** ISO 8601 格式的最近活动时间。 */
  updated_at: string;
}

/** 会话资料的局部更新请求。 */
export interface ConversationUpdate {
  /** 新标题；省略表示不修改。 */
  title?: string;
  /** 新置顶状态；省略表示不修改。 */
  is_pinned?: boolean;
}

/** 最近会话列表响应。 */
export type ConversationListResponse = PaginatedListResponse<ConversationSummary>;

/** 后端普通列表接口统一返回的分页结构。 */
export interface PaginatedListResponse<T> {
  /** 当前页的数据集合。 */
  items: T[];
  /** 符合当前过滤条件的数据总数。 */
  total: number;
  /** 当前页码，从 1 开始。 */
  page: number;
  /** 当前每页数据数量。 */
  page_size: number;
  /** 按当前每页数量计算的总页数。 */
  pages: number;
}

/** 消息已持久化并创建异步 Run 后的响应。 */
export interface MessageAccepted {
  /** 已持久化的用户消息 UUID。 */
  message_id: string;
  /** 异步 Agent Run UUID。 */
  run_id: string;
  /** Run 的初始状态。 */
  status: RunStatus;
  /** 当前 Run 的 SSE 事件订阅路径。 */
  events_url: string;
  /** Run 固定使用的 Agent UUID。 */
  agent_id: string;
  /** Run 固定使用的 AgentVersion UUID。 */
  agent_version_id: string;
  /** Run 固定使用的 ModelEndpointVersion UUID。 */
  model_endpoint_version_id: string;
}

/** SSE 返回的单条 Run 生命周期或流式输出事件。 */
export interface RunEvent {
  /** 全局递增事件 ID，也用于 Last-Event-ID 重连。 */
  id: number;
  /** 规范事件类型，例如 text.delta 或 run.completed。 */
  type: string;
  /** 已脱敏的事件负载。 */
  data: Record<string, unknown>;
}

/** 当前业务 Run 与尝试恢复状态。 */
export interface RunDetail {
  /** 业务 Run UUID。 */
  id: string;
  /** 所属会话 UUID。 */
  conversation_id: string;
  /** 固定 Agent UUID。 */
  agent_id: string;
  /** 固定 Agent 版本 UUID。 */
  agent_version_id: string;
  /** 固定模型版本 UUID。 */
  model_endpoint_version_id: string;
  /** 原六种业务生命周期状态。 */
  status: RunStatus;
  /** 稳定失败码；未知时为空。 */
  error_code: string | null;
  /** 请求关联标识。 */
  correlation_id: string;
  /** 固定输入序号。 */
  input_sequence: number | null;
  /** 当前尝试 UUID。 */
  current_attempt_id: string | null;
  /** 当前执行 generation。 */
  execution_generation: number;
  /** 已使用的恢复次数。 */
  recovery_count: number;
  /** 恢复或人工诊断原因。 */
  recovery_reason: string | null;
  /** 创建时间。 */
  created_at: string;
  /** 最近领取时间。 */
  started_at: string | null;
  /** 业务终态时间。 */
  finished_at: string | null;
}

/** 管理员可查询的执行尝试，不包含凭据、输入或工具参数。 */
export interface RunAttempt {
  /** 尝试 UUID。 */
  id: string;
  /** 所属业务 Run UUID。 */
  run_id: string;
  /** Run 内递增 generation。 */
  generation: number;
  /** Worker 启动实例标识。 */
  owner_id: string;
  /** 尝试自身的状态，与业务 Run 状态分开。 */
  status: "running" | "completed" | "failed" | "cancelled" | "expired" | "quarantined";
  /** 最后续租时间。 */
  heartbeat_at: string;
  /** 当前尝试租约到期时间。 */
  lease_expires_at: string;
  /** 该尝试独占的 checkpoint thread。 */
  checkpoint_thread_id: string;
  /** 成功提交关联的精确 checkpoint ID。 */
  checkpoint_id: string | null;
  /** 自动重放安全性。 */
  replay_policy: "read_only" | "manual";
  /** 需要对账的副作用事实。 */
  side_effect_state: "none" | "unknown" | "confirmed";
  /** 尝试脱敏错误码。 */
  error_code: string | null;
  /** 创建该恢复尝试的原因。 */
  recovery_reason: string | null;
  /** 已确认的本尝试脱敏计量。 */
  metrics: Record<string, unknown>;
  /** 当前输入/历史边界与证据定位，不含原始问题或秘密。 */
  context_snapshot?: Record<string, unknown> | null;
  /** 尝试开始时间。 */
  started_at: string;
  /** 尝试结束/隔离时间。 */
  finished_at: string | null;
}

/** 当前登录用户的可信服务端身份上下文。 */
export interface Identity {
  /** 当前用户 UUID。 */
  user_id: string;
  /** 当前租户 UUID；前端不得覆盖该边界。 */
  tenant_id: string;
  /** 规范化登录邮箱。 */
  email: string;
  /** 用户显示名称。 */
  display_name: string;
  /** 服务端签发并校验的角色列表。 */
  roles: string[];
  /** 当前所属部门 UUID。 */
  organization_unit_id: string | null;
  /** 当前职位名称。 */
  job_title: string;
  /** 当前公司名称。 */
  company_name: string;
  /** 当前公司 Logo 地址。 */
  company_logo_url: string | null;
}

/** 邮箱密码登录成功后的访问令牌。 */
export interface LoginResponse {
  /** 服务端签名的短期 JWT。 */
  access_token: string;
  /** Authorization 请求头使用的令牌类型。 */
  token_type: "bearer";
  /** 令牌剩余有效期，单位为秒。 */
  expires_in: number;
}

/** 当前租户的公司展示资料。 */
export interface Company {
  /** 公司（租户）UUID。 */
  id: string;
  /** 公司完整名称。 */
  name: string;
  /** 用于展示和识别的公司唯一简称。 */
  slug: string | null;
  /** 公司 Logo 地址。 */
  logo_url: string | null;
  /** 公司联系邮箱。 */
  contact_email: string | null;
  /** 公司生命周期状态。 */
  status: string;
  /** ISO 8601 格式的最近更新时间。 */
  updated_at: string;
}

/** 管理员更新公司资料的请求。 */
export interface CompanyUpdateInput {
  /** 公司完整名称。 */
  name: string;
  /** 租户内展示用简称，只允许小写字母、数字和连字符。 */
  slug: string;
  /** 公司 Logo 地址；未设置时为空。 */
  logo_url: string | null;
  /** 公司联系邮箱；未设置时为空。 */
  contact_email: string | null;
}

/** 树形部门节点。 */
export interface OrganizationUnit {
  /** 部门 UUID。 */
  id: string;
  /** 上级部门 UUID；顶级部门为空。 */
  parent_id: string | null;
  /** 部门名称。 */
  name: string;
  /** 直接归属当前部门的用户数量，不包含子部门用户。 */
  direct_user_count: number;
  /** 当前部门及全部子部门的用户总数量。 */
  user_count: number;
  /** 按名称稳定排序的下级部门。 */
  children: OrganizationUnit[];
}

/** 创建部门所需字段。 */
export interface OrganizationUnitCreateInput {
  /** 上级部门 UUID；创建顶级部门时为空。 */
  parent_id: string | null;
  /** 部门名称。 */
  name: string;
}

/** 编辑部门名称与上级部门的请求。 */
export type OrganizationUnitUpdateInput = OrganizationUnitCreateInput;

/** 管理员可查看但不包含密码哈希的用户资料。 */
export interface AdminUser {
  /** 系统用户 UUID，也是 JWT 的唯一主体。 */
  id: string;
  /** 规范化登录邮箱。 */
  email: string;
  /** 用户显示名称。 */
  display_name: string;
  /** 所属部门 UUID。 */
  organization_unit_id: string | null;
  /** 所属部门名称。 */
  organization_unit_name: string | null;
  /** 企业职位名称。 */
  job_title: string;
  /** 联系电话。 */
  phone: string;
  /** 服务端授权角色列表。 */
  roles: string[];
  /** 用户登录与 API 访问状态。 */
  status: "active" | "disabled";
  /** ISO 8601 格式的最近登录时间。 */
  last_login_at: string | null;
  /** ISO 8601 格式的创建时间。 */
  created_at: string;
  /** ISO 8601 格式的最近更新时间。 */
  updated_at: string;
}

/** 管理员创建本地登录用户的请求。 */
export interface AdminUserCreateInput {
  /** 唯一登录邮箱。 */
  email: string;
  /** 满足服务端策略的初始明文密码，只在当前请求中传输。 */
  password: string;
  /** 用户显示名称。 */
  display_name: string;
  /** 所属部门 UUID。 */
  organization_unit_id: string | null;
  /** 企业职位名称。 */
  job_title: string;
  /** 联系电话。 */
  phone: string;
  /** 初始角色列表。 */
  roles: string[];
}

/** 管理员编辑用户资料、角色和状态的请求。 */
export interface AdminUserUpdateInput {
  /** 用户显示名称。 */
  display_name: string;
  /** 所属部门 UUID。 */
  organization_unit_id: string | null;
  /** 企业职位名称。 */
  job_title: string;
  /** 联系电话。 */
  phone: string;
  /** 更新后的角色列表。 */
  roles: string[];
  /** 更新后的登录与 API 访问状态。 */
  status: "active" | "disabled";
}

/** 员工目录和聊天工作台可读取的安全 Agent 资料。 */
export interface Agent {
  /** Agent UUID。 */
  id: string;
  /** 租户内唯一稳定短名。 */
  slug: string;
  /** Agent 显示名称。 */
  name: string;
  /** Agent Logo 地址；未配置时为空。 */
  logo_url: string | null;
  /** Agent 能力描述。 */
  description: string;
  /** 新建对话欢迎语。 */
  welcome_message: string;
  /** 新建对话建议问题。 */
  suggested_prompts: string[];
  /** 新会话将固定使用的当前 AgentVersion UUID。 */
  active_version_id: string;
}

/** 管理控制台使用的 Agent 资料和生命周期状态。 */
export interface AdminAgent extends Omit<Agent, "active_version_id"> {
  /** Agent 生命周期状态。 */
  status: "draft" | "active" | "disabled";
  /** 历史迁移占位 Agent 为只读，禁止控制面写操作。 */
  read_only: boolean;
  /** 当前草稿 revision；用于乐观并发控制。 */
  draft_revision: number | null;
  /** 当前活动 AgentVersion UUID；未发布或停用状态可为空。 */
  active_version_id: string | null;
  /** 当前活动版本的规范化配置摘要；用于识别尚未发布的配置变更。 */
  active_version_config_digest: string | null;
  /** 当前活动版本发布时间；用于识别发布后的配置保存。 */
  active_version_published_at: string | null;
  /** ISO 8601 格式的创建时间。 */
  created_at: string;
  /** ISO 8601 格式的最近更新时间。 */
  updated_at: string;
}

/** 管理员可启停和调整元数据的服务端工具目录条目。 */
export interface ToolCatalogEntry {
  tool_id: string;
  name: string;
  description: string;
  implementation_key: string;
  required_roles: string[];
  risk_level: "low" | "medium" | "high";
  version: number;
  is_enabled: boolean;
  /** 独立紧急撤销标记，重新启用目录不会解除。 */
  is_revoked: boolean;
}

/** 可由多个 Agent 复用的稳定模型端点资源。 */
export interface ModelEndpoint {
  /** 模型端点 UUID。 */
  id: string;
  /** 管理端显示名称。 */
  name: string;
  /** 模型端点 Logo 地址。 */
  logo_url: string | null;
  /** 创建时选择的供应商预设；自定义连接为空。 */
  provider_preset: string | null;
  /** 当前连接使用的供应商类型。 */
  provider_kind: "openai_official" | "openai_compatible" | null;
  /** 当前连接的 API Base URL。 */
  base_url: string | null;
  /** 当前连接是否允许 Agent 新绑定和使用。 */
  is_enabled: boolean;
  /** 历史迁移占位端点为只读。 */
  read_only: boolean;
  /** 当前活动 ModelEndpointVersion UUID。 */
  active_version_id: string | null;
  /** API Key 的不可逆脱敏提示；未配置时为空。 */
  credential_masked_hint: string | null;
  /** 当前凭据 revision；前端永远无法读取密钥明文。 */
  credential_revision: number | null;
  /** 当前连接下可供 Agent 选择的完整模型集合。 */
  models: ModelEndpointModel[];
  /** 曾发布版本绑定当前模型的 Agent 数量。 */
  used_agent_count: number;
  /** 曾发布版本绑定当前模型的 Agent 名称集合。 */
  used_agent_names: string[];
  /** 阻止当前连接启用的逐模型原因。 */
  enable_blockers: string[];
  /** ISO 8601 格式的创建时间。 */
  created_at: string;
  /** ISO 8601 格式的最近更新时间。 */
  updated_at: string;
}

/** 发布后不可变的模型调用配置和能力验证结果。 */
export interface ModelEndpointVersion {
  /** 模型端点版本 UUID。 */
  id: string;
  /** 所属稳定模型端点 UUID。 */
  endpoint_id: string;
  /** 所属稳定模型 UUID；历史迁移版本可能为空。 */
  endpoint_model_id: string | null;
  /** 端点内单调递增的版本号。 */
  version_number: number;
  /** OpenAI 官方或 OpenAI-compatible 中转站。 */
  provider_kind: "openai_official" | "openai_compatible";
  /** 该版本使用的 OpenAI API 协议。 */
  api_protocol: "responses" | "chat_completions";
  /** 管理员可见的调用 Base URL；员工接口不返回。 */
  base_url: string;
  /** 供应商识别的远端模型名称。 */
  remote_model_name: string;
  /** 分项声明并验证的模型能力。 */
  capabilities: Record<string, boolean>;
  /** 模型默认值和参数上限。 */
  defaults: Record<string, unknown>;
  /** 最近一次能力验证总状态。 */
  verification_status: "untested" | "verified" | "partial" | "failed";
  /** 不含业务数据和秘密的分项验证结果。 */
  verification_result: Record<string, unknown>;
  /** ISO 8601 格式的最近验证时间。 */
  verified_at: string | null;
  /** ISO 8601 格式的不可变版本创建时间。 */
  created_at: string;
}

/** 一套供应商连接下可独立测试和选择的稳定模型。 */
export interface ModelEndpointModel {
  /** 稳定模型 UUID。 */
  id: string;
  /** 发送给供应商的真实模型 ID。 */
  upstream_model_id: string;
  /** 菜单显示名称；为空时使用真实模型 ID。 */
  display_name: string;
  /** 可选短后缀或 Emoji。 */
  badge: string;
  /** 当前不可变调用版本 UUID。 */
  current_version_id: string | null;
  /** 模型使用的 API 协议。 */
  api_protocol: "responses" | "chat_completions";
  /** 可选上下文窗口 Token 数。 */
  context_window_tokens: number | null;
  /** 随模型请求发送的 JSON 扩展对象，未配置时为空对象。 */
  extension_options: Record<string, unknown>;
  /** 与当前配置匹配的最新测试状态。 */
  test_status: "untested" | "running" | "passed" | "failed" | "stale" | "cancelled";
  /** 最近一次测试的安全摘要。 */
  latest_test: ModelTestRun | null;
  /** ISO 8601 格式创建时间。 */
  created_at: string;
  /** ISO 8601 格式更新时间。 */
  updated_at: string;
}

/** 单模型真实流式连通性测试的持久化进度。 */
export interface ModelTestRun {
  /** 持久化任务；旧历史为空，重试次数不由浏览器推断。 */ probe_job?: {
    /** 任务 UUID。 */ id: string;
    /** 实际任务状态。 */ status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
    /** 当前阶段。 */ stage: string;
    /** 已领取次数。 */ attempt_count: number;
    /** 当前 fencing generation。 */ generation: number;
    /** 下一次有限重试时间。 */ next_attempt_at: string;
    /** 已持久化的取消意图。 */ cancel_requested: boolean;
    /** 稳定失败原因。 */ error_code: string | null;
    /** 终态时间。 */ finished_at: string | null;
    /** 脱敏尝试摘要，不含请求或供应商原文。 */ attempt_history: Record<string, unknown>[];
  } | null;
  /** 测试运行 UUID。 */
  id: string;
  /** 所属连接 UUID。 */
  endpoint_id: string;
  /** 被测试稳定模型 UUID。 */
  endpoint_model_id: string;
  /** 测试固定的不可变模型版本 UUID。 */
  model_version_id: string;
  /** 测试使用的凭据 revision。 */
  credential_revision: number;
  /** 测试执行状态。 */
  status: "queued" | "running" | "passed" | "failed" | "cancelled";
  /** 当前真实测试阶段。 */
  stage: "queued" | "claimed" | "retry_queued" | "request_sent" | "response_headers" | "first_content" | "completed" | "failed" | "cancelled";
  /** 可安全展示的请求主机名。 */
  request_host: string;
  /** 可安全展示的请求路径。 */
  request_path: string;
  /** 供应商 HTTP 状态码。 */
  provider_status: number | null;
  /** 收到响应头耗时，单位毫秒。 */
  response_headers_ms: number | null;
  /** 收到首包内容耗时，单位毫秒。 */
  first_content_ms: number | null;
  /** 测试总耗时，单位毫秒。 */
  total_ms: number | null;
  /** 模型对固定测试问题返回的文本。 */
  response_content: string;
  /** 已到达阶段的安全摘要。 */
  milestones: Record<string, unknown>;
  /** 稳定错误码。 */
  error_code: string | null;
  /** 截断并脱敏后的错误说明。 */
  error_message: string | null;
  /** 请求关联标识。 */
  correlation_id: string;
  /** ISO 8601 格式开始时间。 */
  started_at: string | null;
  /** ISO 8601 格式完成时间。 */
  completed_at: string | null;
  /** ISO 8601 格式创建时间。 */
  created_at: string;
}

/** 服务端受控供应商预设。 */
export interface ModelProviderPreset {
  /** 预设稳定标识。 */
  id: string;
  /** 预设显示名称。 */
  name: string;
  /** 预设 API Base URL。 */
  base_url: string;
}

/** Agent 当前可编辑配置及乐观锁 revision。 */
export interface AgentDraft {
  /** 草稿所属 Agent UUID。 */
  agent_id: string;
  /** 保存时必须携带的当前 revision。 */
  revision: number;
  /** 配置 JSON Schema 版本。 */
  schema_version: string;
  /** 尚未发布的声明式 Agent 配置。 */
  config: AgentConfig;
  /** 当前配置的规范化摘要，用于与正在运行的版本进行一致性比对。 */
  config_digest: string;
  /** ISO 8601 格式的最近保存时间。 */
  updated_at: string;
}

/** Agent Schema v2 的声明式运行配置。 */
export interface AgentConfig {
  /** 固定配置 Schema 版本。 */
  schema_version: "2";
  /** 每次模型调用前注入的稳定系统指令。 */
  prompt: { system_prompt: string };
  /** 模型端点绑定和允许管理员调整的生成参数。 */
  model: {
    /** 发布时解析为具体 ModelEndpointVersion 的稳定端点 UUID。 */
    model_endpoint_id: string;
    /** 发布时解析为端点下具体 ModelEndpointVersion 的稳定模型 UUID。 */
    model_endpoint_model_id?: string | null;
    /** 首版不开放回退端点，保留字段必须为空数组。 */
    fallback_model_endpoint_ids?: string[];
    /** 单次模型生成参数。 */
    generation?: {
      /** 采样温度，允许范围由服务端再次校验。 */
      temperature?: number;
      /** 单次回答最大输出 Token。 */
      max_output_tokens?: number;
      /** 推理模型的推理强度；为空时继承模型默认值。 */
      reasoning_effort?: "none" | "low" | "medium" | "high" | "xhigh" | "max" | null;
      /** Responses 模型的回答详细程度；为空时继承模型默认值。 */
      verbosity?: "low" | "medium" | "high" | null;
      /** 单次模型请求超时秒数。 */
      timeout_seconds?: number;
      /** 模型请求最大重试次数。 */
      max_retries?: number;
    };
  };
  /** 从服务端 Tool Registry 选择的工具绑定。 */
  tools: Array<{
    /** 服务端注册的稳定工具标识。 */
    tool_id: string;
    /** 是否在该 Agent 版本中启用。 */
    enabled: boolean;
    /** 单次 Run 内该工具最多调用次数。 */
    max_calls_per_run?: number;
    /** 工具执行前是否要求人工审批。 */
    approval_policy?: "none" | "required";
  }>;
  /** 平台可强制收紧的 Agent Run 运行边界。 */
  runtime: {
    /** 平台维护的 LangChain create_agent 模板标识。 */
    engine?: "langchain_create_agent_v1";
    /** 可信运行上下文 Schema。 */
    context_schema?: "support_context_v1";
    /** 结构化回答 Schema。 */
    response_schema?: "support_answer_v1";
    /** 结构化输出由工具调用或供应商原生能力完成。 */
    response_strategy: "tool" | "provider";
    /** 生产运行固定使用 PostgreSQL checkpointer。 */
    checkpointer?: "postgres";
    /** 单次 Run 允许的模型调用上限。 */
    model_call_limit?: number;
    /** 单次 Run 允许的工具调用上限。 */
    tool_call_limit?: number;
    /** 单次 Run 总超时秒数。 */
    run_timeout_seconds?: number;
    /** 同时执行工具的最大并行数。 */
    max_parallel_tools?: number;
    /** 可选输入 Token 硬上限。 */
    max_input_tokens?: number | null;
    /** 可选单次 Run 成本预算上限，单位为美元。 */
    max_cost_usd?: number | null;
  };
  /** 预留知识库绑定；首版必须为空。 */
  knowledge?: { knowledge_base_ids: string[] };
  /** 预留长期记忆策略；首版必须为空。 */
  memory?: { long_term_policy_id: null };
}

/** 发布后不可变且可激活或回滚的 Agent 配置版本。 */
export interface AgentVersion {
  /** AgentVersion UUID。 */
  id: string;
  /** 所属稳定 Agent UUID。 */
  agent_id: string;
  /** Agent 内单调递增的版本号。 */
  version_number: number;
  /** 配置快照 Schema 版本。 */
  schema_version: string;
  /** 规范化配置 SHA-256 摘要。 */
  config_digest: string;
  /** 发布时固定的 ModelEndpointVersion UUID。 */
  model_endpoint_version_id: string;
  /** 发布时解析出的受控工具标识。 */
  resolved_tool_ids: string[];
  /** 发布固定的实现版本；目录元数据版本另行变化。 */
  resolved_tool_versions?: Record<string, number>;
  /** 管理员填写的版本说明。 */
  release_notes: string;
  /** ISO 8601 格式的发布时间。 */
  published_at: string;
}

/** Agent 对用户或角色主体的一条显式使用授权。 */
export interface AgentGrant {
  /** 授权记录 UUID。 */
  id: string;
  /** 授权给具体用户或可信角色。 */
  subject_type: "user" | "role";
  /** 用户 UUID 或规范角色标识。 */
  subject_id: string;
  /** ISO 8601 格式的授权创建时间。 */
  created_at: string;
}

/** Agent 或模型控制面的追加写审计摘要。 */
export interface AgentAuditEvent {
  /** 审计事件递增 ID。 */
  id: number;
  /** 规范化控制面动作。 */
  action: string;
  /** 执行操作的用户 UUID。 */
  actor_user_id: string;
  /** 相关不可变版本 UUID；不涉及版本时为空。 */
  version_id: string | null;
  /** 已排除秘密和完整 Prompt 的审计元数据。 */
  metadata_payload: Record<string, unknown>;
  /** 串联 API 请求与服务端日志的关联标识。 */
  correlation_id: string;
  /** ISO 8601 格式的事件时间。 */
  created_at: string;
}

export interface DashboardMetric {
  value: number | null;
  previous_value?: number | null;
  change_percent?: number | null;
  available: boolean;
}

export interface DashboardSummary {
  window_start: string;
  window_end: string;
  metrics: Record<string, DashboardMetric>;
  status_counts: Record<string, number>;
  service: { status?: string; instances?: number; available_instances?: number };
}

export interface DashboardTimeseriesPoint {
  bucket_start: string;
  values: Record<string, number | null>;
}

export interface DashboardRun {
  id: string;
  agent_id: string;
  agent_name: string;
  /** 使用者显示名称；无法关联时为空。 */
  user_name: string | null;
  /** 使用者所属末级部门 UUID，用于拼接多级部门路径。 */
  organization_unit_id: string | null;
  /** 使用者直属部门名称；前端优先使用部门树拼接完整路径。 */
  department_name: string | null;
  conversation_id: string;
  status: RunStatus;
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_cost_microusd: number | null;
  end_to_end_latency_ms: number | null;
  correlation_id: string;
}

export interface DashboardError {
  error_code: string;
  count: number;
  affected_users: number;
  agent_count: number;
  last_seen_at: string | null;
}

export interface DashboardErrorEvent {
  id: string;
  occurred_at: string;
  severity: string;
  error_code: string;
  reason: string;
  stage: string;
  resolution_status: string;
  agent_id: string;
  agent_name: string | null;
  user_id: string;
  user_name: string | null;
  conversation_id: string;
  run_id: string;
  correlation_id: string | null;
  agent_version_number: number | null;
  model_name: string | null;
  retry_count: number | null;
  latency_ms: number | null;
}

export interface DashboardErrorEventDetail extends DashboardErrorEvent {
  metadata: Record<string, unknown>;
  observations: DashboardTrace["observations"];
}

export interface DashboardTrace {
  run: DashboardRun;
  /** 整个 Run 的物理模型调用合计；缺失 usage 不填零。 */
  budget?: {
    /** 包括依赖重试的请求数。 */ model_call_count: number;
    /** 明确记账的依赖重试次数。 */ retry_count: number;
    /** 未取得完整 usage 的请求数。 */ unknown_usage_call_count: number;
    /** 全部请求都有 usage 时的输入合计。 */ input_tokens: number | null;
    /** 全部请求都有 usage 时的输出合计。 */ output_tokens: number | null;
    /** 输入中的缓存 Token。 */ cached_input_tokens: number | null;
    /** 输出中的推理 Token。 */ reasoning_tokens: number | null;
    /** 完整已知费用合计，微 USD。 */ total_cost_microusd: number | null;
    /** 费用是否具有完整数据。 */ cost_source: "calculated" | "unknown";
  } | null;
  observations: Array<{
    id: string;
    kind: string;
    name: string;
    status: string;
    started_at: string;
    finished_at: string | null;
    duration_ms: number | null;
    input_tokens: number | null;
    output_tokens: number | null;
    total_cost_microusd: number | null;
    error_code: string | null;
    metadata: Record<string, unknown>;
    /** 执行尝试；旧观测可为空。 */ attempt_id?: string | null;
    /** 安全的中文处置说明。 */ error_message?: string | null;
  }>;
}

export interface SystemAgentStatus {
  id: string;
  name: string;
  lifecycle_status: string;
  execution_status: string;
  health_status: string;
  health_reason: string;
  last_run_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  active_run_count: number;
  error_rate: number | null;
  p95_latency_ms: number | null;
  pending_publish: boolean;
  observed_at: string | null;
}

export type KnowledgeDocumentStatus = "draft" | "published" | "expired" | "archived";
export type KnowledgeVersionStatus = "draft" | "in_review" | "published" | "superseded" | "retired";

export interface KnowledgeDocument {
  id: string;
  source_id: string;
  /** 来源显示名，由服务端按当前页引用批量解析。 */
  source_name?: string | null;
  /** 来源类型；来源不可见时为空。 */
  source_type?: string | null;
  /** 来源环境，不依赖前端加载完整来源目录。 */
  source_environment?: string | null;
  external_key: string;
  owner_user_id: string;
  owner_user_name?: string | null;
  created_by?: string;
  created_by_name?: string | null;
  status: KnowledgeDocumentStatus;
  current_version_id: string | null;
  review_due_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeSource {
  id: string;
  external_key: string;
  name: string;
  source_type: string;
  environment: string;
  owner_user_id: string;
  owner_user_name?: string | null;
  created_by?: string;
  created_by_name?: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeVersion {
  id: string;
  document_id: string;
  version_number: number;
  status: KnowledgeVersionStatus;
  title: string;
  content_markdown: string;
  content_digest: string;
  section_anchors: Array<{ id: string; title: string; level: string }>;
  change_summary: string;
  effective_from: string;
  effective_until: string | null;
  created_by: string;
  created_by_name?: string | null;
  reviewed_by: string | null;
  reviewed_by_name?: string | null;
  /** 实际提交人，与审核人分离。 */ submitted_by?: string | null;
  /** 当前可读提交人名称。 */ submitted_by_name?: string | null;
  /** 最近提交审核时间。 */ submitted_at?: string | null;
  /** 实际审核决策；旧正式记录为空，不补造批准。 */ review_decision?: "pending" | "approved" | "rejected" | null;
  /** 审核状态乐观 revision。 */ review_revision?: number;
  /** 内容/标题/有效期/ACL 的批准摘要。 */ review_scope_digest?: string | null;
  /** 管理员审核说明。 */ review_comment?: string | null;
  published_by: string | null;
  published_by_name?: string | null;
  created_at: string;
  reviewed_at: string | null;
  published_at: string | null;
}
