from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from supportops_core.enums import (
    AgentStatus,
    AuditAction,
    AuditResourceType,
    ConversationStatus,
    GrantSubjectType,
    MessageRole,
    ModelApiProtocol,
    ModelProviderKind,
    ModelTestStatus,
    ModelVerificationStatus,
    RunStatus,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    """企业租户，是所有业务数据隔离的根边界。"""

    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenants_slug"),
        {"comment": "企业公司资料和全平台租户隔离根边界。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="租户唯一标识。")
    name: Mapped[str] = mapped_column(String(200), comment="租户显示名称。")
    slug: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="公司唯一短名，用于稳定识别租户。"
    )
    logo_url: Mapped[str | None] = mapped_column(
        String(2000), nullable=True, comment="公司 Logo 地址。"
    )
    contact_email: Mapped[str | None] = mapped_column(
        String(320), nullable=True, comment="公司主要联系邮箱。"
    )
    status: Mapped[str] = mapped_column(String(32), default="active", comment="租户状态。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="租户创建时间。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        comment="公司资料最近更新时间。",
    )


class User(Base):
    """可通过邮箱密码登录的租户系统用户。"""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        {"comment": "租户内本地登录用户、组织归属及可信角色。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="用户唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="用户所属租户。"
    )
    email: Mapped[str] = mapped_column(String(320), index=True, comment="规范化的小写登录邮箱。")
    password_hash: Mapped[str] = mapped_column(
        Text, comment="版本化 scrypt 密码哈希；不保存明文密码。"
    )
    organization_unit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization_units.id"),
        nullable=True,
        index=True,
        comment="用户所属部门；未分配部门时为空。",
    )
    display_name: Mapped[str] = mapped_column(String(200), comment="用户显示名称。")
    job_title: Mapped[str] = mapped_column(
        String(200), default="", comment="用户在公司内的职位名称。"
    )
    phone: Mapped[str] = mapped_column(String(64), default="", comment="用户联系电话。")
    roles: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=list,
        comment="来自可信身份上下文的角色列表。",
    )
    status: Mapped[str] = mapped_column(String(32), default="active", comment="用户状态。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="系统用户创建时间。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        comment="用户资料最近更新时间。",
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近一次密码登录成功时间。"
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近一次设置或重置密码时间。"
    )


class OrganizationUnit(Base):
    """租户内使用邻接表表达的部门节点。"""

    __tablename__ = "organization_units"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "parent_id", "name", name="uq_organization_units_sibling_name"
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_org_unit_not_self"),
        {"comment": "企业部门节点；父子关系形成租户内树，删除不得级联用户或子部门。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="部门唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="部门所属租户。"
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization_units.id"),
        nullable=True,
        index=True,
        comment="上级部门；顶级部门为空。",
    )
    name: Mapped[str] = mapped_column(String(200), comment="部门名称。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="部门创建时间。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        comment="部门最近更新时间。",
    )


class ModelCredential(Base):
    """模型端点当前使用的加密凭据指针和 revision。"""

    __tablename__ = "model_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "endpoint_id"),
        {"comment": "模型端点当前凭据；密钥只以加密密文保存且不可通过 API 读回。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="凭据唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="凭据所属租户。"
    )
    endpoint_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "model_endpoints.id",
            name="fk_model_credentials_endpoint_id",
            use_alter=True,
        ),
        index=True,
        comment="凭据所属的稳定模型端点。",
    )
    provider: Mapped[str] = mapped_column(String(64), comment="Secret Provider 类型。")
    encrypted_secret: Mapped[str] = mapped_column(
        Text, comment="由服务端主密钥封装后的模型 API Key 密文。"
    )
    masked_hint: Mapped[str] = mapped_column(String(32), comment="供管理员识别密钥的脱敏提示。")
    revision: Mapped[int] = mapped_column(Integer, default=1, comment="当前凭据修订号。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="凭据创建时间。"
    )
    rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="最近一次密钥轮换时间。"
    )


