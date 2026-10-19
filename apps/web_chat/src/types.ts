/** Agent Run 的完整生命周期状态。 */
export type RunStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "waiting_approval"
  | "waiting_action"
  | "completed"
  | "failed"
  | "cancelled";

export interface AuthSession {
  /** 登录会话标识，单独持有该 ID 不能访问 API。 */ id: string;
  /** 密码登录签发时间。 */ created_at: string;
  /** 固定服务器过期时间，不刷新延长。 */ expires_at: string;
  /** 永久撤销时间。 */ revoked_at: string | null;
  /** 固定撤销原因码，不含敏感正文。 */ revocation_code: string | null;
  /** 是否为当前请求使用的会话。 */ is_current: boolean;
  /** 当前代次、期限和撤销事实下是否有效。 */ is_active: boolean;
}

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
  /** 独立权限，不覆盖知识 ACL。 */ permission: "approve" | "ticket_handle" | "support_context_read";
  /** 当前启停状态。 */ is_enabled: boolean;
  /** 并发更新版本。 */ revision: number;
  /** 变更时间。 */ updated_at: string;
  /** 隔离数据。 */ data_environment: "mock";
}

/** 升级来源摘要；正文另行读取并重新判权。 */
export interface EscalationPackageSummary {
  /** 包 ID。 */ id: string;
  /** 稳定动作。 */ action_id: string;
  /** 排查 Run。 */ run_id: string;
  /** 实际尝试。 */ attempt_id: string | null;
  /** 保存时间。 */ created_at: string;
  /** 明确隔离来源。 */ data_environment: "mock";
}

/** 当前权限下的有界升级包。 */
export interface EscalationPackageDetail extends EscalationPackageSummary {
  /** 脱敏问题与独立来源事实。 */ payload: {
    /** 契约版本。 */ schema_version: "1";
    /** 隔离来源。 */ data_environment: "mock";
    /** 有界当前问题，不是完整历史或 Prompt。 */ question: string;
    /** 排查 Run。 */ run_id: string;
    /** 原尝试。 */ attempt_id: string | null;
    /** 服务端 Trace。 */ trace_id: string;
    /** 实际观察事实，不能当成根因确认。 */ observed_facts: DiagnosticReport["facts"];
    /** 来源/规则一致的模拟原因。 */ confirmed_causes: DiagnosticReport["candidates"];
    /** 未确认原因。 */ unconfirmed_candidates: DiagnosticReport["candidates"];
    /** 查询时间与调用来源。 */ sources: DiagnosticReport["sources"];
    /** 已进行/失败/跳过的步骤。 */ steps: DiagnosticReport["stages"];
    /** 引用 ID，读取复检当前 ACL。 */ evidence_ids: string[];
    /** 待处理事项。 */ unresolved_items: string[];
    /** 升级原因。 */ reason_codes: string[];
    /** 包形成时间。 */ captured_at: string;
  };
}

/** 隔离工单当前目标事实与当前人员操作权限。 */
export interface MockTicket {
  /** 工单 ID。 */ id: string;
  /** MOCK 前缀业务编号。 */ external_id: string;
  /** 冻结标题。 */ title: string;
  /** 受限摘要。 */ summary: string;
  /** 实际目标状态。 */ status: "open" | "in_progress" | "escalated" | "closed";
  /** 并发操作版本。 */ version: number;
  /** 明确认领人员。 */ assigned_to: string | null;
  /** 当前资源主体显示名。 */ owner_name: string | null;
  /** 当前处理人员显示名。 */ handler_name: string | null;
  /** 关联问题正在人工接管。 */ takeover_active: boolean;
  /** 当前显式权限允许认领。 */ can_claim: boolean;
  /** 当前显式认领人可回复/备注/更新。 */ can_update: boolean;
  /** 来源动作；不自动授予其诊断正文权限。 */ source_action_id: string | null;
  /** 当前主体具备项目上下文读取范围；详情仍需复检来源及知识 ACL。 */ can_read_context: boolean;
  /** 实际创建时间。 */ created_at: string;
  /** 实际状态更新。 */ updated_at: string;
  /** 隔离来源。 */ data_environment: "mock";
}

/** 人工操作种类。 */
export type MockTicketCommandKind = "claim" | "note" | "reply" | "close" | "escalate" | "release";

