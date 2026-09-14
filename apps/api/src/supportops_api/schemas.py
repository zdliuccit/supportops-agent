from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from supportops_core.agent_config import AgentConfigV2
from supportops_core.enums import (
    AgentStatus,
    ConversationStatus,
    GrantSubjectType,
    KnowledgeAclSubjectType,
    KnowledgeDocumentStatus,
    KnowledgeSourceEnvironment,
    KnowledgeVersionStatus,
    MessageRole,
    ModelApiProtocol,
    ModelProviderKind,
    ModelTestStatus,
    ModelVerificationStatus,
    RunStatus,
)
from supportops_core.model_config import ModelEndpointVersionConfig


class ErrorBody(BaseModel):
    code: str
    message: str
    correlation_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class PaginatedListResponse(BaseModel):
    """所有普通列表接口共用的分页响应元数据。"""

    total: int = Field(ge=0, description="符合当前过滤条件的数据总数。")
    page: int = Field(ge=1, description="当前页码，从 1 开始。")
    page_size: int = Field(ge=1, description="当前每页数据数量。")
    pages: int = Field(ge=0, description="按当前每页数量计算的总页数。")


class LoginRequest(BaseModel):
    """邮箱密码登录请求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    """认证成功后返回的短期访问令牌。"""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class IdentityResponse(BaseModel):
    user_id: UUID
    tenant_id: UUID
    email: str
    display_name: str
    roles: list[str]
    organization_unit_id: UUID | None
    job_title: str
    company_name: str
    company_logo_url: str | None


class CompanyResponse(BaseModel):
    """当前身份所属公司资料。"""

    id: UUID
    name: str
    slug: str | None
    logo_url: str | None
    contact_email: str | None
    status: str
    updated_at: datetime


class CompanyUpdate(BaseModel):
    """公司可编辑展示资料。"""

    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")
    logo_url: str | None = Field(default=None, max_length=2000)
    contact_email: str | None = Field(default=None, max_length=320)


class OrganizationUnitCreate(BaseModel):
    """创建部门所需的最小业务字段。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    parent_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)