class ModelCredentialRevision(Base):
    """为已排队 Run 保留的不可变历史凭据密文。"""

    __tablename__ = "model_credential_revisions"
    __table_args__ = (
        UniqueConstraint("credential_id", "revision"),
        {"comment": "模型凭据不可变历史版本，确保 Run 可解析其固定 revision。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="历史凭据唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="历史凭据所属租户。"
    )
    credential_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_credentials.id"), index=True, comment="稳定凭据标识。"
    )
    revision: Mapped[int] = mapped_column(Integer, comment="不可变凭据修订号。")
    encrypted_secret: Mapped[str] = mapped_column(
        Text, comment="该 revision 对应的加密 API Key 密文。"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="历史凭据创建时间。"
    )


class ModelEndpoint(Base):
    """管理员可复用的稳定模型配置资源。"""

    __tablename__ = "model_endpoints"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        {"comment": "租户内稳定模型端点，资料与不可变调用版本分离管理。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="模型端点唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="模型端点所属租户。"
    )
    name: Mapped[str] = mapped_column(String(200), comment="模型端点显示名称。")
    logo_url: Mapped[str | None] = mapped_column(
        String(2000), nullable=True, comment="模型端点 Logo 地址。"
    )
    provider_preset: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="创建时选择的供应商预设标识；自定义配置为空。"
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        index=True,
        comment="当前模型连接是否允许 Agent 新绑定和使用。",
    )
    read_only: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否为仅用于历史引用的只读迁移记录。"
    )
    active_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "model_endpoint_versions.id",
            name="fk_model_endpoints_active_version_id",
            use_alter=True,
        ),
        nullable=True,
        comment="当前启用的不可变模型端点版本。",
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="创建该端点的用户。")
    updated_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), comment="最近修改端点资料的用户。"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="模型端点创建时间。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        comment="模型端点最近更新时间。",
    )


class ModelEndpointVersion(Base):
    """一次发布后不可变的模型协议、地址、能力和参数快照。"""

    __tablename__ = "model_endpoint_versions"
    __table_args__ = (
        UniqueConstraint("endpoint_id", "version_number"),
        {"comment": "模型端点不可变调用配置版本，供 AgentVersion 固定引用。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="模型版本唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="模型版本所属租户。"
    )
    endpoint_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_endpoints.id"), index=True, comment="所属稳定模型端点。"
    )
    endpoint_model_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "model_endpoint_models.id",
            name="fk_model_endpoint_versions_endpoint_model_id",
            use_alter=True,
        ),
        nullable=True,
        index=True,
        comment="所属稳定模型；历史单模型版本迁移时允许为空。",
    )
    version_number: Mapped[int] = mapped_column(Integer, comment="端点内单调递增的版本号。")
    provider_kind: Mapped[ModelProviderKind] = mapped_column(
        Enum(ModelProviderKind, native_enum=False, length=32), comment="模型供应商类型。"
    )
    api_protocol: Mapped[ModelApiProtocol] = mapped_column(
        Enum(ModelApiProtocol, native_enum=False, length=32), comment="调用模型使用的 API 协议。"
    )
    base_url: Mapped[str] = mapped_column(String(2000), comment="通过安全策略校验的 API Base URL。")
    remote_model_name: Mapped[str] = mapped_column(
        String(300), comment="供应商端点识别的远端模型名称。"
    )
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict, comment="声明并验证的模型能力。"
    )
    defaults: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict, comment="默认参数及模型限制。"
    )
    request_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
        comment="允许随请求发送的非秘密组织或项目元数据。",
    )
    pricing: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
        comment="用于成本预算的价格元数据。",
    )
    credential_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_credentials.id"), index=True, comment="运行时解析的稳定凭据标识。"
    )
    verification_status: Mapped[ModelVerificationStatus] = mapped_column(
        Enum(ModelVerificationStatus, native_enum=False, length=32),
        default=ModelVerificationStatus.UNTESTED,
        comment="最近一次连接及能力验证状态。",
    )
    verification_result: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
        comment="不含秘密和业务数据的分项验证结果。",
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近一次验证完成时间。"
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), comment="创建该不可变版本的用户。"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="模型端点版本创建时间。"
    )


