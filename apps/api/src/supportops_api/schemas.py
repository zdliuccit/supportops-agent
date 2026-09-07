from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from supportops_core.agent_config import AgentConfigV2
from supportops_core.enums import (
    AgentStatus,
    ConversationStatus,
    GrantSubjectType,
    MessageRole,
    ModelApiProtocol,
    ModelEndpointStatus,
    ModelProviderKind,
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

    model_config = ConfigDict(str_strip_whitespace=True)

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
    user_count: int = Field(
        ge=0, description="当前部门及全部后代部门的用户总数量。"
    )
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
    created_at: datetime
    updated_at: datetime


class AdminAgentListResponse(PaginatedListResponse):
    items: list[AdminAgentResponse]


class AgentDraftResponse(BaseModel):
    agent_id: UUID
    revision: int
    schema_version: str
    config: dict[str, Any]
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


class ModelEndpointCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    logo_url: str | None = Field(default=None, max_length=2000)
    api_key: str = Field(min_length=1, max_length=20_000)
    version: ModelEndpointVersionConfig


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


class ModelEndpointResponse(BaseModel):
    id: UUID
    name: str
    logo_url: str | None
    status: ModelEndpointStatus
    read_only: bool
    active_version_id: UUID | None
    credential_masked_hint: str | None
    credential_revision: int | None
    created_at: datetime
    updated_at: datetime


class ModelEndpointListResponse(PaginatedListResponse):
    items: list[ModelEndpointResponse]


class ModelEndpointVersionListResponse(PaginatedListResponse):
    items: list[ModelEndpointVersionResponse]


class ConnectionTestResponse(BaseModel):
    status: ModelVerificationStatus
    checks: dict[str, str]
    latency_ms: int | None
    provider_status: int | None
    error_code: str | None