class OrganizationUnitUpdate(BaseModel):
    """部门名称和上级部门更新。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    parent_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)


class OrganizationUnitResponse(BaseModel):
    """包含递归子部门、直属人数和部门树总人数的部门节点。"""

    id: UUID
    parent_id: UUID | None
    name: str
    direct_user_count: int = Field(
        ge=0, description="直接归属当前部门的用户数量，不包含子部门用户。"
    )
    user_count: int = Field(ge=0, description="当前部门及全部后代部门的用户总数量。")
    children: list["OrganizationUnitResponse"] = Field(default_factory=list)


class OrganizationTreeResponse(BaseModel):
    items: list[OrganizationUnitResponse]


class AdminUserCreate(BaseModel):
    """管理员创建本地登录用户的请求。"""

    model_config = ConfigDict(str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(min_length=1, max_length=200)
    organization_unit_id: UUID | None = None
    job_title: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=64)
    roles: list[str] = Field(default_factory=lambda: ["employee"], min_length=1)


class AdminUserUpdate(BaseModel):
    """管理员更新用户资料、权限和状态的完整请求。"""

    model_config = ConfigDict(str_strip_whitespace=True)
    display_name: str = Field(min_length=1, max_length=200)
    organization_unit_id: UUID | None = None
    job_title: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=64)
    roles: list[str] = Field(min_length=1)
    status: Literal["active", "disabled"]


class AdminPasswordReset(BaseModel):
    """管理员为用户设置的新密码。"""

    password: str = Field(min_length=10, max_length=256)


class AdminUserResponse(BaseModel):
    """不包含密码哈希的管理员用户视图。"""

    id: UUID
    email: str
    display_name: str
    organization_unit_id: UUID | None
    organization_unit_name: str | None
    job_title: str
    phone: str
    roles: list[str]
    status: str
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminUserListResponse(PaginatedListResponse):
    items: list[AdminUserResponse]


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: MessageRole
    content: str
    created_at: datetime


class ConversationCreate(BaseModel):
    agent_id: UUID
    title: str | None = Field(default=None, max_length=200)


class AgentSafeProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    logo_url: str | None
    description: str
    welcome_message: str
    suggested_prompts: list[str]
    active_version_id: UUID


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    is_pinned: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "ConversationUpdate":
        if self.title is None and self.is_pinned is None:
            raise ValueError("至少需要提供一项会话变更")
        return self


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    agent_version_id: UUID
    current_agent_version_id: UUID | None
    agent_version_number: int
    model_endpoint_version_id: UUID
    agent: AgentSafeProfile
    title: str | None
    is_pinned: bool
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse] = Field(default_factory=list)


class ConversationSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    agent_version_id: UUID
    title: str | None
    is_pinned: bool
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(PaginatedListResponse):
    items: list[ConversationSummaryResponse]


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class MessageAccepted(BaseModel):
    message_id: UUID
    run_id: UUID
    status: RunStatus
    events_url: str
    agent_id: UUID
    agent_version_id: UUID
    model_endpoint_version_id: UUID


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    agent_id: UUID
    agent_version_id: UUID
    model_endpoint_version_id: UUID
    status: RunStatus
    error_code: str | None
    correlation_id: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class HealthResponse(BaseModel):
    status: str
    service: str
    dependencies: dict[str, Any] | None = None


class AgentCatalogResponse(PaginatedListResponse):
    items: list[AgentSafeProfile]


class AdminAgentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{1,99}$")
    name: str = Field(min_length=1, max_length=200)
    logo_url: str | None = Field(default=None, max_length=2000)
    description: str = Field(default="", max_length=2000)
    welcome_message: str = Field(default="", max_length=2000)
    suggested_prompts: list[str] = Field(default_factory=list, max_length=12)
    config: AgentConfigV2


class AgentProfileUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    logo_url: str | None = Field(default=None, max_length=2000)
    description: str = Field(default="", max_length=2000)
    welcome_message: str = Field(default="", max_length=2000)
    suggested_prompts: list[str] = Field(default_factory=list, max_length=12)


class AdminAgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    logo_url: str | None
    description: str
    welcome_message: str
    suggested_prompts: list[str]
    status: AgentStatus
    read_only: bool
    draft_revision: int | None = None
    active_version_id: UUID | None
    active_version_config_digest: str | None = None
    active_version_published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminAgentListResponse(PaginatedListResponse):
    items: list[AdminAgentResponse]


class KnowledgeSourceCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    external_key: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    source_type: str = Field(min_length=1, max_length=32)
    environment: KnowledgeSourceEnvironment = KnowledgeSourceEnvironment.MOCK
    owner_user_id: UUID


class KnowledgeSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    external_key: str
    name: str
    source_type: str
    environment: KnowledgeSourceEnvironment
    owner_user_id: UUID
    owner_user_name: str | None = None
    created_by: UUID
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeSourceListResponse(BaseModel):
    items: list[KnowledgeSourceResponse]


class KnowledgeDocumentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    source_id: UUID
    external_key: str = Field(min_length=1, max_length=200)
    owner_user_id: UUID
    title: str = Field(min_length=1, max_length=500)
    content_markdown: str = Field(min_length=1)
    change_summary: str = Field(default="", max_length=2000)
    effective_from: datetime | None = None
    effective_until: datetime | None = None


class KnowledgeDocumentUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    owner_user_id: UUID | None = None
    review_due_at: datetime | None = None


class KnowledgeVersionCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    content_markdown: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    change_summary: str = Field(default="", max_length=2000)
    effective_from: datetime | None = None
    effective_until: datetime | None = None


class KnowledgeDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_id: UUID
    external_key: str
    owner_user_id: UUID
    owner_user_name: str | None = None
    created_by: UUID
    created_by_name: str | None = None
    status: KnowledgeDocumentStatus
    current_version_id: UUID | None
    review_due_at: datetime | None
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentListResponse(PaginatedListResponse):
    items: list[KnowledgeDocumentResponse]


class KnowledgeVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    version_number: int
    status: KnowledgeVersionStatus
    title: str
    content_markdown: str
    content_digest: str
    section_anchors: list[dict[str, str]]
    change_summary: str
    effective_from: datetime
    effective_until: datetime | None
    created_by: UUID
    created_by_name: str | None = None
    reviewed_by: UUID | None
    reviewed_by_name: str | None = None
    published_by: UUID | None
    published_by_name: str | None = None
    created_at: datetime
    reviewed_at: datetime | None
    published_at: datetime | None


class KnowledgeAclEntryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: KnowledgeAclSubjectType
    subject_id: str = Field(min_length=1, max_length=200)


class KnowledgeAclReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[KnowledgeAclEntryInput] = Field(max_length=100)


class KnowledgeSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    version_id: UUID
    content_digest: str
    title: str
    section_anchors: list[dict[str, str]]
    acl_digest: str
    effective_from: datetime
    effective_until: datetime | None
    published_at: datetime


class KnowledgeAuditEventResponse(BaseModel):
    id: int
    resource_type: str
    action: str
    actor_user_id: UUID
    version_id: UUID | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str
    created_at: datetime


class KnowledgeAuditEventListResponse(PaginatedListResponse):
    items: list[KnowledgeAuditEventResponse]


class KnowledgeMockImportItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    external_key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    content_markdown: str = Field(min_length=1)


class KnowledgeMockImportRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    source_external_key: str = Field(default="p0-mock", min_length=1, max_length=200)
    source_name: str = Field(default="P0 Mock Knowledge", min_length=1, max_length=200)
    items: list[KnowledgeMockImportItem] = Field(min_length=1, max_length=100)


class KnowledgeMockImportResponse(BaseModel):
    created: int = Field(ge=0)
    skipped: int = Field(ge=0)


class AgentDraftResponse(BaseModel):
    agent_id: UUID
    revision: int
    schema_version: str
    config: dict[str, Any]
    config_digest: str
    updated_at: datetime


class AgentDraftUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    config: AgentConfigV2


class AgentValidateRequest(BaseModel):
    config: AgentConfigV2


class AgentValidateResponse(BaseModel):
    valid: bool = True
    model_endpoint_version_id: UUID
    credential_revision: int
    tool_ids: list[str]


class AgentPublishRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    release_notes: str = Field(default="", max_length=2000)
    activate: bool = False


class AgentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    version_number: int
    schema_version: str
    config_digest: str
    model_endpoint_version_id: UUID
    resolved_tool_ids: list[str]
    release_notes: str
    published_at: datetime


class AgentVersionListResponse(PaginatedListResponse):
    items: list[AgentVersionResponse]


class AgentGrantInput(BaseModel):
    subject_type: GrantSubjectType
    subject_id: str = Field(min_length=1, max_length=200)


class AgentGrantReplace(BaseModel):
    grants: list[AgentGrantInput] = Field(default_factory=list, max_length=500)


class AgentGrantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_type: GrantSubjectType
    subject_id: str
    created_at: datetime


class AgentGrantListResponse(PaginatedListResponse):
    items: list[AgentGrantResponse]


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    actor_user_id: UUID
    version_id: UUID | None
    metadata_payload: dict[str, Any]
    correlation_id: str
    created_at: datetime


class AuditEventListResponse(PaginatedListResponse):
    items: list[AuditEventResponse]


class ToolCatalogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tool_id: str
    name: str
    description: str
    implementation_key: str
    required_roles: list[str]
    risk_level: Literal["low", "medium", "high"]
    version: int
    is_enabled: bool


class ToolCatalogListResponse(BaseModel):
    items: list[ToolCatalogResponse]


class ToolCatalogUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    required_roles: list[str] = Field(default_factory=list, max_length=20)
    risk_level: Literal["low", "medium", "high"] = "low"
    is_enabled: bool = True


class ModelEndpointCreate(BaseModel):
    """创建模型配置；兼容旧单模型请求，新增页面使用多模型字段。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200, description="模型显示名称。")
    logo_url: str | None = Field(default=None, max_length=2000)
    api_key: str = Field(min_length=1, max_length=20_000, description="只写模型 API Key。")
    version: ModelEndpointVersionConfig | None = Field(
        default=None, description="旧单模型创建契约；新页面不再使用。"
    )
    provider_preset: str | None = Field(
        default=None, max_length=64, description="供应商预设标识；自定义配置为空。"
    )
    provider_kind: ModelProviderKind | None = Field(default=None, description="模型供应商类型。")
    base_url: str | None = Field(default=None, max_length=2000, description="API Base URL。")
    models: list["ModelEndpointModelInput"] = Field(
        default_factory=list, description="当前连接下完整的模型集合。"
    )
    is_enabled: bool = Field(
        default=False, description="是否请求在保存后立即启用；仍受测试结果约束。"
    )

    @model_validator(mode="after")
    def validate_configuration_shape(self) -> "ModelEndpointCreate":
        if self.version is None and (
            self.provider_kind is None or self.base_url is None or not self.models
        ):
            raise ValueError("必须提供供应商、Base URL 和至少一个模型")
        return self