class ModelEndpointModel(Base):
    """一套供应商连接下可被 Agent 独立选择的稳定模型。"""

    __tablename__ = "model_endpoint_models"
    __table_args__ = (
        UniqueConstraint("endpoint_id", "upstream_model_id"),
        {"comment": "模型端点中的稳定模型，保存展示信息并指向当前不可变调用版本。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="稳定模型唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="稳定模型所属租户。"
    )
    endpoint_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_endpoints.id"), index=True, comment="稳定模型所属连接端点。"
    )
    upstream_model_id: Mapped[str] = mapped_column(
        String(300), comment="发送给供应商的真实模型 ID。"
    )
    display_name: Mapped[str] = mapped_column(
        String(300), default="", comment="管理端和 Agent 选择器中的模型显示名称。"
    )
    badge: Mapped[str] = mapped_column(
        String(16), default="", comment="用于区分同名模型的短后缀或 Emoji。"
    )
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "model_endpoint_versions.id",
            name="fk_model_endpoint_models_current_version_id",
            use_alter=True,
        ),
        nullable=True,
        comment="该模型当前使用的不可变调用配置版本。",
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="从当前配置移除的时间；为空表示仍可选择。"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="稳定模型创建时间。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="稳定模型更新时间。"
    )


class ModelEndpointTestRun(Base):
    """单个模型一次不含业务数据的真实流式连通性测试。"""

    __tablename__ = "model_endpoint_test_runs"
    __table_args__ = (
        Index("ix_model_endpoint_test_runs_model_created", "endpoint_model_id", "created_at"),
        {"comment": "逐模型连通性测试历史、阶段耗时和脱敏诊断结果。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="测试运行唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="测试运行所属租户。"
    )
    endpoint_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_endpoints.id"), index=True, comment="被测试的模型连接端点。"
    )
    endpoint_model_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_endpoint_models.id"), index=True, comment="被测试的稳定模型。"
    )
    model_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_endpoint_versions.id"), index=True, comment="本次测试固定的模型配置版本。"
    )
    credential_revision: Mapped[int] = mapped_column(Integer, comment="本次测试使用的凭据修订号。")
    config_digest: Mapped[str] = mapped_column(
        String(64), comment="模型配置和凭据 revision 的稳定摘要。"
    )
    status: Mapped[ModelTestStatus] = mapped_column(
        Enum(ModelTestStatus, native_enum=False, length=32),
        default=ModelTestStatus.QUEUED,
        index=True,
        comment="测试执行状态。",
    )
    stage: Mapped[str] = mapped_column(
        String(32), default="queued", comment="页面当前展示的测试阶段。"
    )
    request_host: Mapped[str] = mapped_column(
        String(500), default="", comment="允许展示的目标主机名，不含凭据和路径参数。"
    )
    request_path: Mapped[str] = mapped_column(
        String(500), default="", comment="允许展示的协议请求路径。"
    )
    provider_status: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="供应商返回的 HTTP 状态码。"
    )
    response_headers_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="从发送请求到收到响应头的耗时。"
    )
    first_content_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="从发送请求到收到首个响应内容的耗时。"
    )
    total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="完整测试总耗时。")
    response_content: Mapped[str] = mapped_column(
        String(4000), default="", comment="模型对固定测试问题返回的截断文本，不包含请求凭据。"
    )
    milestones: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
        comment="各真实测试阶段的安全摘要。",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="稳定且不含供应商秘密的失败代码。"
    )
    error_message: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="截断并脱敏后的失败说明。"
    )
    correlation_id: Mapped[str] = mapped_column(
        String(100), index=True, comment="串联请求日志和审计事件的关联标识。"
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), comment="发起测试的平台管理员。"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="测试实际开始时间。"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="测试进入终态的时间。"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="测试记录创建时间。"
    )


