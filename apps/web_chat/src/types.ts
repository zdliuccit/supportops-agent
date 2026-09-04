/** Agent Run 的完整生命周期状态。 */
export type RunStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "completed"
  | "failed"
  | "cancelled";

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
  /** 按创建时间返回的用户可见消息历史。 */
  messages: Message[];
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
export interface ConversationListResponse {
  /** 已按当前 Agent 和当前用户过滤的会话摘要。 */
  items: ConversationSummary[];
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
  /** 当前组织单元 UUID。 */
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

/** 树形公司组织单元。 */
export interface OrganizationUnit {
  /** 组织单元 UUID。 */
  id: string;
  /** 上级组织 UUID；根组织为空。 */
  parent_id: string | null;
  /** 组织单元名称。 */
  name: string;
  /** 当前租户内唯一的组织代码。 */
  code: string;
  /** 组织层级类型。 */
  unit_type: "company" | "department" | "team";
  /** 同级节点的显示顺序。 */
  sort_order: number;
  /** 组织单元生命周期状态。 */
  status: "active" | "disabled";
  /** 直接归属当前组织的用户数量。 */
  direct_user_count: number;
  /** 已稳定排序的下级组织。 */
  children: OrganizationUnit[];
}

/** 创建组织单元所需字段。 */
export interface OrganizationUnitCreateInput {
  /** 上级组织 UUID；创建根级节点时为空。 */
  parent_id: string | null;
  /** 组织单元名称。 */
  name: string;
  /** 当前租户内唯一的组织代码。 */
  code: string;
  /** 组织层级类型。 */
  unit_type: "company" | "department" | "team";
  /** 同级节点的显示顺序。 */
  sort_order: number;
}

/** 编辑组织单元资料与父子关系的请求。 */
export interface OrganizationUnitUpdateInput extends OrganizationUnitCreateInput {
  /** 更新后的生命周期状态。 */
  status: "active" | "disabled";
}

/** 管理员可查看但不包含密码哈希的用户资料。 */
export interface AdminUser {
  /** 系统用户 UUID，也是 JWT 的唯一主体。 */
  id: string;
  /** 规范化登录邮箱。 */
  email: string;
  /** 用户显示名称。 */
  display_name: string;
  /** 所属组织单元 UUID。 */
  organization_unit_id: string | null;
  /** 所属组织单元名称。 */
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
  /** 所属组织单元 UUID。 */
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
  /** 所属组织单元 UUID。 */
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
  /** ISO 8601 格式的创建时间。 */
  created_at: string;
  /** ISO 8601 格式的最近更新时间。 */
  updated_at: string;
}

/** 可由多个 Agent 复用的稳定模型端点资源。 */
export interface ModelEndpoint {
  /** 模型端点 UUID。 */
  id: string;
  /** 管理端显示名称。 */
  name: string;
  /** 模型端点 Logo 地址。 */
  logo_url: string | null;
  /** 模型端点生命周期状态。 */
  status: "draft" | "active" | "disabled";
  /** 历史迁移占位端点为只读。 */
  read_only: boolean;
  /** 当前活动 ModelEndpointVersion UUID。 */
  active_version_id: string | null;
  /** API Key 的不可逆脱敏提示；未配置时为空。 */
  credential_masked_hint: string | null;
  /** 当前凭据 revision；前端永远无法读取密钥明文。 */
  credential_revision: number | null;
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
    /** 首版不开放回退端点，保留字段必须为空数组。 */
    fallback_model_endpoint_ids?: string[];
    /** 单次模型生成参数。 */
    generation?: {
      /** 采样温度，允许范围由服务端再次校验。 */
      temperature?: number;
      /** 单次回答最大输出 Token。 */
      max_output_tokens?: number;
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