class ModelEndpointModelInput(BaseModel):
    """创建或更新连接时提交的单个模型配置。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: UUID | None = Field(default=None, description="既有稳定模型 UUID；新模型为空。")
    upstream_model_id: str = Field(
        min_length=1, max_length=300, description="供应商识别的真实模型 ID。"
    )
    extension_options: dict[str, Any] = Field(
        default_factory=dict,
        description="随模型请求发送的 JSON 扩展对象；默认空对象。",
    )


ModelEndpointCreate.model_rebuild()


class ModelEndpointConfigurationSave(BaseModel):
    """整体替换模型配置及其当前模型集合。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200, description="模型显示名称。")
    provider_preset: str | None = Field(default=None, max_length=64)
    provider_kind: ModelProviderKind
    base_url: str = Field(min_length=1, max_length=2000)
    api_key: str | None = Field(
        default=None, min_length=1, max_length=20_000, description="可选新密钥；为空保持原密钥。"
    )
    models: list[ModelEndpointModelInput] = Field(min_length=1)
    is_enabled: bool = Field(
        default=False, description="保存并使用时为 true；测试不满足时仍保持未启用。"
    )


class ModelDiscoveryRequest(BaseModel):
    """通过当前 Base URL 和 API Key 显式获取供应商模型目录。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_url: str = Field(
        min_length=1,
        max_length=2000,
        description="供应商或中转站的 API Base URL，服务端统一请求其 /models 路径。",
    )
    api_key: str = Field(
        min_length=1,
        max_length=20_000,
        description="本次模型发现使用的 API Key；仅用于当前请求，不持久化。",
    )


class ModelDiscoveryResponse(BaseModel):
    """供应商模型目录；仅用于选择，不代表连通性测试通过。"""

    items: list[str]


class ModelEndpointProfileUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    logo_url: str | None = Field(default=None, max_length=2000)


class ModelEndpointVersionCreate(BaseModel):
    version: ModelEndpointVersionConfig


class ModelCredentialRotate(BaseModel):
    api_key: str = Field(min_length=1, max_length=20_000)


class ModelEndpointVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    endpoint_id: UUID
    endpoint_model_id: UUID | None
    version_number: int
    provider_kind: ModelProviderKind
    api_protocol: ModelApiProtocol
    base_url: str
    remote_model_name: str
    capabilities: dict[str, Any]
    defaults: dict[str, Any]
    request_metadata: dict[str, Any]
    pricing: dict[str, Any]
    verification_status: ModelVerificationStatus
    verification_result: dict[str, Any]
    verified_at: datetime | None
    created_at: datetime


class ModelTestRunResponse(BaseModel):
    """单模型连通性测试的安全进度和结果。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    endpoint_id: UUID
    endpoint_model_id: UUID
    model_version_id: UUID
    credential_revision: int
    status: ModelTestStatus
    stage: str
    request_host: str
    request_path: str
    provider_status: int | None
    response_headers_ms: int | None
    first_content_ms: int | None
    total_ms: int | None
    response_content: str = Field(
        default="", description="模型对固定测试问题返回的截断文本。"
    )
    milestones: dict[str, Any]
    error_code: str | None
    error_message: str | None
    correlation_id: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class ModelEndpointModelResponse(BaseModel):
    """连接下稳定模型及其最新测试摘要。"""

    id: UUID
    upstream_model_id: str
    display_name: str
    badge: str
    current_version_id: UUID | None
    api_protocol: ModelApiProtocol
    context_window_tokens: int | None
    extension_options: dict[str, Any]
    test_status: Literal["untested", "running", "passed", "failed", "stale", "cancelled"]
    latest_test: ModelTestRunResponse | None
    created_at: datetime
    updated_at: datetime