class Agent(Base):
    """租户内稳定 Agent 身份及动态展示资料。"""

    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug"),
        {"comment": "企业 Agent 稳定身份、展示资料、生命周期状态及当前活动版本。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="Agent 唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="Agent 所属租户。"
    )
    slug: Mapped[str] = mapped_column(
        String(100), comment="租户内唯一、适合 URL 和审计的稳定短名。"
    )
    name: Mapped[str] = mapped_column(String(200), comment="员工界面展示的 Agent 名称。")
    logo_url: Mapped[str | None] = mapped_column(
        String(2000), nullable=True, comment="员工界面展示的 Agent Logo 地址。"
    )
    description: Mapped[str] = mapped_column(String(2000), default="", comment="Agent 能力描述。")
    welcome_message: Mapped[str] = mapped_column(
        String(2000), default="", comment="新建对话时展示的欢迎语。"
    )
    suggested_prompts: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list, comment="新建对话建议问题列表。"
    )
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, native_enum=False, length=32),
        default=AgentStatus.DRAFT,
        index=True,
        comment="Agent 生命周期状态。",
    )
    read_only: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否为仅用于历史会话的只读迁移 Agent。"
    )
    active_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "agent_versions.id",
            name="fk_agents_active_version_id",
            use_alter=True,
        ),
        nullable=True,
        comment="新会话使用的当前活动 AgentVersion。",
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="创建 Agent 的用户。")
    updated_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), comment="最近更新 Agent 资料或状态的用户。"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="Agent 创建时间。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="Agent 最近更新时间。"
    )


class AgentDraft(Base):
    """每个 Agent 唯一的可编辑运行配置草稿。"""

    __tablename__ = "agent_drafts"
    __table_args__ = {"comment": "Agent 当前可编辑配置，使用 revision 实现乐观并发控制。"}

    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id"), primary_key=True, comment="草稿所属 Agent，同时作为主键。"
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="草稿所属租户。"
    )
    revision: Mapped[int] = mapped_column(
        Integer, default=1, comment="每次成功保存后单调递增的乐观锁版本。"
    )
    schema_version: Mapped[str] = mapped_column(
        String(20), default="2", comment="Agent 配置 JSON Schema 版本。"
    )
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), comment="待校验和发布的声明式 Agent 配置。"
    )
    updated_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), comment="最近保存草稿的管理员。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="草稿最近保存时间。"
    )


class AgentVersion(Base):
    """发布后不可变、可回滚和可审计的 Agent 运行快照。"""

    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version_number"),
        {"comment": "不可变 Agent 运行配置版本，供 Conversation 和 Run 固定引用。"},
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, comment="Agent 版本唯一标识。"
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="Agent 版本所属租户。"
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id"), index=True, comment="所属稳定 Agent。"
    )
    version_number: Mapped[int] = mapped_column(Integer, comment="Agent 内单调递增的版本号。")
    schema_version: Mapped[str] = mapped_column(String(20), comment="配置快照的 Schema 版本。")
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), comment="发布时规范化后的完整运行配置快照。"
    )
    config_digest: Mapped[str] = mapped_column(
        String(64), comment="规范化配置的 SHA-256 摘要，用于一致性校验。"
    )
    model_endpoint_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_endpoint_versions.id"),
        index=True,
        comment="发布时固定的不可变模型端点版本。",
    )
    resolved_tool_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=list,
        comment="发布时解析并允许执行的工具标识列表。",
    )
    release_notes: Mapped[str] = mapped_column(
        String(2000), default="", comment="管理员填写的版本变更说明。"
    )
    published_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), comment="发布该版本的管理员。"
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="版本发布时间。"
    )