/** 人工命令同事务写入的目标状态事实。 */
export interface MockTicketCommand {
  /** 命令 ID。 */ id: string;
  /** 实际操作。 */ kind: MockTicketCommandKind;
  /** 脱敏正文，私有备注只有获准客服可读。 */ body: string;
  /** 实际操作人员显示名。 */ actor_name: string | null;
  /** 提交后版本，可能早于当前工单版本。 */ ticket_version: number;
  /** 提交后状态，当前状态需另外读取。 */ ticket_status: MockTicket["status"];
  /** 实际提交时间。 */ created_at: string;
  /** 隔离来源。 */ data_environment: "mock";
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
  /** 显式替代的未执行旧提案；历史参数不会覆盖。 */ supersedes_action_id?: string | null;
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
  /** 当前接管聚合，业务授权始终由服务端决定。 */ support_takeover?: {
    /** 关联问题被人工接管，Agent 写动作冻结。 */ active: boolean;
    /** 实际接管问题数。 */ active_case_count: number;
    /** 服务器观察时间。 */ observed_at: string;
  } | null;
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
  /** 业务生命周期状态，包含审批和动作等待。 */
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

/** 工具来源的精确批准策略；仅目录治理，不提供地址、凭据或执行许可。 */
export interface ToolDiscoveryPolicy {
  /** 当前来源策略契约。 */ schema_version?: "tool_source_policy_v1";
  /** 批准连接配置/来源身份的SHA-256指纹。 */ source_fingerprint: string;
  /** 管理员明确启用目录来源，默认关闭。 */ enabled: boolean;
  /** 带时区的批准截止时间。 */ valid_until: string;
  /** 允许发现的远端工具与完整描述摘要，最多50项。 */
  allowed_tools: { name: string; descriptor_digest: string }[];
}

/** 固定目录工具定位；模型不能凭此绕过网关或资源权限。 */
export interface ToolDiscoveryBinding {
  /** 按租户/来源/远端名称消歧的稳定别名。 */ tool_id: string;
  /** 来源中的原始工具名。 */ remote_name: string;
  /** 完整工具描述摘要。 */ descriptor_digest: string;
  /** 输入Schema摘要。 */ input_schema_digest: string;
  /** 输出Schema摘要。 */ output_schema_digest: string;
}

/** 单来源当前修订及最多50项完整绑定集合，不是分页列表。 */
export interface ToolDiscoveryView {
  /** 当前读取契约。 */ schema_version: "tool_discovery_view_v1";
  /** 不可变修订记录。 */ record_id: string;
  /** 当前租户内来源别名。 */ source_key: string;
  /** 单调修订及失效围栏。 */ revision: number;
  /** 未完成验证、可用、停用、拒绝或自然到期。 */ status: "pending" | "ready" | "disabled" | "rejected" | "expired";
  /** 绑定租户/来源的批准策略摘要。 */ policy_digest: string;
  /** 当前完整批准策略，编辑时不从可用工具反推allowlist。 */ policy: ToolDiscoveryPolicy;
  /** 当前批准来源指纹。 */ source_fingerprint: string;
  /** 批准截止时间。 */ valid_until: string;
  /** 固定目录摘要，到期或无目录为空。 */ catalog_digest: string | null;
  /** 仅当前可用批准版本的绑定。 */ tools: ToolDiscoveryBinding[];
  /** 固定拒绝码，不含远端诊断。 */ failure_code: string | null;
  /** 本次观察数量，不透出未批准工具描述。 */ observed_count: number;
  /** 原服务器追加时间。 */ created_at: string;
  /** 目录治理不会启用远端执行。 */ execution_enabled: false;
  /** 真实协议连接尚未接入，不能由目录ready推断健康。 */ connection_status: "not_connected";
}

/** 当前获准固定版本的工具描述。 */
export interface DiscoveredToolDescriptor {
  /** 远端原始名称。 */ name: string;
  /** 已批准的有界不可信说明。 */ description: string;
  /** 封闭对象输入定义。 */ input_schema: Record<string, unknown>;
  /** 封闭对象输出定义。 */ output_schema: Record<string, unknown>;
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
  /** 当前 revision 的可用性，历史探测通过不能覆盖撤销。 */ credential_status: "available" | "revoked" | "unavailable";
  /** 历史密封实际 provider，不含密钥材料。 */ credential_provider: string | null;
  /** 当前 revision 永久撤销时间。 */ credential_revoked_at: string | null;
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
export interface ModelCredentialRevision {
  /** 历史凭据标识。 */ id: string;
  /** 固定修订号，轮换不会改写旧行。 */ revision: number;
  /** 实际秘密 provider。 */ provider: string;
  /** 此 revision 的脱敏提示；旧未知值不补造。 */ masked_hint: string;
  /** 密封保存时间。 */ created_at: string;
  /** 永久撤销时间。 */ revoked_at: string | null;
  /** 实际撤销管理员。 */ revoked_by: string | null;
  /** 控制面专属的脱敏撤销说明。 */ revocation_reason: string | null;
}

export interface ModelCredentialAudit {
  /** 追加审计标识。 */ id: number;
  /** 解封、轮换、撤销或访问拒绝类型。 */ action: string;
  /** 可信主体，Worker 代表原 Run 用户调用。 */ actor_user_id: string;
  /** 审计保存时间。 */ created_at: string;
  /** 请求关联 ID。 */ correlation_id: string;
  /** 白名单元数据，不含明文或密文秘密。 */ metadata: Record<string, string | number | null>;
}

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

/** 发布时服务器生成的版本与策略清单。 */
export interface AgentReleaseManifest {
  /** 发布清单契约版本。 */ schema_version: "agent_release_manifest_v1";
  /** 稳定 Agent 标识。 */ agent_id: string;
  /** 关联不可变 Agent 版本。 */ agent_version_id: string;
  /** 规范化配置摘要。 */ config_digest: string;
  /** 平台数据规则及系统 Prompt 摘要，不返回正文。 */ prompt_digest: string;
  /** 固定单 Agent 引擎。 */ engine: "langchain_create_agent_v1";
  /** 执行图/运行策略与源码组合摘要。 */ graph_policy_digest: string;
  /** 可信上下文 Schema 摘要。 */ context_schema_digest: string;
  /** 最终结构化回答 Schema 摘要。 */ response_schema_digest: string;
  /** 明确包内组件的发布时源码摘要。 */ source_digests: Record<string, string>;
  /** 相关包版本，缺少安装元数据时为 null。 */ dependencies: Record<string, string | null>;
  /** 固定模型及本轮凭据重新绑定说明。 */ model: {
    /** 稳定端点。 */ endpoint_id: string;
    /** 固定模型版本。 */ model_version_id: string;
    /** 稳定模型记录，旧单模型端点可为空。 */ model_record_id: string | null;
    /** 固定协议。 */ protocol: string;
    /** 能力/默认值/价格/生成参数摘要。 */ settings_digest: string;
    /** 凭据引用摘要，不返回秘密或密文。 */ credential_reference_digest: string;
    /** 发布时可用 revision，仅作历史记录。 */ credential_revision_at_publish: number;
    /** 每轮创建 Run 时固定实际凭据 revision。 */ credential_binding: "repinned_at_run_creation";
    /** 固定模型，无动态路由。 */ routing: "fixed_model_v1";
  };
  /** 发布时工具/实现/Schema/目录/绑定策略引用。 */ tools: Array<{
    /** 工具标识。 */ tool_id: string;
    /** 固定实现版本。 */ implementation_version: number;
    /** 工具调用 Schema 摘要。 */ schema_digest: string;
    /** 静态/实际工具描述摘要。 */ description_digest: string;
    /** 静态最低权限/风险摘要。 */ static_policy_digest: string;
    /** 当时的目录版本，未登记时为空。 */ catalog_version: number | null;
    /** 当时目录策略摘要。 */ catalog_digest: string | null;
    /** Agent 工具绑定摘要。 */ binding_digest: string;
    /** 发布当时的最低风险。 */ minimum_risk: string;
    /** 实际本地/Mock/关键词适配契约。 */ connector: string;
  }>;
  /** 检索策略；当前知识语料不是冻结版本。 */ knowledge: {
    /** 关键词策略。 */ strategy: "keyword_v1";
    /** 是否绑定知识检索。 */ enabled: boolean;
    /** Agent 知识配置摘要。 */ binding_digest: string;
    /** 检索时读取当前获准发布快照。 */ scope: "current_authorized_snapshots_at_retrieval";
    /** 运行证据的版本定位。 */ evidence_binding: "run_attempt_snapshot_chunk_digest";
    /** 向量保持关闭。 */ vector_enabled: false;
    /** 不虚构固定整个语料版本。 */ corpus_version: null;
  };
  /** 权限是当前数据库事实，发布快照不授予执行许可。 */ authorization: {
    /** 运行实时权限边界。 */ boundary: "current_database_at_each_boundary";
    /** 发布时 Agent grant 摘要。 */ grant_digest_at_publish: string;
    /** 发布时 grant 数量。 */ grant_count_at_publish: number;
    /** 绑定与审批服务策略摘要。 */ approval_policy_digest: string;
    /** 非秘密治理配置摘要。 */ governance_settings_digest: string;
    /** 清单绝不充当执行许可。 */ snapshot_is_execution_permit: false;
  };
  /** 评测尚未接通，不把模型连通性当成业务验收。 */ evaluation: {
    /** 明确未评测。 */ status: "not_assessed";
    /** 尚无批准的数据集版本。 */ dataset_version: null;
    /** 尚无评测结果引用。 */ result_id: null;
    /** 尚未通过评测发布门禁。 */ gate_passed: false;
  };
}

/** 发布后不可变且可激活或回滚的 Agent 配置版本。 */
export interface AgentReleaseComparison {
  /** 差异契约版本。 */ schema_version: "agent_release_comparison_v1";
  /** 两个版本所属 Agent。 */ agent_id: string;
  /** 当前查看版本。 */ version_id: string;
  /** 显式或读取时活动的基线版本。 */ baseline_version_id: string | null;
  /** 比较响应生成时间，不代表性能或质量验收。 */ observed_at: string;
  /** 有基线或当前没有活动基线。 */ status: "available" | "no_baseline";
  /** 查看版本清单状态。 */ version_manifest_status: "recorded" | "legacy_unrecorded" | "integrity_failed";
  /** 基线清单状态，无基线时为空。 */ baseline_manifest_status: "recorded" | "legacy_unrecorded" | "integrity_failed" | null;
  /** 有限两个版本的元数据差异，不返回 Prompt/秘密/知识正文。 */ differences: Array<{
    /** 组件固定路径。 */ component: string;
    /** 已知相同/变化/增加/删除或缺记录。 */ status: "equal" | "changed" | "added" | "removed" | "unknown";
    /** 基线的 ID/摘要/版本标识，缺记录为空。 */ before: string | null;
    /** 查看版本的 ID/摘要/版本标识，缺记录为空。 */ after: string | null;
  }>;
  /** 已知变化/增加/删除的组件数量。 */ changed_count: number;
  /** 无法由历史记录比较的组件数量。 */ unknown_count: number;
  /** 只读差异绝不是发布批准。 */ comparison_is_release_approval: false;
}

/** 发布后不可变且可激活或回滚的 Agent 配置版本。 */
export interface ReleaseGateCheck {
  /** 固定门禁类别。 */ category: "references" | "evidence" | "functionality" | "security" | "quality" | "cost" | "latency";
  /** 稳定检查代码，用于定位拒绝原因。 */ code: string;
  /** 通过、失败、缺失实测或失去适用性。 */ status: "passed" | "failed" | "missing" | "stale";
  /** 服务器生成的固定说明，无正文或秘密。 */ message: string;
  /** 实测值；缺失不得显示为零。 */ observed: number | null;
  /** 固定或明确批准的阈值。 */ threshold: number | null;
  /** 实际适用样本数。 */ sample_count: number | null;
}

/** 灰度策略需要服务器配置批准；数值不是评测结果。 */
export interface ReleaseRolloutPolicy {
  /** 版本化策略。 */ schema_version: "release_rollout_policy_v1";
  /** 每轮观察时长，300至86400秒。 */ observation_seconds: number;
  /** 每版本至少3至5000个终态样本。 */ minimum_runs_per_version: number;
  /** 失败率上限，0至0.1。 */ max_failure_rate: number;
  /** 每次运行平均费用上限，美元。 */ max_average_cost_usd: number;
  /** API可见首正文P95上限，毫秒。 */ max_p95_api_first_text_ms: number;
  /** 完整响应P95上限，毫秒。 */ max_p95_complete_ms: number;
  /** 候选平均费用相对基线的最高倍数。 */ max_cost_increase_ratio: number;
  /** 候选完整响应耗时相对基线的最高倍数。 */ max_latency_increase_ratio: number;
  /** 服务器观测来源，不等于客户端实测。 */ first_text_source: "api_database_visibility_v1";
  /** 单组元数据读取上限。 */ max_metadata_rows_per_version: 5000;
  /** 灰度最高90%，保留基线组。 */ max_candidate_bps: 9000;
  /** 每次扩量最多增加10个百分点。 */ max_step_bps: 1000;
  /** 不自动扩量。 */ automatic_expansion: false;
  /** 授权拒绝必须排查。 */ authorization_denial_tolerance: 0;
}

export interface ReleaseRolloutCreate {
  /** 比当前活动版本更新的候选。 */ candidate_version_id: string;
  /** 初始万分比，1至1000。 */ initial_candidate_bps: number;
  /** 最多200个有效租户用户UUID；空为当前获准主体。 */ subject_ids: string[];
  /** 不可变观察策略。 */ policy: ReleaseRolloutPolicy;
}

export interface ReleaseRolloutCommand {
  /** 当前修订，防止陈旧操作。 */ expected_revision: number;
  /** 显式人工控制操作。 */ operation: "start" | "pause" | "resume" | "expand" | "stop";
  /** 只有expand可以提供新比例，单位万分之一。 */ candidate_bps?: number;
}

export interface ReleaseRolloutSummary {
  /** 稳定灰度ID。 */ id: string;
  /** 所属Agent。 */ agent_id: string;
  /** 不可变基线版本。 */ baseline_version_id: string;
  /** 不可变候选版本。 */ candidate_version_id: string;
  /** 草稿、运行、暂停或停止。 */ status: "draft" | "active" | "paused" | "stopped";
  /** 候选万分比。 */ candidate_bps: number;
  /** 控制面修订。 */ revision: number;
  /** 当前观察轮次。 */ epoch: number;
  /** 固定策略摘要。 */ policy_digest: string;
  /** 固定范围定义摘要。 */ definition_digest: string;
  /** 当前轮次开始，未启动为空。 */ epoch_started_at: string | null;
  /** 最近系统复检，未复检为空。 */ last_checked_at: string | null;
  /** 固定暂停或停止原因。 */ reason_code: string | null;
  /** 创建时间。 */ created_at: string;
  /** 最近控制时间。 */ updated_at: string;
}

export interface RolloutCohortMetrics {
  /** 固定观察组。 */ selection: "baseline" | "candidate";
  /** 固定版本。 */ version_id: string;
  /** 有界读取的Run数量，超限另行标记。 */ run_count: number;
  /** 已完成数量，不推断业务成功。 */ completed: number;
  /** 明确失败数量。 */ failed: number;
  /** 执行及业务等待数量。 */ pending: number;
  /** 已取消数量。 */ cancelled: number;
  /** 已知终态失败率；缺失为空。 */ failure_rate: number | null;
  /** 完整计量的平均费用；缺失为空。 */ average_cost_usd: number | null;
  /** API首正文上界P95；缺失为空。 */ p95_api_first_text_ms: number | null;
  /** 完整响应P95；缺失为空。 */ p95_complete_ms: number | null;
  /** 缺费用记录数。 */ missing_cost_count: number;
  /** 缺API首正文记录数。 */ missing_first_text_count: number;
  /** 缺完整耗时记录数。 */ missing_complete_count: number;
  /** 已完成但缺绑定产品结果数。 */ missing_result_count: number;
  /** 工具授权拒绝次数。 */ authorization_denial_count: number;
  /** 元数据超限时拒绝扩量。 */ truncated: boolean;
}

export interface RolloutObservationCheck {
  /** 稳定检查代码。 */ code: string;
  /** 通过、失败、缺失或等待。 */ status: "passed" | "failed" | "missing" | "waiting";
  /** 服务器固定说明。 */ message: string;
}

export interface ReleaseRolloutDetail {
  /** 当前灰度控制状态。 */ rollout: ReleaseRolloutSummary;
  /** 固定主体范围；不授予权限。 */ subject_ids: string[];
  /** 固定策略。 */ policy: ReleaseRolloutPolicy;
  /** 摘要是否匹配服务器批准配置。 */ policy_approved: boolean;
  /** Worker复检是否仍有效。 */ supervisor_fresh: boolean;
  /** 基线当前完整门禁。 */ baseline_readiness: ReleaseReadiness;
  /** 候选当前完整门禁。 */ candidate_readiness: ReleaseReadiness;
  /** 当前轮次的只读观测，不授予发布许可。 */ observation: {
    /** 观测契约。 */ schema_version: "release_rollout_observation_v1";
    /** 服务器观测时间。 */ observed_at: string;
    /** 观测轮次。 */ epoch: number;
    /** 轮次起点。 */ started_at: string | null;
    /** 最早可考虑扩大时间；仍须检查所有门槛。 */ earliest_expansion_at: string | null;
    /** 基线元数据。 */ baseline: RolloutCohortMetrics;
    /** 候选元数据。 */ candidate: RolloutCohortMetrics;
    /** 全部运行观测检查。 */ checks: RolloutObservationCheck[];
    /** 观测是否允许考虑扩大，提交仍复检。 */ can_expand: boolean;
    /** 系统巡检是否应暂停。 */ pause_recommended: boolean;
    /** 不从完成状态推断业务成功。 */ business_success_inferred: false;
    /** API观测不证明客户端SLO。 */ client_latency_proven: false;
  };
  /** 当前是否可显式启动。 */ start_allowed: boolean;
  /** 当前是否可显式恢复。 */ resume_allowed: boolean;
  /** 当前是否可显式扩量。 */ expand_allowed: boolean;
  /** 持续展示的禁用原因。 */ disabled_reasons: string[];
}

export interface ReleaseRolloutEvent {
  /** 追加事件ID。 */ id: string;
  /** 变更后修订。 */ revision: number;
  /** 人工或系统，不伪造人工授权。 */ actor_kind: "user" | "system";
  /** 实际操作者；系统为空。 */ actor_user_id: string | null;
  /** 固定事件名称。 */ event_type: string;
  /** 变更后状态。 */ status: ReleaseRolloutSummary["status"];
  /** 变更后万分比。 */ candidate_bps: number;
  /** 变更后轮次。 */ epoch: number;
  /** 固定控制原因。 */ reason_code: string | null;
  /** 固定策略摘要。 */ policy_digest: string;
  /** 服务器追加时间。 */ created_at: string;
}

/** 当前环境只读门禁投影，实际激活仍在事务中重新检查。 */
export interface ReleaseReadiness {
  /** 门禁投影契约版本。 */ schema_version: "release_readiness_v1";
  /** 固定版本所属 Agent。 */ agent_id: string;
  /** 被检查的固定版本。 */ version_id: string;
  /** 本次服务器检查时间。 */ observed_at: string;
  /** 只适用于此运行环境。 */ environment: "local" | "dev" | "test" | "staging" | "production";
  /** 产品 P0/P1 门禁范围。 */ stage: "p0" | "p1";
  /** 开发激活或正式强制门禁，无关闭开关。 */ enforcement: "development_only" | "required";
  /** 拒绝、仅开发使用或全部门禁通过。 */ status: "blocked" | "development_only" | "passed";
  /** 当前检查是否允许显式激活，提交时重新检查。 */ activation_allowed: boolean;
  /** 当前正式环境的全部门禁通过；不等于已部署或生产验收。 */ formal_release_approved: boolean;
  /** 固定发布清单摘要；历史缺失为空。 */ manifest_digest: string | null;
  /** 当前模型/工具/源码/知识/权限/凭据范围摘要。 */ current_scope_digest: string | null;
  /** 批准基线和当前环境的策略摘要。 */ policy_digest: string;
  /** 采用的最新评测事实，不回退选取旧通过结果。 */ evaluation_id: string | null;
  /** 最新评测实际完成时间。 */ evaluation_completed_at: string | null;
  /** 当前策略推导的评测到期时间。 */ evaluation_expires_at: string | null;
  /** 所有适用检查的有限集合。 */ checks: ReleaseGateCheck[];
  /** 只读投影不是执行许可。 */ readiness_is_execution_permit: false;
}

/** 追加式评测历史的脱敏元数据，不返回完整报告或业务正文。 */
export interface ReleaseEvaluationSummary {
  /** 评测事实 ID。 */ id: string;
  /** 被评测固定版本。 */ version_id: string;
  /** 可信执行器物理执行 ID。 */ execution_id: string;
  /** 固定发布清单摘要。 */ manifest_digest: string;
  /** 执行时当前范围摘要。 */ scope_digest: string;
  /** 批准评测策略摘要。 */ policy_digest: string;
  /** 规范化报告摘要。 */ report_digest: string;
  /** 实际开始时间。 */ started_at: string;
  /** 实际完成时间。 */ completed_at: string;
  /** 服务器追加时间。 */ recorded_at: string;
}

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
  /** 发布清单 SHA-256；旧版本为空。 */ release_manifest_digest?: string | null;
  /** 服务器生成的不可变清单；旧版本不补造。 */ release_manifest?: AgentReleaseManifest | null;
  /** 清单记录/历史缺失/完整性失败；失败不显示清单字段。 */ release_manifest_status?: "recorded" | "legacy_unrecorded" | "integrity_failed";
  /** 回滚撤回时间；阻止新激活/新写动作，不删除历史和对账。 */ withdrawn_at?: string | null;
  /** 实际撤回该版本的管理员。 */ withdrawn_by?: string | null;
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
  /** 指标事实来源及统计约定；不是生产验收证明。 */ source?: string | null;
  /** 可计算该指标的样本数。 */ sample_count?: number | null;
  /** 范围内缺失或无效字段数量。 */ missing_count?: number | null;
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

/** Analytics 共用范围；生命周期/健康为当前状态，版本/模型等约束历史调用。 */
export interface DashboardQuery {
  /** 默认滚动天数，显式区间优先。 */ days?: number;
  /** 带时区的包含起点。 */ start?: string;
  /** 带时区的不包含终点，和起点同时提供。 */ end?: string;
  /** 指定 Agent UUID，不能作为权限。 */ agentId?: string;
  /** 当前 Agent 生命周期。 */ lifecycle?: "draft" | "active" | "disabled";
  /** 当前快照健康；缺失/过期属于 unknown。 */ health?: "healthy" | "degraded" | "unknown" | "no_recent_activity";
  /** 历史调用固定的 Agent 版本 UUID。 */ agentVersionId?: string;
  /** 历史调用固定的模型版本 UUID。 */ modelEndpointVersionId?: string;
  /** 历史调用原会话用户 UUID。 */ userId?: string;
  /** 用户当前末级部门 UUID，不含子部门。 */ organizationUnitId?: string;
  /** 业务 Run 状态，等待不当作执行。 */ status?: RunStatus;
}

/** 排行指标各自明确口径，未知不会排作零值。 */
export type DashboardRankingMetric = "agent_runs" | "active_users" | "failure_rate" | "tokens" | "cost_microusd" | "errors" | "p95_latency_ms" | "tool_calls";

export interface DashboardRankingItem {
  /** 仅当前租户的 Agent 标识。 */ agent_id: string;
  /** 管理分析使用的名称。 */ agent_name: string;
  /** 指标值；不完整 usage/账本或不足样本保留 null。 */ value: number | null;
}

export interface DashboardRankings {
  /** 完整分组报告，各指标固定 Top 10，平局按 Agent UUID。 */ rankings: Record<DashboardRankingMetric, DashboardRankingItem[]>;
}

export interface DashboardTimeseriesPoint {
  bucket_start: string;
  /** 服务端裁切后的桶终点，下钻使用准确左闭右开范围。 */
  bucket_end: string;
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

export interface RuntimeAlert {
  /** 告警唯一标识。 */ id: string;
  /** 固定规则代码。 */ rule_code: string;
  /** 固定中文标题。 */ title: string;
  /** 规则的操作说明，不含敏感来源正文。 */ description: string;
  /** Run 或独立动作。 */ scope_kind: "run" | "action";
  /** 来源业务标识，不作为授权。 */ scope_id: string;
  /** 关联 Run，详情仍需当前租户权限。 */ run_id: string | null;
  /** 观测级别。 */ severity: "info" | "warning" | "critical";
  /** 最近观测仍是否命中条件。 */ is_active: boolean;
  /** 当前观测是否过期；过期不能当作恢复正常。 */ is_stale: boolean;
  /** 已阅确认的乐观版本。 */ revision: number;
  /** 业务来源 generation，独立于告警确认。 */ source_generation: number;
  /** 白名单标量或缺失字段名称，不含正文和凭据。 */ metadata: Record<string, string | number | boolean | null | string[]>;
  /** 首次观测时间。 */ first_observed_at: string;
  /** 最近成功观测时间。 */ last_observed_at: string;
  /** 条件不再命中时间，不代表业务成功。 */ resolved_at: string | null;
  /** 管理员已阅时间，不作为修复证明。 */ acknowledged_at: string | null;
  /** 实际已阅人员。 */ acknowledged_by: string | null;
}

export interface RuntimeAlertList extends PaginatedListResponse<RuntimeAlert> {
  /** 共享扫描最近成功时间，不含其他租户来源 ID。 */ scan_observed_at: string | null;
  /** 扫描状态缺失或过期。 */ scan_stale: boolean;
  /** 告警字段约定版本。 */ convention_version: "runtime_alerts_v1";
}

export interface RunSecurityEvent {
  /** Run 内追加事件序号。 */ sequence: number;
  /** 授权拒绝、审批或动作事件类型。 */ event_type: string;
  /** 来源事实保存时间。 */ occurred_at: string;
  /** 模型执行尝试；控制面事件可为空。 */ attempt_id: string | null;
  /** 租户内排查关联 ID。 */ correlation_id: string;
  /** 严格白名单脱敏元数据，不含业务正文。 */ metadata: Record<string, string | number | boolean | null>;
}

export interface DashboardTrace {
  /** 来自持久化事实的阶段时间线，等待与执行分开，不能累加父子耗时。 */ business_trace?: {
    /** 投影字段约定版本。 */ convention_version: "business_phases_v1";
    /** 当前 Run 的根阶段标识。 */ root_span_id: string;
    /** 本次读取的观测时间。 */ observed_at: string;
    /** 每类来源有界查询是否截断。 */ truncated: boolean;
    /** 每类事实查询上限。 */ source_limit: 100;
    /** 当前权限下的阶段元数据，不含业务正文。 */ phases: Array<{
      /** 阶段标识。 */ span_id: string;
      /** 父阶段标识。 */ parent_span_id: string;
      /** 阶段类型，等待不计作执行。 */ kind: "execution" | "diagnostic" | "approval_wait" | "action_wait" | "recovery_wait" | "action" | "verification" | "reconciliation" | "receipt";
      /** 稳定阶段名称。 */ name: string;
      /** 来源事实的当前状态。 */ status: string;
      /** 来源开始时间。 */ started_at: string;
      /** 已确认结束时间；未结束为空。 */ finished_at: string | null;
      /** 已结束阶段耗时；未知为空。 */ duration_ms: number | null;
      /** 模型执行尝试。 */ run_attempt_id: string | null;
      /** 逻辑动作关联。 */ action_id: string | null;
      /** 动作执行/对账尝试。 */ action_attempt_id: string | null;
      /** 审批申请关联。 */ approval_id: string | null;
      /** 独立业务回执关联。 */ receipt_id: string | null;
      /** 持久化事实来源。 */ source: "durable_business_facts_v1";
    }>;
  } | null;
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
    /** 当前步骤首输出；供应商回调与 API 可见性观测各自注明边界。 */ first_output_at?: string | null;
    finished_at: string | null;
    duration_ms: number | null;
    input_tokens: number | null;
    output_tokens: number | null;
    total_cost_microusd: number | null;
    error_code: string | null;
    metadata: Record<string, unknown>;
    /** 执行尝试；旧观测可为空。 */ attempt_id?: string | null;
    /** 当前观测步骤的稳定 Span。 */ span_id?: string | null;
    /** 父观测步骤 ID。 */ parent_observation_id?: string | null;
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
  /** 健康协调任务的计算时间；缺快照不伪造当前时间。 */
  computed_at: string | null;
  /** 健康快照缺失、过期或超出允许时钟偏差时为 true。 */
  health_stale: boolean;
}

export type KnowledgeDocumentStatus = "draft" | "published" | "expired" | "archived";
/** 当前导入持久化支持的状态；未知状态不允许提交操作。 */
export type KnowledgeIngestionStatus = "queued" | "running" | "retry_wait" | "completed" | "failed" | "cancelled";

export interface KnowledgeIngestionCounts {
  /** 已提交的新草稿数，不表示已发布。 */ draft_created: number;
  /** 已提交的原版本复用数。 */ unchanged: number;
  /** 来源移除观察数，不表示删除知识。 */ source_removed: number;
  /** 明确拒绝对象数。 */ rejected: number;
  /** 待隔离复核对象数。 */ quarantined: number;
}

export interface KnowledgeIngestionSourceReadiness {
  /** 来源可用性契约版本，未知版本禁用创建。 */ schema_version: "knowledge_ingestion_source_readiness_v1";
  /** 当前来源配置与本人身份可用，提交时仍须复检。 */ available: boolean;
  /** 来源剩余批准时长允许创建固定窗口任务，提交时仍须复检。 */ create_available: boolean;
  /** 当前批准的同租户来源ID，未获准时为空。 */ source_id: string | null;
  /** 当前来源名称，未获准时为空。 */ source_name: string | null;
  /** 本次批准范围摘要，不包含路径、正文或完整策略。 */ scope_digest: string | null;
  /** 精确清单对象数，不包含文件名或路径。 */ object_count: number | null;
  /** 当前来源批准的带时区截止时间。 */ valid_until: string | null;
  /** 服务器本次检查时间，不代表已经读取来源。 */ observed_at: string;
  /** 人工导入不能自动发布。 */ automatic_publish_enabled: false;
}

export interface KnowledgeIngestionCreationStatus {
  /** 原请求状态契约，未知版本不得清除本地定位。 */ schema_version: "knowledge_ingestion_creation_status_v1";
  /** 当前查询或显式停止的原UUID请求键。 */ request_key: string;
  /** 尚未观察到不代表未提交；stopped才表示有持久停止事实。 */ status: "unobserved" | "recorded" | "stopped";
  /** 已生成的本人原任务；停止不会取消该任务。 */ job: KnowledgeIngestionJob | null;
  /** 明确停止事实的原始时间，其他状态为空。 */ stopped_at: string | null;
  /** 服务器本次观察时间，不是业务完成或停止时间。 */ observed_at: string;
}

export interface KnowledgeIngestionJob {
  /** 元数据契约版本；未知版本禁用操作。 */ schema_version: "knowledge_ingestion_job_view_v1";
  /** 本人申请的任务ID。 */ id: string;
  /** 规范UUID格式的原创建请求键，非规范旧字符串或完整性失败时为空；不是执行许可。 */ creation_request_key: string | null;
  /** 原始任务定义与状态的完整性结果。 */ integrity_status: "recorded" | "integrity_failed";
  /** 数据库记录状态；过期租约不自动变为失败。 */ status: KnowledgeIngestionStatus | "unknown";
  /** 完整性校验通过后可见的来源ID。 */ source_id: string | null;
  /** 任务固定范围摘要，仅用于比对当前来源批准，不构成执行许可。 */ scope_digest: string | null;
  /** 当前同租户来源名称，缺失时为空。 */ source_name: string | null;
  /** 显式操作必须提交的当前修订。 */ revision: number | null;
  /** 执行围栏代次，不构成授权。 */ generation: number | null;
  /** 累计实际领取次数。 */ attempts: number | null;
  /** 固定尝试上限。 */ max_attempts: number | null;
  /** 原始创建时间，损坏时为空。 */ created_at: string | null;
  /** 固定任务截止时间。 */ deadline_at: string | null;
  /** 当前执行租约截止时间，不保证执行器仍存活。 */ lease_expires_at: string | null;
  /** 有限重试最早时间，不表示已启用后台执行。 */ retry_at: string | null;
  /** 固定来源失败码，不包含原始错误。 */ failure_code: string | null;
  /** 检查点类型，无原始游标或秘密引用。 */ checkpoint_kind: "initial" | "continuation" | "complete" | null;
  /** 已提交页面检查点序号。 */ committed_pages: number | null;
  /** 固定页面上限。 */ max_pages: number | null;
  /** 固定单页对象上限。 */ max_objects_per_page: number | null;
  /** 已提交计数，未知或损坏时为空。 */ counts: KnowledgeIngestionCounts | null;
  /** 服务端当前允许本人显式取消，提交时仍复检。 */ cancel_available: boolean;
  /** 服务端当前允许显式记录超时失败，读取本身不改变状态。 */ expire_available: boolean;
  /** 当前导入不支持自动发布。 */ automatic_publish_enabled: false;
}

export interface KnowledgeIngestionDraft {
  /** 本任务首次创建的物化事实ID，复用版本不重复列入。 */ id: string;
  /** 首次物化的服务端时间。 */ created_at: string;
}

export interface KnowledgeIngestionComparedVersion {
  /** 当前正文获准后可见的知识版本ID。 */ id: string;
  /** 文档内单调版本号。 */ version_number: number;
  /** 版本标题，以纯文本显示。 */ title: string;
  /** 不可变正文SHA-256。 */ content_digest: string;
}

export interface KnowledgeIngestionTextChange {
  /** 正文是否有字面变化，不表示语义判断。 */ content_changed: boolean;
  /** 标题是否变化。 */ title_changed: boolean;
  /** 省略的共同开头行数。 */ common_prefix_lines: number;
  /** 省略的共同结尾行数。 */ common_suffix_lines: number;
  /** 当前发布版的完整替换区间，保留换行，仅纯文本。 */ removed_text: string;
  /** 候选版的完整替换区间，保留换行，仅纯文本。 */ added_text: string;
}

export interface KnowledgeIngestionComparison {
  /** 未知比较契约不显示正文。 */ schema_version: "knowledge_ingestion_comparison_v1";
  /** 本人申请任务ID。 */ job_id: string;
  /** 首次物化事实ID。 */ materialization_id: string;
  /** 比较可用性；非available时其他范围和内容字段均隐藏。 */ status: "available" | "content_unavailable" | "target_unavailable" | "newer_candidate" | "candidate_withdrawn" | "scope_changed" | "integrity_failed" | "too_large";
  /** 当前获准的文档ID。 */ document_id: string | null;
  /** 当前文档负责人ID，不授予修改许可。 */ owner_user_id: string | null;
  /** 已配置的下次复核时间；为空不补造提醒。 */ review_due_at: string | null;
  /** 当前候选治理状态，不表示导入任务已获批准。 */ candidate_status: "draft" | "in_review" | "published" | null;
  /** 已记录审核决定，发布时仍须重新校验。 */ review_decision: "pending" | "approved" | "rejected" | null;
  /** 当前获准的候选摘要。 */ candidate: KnowledgeIngestionComparedVersion | null;
  /** 读取时的当前有效发布版本，不是导入时基线。 */ baseline: KnowledgeIngestionComparedVersion | null;
  /** 有界共同首尾替换差异，超限不返回部分内容。 */ change: KnowledgeIngestionTextChange | null;
  /** 导入与查看均不自动发布。 */ automatic_publish_enabled: false;
}

export type KnowledgeVersionStatus = "draft" | "in_review" | "published" | "superseded" | "retired";

export interface KnowledgeIngestionGovernance {
  /** 治理元数据契约版本，未知时禁止修改。 */ schema_version: "knowledge_ingestion_governance_v1";
  /** 当前管理员本人的导入任务ID。 */ job_id: string;
  /** 本任务首次物化事实ID。 */ materialization_id: string;
  /** 当前治理目标可用性；受限状态隐藏范围。 */ status: "available" | "target_unavailable" | "newer_candidate" | "integrity_failed" | "candidate_withdrawn";
  /** 服务端本次观察时间，到期提示以此为准。 */ observed_at: string;
  /** 可用的当前知识文档ID。 */ document_id: string | null;
  /** 当前负责人ID，分配不会赋予知识ACL。 */ owner_user_id: string | null;
  /** 当前同租户负责人显示名称，不含联系方式。 */ owner_display_name: string | null;
  /** 当前负责人是否为有效用户，缺失为false。 */ owner_active: boolean | null;
  /** 复核日期，null表示未配置。 */ review_due_at: string | null;
  /** 服务端判断未配置/尚未到期/已到期，不改变知识状态。 */ review_timing: "unscheduled" | "scheduled" | "due" | null;
  /** 最新候选的当前生命周期状态。 */ candidate_status: "draft" | "in_review" | "published" | null;
  /** 已记录的审核决定，不代表发布许可。 */ review_decision: "pending" | "approved" | "rejected" | null;
  /** 修改必须绑定的当前文档/候选/ACL治理摘要。 */ governance_digest: string | null;
  /** 当前允许设置负责人/日期，提交时仍会复检。 */ update_available: boolean;
  /** 本人未发布最新候选当前可显式撤回，不代表已经执行。 */ withdraw_available: boolean;
  /** 不支持自动发布。 */ automatic_publish_enabled: false;
}

export interface KnowledgeIngestionGovernanceInput {
  /** 最后读取的治理摘要，变化时拒绝覆盖。 */ expected_governance_digest: string;
  /** 同租户当前有效的负责人ID。 */ owner_user_id: string;
  /** 带时区ISO复核时间，显式null清除日期。 */ review_due_at: string | null;
}

export interface KnowledgeIngestionOwnerOption {
  /** 同租户当前有效的用户ID，不代表授予正文或操作许可。 */ id: string;
  /** 负责人显示名称，不携带邮箱、电话或角色。 */ display_name: string;
}

/** 用户明确选择的处置原因；不代表系统证实来源发生语义冲突。 */
export type KnowledgeIngestionWithdrawalReason = "source_scope_changed" | "manual_resolution_required" | "no_longer_needed";

export interface KnowledgeIngestionWithdrawalInput {
  /** 最后读取的当前治理范围摘要。 */ expected_governance_digest: string;
  /** 本人明确选择的撤回原因，禁止自动推断。 */ reason_code: KnowledgeIngestionWithdrawalReason;
}

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
/** 本人可申请影响预览的数据类别。 */
export type DataCategory = "identity" | "auth_sessions" | "conversations" | "messages" | "runs" | "events" | "observations" | "knowledge" | "credentials" | "actions" | "approvals" | "checkpoints" | "memory" | "backups" | "external_copies" | "governance_audit";
export interface ErasureRequestInput {
  /** 固定选择的类别，不接受目标主体。 */
  categories: DataCategory[];
  /** 固定原因码，不上传自由文本个人信息。 */
  reason_code: "privacy_request" | "account_closure" | "data_correction";
}
export interface RetentionPolicyInput {
  /** 草稿名称。 */
  name: string;
  /** 明确类别规则，未确认周期/法律保留为null。 */
  rules: Array<{
    /** 规则所属数据类别。 */
    category: DataCategory;
    /** 保留天数，未确认不填默认。 */
    retain_days: number | null;
    /** 法律保留要求，未确认不填false。 */
    legal_hold_required: boolean | null;
    /** 备份恢复处理意向，尚不执行。 */
    backup_handling: "unresolved" | "isolate_on_restore";
  }>;
  /** 同租户候选人，不授予复核权限。 */
  reviewer_user_ids: string[];
  /** 草稿协议版本。 */
  schema_version: "retention_policy_draft_v1";
  /** 未来须独立非本人及显式主体范围复核。 */
  reviewer_strategy: "explicit_subject_scope_non_self";
  /** 当前不自动清理。 */
  automatic_cleanup_enabled: false;
}
export interface RetentionPolicySummary {
  /** 草稿ID。 */
  id: string;
  /** 草稿名称。 */
  name: string;
  /** 固定草稿摘要。 */
  policy_digest: string;
  /** 只支持草稿。 */
  status: "draft";
  /** 创建管理员ID。 */
  created_by: string;
  /** 服务器创建时间。 */
  created_at: string;
  /** 草稿不批准维护。 */
  approval_granted: false;
}
export interface RetentionPolicyDetail {
  /** 草稿摘要。 */
  policy: RetentionPolicySummary;
  /** 完整定义，损坏时隐藏。 */
  definition: RetentionPolicyInput | null;
  /** 已记录或完整性失败。 */
  integrity_status: "recorded" | "integrity_failed";
}
export interface ErasureRequestSummary extends ErasureRequestInput {
  /** 申请唯一标识。 */
  id: string;
  /** 本人申请状态，不含批准/已擦除。 */
  status: "requested" | "previewed" | "withdrawn";
  /** 预览/撤回并发版本。 */
  revision: number;
  /** 创建时策略草稿，未配置为空。 */
  policy_id: string | null;
  /** 固定草稿摘要，不代表批准。 */
  policy_digest: string | null;
  /** 固定本人/类别/策略范围摘要。 */
  scope_digest: string;
  /** 关联记录的创建截止时间。 */
  cutoff_at: string;
  /** 申请创建时间。 */
  created_at: string;
  /** 一次预览完成时间。 */
  previewed_at: string | null;
  /** 本人撤回时间。 */
  withdrawn_at: string | null;
  /** 当前不提供实际执行。 */
  execution_enabled: false;
}
export interface ErasureCategoryImpact {
  /** 数据类别。 */
  category: DataCategory;
  /** 已知关联行数，各类可能重叠，不是可删数量。 */
  known_related_rows: number;
  /** 未枚举的依赖项数量。 */
  unknown_table_count: number;
  /** 固定元数据计数目录，无正文/资源ID。 */
  tables: Array<{
    /** 固定内部数据集合名。 */
    table: string;
    /** 行数，未覆盖为null。 */
    row_count: number | null;
    /** 本人/参与/共享/未覆盖关系。 */
    relation: "subject_owned" | "subject_participation" | "shared_reference" | "unresolved";
    /** 固定引用说明或未覆盖原因；旧未记录为空。 */
    reason_code: string | null;
  }>;
  /** 当前阻塞原因。 */
  blockers: string[];
  /** 当前目录未完整覆盖。 */
  complete_inventory: false;
  /** 影响计数不批准擦除。 */
  eligible_for_erasure: false;
}
export interface ErasurePreviewReport {
  /** 只读预览协议。 */
  schema_version: "erasure_preview_v1";
  /** 绑定本人申请。 */
  request_id: string;
  /** 绑定固定申请范围。 */
  scope_digest: string;
  /** 绑定创建时策略草稿。 */
  policy_digest: string | null;
  /** 创建记录统计上界。 */
  cutoff_at: string;
  /** 计数开始时间，跨表不是原子快照。 */
  observed_at: string;
  /** 计数结束时间。 */
  finished_at: string;
  /** 各类聚合与未知项。 */
  impacts: ErasureCategoryImpact[];
  /** 预览时尚未结束的本人Run。 */
  active_run_count: number;
  /** 预览时尚未确认终态的本人动作。 */
  unresolved_action_count: number;
  /** 整体阻塞原因。 */
  blockers: string[];
  /** 当前未覆盖完整依赖。 */
  complete_inventory: false;
  /** 预览不产生执行许可。 */
  snapshot_is_execution_permit: false;
}
export interface ErasureRequestDetail {
  /** 本人申请摘要。 */
  request: ErasureRequestSummary;
  /** 一次聚合预览，未生成或完整性失败时为空。 */
  report: ErasurePreviewReport | null;
  /** 完整预览摘要。 */
  report_digest: string | null;
  /** 已记录/未记录/完整性失败。 */
  report_status: "not_recorded" | "recorded" | "integrity_failed";
  /** 未来独立复核需绑定的范围，当前没有批准能力。 */
  review_scope: {
    /** 固定申请范围。 */
    scope_digest: string;
    /** 固定预览摘要。 */
    preview_digest: string;
    /** 申请/预览/策略联合摘要。 */
    approval_scope_digest: string;
    /** 必须独立非本人、显式主体范围复核。 */
    reviewer_strategy: "explicit_subject_scope_non_self";
    /** 需要独立维护身份。 */
    separate_maintenance_identity_required: true;
    /** 需要重新盘点当前依赖。 */
    fresh_inventory_required: true;
    /** 当前未批准。 */
    approval_granted: false;
    /** 当前无执行能力。 */
    execution_enabled: false;
  } | null;
}

/** 当前隔离Mock关闭工单补偿意向的固定范围，无正文或秘密。 */
export interface CompensationScope {
  /** 补偿范围协议。 */ schema_version: "mock_ticket_compensation_v1";
  /** 可信租户。 */ tenant_id: string;
  /** 原动作本人主体。 */ subject_user_id: string;
  /** 原动作ID。 */ action_id: string;
  /** 原动作generation。 */ action_generation: number;
  /** 原动作历史状态。 */ action_status: "succeeded" | "needs_attention";
  /** 原冻结参数摘要。 */ original_parameters_digest: string;
  /** 原稳定业务键。 */ logical_key: string;
  /** 独立查询回执ID。 */ source_receipt_id: string;
  /** 原回执内容摘要。 */ source_receipt_digest: string;
  /** 独立查询确定的成功或冲突。 */ source_receipt_status: "verified" | "conflict";
  /** 当前本人Mock工单ID。 */ target_ticket_id: string;
  /** 当前Mock工单编号。 */ target_external_id: string;
  /** 当前工单版本，未来执行须重检。 */ target_version: number;
  /** 当前工单状态，不含已关闭。 */ target_status: "open" | "in_progress" | "escalated";
  /** 当前工单受限参数摘要。 */ target_parameters_digest: string;
  /** 当前已完成并关联的授权Run。 */ context_run_id: string;
  /** 当前固定Agent版本。 */ agent_version_id: string;
  /** 当前固定配置摘要。 */ config_digest: string;
  /** 当前固定模型版本。 */ model_version_id: string;
  /** 当前上下文固定凭据修订，无秘密。 */ credential_revision: number;
  /** 原动作工具实现。 */ original_tool_implementation_version: number;
  /** 当前上下文绑定的工具实现。 */ context_tool_implementation_version: number;
  /** 当前工具目录版本。 */ current_catalog_version: number;
  /** 唯一支持的关闭Mock工单意向。 */ intent: "close_mock_ticket";
  /** 历史冲突或本人明确关闭意向。 */ reason_code: "verification_conflict" | "user_requested_closure";
  /** 最低风险R3，不能复用原R2许可。 */ minimum_risk: "R3" | "R4";
  /** 须重新独立审批。 */ approval_required: true;
  /** 当前无执行能力。 */ execution_enabled: false;
}
export interface CompensationReadiness {
  /** 当前可冻结范围，不适用为空。 */ scope: CompensationScope | null;
  /** 当前范围摘要。 */ scope_digest: string | null;
  /** 仅表示可以保存提案。 */ proposal_supported: boolean;
  /** 保存或执行的明确阻塞原因。 */ blockers: string[];
  /** 当前没有批准。 */ approval_granted: false;
  /** 当前不能执行。 */ execution_enabled: false;
}
export interface CompensationSummary {
  /** 与原动作分离的提案ID。 */ id: string;
  /** 原动作ID。 */ action_id: string;
  /** 独立查询回执ID。 */ source_receipt_id: string;
  /** 固定范围摘要。 */ scope_digest: string;
  /** 最低R3，保留R4。 */ minimum_risk: "R3" | "R4";
  /** 固定原因码。 */ reason_code: "verification_conflict" | "user_requested_closure";
  /** 关闭Mock工单意向。 */ intent: "close_mock_ticket";
  /** 仅提案，没有执行事实。 */ status: "proposed";
  /** 服务器保存时间。 */ created_at: string;
  /** 必须重新独立审批。 */ approval_required: true;
  /** 未批准。 */ approval_granted: false;
  /** 当前无执行能力。 */ execution_enabled: false;
}
export interface CompensationDetail {
  /** 已保存提案摘要。 */ proposal: CompensationSummary;
  /** 原固定范围，损坏或目标不可访问时隐藏。 */ scope: CompensationScope | null;
  /** 历史完整性校验结果。 */ integrity_status: "recorded" | "integrity_failed";
  /** 当前适用性，变化不改写历史。 */ applicability: "current" | "changed" | "unavailable" | "integrity_failed";
  /** 当前阻塞原因。 */ blockers: string[];
  /** 没有从原动作继承批准。 */ approval_granted: false;
  /** 当前没有补偿执行入口。 */ execution_enabled: false;
}