class ModelEndpointResponse(BaseModel):
    id: UUID
    name: str
    logo_url: str | None
    provider_preset: str | None
    provider_kind: ModelProviderKind | None
    base_url: str | None
    is_enabled: bool
    read_only: bool
    active_version_id: UUID | None
    credential_masked_hint: str | None
    credential_revision: int | None
    models: list[ModelEndpointModelResponse] = Field(default_factory=list)
    used_agent_count: int = Field(
        default=0,
        ge=0,
        description="曾发布版本绑定当前模型的 Agent 数量。",
    )
    used_agent_names: list[str] = Field(
        default_factory=list,
        description="曾发布版本绑定当前模型的 Agent 名称，按名称排序。",
    )
    enable_blockers: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ModelEndpointListResponse(PaginatedListResponse):
    items: list[ModelEndpointResponse]


class ModelEndpointVersionListResponse(PaginatedListResponse):
    items: list[ModelEndpointVersionResponse]


class ModelTestRunListResponse(PaginatedListResponse):
    items: list[ModelTestRunResponse]


class ConnectionTestResponse(BaseModel):
    status: ModelVerificationStatus
    checks: dict[str, str]
    latency_ms: int | None
    provider_status: int | None
    error_code: str | None


class DashboardMetric(BaseModel):
    value: int | float | None
    previous_value: int | float | None = None
    change_percent: float | None = None
    available: bool = True