class ToolCatalogEntry(Base):
    """租户可管理的受控工具目录条目；实现仍由服务端注册表提供。"""

    __tablename__ = "tool_catalog_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "tool_id"),
        CheckConstraint("risk_level IN ('low', 'medium', 'high')", name="ck_tool_risk_level"),
        {"comment": "Agent 可绑定的受控工具目录；不允许上传或执行任意代码。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="工具目录条目 ID。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="工具目录所属租户。"
    )
    tool_id: Mapped[str] = mapped_column(String(100), comment="稳定工具标识。")
    name: Mapped[str] = mapped_column(String(200), comment="工具显示名称。")
    description: Mapped[str] = mapped_column(Text, comment="工具能力说明。")
    implementation_key: Mapped[str] = mapped_column(
        String(100), comment="服务端实现注册键，不接受客户端代码。"
    )
    required_roles: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list, comment="调用工具所需角色。"
    )
    risk_level: Mapped[str] = mapped_column(String(16), default="low", comment="工具风险级别。")
    version: Mapped[int] = mapped_column(Integer, default=1, comment="工具目录元数据版本。")
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, index=True, comment="是否允许新 Agent 绑定该工具。"
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="创建人。")
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="最近更新人。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="工具目录创建时间。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="工具目录最近更新时间。"
    )


class AgentAccessGrant(Base):
    """Agent 对用户或角色主体的显式使用授权。"""

    __tablename__ = "agent_access_grants"
    __table_args__ = (
        UniqueConstraint("agent_id", "subject_type", "subject_id"),
        CheckConstraint("length(subject_id) > 0", name="ck_agent_grant_subject_id"),
        {"comment": "租户内 Agent 使用授权；未命中任何授权时默认拒绝。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="授权记录唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="授权所属租户。"
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id"), index=True, comment="被授权使用的 Agent。"
    )
    subject_type: Mapped[GrantSubjectType] = mapped_column(
        Enum(GrantSubjectType, native_enum=False, length=32), comment="授权主体类型：用户或角色。"
    )
    subject_id: Mapped[str] = mapped_column(String(200), comment="用户 UUID 或规范角色标识。")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="创建授权的管理员。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="授权创建时间。"
    )


class AgentAuditEvent(Base):
    """模型和 Agent 控制面操作的追加写审计事件。"""

    __tablename__ = "agent_audit_events"
    __table_args__ = {"comment": "Agent 与模型控制面追加写审计，不保存密钥或完整 Prompt。"}

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
        comment="审计事件递增标识。",
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="审计事件所属租户。"
    )
    resource_type: Mapped[AuditResourceType] = mapped_column(
        Enum(AuditResourceType, native_enum=False, length=32), comment="被操作资源类型。"
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True, comment="相关 Agent；模型事件可为空。"
    )
    model_endpoint_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_endpoints.id"), nullable=True, comment="相关模型端点；Agent 事件可为空。"
    )
    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, native_enum=False, length=64), comment="规范化控制面动作。"
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), comment="执行操作的可信用户。"
    )
    version_id: Mapped[UUID | None] = mapped_column(
        nullable=True, comment="相关 AgentVersion 或 ModelEndpointVersion 标识。"
    )
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
        comment="不含秘密和完整 Prompt 的审计摘要。",
    )
    correlation_id: Mapped[str] = mapped_column(
        String(64), index=True, comment="串联请求、审计与运行日志的关联标识。"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="审计事件发生时间。"
    )


class Conversation(Base):
    """用户与单个固定 AgentVersion 的产品会话。"""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_tenant_user_created", "tenant_id", "user_id", "created_at"),
        Index(
            "ix_conversations_tenant_user_pinned_updated",
            "tenant_id",
            "user_id",
            "is_pinned",
            "updated_at",
        ),
        Index(
            "ix_conversations_tenant_user_agent_updated",
            "tenant_id",
            "user_id",
            "agent_id",
            "updated_at",
        ),
        {"comment": "用户可见会话；创建时固定 AgentVersion，产品消息是历史事实源。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="会话唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="会话所属租户。"
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), index=True, comment="会话所属用户。"
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id"), index=True, comment="会话使用的稳定 Agent。"
    )
    agent_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_versions.id"), index=True, comment="会话创建时固定的 AgentVersion。"
    )
    title: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="用户可修改的会话标题。"
    )
    is_pinned: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", comment="是否在最近会话中置顶。"
    )
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, native_enum=False, length=32),
        default=ConversationStatus.ACTIVE,
        comment="会话生命周期状态。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="会话创建时间。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="会话最近活动时间。"
    )
    # relationship 仅用于 API 聚合，不是额外数据库字段。
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", order_by="Message.created_at"
    )