class AdminDashboardSummaryResponse(BaseModel):
    window_start: datetime
    window_end: datetime
    metrics: dict[str, DashboardMetric]
    status_counts: dict[str, int] = Field(default_factory=dict)
    service: dict[str, Any] = Field(default_factory=dict)


class DashboardTimeseriesPoint(BaseModel):
    bucket_start: datetime
    values: dict[str, int | float | None]


class AdminDashboardTimeseriesResponse(BaseModel):
    interval: str
    items: list[DashboardTimeseriesPoint]


class AdminDashboardRunItem(BaseModel):
    id: UUID
    agent_id: UUID
    agent_name: str
    user_name: str | None = None
    organization_unit_id: UUID | None = None
    department_name: str | None = None
    conversation_id: UUID
    status: RunStatus
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    input_tokens: int | None
    output_tokens: int | None
    total_cost_microusd: int | None
    end_to_end_latency_ms: int | None
    correlation_id: str


class AdminDashboardRunListResponse(PaginatedListResponse):
    items: list[AdminDashboardRunItem]


class AdminDashboardErrorItem(BaseModel):
    error_code: str
    count: int
    affected_users: int
    agent_count: int
    last_seen_at: datetime | None


class AdminDashboardErrorListResponse(BaseModel):
    items: list[AdminDashboardErrorItem]


class AdminDashboardErrorEventItem(BaseModel):
    id: UUID
    occurred_at: datetime
    severity: str
    error_code: str
    reason: str
    stage: str
    resolution_status: str
    agent_id: UUID
    agent_name: str | None
    user_id: UUID
    user_name: str | None
    conversation_id: UUID
    run_id: UUID
    correlation_id: str | None
    agent_version_number: int | None
    model_name: str | None
    retry_count: int | None
    latency_ms: int | None


class AdminDashboardErrorEventListResponse(PaginatedListResponse):
    items: list[AdminDashboardErrorEventItem]


class AdminDashboardTraceObservation(BaseModel):
    id: UUID
    kind: str
    name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    total_cost_microusd: int | None
    error_code: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdminDashboardErrorEventDetail(AdminDashboardErrorEventItem):
    metadata: dict[str, Any] = Field(default_factory=dict)
    observations: list[AdminDashboardTraceObservation] = Field(default_factory=list)


class AdminDashboardTraceResponse(BaseModel):
    run: AdminDashboardRunItem
    observations: list[AdminDashboardTraceObservation]


class SystemAgentStatusItem(BaseModel):
    id: UUID
    name: str
    lifecycle_status: str
    execution_status: str
    health_status: str
    health_reason: str
    last_run_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    active_run_count: int
    error_rate: float | None
    p95_latency_ms: int | None
    pending_publish: bool
    observed_at: datetime | None


class SystemAgentStatusListResponse(PaginatedListResponse):
    items: list[SystemAgentStatusItem]


class SystemAgentRankingItem(BaseModel):
    agent_id: UUID
    agent_name: str
    value: int | float | None


class SystemAgentRankingsResponse(BaseModel):
    rankings: dict[str, list[SystemAgentRankingItem]]