class Message(Base):
    """用户可见会话消息，是聊天历史的产品事实记录。"""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "conversation_id", "idempotency_key"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        {"comment": "用户可见会话消息；通过幂等键防止重复提交。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="消息唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="消息所属租户。"
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id"), index=True, comment="消息所属会话。"
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), index=True, comment="消息归属用户；助手消息沿用会话用户。"
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, native_enum=False, length=32), comment="消息角色。"
    )
    content: Mapped[str] = mapped_column(Text, comment="用户可见消息正文。")
    content_hash: Mapped[str] = mapped_column(
        String(64), comment="消息正文 SHA-256 摘要，用于幂等冲突检测。"
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(200), comment="客户端或 Worker 提供的幂等键。"
    )
    correlation_id: Mapped[str] = mapped_column(
        String(64), index=True, comment="关联消息请求、Run 和日志的标识。"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="消息创建时间。"
    )
    # 反向关系帮助按会话加载消息，不生成独立列。
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class AgentRun(Base):
    """一次固定 Agent/模型版本的异步执行记录。"""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_tenant_conversation", "tenant_id", "conversation_id"),
        {"comment": "Agent 单轮执行记录，固定身份、配置、模型和凭据 revision。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="Run 唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="Run 所属租户。"
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id"), index=True, comment="触发 Run 的会话。"
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id"), index=True, comment="执行使用的稳定 Agent。"
    )
    agent_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_versions.id"), index=True, comment="执行固定的 AgentVersion。"
    )
    model_endpoint_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_endpoint_versions.id"), index=True, comment="执行固定的模型端点版本。"
    )
    credential_revision: Mapped[int] = mapped_column(
        Integer, comment="执行固定使用的模型凭据 revision。"
    )
    config_digest: Mapped[str] = mapped_column(
        String(64), comment="执行配置摘要，用于检测版本引用漂移。"
    )
    identity_roles: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=list,
        comment="创建 Run 时固定的可信身份角色。",
    )
    input_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id"), unique=True, comment="触发本次 Run 的唯一用户消息。"
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, length=32),
        default=RunStatus.QUEUED,
        index=True,
        comment="Run 生命周期状态。",
    )
    correlation_id: Mapped[str] = mapped_column(
        String(64), index=True, comment="关联请求、事件和日志的标识。"
    )
    error_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="失败终态的稳定脱敏错误码。"
    )
    input_tokens: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="供应商或适配器报告的输入 Token；未知时为空。"
    )
    output_tokens: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="供应商或适配器报告的输出 Token；未知时为空。"
    )
    cached_input_tokens: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="缓存输入 Token 明细；未知时为空。"
    )
    reasoning_tokens: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="推理 Token 明细；未知时为空。"
    )
    total_cost_microusd: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="本次运行成本，单位为微美元；未知时为空。"
    )
    cost_source: Mapped[str | None] = mapped_column(
        String(24), nullable=True, comment="成本来源：provider、calculated 或 unknown。"
    )
    model_call_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="本次运行模型调用次数。"
    )
    tool_call_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="本次运行工具调用次数。"
    )
    retry_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="本次运行重试次数。"
    )
    queue_latency_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="从入队到 Worker 开始的耗时。"
    )
    execution_latency_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="从 Worker 开始到终态的耗时。"
    )
    end_to_end_latency_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="从入队到终态的端到端耗时。"
    )
    time_to_first_token_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="真实首个输出事件耗时；非流式运行保持为空。"
    )
    finish_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="模型完成原因。"
    )
    provider_request_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="供应商请求 ID；不一定由供应商提供。"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="Run 创建并入队的时间。"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Worker 成功领取 Run 的时间。"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Run 进入终态的时间。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="Run 最近更新时间。"
    )


class AgentErrorEvent(Base):
    """一次可追踪的 Agent 失败事件；展示名称通过查询时关联业务表获取。"""

    __tablename__ = "agent_error_events"
    __table_args__ = (
        Index("ix_agent_error_events_tenant_occurred", "tenant_id", "occurred_at"),
        Index("ix_agent_error_events_agent_occurred", "agent_id", "occurred_at"),
        Index(
            "ix_agent_error_events_tenant_severity_occurred", "tenant_id", "severity", "occurred_at"
        ),
        Index(
            "ix_agent_error_events_tenant_code_occurred", "tenant_id", "error_code", "occurred_at"
        ),
        {"comment": "Agent 运行失败事件，保存脱敏上下文供管理员分析。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="错误事件唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="事件所属租户。"
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id"), index=True, comment="关联的 Agent Run。"
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id"), index=True, comment="关联会话。"
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id"), index=True, comment="失败的 Agent。"
    )
    agent_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_versions.id"), index=True, comment="运行固定的 Agent 版本。"
    )
    model_endpoint_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_endpoint_versions.id"), index=True, comment="运行固定的模型版本。"
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), index=True, comment="受影响的用户。"
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, comment="错误发生时间。"
    )
    severity: Mapped[str] = mapped_column(
        String(16), default="error", index=True, comment="info、warning、error 或 critical。"
    )
    error_code: Mapped[str] = mapped_column(String(100), index=True, comment="稳定脱敏错误码。")
    reason: Mapped[str] = mapped_column(
        String(1000), default="", comment="面向管理员的脱敏错误原因。"
    )
    stage: Mapped[str] = mapped_column(
        String(64), default="agent.execute", comment="发生错误的运行阶段。"
    )
    resolution_status: Mapped[str] = mapped_column(
        String(32),
        default="unresolved",
        index=True,
        comment="unresolved、acknowledged 或 resolved。",
    )
    retry_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="失败前已发生的重试次数。"
    )
    latency_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="失败时累计端到端耗时。"
    )
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
        comment="白名单脱敏运行上下文。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="事件写入时间。"
    )


class RunEvent(Base):
    """供 SSE 消费的 Run 内追加写有序事件。"""

    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        Index("ix_run_events_run_sequence", "run_id", "sequence"),
        {"comment": "Run 生命周期和流式输出的追加写事件流。"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
        comment="事件全局递增标识，兼作 SSE event ID。",
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="事件所属租户。"
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id"), index=True, comment="事件所属 Run。"
    )
    sequence: Mapped[int] = mapped_column(Integer, comment="Run 内从 1 开始单调递增的事件序号。")
    event_type: Mapped[str] = mapped_column(String(100), comment="事件类型。")
    data: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), comment="脱敏后的事件负载。"
    )
    correlation_id: Mapped[str] = mapped_column(
        String(64), index=True, comment="关联请求、Run 与日志的标识。"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="事件创建时间。"
    )


class AgentRunObservation(Base):
    """一次 AgentRun 内的 Agent、模型、工具或检索观测步骤。"""

    __tablename__ = "agent_run_observations"
    __table_args__ = (
        Index("ix_agent_run_observations_tenant_started", "tenant_id", "started_at"),
        Index("ix_agent_run_observations_run_started", "run_id", "started_at"),
        Index("ix_agent_run_observations_tenant_kind_started", "tenant_id", "kind", "started_at"),
        {"comment": "Agent 运行 Trace 明细；默认只保存结构化脱敏元数据。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="观测步骤唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="观测所属租户。"
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id"), index=True, comment="观测所属 AgentRun。"
    )
    parent_observation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_run_observations.id"), nullable=True, comment="父级观测步骤。"
    )
    trace_id: Mapped[str] = mapped_column(String(100), index=True, comment="Trace 关联标识。")
    span_id: Mapped[str] = mapped_column(String(100), index=True, comment="Span 关联标识。")
    kind: Mapped[str] = mapped_column(String(32), comment="agent、llm、tool 或 retrieval。")
    name: Mapped[str] = mapped_column(String(200), comment="观测步骤名称。")
    status: Mapped[str] = mapped_column(String(32), comment="running、completed 或 failed。")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="步骤开始时间。")
    first_output_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="首个输出时间。"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="步骤结束时间。"
    )
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="步骤耗时。")
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="模型供应商。")
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_endpoint_versions.id"), nullable=True, comment="模型版本。"
    )
    tool_id: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="工具标识。")
    input_tokens: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="该步骤输入 Token。"
    )
    output_tokens: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="该步骤输出 Token。"
    )
    total_cost_microusd: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="该步骤成本，单位微美元。"
    )
    error_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="该步骤脱敏错误码。"
    )
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
        comment="经过白名单限制的结构化 metadata，不保存完整 Prompt 或回答。",
    )


class AgentRuntimeHealth(Base):
    """Agent 运行健康的可重复计算读模型。"""

    __tablename__ = "agent_runtime_health"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id"),
        Index("ix_agent_runtime_health_tenant_status", "tenant_id", "health_status"),
        Index("ix_agent_runtime_health_tenant_observed", "tenant_id", "observed_at"),
        {"comment": "由 AgentRun 和执行服务事实派生的 Agent 健康快照。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="健康快照唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="所属租户。"
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id"), index=True, comment="所属 Agent。"
    )
    health_status: Mapped[str] = mapped_column(
        String(32),
        default="unknown",
        index=True,
        comment="healthy、degraded、no_recent_activity 或 unknown。",
    )
    health_reason: Mapped[str] = mapped_column(
        String(500), default="", comment="面向管理员的健康状态原因。"
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近一次运行时间。"
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近一次成功时间。"
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近一次失败时间。"
    )
    active_run_count: Mapped[int] = mapped_column(
        Integer, default=0, comment="当前排队或运行中的 Run 数。"
    )
    error_rate: Mapped[float | None] = mapped_column(
        nullable=True, comment="统计窗口内失败率百分比。"
    )
    p95_latency_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="统计窗口内端到端 P95 耗时。"
    )
    pending_publish: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="当前配置是否有待发布变更。"
    )
    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="健康统计窗口开始。"
    )
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="健康统计窗口结束。"
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="健康快照计算时间。"
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="底层事实被观察到的时间。"
    )


class RuntimeServiceLease(Base):
    """共享 Worker 执行服务实例的全局心跳租约。"""

    __tablename__ = "runtime_service_leases"
    __table_args__ = (
        UniqueConstraint("instance_id"),
        Index("ix_runtime_service_leases_status_expires", "service_status", "expires_at"),
        {"comment": "跨租户共享 Worker 实例的心跳、队列和服务状态。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="服务租约唯一标识。")
    instance_id: Mapped[str] = mapped_column(String(100), comment="Worker 实例稳定标识。")
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="最近一次 Worker 心跳时间。"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), comment="超过该时间未续租即视为过期。"
    )
    queue_depth: Mapped[int] = mapped_column(Integer, default=0, comment="共享执行队列当前积压数。")
    active_run_count: Mapped[int] = mapped_column(
        Integer, default=0, comment="该实例当前执行中的 Run 数。"
    )
    service_status: Mapped[str] = mapped_column(
        String(32), default="healthy", index=True, comment="healthy、degraded 或 stopped。"
    )
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
        comment="服务实例的脱敏结构化元数据。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="租约创建时间。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="租约最近更新时间。"
    )
