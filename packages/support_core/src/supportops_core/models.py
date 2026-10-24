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
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from supportops_core.enums import (
    AgentStatus,
    AuditAction,
    AuditResourceType,
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


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SoftDeleteMixin:
    """可删除业务资源统一保留删除时间，关联历史仍通过外键完整保存。"""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="软删除时间；为空表示未删除。"
    )


def active_unique_index(name: str, *columns: str) -> Index:
    """唯一性只约束未删除记录，允许删除后重新创建同名资源。"""
    condition = "deleted_at IS NULL"
    return Index(
        name,
        *columns,
        unique=True,
        postgresql_where=text(condition),
        sqlite_where=text(condition),
    )


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
    auth_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="登录许可代次；安全身份变化原子递增，旧会话不复活。")


class AuthSession(Base):
    """JWT 只携带会话引用，不存令牌、密码、IP 或完整设备标识。"""
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("auth_generation >= 0", name="ck_auth_sessions_generation"),
        Index("ix_auth_sessions_owner_created", "tenant_id", "user_id", "created_at"),
        {"comment": "短期 Bearer 登录会话，可撤销，无刷新令牌或自动身份预配。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="登录会话 UUID，对应 JWT jti。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="固定租户。")
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="固定登录用户。")
    auth_generation: Mapped[int] = mapped_column(Integer, comment="签发时用户登录许可代次。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="登录创建时间。")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="服务器固定过期时间，不续期。")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="永久撤销时间。")
    revoked_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", name="fk_auth_session_revoker"), nullable=True, comment="实际撤销人员。")
    revocation_code: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="固定撤销原因，不记录用户自由文本。")


class OrganizationUnit(SoftDeleteMixin, Base):
    """租户内使用邻接表表达的部门节点。"""

    __tablename__ = "organization_units"
    __table_args__ = (
        active_unique_index("uq_organization_units_sibling_name", "tenant_id", "parent_id", "name"),
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


class ModelCredential(SoftDeleteMixin, Base):
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
    # 普通实体查询不读取密文；误读未加载属性时拒绝，不隐式补查秘密。
    encrypted_secret: Mapped[str] = mapped_column(
        Text, deferred=True, deferred_raiseload=True,
        comment="由服务端主密钥封装后的模型 API Key 密文。"
    )
    masked_hint: Mapped[str] = mapped_column(String(32), comment="供管理员识别密钥的脱敏提示。")
    revision: Mapped[int] = mapped_column(Integer, default=1, comment="当前凭据修订号。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="凭据创建时间。"
    )
    rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="最近一次密钥轮换时间。"
    )


class ModelCredentialRevision(SoftDeleteMixin, Base):
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
    # 解封只能走受控显式列查询；固定历史元数据读取不附带密文。
    encrypted_secret: Mapped[str] = mapped_column(
        Text, deferred=True, deferred_raiseload=True,
        comment="该 revision 对应的加密 API Key 密文。"
    )
    provider: Mapped[str] = mapped_column(String(64), default="local_envelope_v1", comment="该历史 revision 的实际密封 provider，不随轮换改写。")
    masked_hint: Mapped[str] = mapped_column(String(32), default="••••", comment="该 revision 的脱敏提示；旧未知提示不补造。")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="永久撤销时间；轮换不复活此 revision。")
    revoked_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", name="fk_credential_revision_revoked_by"), nullable=True, comment="当前租户实际撤销管理员。")
    revocation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="脱敏撤销说明，仅控制面可读。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="历史凭据创建时间。"
    )


class ModelEndpoint(SoftDeleteMixin, Base):
    """管理员可复用的稳定模型配置资源。"""

    __tablename__ = "model_endpoints"
    __table_args__ = (
        active_unique_index("uq_model_endpoints_tenant_name", "tenant_id", "name"),
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


class ModelEndpointVersion(SoftDeleteMixin, Base):
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


class ModelEndpointModel(SoftDeleteMixin, Base):
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


class ModelEndpointTestRun(SoftDeleteMixin, Base):
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
        CheckConstraint("(release_manifest IS NULL AND release_manifest_digest IS NULL) OR (release_manifest IS NOT NULL AND release_manifest_digest IS NOT NULL)",
            name="ck_agent_versions_manifest_pair"),
        CheckConstraint("(withdrawn_at IS NULL AND withdrawn_by IS NULL) OR (withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL)",
            name="ck_agent_version_withdrawal_pair"),
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
    resolved_tool_versions: Mapped[dict[str, int]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict, comment="发布时固定的服务端工具实现版本，不随目录元数据升级。",
    )
    release_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
        nullable=True, comment="发布时服务器生成的版本/源码/策略摘要；无秘密或正文，旧版本为空。")
    release_manifest_digest: Mapped[str | None] = mapped_column(String(64), nullable=True,
        comment="发布清单 SHA-256；不等于评测通过或执行授权。")
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True,
        comment="回滚后撤回该版本的新激活/新写动作时间；保留历史与只读对账，不解除。")
    withdrawn_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", name="fk_agent_version_withdrawal_actor"), nullable=True,
        comment="执行回滚并撤回该版本的管理员；与撤回时间成对保存。")
    release_notes: Mapped[str] = mapped_column(
        String(2000), default="", comment="管理员填写的版本变更说明。"
    )
    published_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), comment="发布该版本的管理员。"
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="版本发布时间。"
    )


class AgentReleaseEvaluation(Base):
    """可信离线执行器追加的有界评测事实；管理员不能手填通过状态。"""

    __tablename__ = "agent_release_evaluations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "execution_id", name="uq_release_evaluation_execution"),
        CheckConstraint("completed_at >= started_at AND recorded_at >= completed_at", name="ck_release_evaluation_times"),
        Index("ix_release_evaluation_latest", "tenant_id", "agent_id", "agent_version_id", "recorded_at", "id"),
        {"comment": "追加式发布评测事实；固定版本、当前范围、批准评测策略和结果摘要，无业务正文或秘密。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="评测事实唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="评测所属租户。")
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("agents.id"), comment="评测所属 Agent。")
    agent_version_id: Mapped[UUID] = mapped_column(ForeignKey("agent_versions.id"), comment="被评测的固定 Agent 版本。")
    execution_id: Mapped[UUID] = mapped_column(comment="可信执行器物理执行 ID；重复提交不得覆盖事实。")
    manifest_digest: Mapped[str] = mapped_column(String(64), comment="被评测固定发布清单 SHA-256。")
    scope_digest: Mapped[str] = mapped_column(String(64), comment="执行时模型/工具/源码/权限/知识/凭据范围摘要。")
    policy_digest: Mapped[str] = mapped_column(String(64), comment="服务器批准评测集/负载/执行器/门槛与环境的策略摘要。")
    report: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="严格版本化计数和完整性指标；不存答案、工具正文或任意链接。")
    report_digest: Mapped[str] = mapped_column(String(64), comment="规范化评测报告 SHA-256；读取时复检。")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="可信执行器实际开始时间。")
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="可信执行器实际完成时间。")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="服务器追加事实时间；不由客户端填入。")


class AgentReleaseRollout(Base):
    """固定两版本及主体范围的灰度；变更只调整比例、阶段与当前观察轮次。"""
    __tablename__ = "agent_release_rollouts"
    __table_args__ = (
        Index("uq_release_rollout_live", "tenant_id", "agent_id", unique=True,
            postgresql_where=text("status IN ('draft', 'active', 'paused')"),
            sqlite_where=text("status IN ('draft', 'active', 'paused')")),
        Index("ix_release_rollout_scan", "status", "last_checked_at", "id"),
        CheckConstraint("status IN ('draft', 'active', 'paused', 'stopped')", name="ck_release_rollout_status"),
        CheckConstraint("candidate_bps BETWEEN 1 AND 9000 AND revision >= 1 AND epoch >= 0", name="ck_release_rollout_bounds"),
        CheckConstraint("baseline_version_id <> candidate_version_id", name="ck_release_rollout_versions"),
        CheckConstraint("status NOT IN ('active', 'paused') OR (epoch >= 1 AND epoch_started_at IS NOT NULL AND last_checked_at IS NOT NULL)", name="ck_release_rollout_started"),
        {"comment": "固定范围灰度控制面，未批准策略或缺实测不能启动；不原地修改旧会话。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="灰度ID，同时作为稳定主体哈希的分组种子。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="灰度所属租户。")
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("agents.id"), comment="灰度所属Agent。")
    baseline_version_id: Mapped[UUID] = mapped_column(ForeignKey("agent_versions.id", name="fk_rollout_baseline"), comment="创建时固定的活动基线版本。")
    candidate_version_id: Mapped[UUID] = mapped_column(ForeignKey("agent_versions.id", name="fk_rollout_candidate"), comment="拟灰度且必须通过完整门禁的候选版本。")
    subject_ids: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=list, comment="显式主体UUID集合；空集合表示当前获准使用该Agent的租户主体。")
    policy: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="不可变的有界观察窗口、样本、失败率、费用和延迟门槛。")
    policy_digest: Mapped[str] = mapped_column(String(64), comment="规范化策略摘要，必须匹配服务器批准配置。")
    definition_digest: Mapped[str] = mapped_column(String(64), comment="固定租户/Agent/两版本/主体/策略的定义摘要。")
    status: Mapped[str] = mapped_column(String(16), default="draft", comment="draft/active/paused/stopped；停止后不能复活。")
    candidate_bps: Mapped[int] = mapped_column(Integer, comment="候选主体比例，单位万分之一；初始/每次扩大最多1000，最高9000保留基线组。")
    revision: Mapped[int] = mapped_column(Integer, default=1, comment="每次控制面或系统暂停单调递增，防陈旧操作。")
    epoch: Mapped[int] = mapped_column(Integer, default=0, comment="启动/恢复/扩大时递增的观察轮次，不重分配既有会话。")
    epoch_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="当前轮次开始时间，观察从此刻至读取时的服务器时间。")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="Worker最后成功复检时间；过期时新候选流量关闭。")
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="服务器固定暂停/停止原因码，不保存自由文本或正文。")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", name="fk_rollout_creator"), comment="创建策略草稿的管理员。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="灰度草稿创建时间。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="最近控制或系统暂停时间。")


class AgentReleaseRolloutEvent(Base):
    """追加灰度事件，系统巡检不冒充人工操作。"""
    __tablename__ = "agent_release_rollout_events"
    __table_args__ = (
        UniqueConstraint("rollout_id", "revision", name="uq_rollout_event_revision"),
        Index("ix_rollout_event_history", "tenant_id", "rollout_id", "created_at", "id"),
        CheckConstraint("(actor_kind = 'system' AND actor_user_id IS NULL) OR (actor_kind = 'user' AND actor_user_id IS NOT NULL)", name="ck_rollout_event_actor"),
        {"comment": "追加式灰度控制/暂停/停止事件；只保存ID、比例、轮次、固定原因与策略摘要。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="灰度事件ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="事件所属租户。")
    rollout_id: Mapped[UUID] = mapped_column(ForeignKey("agent_release_rollouts.id"), comment="关联固定灰度。")
    revision: Mapped[int] = mapped_column(Integer, comment="该次变更后的控制面版本。")
    actor_kind: Mapped[str] = mapped_column(String(8), comment="user或system；系统暂停不伪造人工决定。")
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="实际人工操作者；系统巡检为空。")
    event_type: Mapped[str] = mapped_column(String(32), comment="created/started/resumed/expanded/paused/stopped/activation_closed。")
    status: Mapped[str] = mapped_column(String(16), comment="事件发生后的灰度状态。")
    candidate_bps: Mapped[int] = mapped_column(Integer, comment="事件发生后的候选主体比例。")
    epoch: Mapped[int] = mapped_column(Integer, comment="事件对应观察轮次。")
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="固定暂停或停止原因。")
    policy_digest: Mapped[str] = mapped_column(String(64), comment="批准策略摘要。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="追加事实服务器时间。")


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
        Boolean, default=True, index=True, comment="是否允许绑定及后续执行，包括历史 Agent 版本。"
    )
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", comment="独立紧急撤销；普通重新启用不会解除。")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="创建人。")
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="最近更新人。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="工具目录创建时间。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="工具目录最近更新时间。"
    )


class ToolDiscoveryRevision(Base):
    """追加式来源批准、目录固定版本与listChanged围栏；不是执行注册表。"""

    __tablename__ = "tool_discovery_revisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_key", "revision", name="uq_tool_discovery_revision"),
        CheckConstraint("revision = parent_revision + 1 AND parent_revision >= 0", name="ck_tool_discovery_revision"),
        CheckConstraint("status IN ('pending','ready','disabled','rejected')", name="ck_tool_discovery_status"),
        CheckConstraint("observed_count BETWEEN 0 AND 100", name="ck_tool_discovery_count"),
        CheckConstraint("(status = 'ready' AND catalog IS NOT NULL AND catalog_digest IS NOT NULL) OR "
            "(status <> 'ready' AND catalog IS NULL AND catalog_digest IS NULL)", name="ck_tool_discovery_catalog_pair"),
        CheckConstraint("(status = 'rejected' AND failure_code IS NOT NULL) OR "
            "(status <> 'rejected' AND failure_code IS NULL)", name="ck_tool_discovery_failure_pair"),
        Index("ix_tool_discovery_latest", "tenant_id", "source_key", "revision", "id"),
        {"comment": "追加式工具来源策略/目录快照/失效事件；只有当前获批快照可解析，历史保留。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="不可变发现修订ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="服务端身份固定租户。")
    source_key: Mapped[str] = mapped_column(String(64), comment="批准来源的稳定别名，不是URL或执行命令。")
    revision: Mapped[int] = mapped_column(Integer, comment="来源单调修订/失效围栏。")
    parent_revision: Mapped[int] = mapped_column(Integer, comment="直接前驱修订，首次为0。")
    event_kind: Mapped[str] = mapped_column(String(32), comment="配置、listChanged、手工刷新或目录结果。")
    status: Mapped[str] = mapped_column(String(16), comment="pending/ready/disabled/rejected，过期在读取时判断。")
    policy: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="封闭来源指纹/工具摘要allowlist/期限，不含地址或凭据。")
    policy_digest: Mapped[str] = mapped_column(String(64), comment="固定租户/来源和批准策略摘要。")
    # 保留JSON数字表示，避免JSONB归一化数字后改变固定目录摘要。
    catalog: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True, comment="仅获批工具的封闭描述与稳定别名，不是执行许可。")
    catalog_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="固定来源/批准策略/目录完整摘要。")
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="固定拒绝码，不含远端诊断。")
    observed_count: Mapped[int] = mapped_column(Integer, default=0, comment="本页观察工具数，不保留未知工具描述。")
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="当前显式管理员操作人。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="服务器追加时间。")


class ToolConnectionProbeJob(Base):
    """独立只读连接探测任务，不承载Agent执行或业务凭据。"""

    __tablename__ = "tool_connection_probe_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','passed','failed','cancelled')", name="ck_tool_connection_job_status"),
        CheckConstraint("generation >= 0 AND revision >= 1", name="ck_tool_connection_job_revision"),
        CheckConstraint("(status = 'running' AND owner_id IS NOT NULL AND lease_until IS NOT NULL) OR (status <> 'running' AND owner_id IS NULL AND lease_until IS NULL)", name="ck_tool_connection_job_lease"),
        CheckConstraint("(status = 'passed' AND result_digest IS NOT NULL) OR (status <> 'passed' AND result_digest IS NULL)", name="ck_tool_connection_job_result"),
        Index("uq_tool_connection_active_job", "tenant_id", "source_key", unique=True,
            postgresql_where=text("status IN ('queued','running')"), sqlite_where=text("status IN ('queued','running')")),
        {"comment": "隔离Mock只读连接探测的一次派发/租约/结果元数据，不含秘密或业务正文。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="探测任务ID兼作派发随机标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="可信租户。")
    source_key: Mapped[str] = mapped_column(String(64), comment="固定来源别名。")
    source_record_id: Mapped[UUID] = mapped_column(ForeignKey("tool_discovery_revisions.id"), comment="创建时固定目录修订。")
    policy_digest: Mapped[str] = mapped_column(String(64), comment="创建时批准策略摘要。")
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="发起管理员。")
    auth_session_id: Mapped[UUID] = mapped_column(ForeignKey("auth_sessions.id"), comment="发起时有效会话引用，不是令牌。")
    auth_generation: Mapped[int] = mapped_column(Integer, comment="固定登录许可代次。")
    status: Mapped[str] = mapped_column(String(16), default="queued", comment="queued/running/passed/failed/cancelled。")
    revision: Mapped[int] = mapped_column(Integer, default=1, comment="任务并发修订。")
    generation: Mapped[int] = mapped_column(Integer, default=0, comment="领取或终止围栏代次。")
    owner_id: Mapped[UUID | None] = mapped_column(nullable=True, comment="独立执行者许可。")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="当前执行租约。")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="固定任务截止，不续期。")
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="已复核规范报告摘要，不存正文。")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="固定失败/取消码。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="服务器创建时间。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="服务器最后状态时间。")


class ToolConnectionEvent(Base):
    """连接探测、人工熔断及恢复的追加事实。"""

    __tablename__ = "tool_connection_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_key", "revision", name="uq_tool_connection_event_revision"),
        CheckConstraint("revision = parent_revision + 1 AND parent_revision >= 0", name="ck_tool_connection_event_revision"),
        CheckConstraint("circuit_state IN ('closed','open','half_open') AND health_state IN ('unknown','healthy','unhealthy')", name="ck_tool_connection_event_state"),
        CheckConstraint("failure_count BETWEEN 0 AND 3", name="ck_tool_connection_event_failures"),
        CheckConstraint("(health_state = 'healthy' AND checked_at IS NOT NULL AND valid_until IS NOT NULL AND circuit_state = 'closed') OR (health_state <> 'healthy' AND checked_at IS NULL AND valid_until IS NULL)", name="ck_tool_connection_event_health"),
        Index("ix_tool_connection_event_latest", "tenant_id", "source_key", "revision"),
        {"comment": "追加式隔离连接状态；只读健康不能授予业务执行许可。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="连接事件ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="可信租户。")
    source_key: Mapped[str] = mapped_column(String(64), comment="固定来源。")
    source_record_id: Mapped[UUID] = mapped_column(ForeignKey("tool_discovery_revisions.id"), comment="本事件对应当前目录修订。")
    policy_digest: Mapped[str] = mapped_column(String(64), comment="固定批准策略摘要。")
    revision: Mapped[int] = mapped_column(Integer, comment="来源连接单调修订。")
    parent_revision: Mapped[int] = mapped_column(Integer, comment="直接前驱，首次为0。")
    kind: Mapped[str] = mapped_column(String(32), comment="manual_open/probe_queued/probe_passed/probe_failed/probe_cancelled。")
    circuit_state: Mapped[str] = mapped_column(String(16), comment="closed/open/half_open。")
    health_state: Mapped[str] = mapped_column(String(16), comment="unknown/healthy/unhealthy。")
    failure_count: Mapped[int] = mapped_column(Integer, default=0, comment="当前范围连续失败数，上限3。")
    probe_job_id: Mapped[UUID | None] = mapped_column(ForeignKey("tool_connection_probe_jobs.id"), nullable=True, comment="关联实际探测任务，人工熔断为空。")
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="发起或任务原管理员。")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="服务器确认只读探测结果时间。")
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最迟60秒且不超过来源批准期限。")
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="人工恢复最早时间。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="服务器追加时间。")


class HttpMcpDispatch(Base):
    """网关固定范围、broker一次领取及令牌元数据；不保存访问/刷新令牌。"""

    __tablename__ = "http_mcp_dispatches"
    __table_args__ = (
        UniqueConstraint("invocation_id", name="uq_http_mcp_dispatch_invocation"),
        UniqueConstraint("lease_id", name="uq_http_mcp_dispatch_lease"),
        CheckConstraint("status IN ('authorized','claimed','bound','blocked','revoked')", name="ck_http_mcp_dispatch_status"),
        CheckConstraint("revision >= 1 AND generation >= 1 AND executor_uid = 10004", name="ck_http_mcp_dispatch_revision"),
        CheckConstraint("(status = 'authorized' AND claim_id IS NULL AND claimed_at IS NULL) OR (status <> 'authorized' AND claim_id IS NOT NULL AND claimed_at IS NOT NULL)", name="ck_http_mcp_dispatch_claim"),
        CheckConstraint("(status IN ('bound','revoked') AND token_state IS NOT NULL AND token_state_digest IS NOT NULL AND lease_id IS NOT NULL) OR (status IN ('authorized','claimed','blocked') AND token_state IS NULL AND token_state_digest IS NULL AND lease_id IS NULL)", name="ck_http_mcp_dispatch_token"),
        CheckConstraint("expires_at > created_at", name="ck_http_mcp_dispatch_expiry"),
        CheckConstraint("(status = 'revoked' AND revoked_at IS NOT NULL) OR (status <> 'revoked' AND revoked_at IS NULL)", name="ck_http_mcp_dispatch_revoked"),
        Index("ix_http_mcp_dispatch_scope", "tenant_id", "actor_user_id", "created_at"),
        {"comment": "HTTP MCP一次派发和不可替换令牌元数据；未知取密不重领，不含令牌。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, comment="网关随机派发ID，单独不构成许可。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="可信租户。")
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="原调用主体。")
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), comment="固定Run。")
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("run_attempts.id"), comment="固定尝试。")
    invocation_id: Mapped[UUID] = mapped_column(ForeignKey("tool_invocations.id"), comment="唯一原工具许可。")
    source_record_id: Mapped[UUID] = mapped_column(ForeignKey("tool_discovery_revisions.id"), comment="固定目录修订。")
    generation: Mapped[int] = mapped_column(Integer, comment="固定Run执行围栏。")
    executor_uid: Mapped[int] = mapped_column(Integer, comment="获准本机执行UID；需SO_PEERCRED核对。")
    request_digest: Mapped[str] = mapped_column(String(64), comment="完整请求SHA，不是签名或业务授权。")
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="封闭非秘密固定配置及调用引用；无参数正文或令牌。")
    status: Mapped[str] = mapped_column(String(16), default="authorized", comment="authorized/claimed/bound/blocked/revoked。")
    revision: Mapped[int] = mapped_column(Integer, default=1, comment="单向状态修订。")
    claim_id: Mapped[UUID | None] = mapped_column(nullable=True, comment="一次领取nonce，永久保留。")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="取密之前已提交的领取时间。")
    token_state: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"), nullable=True, comment="一次绑定的非秘密令牌状态，撤销另行记录。")
    token_state_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="固定令牌状态SHA。")
    lease_id: Mapped[UUID | None] = mapped_column(nullable=True, comment="一次绑定的提供者lease，不可跨派发复用。")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="提供者确认的首次撤销时间，不是本地推测。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="网关保存时间。")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="固定截止，不续期。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="服务器状态时间。")


class AgentAccessGrant(SoftDeleteMixin, Base):
    """Agent 对用户或角色主体的显式使用授权。"""

    __tablename__ = "agent_access_grants"
    __table_args__ = (
        active_unique_index(
            "uq_agent_access_grants_active_subject", "agent_id", "subject_type", "subject_id"
        ),
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
        nullable=True, comment="相关固定资源版本标识；具体类型以resource_type为准。"
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


class KnowledgeSource(Base):
    """租户内知识来源及其治理归属。"""

    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_key", name="uq_knowledge_sources_tenant_external"),
        {"comment": "租户级知识来源、环境和治理负责人。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="知识来源唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, comment="来源所属租户。"
    )
    external_key: Mapped[str] = mapped_column(String(200), comment="来源稳定外部标识。")
    name: Mapped[str] = mapped_column(String(200), comment="来源显示名称。")
    source_type: Mapped[str] = mapped_column(String(32), comment="来源类型，如 manual、mock 或 cms。")
    environment: Mapped[KnowledgeSourceEnvironment] = mapped_column(
        Enum(KnowledgeSourceEnvironment, native_enum=False, length=32),
        default=KnowledgeSourceEnvironment.MOCK,
        index=True,
        comment="来源环境；mock 不得发布到 production。",
    )
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="来源负责人。")
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON().with_variant(JSONB(), "postgresql"), default=dict, comment="来源治理元数据。"
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="创建人。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="来源创建时间。")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="来源最近更新时间。"
    )


class KnowledgeDocument(SoftDeleteMixin, Base):
    """知识文档稳定身份和当前治理状态。"""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        active_unique_index(
            "uq_knowledge_documents_external", "tenant_id", "source_id", "external_key"
        ),
        {"comment": "知识文档稳定身份、受众、有效期和当前发布版本。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="知识文档唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="文档所属租户。")
    source_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_sources.id"), index=True, comment="文档来源。")
    external_key: Mapped[str] = mapped_column(String(200), comment="来源内稳定文档标识。")
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="文档负责人。")
    status: Mapped[KnowledgeDocumentStatus] = mapped_column(
        Enum(KnowledgeDocumentStatus, native_enum=False, length=32),
        default=KnowledgeDocumentStatus.DRAFT,
        index=True,
        comment="文档当前治理状态。",
    )
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("knowledge_versions.id", use_alter=True), nullable=True, index=True, comment="当前已发布版本。"
    )
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="下次复核时间。")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="文档创建人。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="文档创建时间。")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="文档最近更新时间。"
    )


class KnowledgeVersion(SoftDeleteMixin, Base):
    """不可变知识内容和发布快照。"""

    __tablename__ = "knowledge_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_knowledge_versions_number"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_knowledge_versions_effective_range",
        ),
        {"comment": "不可变 Markdown 知识版本、章节锚点和有效期。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="知识版本唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="版本所属租户。")
    document_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_documents.id"), index=True, comment="所属文档。")
    version_number: Mapped[int] = mapped_column(Integer, comment="文档内单调递增版本号。")
    status: Mapped[KnowledgeVersionStatus] = mapped_column(
        Enum(KnowledgeVersionStatus, native_enum=False, length=32),
        default=KnowledgeVersionStatus.DRAFT,
        index=True,
        comment="知识版本生命周期状态。",
    )
    title: Mapped[str] = mapped_column(String(500), comment="知识标题。")
    content_markdown: Mapped[str] = mapped_column(Text, comment="UTF-8 Markdown 正文。")
    content_digest: Mapped[str] = mapped_column(String(64), comment="规范化内容 SHA-256。")
    section_anchors: Mapped[list[dict[str, str]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list, comment="确定性章节定位锚点。"
    )
    change_summary: Mapped[str] = mapped_column(String(2000), default="", comment="版本变更说明。")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="版本生效时间。")
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="版本失效时间。")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="版本创建人。")
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="审核人。")
    submitted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="提交审核人，与审核人分离。")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最近提交审核时间。")
    review_decision: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="pending/approved/rejected；旧正式版本不伪造批准。")
    review_revision: Mapped[int] = mapped_column(Integer, default=0, comment="提交与审核乐观并发 revision。")
    review_scope_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="审核内容/标题/有效期/ACL 摘要。")
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True, comment="受限审核说明。")
    published_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="发布人。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="版本创建时间。")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="审核时间。")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="发布时间。")


class KnowledgeAclEntry(SoftDeleteMixin, Base):
    """知识文档的允许列表 ACL；未命中默认拒绝。"""

    __tablename__ = "knowledge_acl_entries"
    __table_args__ = (
        active_unique_index(
            "uq_knowledge_acl_subject",
            "tenant_id", "document_id", "subject_type", "subject_id"
        ),
        {"comment": "知识文档角色、部门或用户允许列表。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="ACL 记录唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="ACL 所属租户。")
    document_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_documents.id"), index=True, comment="受保护文档。")
    subject_type: Mapped[KnowledgeAclSubjectType] = mapped_column(
        Enum(KnowledgeAclSubjectType, native_enum=False, length=32), comment="允许主体类型。"
    )
    subject_id: Mapped[str] = mapped_column(String(200), comment="角色、组织单元或用户标识。")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="ACL 创建人。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="ACL 创建时间。")


class KnowledgeSnapshot(SoftDeleteMixin, Base):
    """发布时生成的只读检索快照元数据。"""

    __tablename__ = "knowledge_snapshots"
    __table_args__ = (
        UniqueConstraint("document_id", "version_id", name="uq_knowledge_snapshots_document_version"),
        {"comment": "供后续检索使用的已发布知识不可变快照。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="知识快照唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="快照所属租户。")
    document_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_documents.id"), index=True, comment="快照文档。")
    version_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_versions.id"), index=True, comment="快照版本。")
    content_digest: Mapped[str] = mapped_column(String(64), comment="内容 SHA-256。")
    title: Mapped[str] = mapped_column(String(500), comment="快照标题。")
    section_anchors: Mapped[list[dict[str, str]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list, comment="快照章节定位。"
    )
    acl_digest: Mapped[str] = mapped_column(String(64), comment="发布时 ACL 摘要。")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="快照生效时间。")
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="快照失效时间。")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="快照发布时间。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="快照创建时间。")


class KnowledgeOutboxEvent(Base):
    """知识发布事实的持久化、幂等消费事件。"""

    __tablename__ = "knowledge_outbox_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_key", name="uq_knowledge_outbox_event_key"),
        Index("ix_knowledge_outbox_consumption", "processed_at", "dead_at", "available_at"),
        {"comment": "知识生命周期事件，供索引器可靠、幂等消费。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="事件唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="事件所属租户。")
    event_key: Mapped[str] = mapped_column(String(200), comment="租户内幂等事件键。")
    event_type: Mapped[str] = mapped_column(String(64), index=True, comment="知识生命周期事件类型。")
    document_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_documents.id"), index=True, comment="关联文档。")
    version_id: Mapped[UUID | None] = mapped_column(ForeignKey("knowledge_versions.id"), nullable=True, comment="关联版本。")
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict, comment="事件脱敏载荷。"
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, comment="已尝试消费次数。")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True, comment="下次可消费时间。")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="成功消费时间。")
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="有限消费耗尽进入死信时间。")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="索引消费稳定错误码，不保存正文或异常原文。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="事件创建时间。")


class KnowledgeIndexGeneration(Base):
    """关键词索引代次；构建成功后才切换可检索数据。"""

    __tablename__ = "knowledge_index_generations"
    __table_args__ = (
        UniqueConstraint("document_id", "generation_number", name="uq_knowledge_index_generation"),
        Index("ix_knowledge_index_tenant_status", "tenant_id", "status", "document_id"),
        {"comment": "知识关键词索引代次及可追溯构建状态。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="索引代次唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="索引所属租户。")
    document_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_documents.id"), comment="关联稳定文档。")
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_snapshots.id"), comment="构建的不可变快照。")
    generation_number: Mapped[int] = mapped_column(Integer, comment="文档内单调递增索引代次。")
    algorithm_version: Mapped[str] = mapped_column(String(64), default="keyword_v1", comment="分块和关键词策略版本，不表示 embedding。")
    content_digest: Mapped[str] = mapped_column(String(64), comment="规范化原文摘要。")
    status: Mapped[str] = mapped_column(String(32), default="pending", comment="pending/ready/invalidated/failed。")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, comment="成功构建的分块数。")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="构建失败稳定错误码。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="代次创建时间。")
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="构建并切换完成时间。")


class KnowledgeChunk(Base):
    """保留原文偏移和摘要的确定性分块，不调用向量模型。"""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("generation_id", "ordinal", name="uq_knowledge_chunk_ordinal"),
        Index("ix_knowledge_chunk_tenant_generation", "tenant_id", "generation_id"),
        {"comment": "按索引代次保存知识原文分块和章节定位。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="分块数据库唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="分块所属租户。")
    generation_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_index_generations.id"), comment="索引代次。")
    ordinal: Mapped[int] = mapped_column(Integer, comment="原文内从零开始的分块序号。")
    chunk_key: Mapped[str] = mapped_column(String(64), comment="快照/算法/偏移/摘要导出的可复现分块键。")
    section_id: Mapped[str] = mapped_column(String(500), comment="章节锚点，无标题时使用 document。")
    section_title: Mapped[str] = mapped_column(String(500), comment="章节显示标题。")
    start_offset: Mapped[int] = mapped_column(Integer, comment="规范化原文字符起点。")
    end_offset: Mapped[int] = mapped_column(Integer, comment="规范化原文字符终点，不包含该字符。")
    content_digest: Mapped[str] = mapped_column(String(64), comment="该分块原文 SHA-256。")
    content: Mapped[str] = mapped_column(Text, comment="获准后才允许返回的知识数据，不是系统指令。")


class KnowledgeEvidence(Base):
    """一次获准检索产生的证据身份，模型不能指定外部 URL。"""

    __tablename__ = "knowledge_evidence"
    __table_args__ = (
        Index("ix_knowledge_evidence_run_attempt", "tenant_id", "run_id", "attempt_id"),
        {"comment": "绑定检索主体、执行尝试、快照与分块的证据记录。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="服务端生成的证据 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="证据所属租户。")
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="检索时可信主体。")
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), comment="关联业务 Run。")
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("run_attempts.id"), comment="实际检索执行尝试。")
    tool_invocation_id: Mapped[UUID] = mapped_column(ForeignKey("tool_invocations.id"), comment="获准并成功返回模型的检索调用。")
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_snapshots.id"), comment="引用的发布快照。")
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_chunks.id"), comment="引用的不可变分块。")
    content_digest: Mapped[str] = mapped_column(String(64), comment="检索时分块摘要。")
    query_digest: Mapped[str] = mapped_column(String(64), comment="查询摘要，不保存用户原始问题。")
    score: Mapped[float] = mapped_column(Float, comment="keyword_v1 词项命中分数，不是语义相似度。")
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="实际检索时间。")


class Conversation(SoftDeleteMixin, Base):
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
        CheckConstraint("(release_rollout_id IS NULL AND release_rollout_epoch IS NULL AND release_selection IS NULL AND release_selection_bucket IS NULL) OR "
            "(release_rollout_id IS NOT NULL AND release_rollout_epoch IS NOT NULL AND release_selection IS NOT NULL AND release_selection_bucket IS NOT NULL AND release_rollout_epoch >= 1 AND release_selection IN ('baseline', 'candidate') AND release_selection_bucket BETWEEN 0 AND 9999)",
            name="ck_conversation_release_selection"),
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
    release_rollout_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_release_rollouts.id", name="fk_conversation_release_rollout"), nullable=True,
        index=True, comment="创建时的灰度来源；旧会话不补造。")
    release_rollout_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="创建时固定的观察轮次。")
    release_selection: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="创建时选中baseline/candidate，之后不重分配。")
    release_selection_bucket: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="主体稳定哈希桶0至9999；不作为业务授权。")
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
    next_input_sequence: Mapped[int] = mapped_column(Integer, default=1, server_default="1", comment="下次用户输入的会话内序号，在会话行锁内递增。")
    active_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id", use_alter=True, name="fk_conversation_active_run"), nullable=True, comment="当前持有会话执行许可的 Run。")
    active_attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("run_attempts.id", use_alter=True, name="fk_conversation_active_attempt"), nullable=True, comment="当前会话执行许可对应的尝试。")
    checkpoint_thread_id: Mapped[str | None] = mapped_column(String(300), nullable=True, comment="最近产品事务成功提交的隔离 checkpoint thread。")
    checkpoint_id: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="最近有效 checkpoint 的精确提交指针。")
    checkpoint_input_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="有效 checkpoint 对应的已完成输入序号。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="会话创建时间。"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="会话最近活动时间。"
    )
    # relationship 仅用于 API 聚合，不是额外数据库字段。
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", order_by="Message.turn_sequence, Message.role.desc(), Message.created_at, Message.id",
        foreign_keys="Message.conversation_id",
    )


class Message(Base):
    """用户可见会话消息，是聊天历史的产品事实记录。"""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "conversation_id", "idempotency_key"),
        UniqueConstraint("tenant_id", "conversation_id", "turn_sequence", "role", name="uq_messages_turn_role"),
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
    turn_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="用户输入及对应助手回答共用的轮次序号；无法关联的旧消息为空。")
    content: Mapped[str] = mapped_column(Text, comment="用户可见消息正文。")
    support_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True,
        comment="服务端校验的版本化 SupportResult；旧正文为空，不回填虚构证据。",
    )
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
    conversation: Mapped[Conversation] = relationship(back_populates="messages", foreign_keys=[conversation_id])


class AgentRun(Base):
    """一次固定 Agent/模型版本的异步执行记录。"""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_tenant_conversation", "tenant_id", "conversation_id"),
        UniqueConstraint("tenant_id", "conversation_id", "input_sequence", name="uq_agent_runs_input_sequence"),
        Index("ix_agent_runs_business_wait", "tenant_id", "conversation_id", "id",
            postgresql_where=text("status IN ('WAITING_APPROVAL', 'WAITING_ACTION')")),
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
    input_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="本轮固定输入边界，不读取后续排队输入。")
    execution_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="递增的执行 fencing generation；旧数据不伪造尝试。")
    current_attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("run_attempts.id", use_alter=True, name="fk_agent_run_current_attempt"), nullable=True, comment="当前获准执行的尝试。")
    recovery_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="执行恢复次数，独立于模型重试次数；人工恢复不绕过上限。")
    recovery_reason: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="最近恢复或人工诊断的稳定原因码。")
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


class RunAttempt(Base):
    """业务 Run 的独立执行者租约、隔离状态与尝试结果。"""

    __tablename__ = "run_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "generation", name="uq_run_attempt_generation"),
        UniqueConstraint("checkpoint_thread_id", name="uq_run_attempt_checkpoint_thread"),
        CheckConstraint("generation >= 1", name="ck_run_attempt_generation"),
        CheckConstraint("status IN ('running', 'completed', 'failed', 'cancelled', 'expired', 'quarantined')", name="ck_run_attempt_status"),
        CheckConstraint("replay_policy IN ('read_only', 'manual')", name="ck_run_attempt_replay"),
        CheckConstraint("side_effect_state IN ('none', 'unknown', 'confirmed')", name="ck_run_attempt_side_effect"),
        Index("ix_run_attempts_expiry", "status", "lease_expires_at"),
        Index("ix_run_attempts_tenant_run", "tenant_id", "run_id", "generation"),
        {"comment": "独立执行尝试和 fencing 租约；未提交 checkpoint 与业务事实隔离。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="执行尝试唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="执行尝试所属租户。")
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), comment="关联业务 Run。")
    generation: Mapped[int] = mapped_column(Integer, comment="本 Run 单调递增的 fencing generation。")
    owner_id: Mapped[str] = mapped_column(String(100), comment="领取该尝试的 Worker 启动实例标识。")
    status: Mapped[str] = mapped_column(String(20), default="running", comment="running/completed/failed/cancelled/expired/quarantined。")
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="当前执行者最后成功续租时间。")
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="超时后失效，旧执行者禁止提交。")
    checkpoint_thread_id: Mapped[str] = mapped_column(String(300), comment="该尝试独占的 checkpoint thread。")
    checkpoint_id: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="成功产品提交对应的 checkpoint ID。")
    replay_policy: Mapped[str] = mapped_column(String(20), default="manual", comment="只有已证明只读的尝试允许自动重放。")
    side_effect_state: Mapped[str] = mapped_column(String(20), default="none", comment="副作用事实：none/unknown/confirmed；未知必须人工对账。")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="尝试失败/过期/隔离的稳定错误码。")
    recovery_reason: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="创建此恢复尝试的原因。")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=dict, comment="本尝试脱敏计量，未知 usage 不记为零。")
    context_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=True, comment="模型输入历史边界与可信证据 ID；不以旧角色快照授权。")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="该尝试开始时间。")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="该尝试结束或隔离时间。")


class ModelCallLedger(Base):
    """整个 Run/租户共享的模型调用许可与保守费用账本。"""

    __tablename__ = "model_call_ledger"
    __table_args__ = (
        UniqueConstraint("run_id", "call_number", name="uq_model_call_number"),
        Index("ix_model_call_tenant_day", "tenant_id", "created_at"),
        {"comment": "逐物理模型调用预留、结算与未知 usage 的持久化计量。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="模型调用许可标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="模型调用所属租户。")
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True, comment="同 Run 恢复共用预算。")
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("run_attempts.id"), comment="调用执行尝试。")
    model_endpoint_version_id: Mapped[UUID] = mapped_column(ForeignKey("model_endpoint_versions.id"), comment="本次实际使用的固定模型端点版本。")
    policy_version: Mapped[str] = mapped_column(String(64), default="runtime_budget_v1", comment="预留和结算策略版本。")
    call_number: Mapped[int] = mapped_column(Integer, comment="整个 Run 内单调递增物理调用序号，含重试。")
    retry_index: Mapped[int] = mapped_column(Integer, default=0, comment="同逻辑请求内有限依赖重试序号；0 表示首次。")
    status: Mapped[str] = mapped_column(String(32), default="reserved", comment="reserved/settled/unknown；出站后未知保留消费。")
    token_strategy: Mapped[str] = mapped_column(String(64), default="utf8_upper_bound_v1", comment="完整输入保守估算策略，不表示精确 tokenizer。")
    estimated_input_tokens: Mapped[int] = mapped_column(Integer, comment="完整系统/历史/工具/结构输入估算。")
    reserved_output_tokens: Mapped[int] = mapped_column(Integer, comment="请求最大输出预留。")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="供应商本次真实输入 usage。")
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="供应商本次真实输出 usage。")
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="包含在输入中的缓存 Token，不重复收费。")
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="包含在输出中的推理 Token，不重复收费。")
    reserved_cost_microusd: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="保守费用预留；未知 USD 定价为 null。")
    settled_cost_microusd: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="按真实总 usage 计算，未知为 null。")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="失败或未知 usage 的稳定原因。")
    omitted_message_count: Mapped[int] = mapped_column(Integer, default=0, comment="本请求裁去的完整旧消息数量。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="调用许可创建时间。")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="结算或未知状态确认时间。")


class SpecialistExecutionObservation(Base):
    """知识子任务的一份完整物理回执；正文密封且不进入SSE事件。"""

    __tablename__ = "specialist_execution_observations"
    __table_args__ = (
        CheckConstraint("generation >= 1", name="ck_specialist_execution_generation"),
        Index("ix_specialist_execution_tenant_run", "tenant_id", "run_id", "created_at"),
        {"comment": "原call唯一追加的完整执行回执与可选脱敏密封回答；不授予运行许可。"},
    )
    call_id: Mapped[UUID] = mapped_column(ForeignKey("model_call_ledger.id"), primary_key=True, comment="原物理调用；一次记录，不重派。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="原调用租户。")
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), comment="原父Run。")
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("run_attempts.id"), comment="原物理调用尝试。")
    generation: Mapped[int] = mapped_column(Integer, comment="原尝试围栏代次。")
    record_digest: Mapped[str] = mapped_column(String(64), comment="封闭元数据规范摘要。")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="完整回执与密封正文定位，无回答明文。")
    sealed_answer: Mapped[str | None] = mapped_column(Text, nullable=True, comment="脱敏回答的专用信封；缺失不表示原SDK没有回答。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="原记录首次持久化时间。")


class MockRemoteOperation(Base):
    """隔离目标系统的幂等登记/执行封锁证明，独立事务模拟远程提交。"""
    __tablename__ = "mock_remote_operations"
    __table_args__ = (UniqueConstraint("tenant_id", "logical_key", name="uq_mock_remote_operation_key"),
        {"comment": "仅隔离 Mock 的目标端幂等与未执行证明；不承诺真实连接器具备同等能力。"})
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="模拟接收端操作 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="目标租户。")
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="服务器可信资源主体。")
    logical_key: Mapped[str] = mapped_column(String(64), comment="与原业务动作相同的稳定幂等键。")
    parameters_digest: Mapped[str] = mapped_column(String(64), comment="目标接收的冻结参数摘要。")
    execution_fence: Mapped[int] = mapped_column(Integer, default=0, comment="低于此 generation 的迟到提交拒绝。")
    status: Mapped[str] = mapped_column(String(20), comment="executed/not_executed/unknown。")
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="已创建的目标 ID。")
    fault_consumed: Mapped[bool] = mapped_column(Boolean, default=False, comment="隔离一次性故障结果已消费。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="目标登记时间。")


class MockSupportCase(Base):
    """同主体/项目/问题的接管许可，共用锁防止客服与 Agent 并发写。"""
    __tablename__ = "mock_support_cases"
    __table_args__ = (UniqueConstraint("tenant_id", "project_id", "issue_key", name="uq_mock_support_case"),
        {"comment": "显式客服接管状态；只读查询继续允许，Agent 新写动作冻结。"})
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="支持问题 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="问题租户。")
    project_id: Mapped[UUID] = mapped_column(comment="服务器派生项目。")
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="问题资源主体。")
    issue_key: Mapped[str] = mapped_column(String(128), comment="稳定问题键。")
    conversation_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversations.id"), nullable=True, index=True, comment="服务端来源会话。")
    handler_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="当前明确接管人员。")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, comment="接管中时冻结 Agent 写。")
    version: Mapped[int] = mapped_column(Integer, default=1, comment="接管授权 fencing 版本。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="接管变化时间。")


class MockTicketCommand(Base):
    """人工 Mock 操作和目标状态同事务的幂等命令账本。"""
    __tablename__ = "mock_ticket_commands"
    __table_args__ = (UniqueConstraint("ticket_id", "idempotency_key", name="uq_mock_ticket_command"),
        CheckConstraint("kind IN ('claim', 'note', 'reply', 'close', 'escalate', 'release')", name="ck_mock_ticket_command_kind"),
        {"comment": "Mock 人工认领/备注/回复/关闭/升级命令事实；不连接真实系统。"})
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="命令 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="命令租户。")
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("mock_support_tickets.id"), index=True, comment="已存在目标工单。")
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="当前有资源权限的操作人员。")
    idempotency_key: Mapped[str] = mapped_column(String(200), comment="人工操作幂等键；重放不再次改状态。")
    request_digest: Mapped[str] = mapped_column(String(64), comment="种类/脱敏正文/原版本摘要。")
    kind: Mapped[str] = mapped_column(String(20), comment="受限操作种类。")
    body: Mapped[str] = mapped_column(String(1000), default="", comment="脱敏受限正文；备注仅获准客服可读。")
    ticket_version: Mapped[int] = mapped_column(Integer, comment="实际提交后的目标版本。")
    ticket_status: Mapped[str] = mapped_column(String(20), comment="实际提交后的状态。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="实际操作时间。")


class MockSupportTicket(Base):
    """隔离模拟工单，不连接或冒充 CRM/Jira/真实工单。"""
    __tablename__ = "mock_support_tickets"
    __table_args__ = (UniqueConstraint("tenant_id", "logical_key", name="uq_mock_ticket_business_key"),
        UniqueConstraint("tenant_id", "external_id", name="uq_mock_ticket_external_id"),
        {"comment": "隔离模拟工单；业务键去重、版本与受限状态独立复核。"})
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="模拟工单 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="模拟租户。")
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True, comment="工单资源主体。")
    project_id: Mapped[UUID] = mapped_column(comment="服务器派生的隔离项目。")
    logical_key: Mapped[str] = mapped_column(String(64), comment="目标端稳定业务键。")
    external_id: Mapped[str] = mapped_column(String(200), comment="明确带 MOCK 前缀的业务编号。")
    title: Mapped[str] = mapped_column(String(200), comment="受限标题，不保存原始 Prompt。")
    summary: Mapped[str] = mapped_column(String(2000), comment="最小支持摘要。")
    issue_key: Mapped[str] = mapped_column(String(128), comment="可信业务意图。")
    status: Mapped[str] = mapped_column(String(20), default="open", comment="open/in_progress/escalated/closed。")
    version: Mapped[int] = mapped_column(Integer, default=1, comment="受限回调/客服操作版本。")
    assigned_to: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="显式认领的处理人。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="实际模拟创建时间。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="模拟状态更新时间。")


class BusinessAction(Base):
    """跨 Run 的稳定逻辑动作，冻结参数不能被模型重试覆盖。"""
    __tablename__ = "business_actions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "tool_id", "logical_key", name="uq_business_action_key"),
        CheckConstraint("risk_level IN ('R2', 'R3', 'R4')", name="ck_business_action_risk"),
        CheckConstraint("status IN ('proposed', 'waiting_approval', 'queued', 'running', 'unknown', 'reconciling', 'succeeded', 'failed', 'needs_attention', 'cancelled')", name="ck_business_action_status"),
        Index("ix_business_action_due", "status", "next_attempt_at"),
        {"comment": "跨模型重试/Run 恢复的业务动作账本，建议/提案/执行事实分离。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="稳定业务动作 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="动作所属租户。")
    proposed_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="服务端可信提案主体。")
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True, comment="原始业务 Run；恢复不能重置动作身份。")
    agent_version_id: Mapped[UUID] = mapped_column(ForeignKey("agent_versions.id"), comment="提案固定 Agent 版本。")
    tool_id: Mapped[str] = mapped_column(String(100), comment="固定动作工具 ID。")
    implementation_version: Mapped[int] = mapped_column(Integer, comment="固定服务端实现版本。")
    catalog_version: Mapped[int] = mapped_column(Integer, comment="冻结时的目录策略版本。")
    execution_catalog_version: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="提交执行意图时复检的当前策略版本，不覆盖原提案事实。")
    execution_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True, comment="重新授权的执行上下文，原逻辑动作和原出处不改变。")
    execution_agent_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_versions.id"), nullable=True, comment="执行意图使用的固定 Agent 版本。")
    supersedes_action_id: Mapped[UUID | None] = mapped_column(ForeignKey("business_actions.id", name="fk_business_action_supersedes"), nullable=True, comment="显式改参替代的未执行旧提案；旧记录不被覆盖。")
    logical_key: Mapped[str] = mapped_column(String(64), comment="主体/资源/业务意图组成的稳定 SHA-256 幂等键。")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="严格 Schema 的冻结业务参数，不保存原始工具或 Prompt。")
    parameters_digest: Mapped[str] = mapped_column(String(64), comment="规范化参数 SHA-256。")
    parameter_schema_version: Mapped[str] = mapped_column(String(32), comment="冻结参数 Schema 版本。")
    resource_kind: Mapped[str] = mapped_column(String(64), comment="服务端解析的资源类别。")
    resource_id: Mapped[str] = mapped_column(String(200), comment="服务端解析的主体范围资源。")
    risk_level: Mapped[str] = mapped_column(String(2), comment="R2–R4，不能由模型降低。")
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True, comment="冻结策略是否需要独立审批；默认拒绝自动执行。")
    status: Mapped[str] = mapped_column(String(32), default="proposed", comment="独立动作生命周期，不等同于 Run 终态。")
    current_attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("action_attempts.id", use_alter=True, name="fk_business_action_attempt"), nullable=True, comment="当前执行/对账尝试。")
    generation: Mapped[int] = mapped_column(Integer, default=0, comment="失效执行者写入保护。")
    reconciliation_count: Mapped[int] = mapped_column(Integer, default=0, comment="累计对账次数，不因恢复清零。")
    reconciliation_limit: Mapped[int] = mapped_column(Integer, default=6, comment="当前有限查询许可，人工恢复仅可追加受限额度。")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="下次执行/对账的受限时间。")
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="已得到可信回执的外部业务 ID。")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="安全状态原因，不代表未知副作用未发生。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="原提案时间。")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="确认终态时间。")


class ActionAttempt(Base):
    """动作执行许可或独立对账许可，区别于模型 RunAttempt。"""
    __tablename__ = "action_attempts"
    __table_args__ = (
        UniqueConstraint("action_id", "generation", name="uq_action_attempt_generation"),
        CheckConstraint("kind IN ('execute', 'reconcile', 'verify')", name="ck_action_attempt_kind"),
        {"comment": "动作执行/对账尝试与未知结果许可；不依赖模型调用 ID。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="动作尝试 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="尝试租户。")
    action_id: Mapped[UUID] = mapped_column(ForeignKey("business_actions.id"), index=True, comment="稳定逻辑动作。")
    generation: Mapped[int] = mapped_column(Integer, comment="许可 generation，同逻辑动作不重复。")
    kind: Mapped[str] = mapped_column(String(20), comment="execute/reconcile/verify。")
    owner_id: Mapped[str] = mapped_column(String(200), comment="当前 Worker owner。")
    status: Mapped[str] = mapped_column(String(20), default="running", comment="执行许可/成功/失败/未知/隔离。")
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="独立动作租约。")
    parameters_digest: Mapped[str] = mapped_column(String(64), comment="本次只能执行冻结参数。")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="受限失败/未知原因。")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="实际领取时间。")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="尝试收敛时间。")


class ActionReceipt(Base):
    """追加的执行/查询回执；HTTP 成功不自动等于业务状态已验证。"""
    __tablename__ = "action_receipts"
    __table_args__ = ({"comment": "追加业务回执与独立状态复核证明，不保存原始外部响应。"},)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="回执 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="回执租户。")
    action_id: Mapped[UUID] = mapped_column(ForeignKey("business_actions.id"), index=True, comment="原逻辑动作。")
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("action_attempts.id"), comment="提供回执的实际尝试。")
    connector_version: Mapped[str] = mapped_column(String(64), comment="服务器可信目标连接器版本。")
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="外部业务 ID；未知时为空。")
    status: Mapped[str] = mapped_column(String(32), comment="submitted/verified/conflict/not_executed/unknown。")
    result_digest: Mapped[str] = mapped_column(String(64), comment="白名单回执 SHA-256。")
    verified_fields: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=dict, comment="独立查询获准的最小业务字段。")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="实际查询/响应时间。")


class ActionCompensationProposal(Base):
    """不可变补偿意向，与原动作及其批准分离，不产生执行任务。"""
    __tablename__ = "action_compensation_proposals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "action_id", "scope_digest", name="uq_compensation_proposal_scope"),
        Index("ix_compensation_proposal_owner", "tenant_id", "subject_user_id", "action_id", "created_at", "id"),
        CheckConstraint("status = 'proposed' AND minimum_risk IN ('R3', 'R4')", name="ck_compensation_proposal_state"),
        CheckConstraint("reason_code IN ('verification_conflict', 'user_requested_closure')", name="ck_compensation_proposal_reason"),
        {"comment": "隔离Mock关闭工单的不可变补偿提案，不继承原动作批准且没有执行入口。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="补偿提案ID，与原逻辑动作分离。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="当前可信租户。")
    subject_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="原动作主体，只有本人提出和读取。")
    action_id: Mapped[UUID] = mapped_column(ForeignKey("business_actions.id"), comment="保留的原业务动作。")
    source_receipt_id: Mapped[UUID] = mapped_column(ForeignKey("action_receipts.id"), comment="当前generation独立复核/冲突回执，不接受人工猜测结果。")
    scope: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="冻结原动作/回执/目标版本/当前授权上下文和关闭意向，无正文或秘密。")
    scope_digest: Mapped[str] = mapped_column(String(64), comment="固定补偿范围SHA256，同一范围幂等复用。")
    minimum_risk: Mapped[str] = mapped_column(String(2), comment="最低R3，原动作R4时保持R4，不能降为原R2。")
    reason_code: Mapped[str] = mapped_column(String(32), comment="复核冲突或本人明确请求关闭，不保存自由文本。")
    status: Mapped[str] = mapped_column(String(16), default="proposed", comment="仅proposed，不能标为approved/queued/completed。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="服务器保存提案时间，不是执行时间。")


class TicketEscalationPackage(Base):
    """升级上下文独立存于产品库，不复制到模拟外部工单。"""
    __tablename__ = "ticket_escalation_packages"
    __table_args__ = (UniqueConstraint("action_id", "run_id", "source_digest", name="uq_ticket_escalation_source"),
        {"comment": "不可覆盖的支持升级包；查询时间、事实与未确认结论分开。"})
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="升级包 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="包所属租户。")
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="原输入与资源主体。")
    action_id: Mapped[UUID] = mapped_column(ForeignKey("business_actions.id"), index=True, comment="同键复用的原业务动作。")
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True, comment="本次排查来源，不覆盖原动作出处。")
    attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("run_attempts.id"), nullable=True, comment="实际排查尝试，缺失不伪造。")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="有界版本化问题/来源/观察事实/未确认事项/引用/步骤/Trace。")
    payload_digest: Mapped[str] = mapped_column(String(64), comment="规范化包摘要。")
    source_digest: Mapped[str] = mapped_column(String(64), comment="除捕获时间外的有界来源摘要；恢复/预算失败追加新事实，不覆盖旧包。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="升级包保存时间。")


class MockResourceGrant(Base):
    """隔离 Mock 项目的显式人员授权；管理员角色不自动产生业务权限。"""
    __tablename__ = "mock_resource_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "user_id", "permission", name="uq_mock_resource_grant"),
        CheckConstraint("permission IN ('approve', 'ticket_handle', 'support_context_read')", name="ck_mock_resource_permission"),
        {"comment": "明确审批人与客服处理人的 Mock 项目授权，不覆盖知识 ACL。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="授权 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="资源租户。")
    project_id: Mapped[UUID] = mapped_column(index=True, comment="服务器派生 Mock 项目。")
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="项目资源主体。")
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True, comment="显式获准人员。")
    permission: Mapped[str] = mapped_column(String(32), comment="approve/ticket_handle/support_context_read 独立授权。")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="撤销后历史记录保留。")
    revision: Mapped[int] = mapped_column(Integer, default=1, comment="变更并发版本。")
    granted_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="授权管理员。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="最后授权变化时间。")


class ApprovalRequest(Base):
    """冻结审批范围，单动作可保留多次已失效申请的事实。"""
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("uq_approval_active_action", "action_id", unique=True,
            postgresql_where=text("status IN ('pending', 'approved')"), sqlite_where=text("status IN ('pending', 'approved')")),
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'expired', 'cancelled', 'invalidated', 'consumed')", name="ck_approval_request_status"),
        CheckConstraint("applicant_id <> approver_id", name="ck_approval_nonself"),
        Index("ix_approval_requests_due", "next_check_at", "id"),
        {"comment": "副作用前的持久化审批范围与期限；不保存凭据或原始诊断数据。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="审批申请 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="审批租户。")
    action_id: Mapped[UUID] = mapped_column(ForeignKey("business_actions.id"), index=True, comment="稳定业务动作。")
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), comment="冻结的授权上下文。")
    applicant_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True, comment="原动作主体。")
    approver_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True, comment="明确指定的审批人，不得等于申请者。")
    tool_id: Mapped[str] = mapped_column(String(100), comment="固定工具 ID。")
    implementation_version: Mapped[int] = mapped_column(Integer, comment="固定实现版本。")
    catalog_version: Mapped[int] = mapped_column(Integer, comment="审批冻结的策略版本。")
    parameters_digest: Mapped[str] = mapped_column(String(64), comment="规范化冻结参数摘要。")
    scope_digest: Mapped[str] = mapped_column(String(64), comment="主体/资源/版本/凭据 revision 等范围摘要。")
    resource_revision: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="批准冻结的 Mock 问题版本；旧未知范围不可直接执行。")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="审批等待/决策/消费状态。")
    revision: Mapped[int] = mapped_column(Integer, default=1, comment="并发决策版本。")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="服务器限定的审批期限。")
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="等待治理轮询时间，避免同一正常申请饿死其他待办。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="申请创建时间。")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="决策或终止时间。")


class ApprovalDecision(Base):
    """追加独立决定，不允许第二次决定覆盖历史。"""
    __tablename__ = "approval_decisions"
    __table_args__ = (UniqueConstraint("request_id", name="uq_approval_decision_request"),
        CheckConstraint("decision IN ('approved', 'rejected')", name="ck_approval_decision"), {"comment": "不可覆盖的审批决定。"})
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="决定 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="决定租户。")
    request_id: Mapped[UUID] = mapped_column(ForeignKey("approval_requests.id"), comment="原申请。")
    decided_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="实际审批人。")
    decision: Mapped[str] = mapped_column(String(20), comment="批准或拒绝。")
    reason: Mapped[str] = mapped_column(String(1000), comment="脱敏理由，不进入通用审计正文。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="决定时间。")


class ApprovalConsumption(Base):
    """一次性批准消费和动作执行许可同事务保存。"""
    __tablename__ = "approval_consumptions"
    __table_args__ = (UniqueConstraint("request_id", name="uq_approval_consumption_request"),
        UniqueConstraint("action_id", name="uq_approval_consumption_action"), {"comment": "一次性批准消费；重启不得再次产生执行许可。"})
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="消费 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="消费租户。")
    request_id: Mapped[UUID] = mapped_column(ForeignKey("approval_requests.id"), comment="已批准申请。")
    action_id: Mapped[UUID] = mapped_column(ForeignKey("business_actions.id"), comment="唯一逻辑动作。")
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("action_attempts.id"), comment="本次唯一出站执行许可。")
    generation: Mapped[int] = mapped_column(Integer, comment="消费时 fencing generation。")
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="原子消费时间。")


class ModelProbeJob(Base):
    """固定模型探测的持久化执行许可，不使用聊天 Run/checkpoint。"""
    __tablename__ = "model_probe_jobs"
    __table_args__ = (
        UniqueConstraint("test_run_id", name="uq_model_probe_test_run"),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_model_probe_status"),
        CheckConstraint("attempt_count >= 0 AND generation >= 0", name="ck_model_probe_counters"),
        Index("ix_model_probe_due", "status", "next_attempt_at"),
        Index("uq_model_probe_active_model", "endpoint_model_id", unique=True,
            postgresql_where=text("status IN ('queued', 'running')"), sqlite_where=text("status IN ('queued', 'running')")),
        {"comment": "端点探测持久化固定引用、有限尝试、owner/lease/generation 与终态。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="探测任务标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="任务租户。")
    test_run_id: Mapped[UUID] = mapped_column(ForeignKey("model_endpoint_test_runs.id"), comment="一对一产品探测记录。")
    endpoint_id: Mapped[UUID] = mapped_column(ForeignKey("model_endpoints.id"), comment="固定端点。")
    endpoint_model_id: Mapped[UUID] = mapped_column(ForeignKey("model_endpoint_models.id"), comment="固定稳定模型。")
    model_version_id: Mapped[UUID] = mapped_column(ForeignKey("model_endpoint_versions.id"), comment="固定模型版本。")
    credential_revision: Mapped[int] = mapped_column(Integer, comment="固定凭据 revision。")
    status: Mapped[str] = mapped_column(String(20), default="queued", comment="queued/running/succeeded/failed/cancelled。")
    stage: Mapped[str] = mapped_column(String(32), default="queued", comment="当前真实探测阶段。")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, comment="已领取物理执行次数，重启不清零。")
    generation: Mapped[int] = mapped_column(Integer, default=0, comment="保护旧 owner 写入的 fencing generation。")
    owner_id: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="当前 Worker owner。")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="独立探测租约。")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="有限退避后的再次领取时间。")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, comment="持久化取消意图。")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="安全稳定错误码。")
    attempt_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=list, comment="有界脱敏尝试摘要，无密钥或供应商正文。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="任务创建时间。")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="任务终态时间。")


class RunSchedulerState(Base):
    """共享队列轮转游标，跨 Worker 与重启保留租户公平顺序。"""
    __tablename__ = "run_scheduler_states"
    __table_args__ = ({"comment": "共享队列持久化租户轮转状态，不是服务或执行租约。"},)
    queue_name: Mapped[str] = mapped_column(String(200), primary_key=True, comment="当前数据库的通知队列名称。")
    last_tenant_id: Mapped[UUID | None] = mapped_column(nullable=True, comment="最近让出轮次的租户游标；不绑定已删除租户外键。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="最近轮转时间。")


class ToolInvocation(Base):
    """整个业务 Run 内的工具许可、原子限额与脱敏执行审计。"""

    __tablename__ = "tool_invocations"
    __table_args__ = (
        UniqueConstraint("run_id", "tool_call_id", name="uq_tool_invocations_call"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed', 'denied', 'cancelled', 'unknown')", name="ck_tool_invocation_status"),
        CheckConstraint("risk_level IN ('R0', 'R1', 'R2', 'R3', 'R4')", name="ck_tool_invocation_risk"),
        Index("ix_tool_invocations_run_tool", "tenant_id", "run_id", "tool_id", "status"),
        Index("ix_tool_invocations_tenant_time", "tenant_id", "created_at"),
        {"comment": "工具许可/限额/策略版本/脱敏结果审计；失败或取消的许可仍消耗次数。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="工具调用唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="工具调用所属租户。")
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), comment="计数关联整个业务 Run。")
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("run_attempts.id"), comment="本次实际执行尝试。")
    tool_call_id: Mapped[str] = mapped_column(String(200), comment="模型调用标识，同 Run 不重复执行。")
    tool_id: Mapped[str] = mapped_column(String(100), comment="稳定工具标识。")
    implementation_version: Mapped[int] = mapped_column(Integer, comment="服务端可信实现版本。")
    catalog_version: Mapped[int] = mapped_column(Integer, comment="执行时目录策略版本。")
    risk_level: Mapped[str] = mapped_column(String(2), comment="服务端复算的 R0–R4 风险。")
    arguments_digest: Mapped[str] = mapped_column(String(64), comment="规范化参数 SHA-256；不保存原始参数。")
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="过滤后结果摘要，不保存完整工具正文。")
    diagnostic_result: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=True, comment="隔离诊断白名单事实契约；不保存真实日志、密钥或 Prompt。")
    business_action_id: Mapped[UUID | None] = mapped_column(ForeignKey("business_actions.id"), nullable=True, comment="只关联已冻结提案或实际动作；调用成功不等于动作执行成功。")
    status: Mapped[str] = mapped_column(String(20), default="running", comment="running/succeeded/failed/denied/cancelled/unknown。")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="稳定授权/额度/执行错误码。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="许可或拒绝创建时间。")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="调用收敛时间，结果未知时显式标记。")


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
    attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("run_attempts.id"), nullable=True, comment="产生执行事件的尝试；控制面和旧事件为空。")
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
    attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("run_attempts.id"), nullable=True, comment="产生该观测的执行尝试；旧观测不伪造关联。")
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


class RuntimeAlert(Base):
    """独立观测告警，确认仅表示已阅，不修改业务结果。"""
    __tablename__ = "runtime_alerts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "rule_code", "scope_kind", "scope_id", name="uq_runtime_alert_scope"),
        CheckConstraint("scope_kind IN ('run', 'action')", name="ck_runtime_alert_scope"),
        CheckConstraint("severity IN ('info', 'warning', 'critical')", name="ck_runtime_alert_severity"),
        Index("ix_runtime_alerts_tenant_active_observed", "tenant_id", "is_active", "last_observed_at"),
        {"comment": "由当前业务事实派生的独立告警，缺失数据不填零，不发送外部消息。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="告警 ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="告警租户。")
    rule_code: Mapped[str] = mapped_column(String(64), comment="固定规则标识。")
    scope_kind: Mapped[str] = mapped_column(String(16), comment="run 或 action。")
    scope_id: Mapped[UUID] = mapped_column(comment="业务事实标识，保留诊断引用，不锁定或改写业务对象。")
    run_id: Mapped[UUID | None] = mapped_column(nullable=True, comment="来源 Run 引用，读取时仍校验租户，不将此观测作为授权。")
    severity: Mapped[str] = mapped_column(String(16), comment="信息、警告或严重级别。")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="本次观测是否仍命中规则。")
    source_generation: Mapped[int] = mapped_column(Integer, default=0, comment="来源 generation；新尝试重新提示，不覆盖业务 fencing。")
    revision: Mapped[int] = mapped_column(Integer, default=1, comment="告警确认的乐观并发版本。")
    metadata_payload: Mapped[dict[str, Any]] = mapped_column("metadata", JSON().with_variant(JSONB(), "postgresql"), default=dict, comment="严格白名单标量和缺失字段名，不含正文或秘密。")
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="首次观测时间。")
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="最近一次成功观测时间。")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="规则条件不再命中的时间，不代表业务成功。")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="管理员已阅时间，不作为问题修复证明。")
    acknowledged_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", name="fk_runtime_alert_ack_user"), nullable=True, comment="当前租户实际确认管理员。")


class RuntimeAlertScanState(Base):
    """多 Worker 共用的有限轮转扫描游标，不承担业务执行许可。"""
    __tablename__ = "runtime_alert_scan_states"
    __table_args__ = (CheckConstraint("source_kind IN ('run', 'action')", name="ck_runtime_alert_scan_kind"),
        {"comment": "按 UUID 游标有界扫描，完整轮转后重新检查，避免提交序号缺口丢失观测。"})
    source_kind: Mapped[str] = mapped_column(String(16), primary_key=True, comment="扫描来源类型。")
    last_source_id: Mapped[UUID | None] = mapped_column(nullable=True, comment="本轮最后来源 UUID，下一轮重新扫描变化或迟到事实。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="最近扫描成功时间。")


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


class RunNotificationOutbox(Base):
    """与用户输入同事务保存的 Run 通知；Redis 只是至少一次投递目标。"""

    __tablename__ = "run_notification_outbox"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_notification_outbox_run"),
        UniqueConstraint("event_key", name="uq_run_notification_outbox_event"),
        CheckConstraint("status IN ('pending', 'delivered', 'dead_letter', 'obsolete')",
                        name="ck_run_notification_outbox_status"),
        CheckConstraint("attempt_count >= 0 AND cycle_attempt_count >= 0 AND retry_count >= 0",
                        name="ck_run_notification_outbox_attempts"),
        Index("ix_run_notification_outbox_due", "status", "next_attempt_at", "id"),
        {"comment": "持久化 Run 通知投递、退避、死信与授权重试事实；不保存消息正文。"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="通知记录唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, comment="通知所属租户。")
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), comment="唯一关联业务 Run。")
    event_key: Mapped[str] = mapped_column(String(100), comment="稳定通知键，重复输入不创建新键。")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="pending/delivered/dead_letter/obsolete。")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, comment="累计投递尝试数，人工重试不清零。")
    cycle_attempt_count: Mapped[int] = mapped_column(Integer, default=0, comment="当前获准投递周期的尝试数。")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, comment="管理员显式重新投递周期数。")
    revision: Mapped[int] = mapped_column(Integer, default=1, comment="通知状态乐观并发版本。")
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="本次有限投递窗口开始时间。")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="下一次允许投递或核对的时间。")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最近一次 Redis 确认时间；不是业务执行成功。")
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="稳定投递错误码，不保存连接串或原始异常。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="通知创建时间。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="最近状态更新时间。")


class RetentionPolicy(Base):
    """不可变策略草稿；不存在默认保留周期或隐式批准。"""
    __tablename__ = "retention_policies"
    __table_args__ = (
        Index("ix_retention_policy_latest", "tenant_id", "created_at", "id"),
        CheckConstraint("status = 'draft'", name="ck_retention_policy_draft"),
        {"comment": "保留规则与候选复核范围的不可变草稿，不授权自动清理或擦除。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="策略草稿版本ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="策略所属租户。")
    name: Mapped[str] = mapped_column(String(100), comment="策略草稿名称。")
    definition: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="严格类别/周期/待确认法律保留/候选复核人员，不含业务正文。")
    policy_digest: Mapped[str] = mapped_column(String(64), comment="固定租户/创建人/策略ID与完整定义的SHA256。")
    status: Mapped[str] = mapped_column(String(16), default="draft", comment="当前只支持draft；草稿不产生批准。")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="创建草稿的当前管理员。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="服务器创建时间。")


class ErasureRequest(Base):
    """本人固定类别的申请及一次元数据预览；没有实际擦除状态。"""
    __tablename__ = "erasure_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "subject_user_id", "idempotency_key", name="uq_erasure_request_key"),
        Index("ix_erasure_request_owner", "tenant_id", "subject_user_id", "created_at", "id"),
        CheckConstraint("status IN ('requested', 'previewed', 'withdrawn') AND revision >= 1", name="ck_erasure_request_state"),
        CheckConstraint("(policy_id IS NULL AND policy_digest IS NULL) OR (policy_id IS NOT NULL AND policy_digest IS NOT NULL)", name="ck_erasure_policy_pair"),
        CheckConstraint("(preview_report IS NULL AND preview_digest IS NULL AND previewed_at IS NULL) OR (preview_report IS NOT NULL AND preview_digest IS NOT NULL AND previewed_at IS NOT NULL)", name="ck_erasure_preview_pair"),
        CheckConstraint("(status = 'withdrawn' AND withdrawn_at IS NOT NULL) OR (status <> 'withdrawn' AND withdrawn_at IS NULL)", name="ck_erasure_withdrawal_pair"),
        CheckConstraint("(status <> 'requested' OR preview_report IS NULL) AND (status <> 'previewed' OR preview_report IS NOT NULL)", name="ck_erasure_preview_state"),
        CheckConstraint("reason_code IN ('privacy_request', 'account_closure', 'data_correction')", name="ck_erasure_reason"),
        {"comment": "本人擦除意向、固定范围和不可覆盖预览；不批准、隔离或删除业务数据。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="申请ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="可信申请租户。")
    subject_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="只能为当前登录本人，客户端不能指定其他用户。")
    idempotency_key: Mapped[str] = mapped_column(String(200), comment="本人申请幂等键；同键不同范围拒绝。")
    payload_digest: Mapped[str] = mapped_column(String(64), comment="类别/固定原因码的请求摘要。")
    categories: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="固定选择的数据类别，不由模型扩展。")
    reason_code: Mapped[str] = mapped_column(String(32), comment="固定申请原因码，不保存自由文本个人信息。")
    policy_id: Mapped[UUID | None] = mapped_column(ForeignKey("retention_policies.id"), nullable=True, comment="创建时最新草稿；为空表示未配置，不改绑后续策略。")
    policy_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建时策略摘要，草稿不代表批准。")
    scope_digest: Mapped[str] = mapped_column(String(64), comment="租户/本人/类别/原因/策略/截止时间的范围摘要。")
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="服务器固定的预览创建记录截止时间，不是删除截止许可。")
    status: Mapped[str] = mapped_column(String(16), default="requested", comment="requested/previewed/withdrawn；没有approved或erased状态。")
    revision: Mapped[int] = mapped_column(Integer, default=1, comment="预览/撤回操作的并发版本。")
    preview_report: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=True, comment="只含聚合计数/未知和阻塞原因，无正文/资源ID/凭据。")
    preview_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="固定元数据预览SHA256，读取复检。")
    previewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="成功生成一次预览的服务器时间。")
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="本人明确撤回时间，保留原申请和预览。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="申请创建时间。")


class DeletionTombstone(Base):
    """未来受控维护的最小追加证明；当前不提供生成或恢复重施入口。"""
    __tablename__ = "deletion_tombstones"
    __table_args__ = (
        UniqueConstraint("tenant_id", "erasure_request_id", "category", "resource_key_digest", "action", name="uq_deletion_tombstone_fact"),
        Index("ix_deletion_tombstone_subject", "tenant_id", "subject_user_id", "recorded_at", "id"),
        CheckConstraint("action IN ('quarantined', 'erased')", name="ck_deletion_tombstone_action"),
        {"comment": "实际维护后的追加式最小事实，主体/请求不设业务外键以避免未来擦除破坏证明；当前不会生成记录。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="最小删除/隔离事实ID。")
    tenant_id: Mapped[UUID] = mapped_column(comment="原事实租户ID；保留独立证明，不依赖被恢复的租户记录。")
    subject_user_id: Mapped[UUID] = mapped_column(comment="原主体ID，不含邮箱/姓名，不依赖被擦除身份外键。")
    erasure_request_id: Mapped[UUID] = mapped_column(comment="原获批申请ID，未来维护者须独立验证，当前不生成。")
    category: Mapped[str] = mapped_column(String(32), comment="实际维护的数据类别。")
    resource_key_digest: Mapped[str] = mapped_column(String(64), comment="稳定资源定位摘要；不保存原始正文或秘密。")
    scope_digest: Mapped[str] = mapped_column(String(64), comment="实际批准维护范围的摘要。")
    action: Mapped[str] = mapped_column(String(16), comment="实际已隔离或已擦除；不会由预览填入。")
    evidence_digest: Mapped[str] = mapped_column(String(64), comment="独立维护者实际结果证明摘要。")
    maintenance_identity: Mapped[str] = mapped_column(String(100), comment="未来独立维护执行身份标识，不保存令牌。")
    record_digest: Mapped[str] = mapped_column(String(64), comment="完整最小事实的规范化摘要。")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="实际维护后追加时间；当前不回填。")


class RetentionCleanupEvent(Base):
    """独立维护批次的追加账本；没有普通应用领取或删除入口。"""

    __tablename__ = "retention_cleanup_events"
    __table_args__ = (
        UniqueConstraint("batch_id", "revision", name="uq_retention_cleanup_revision"),
        Index("uq_retention_cleanup_sequence", "tenant_id", "subject_user_id", "job_id", "batch_sequence",
            unique=True, postgresql_where=text("revision = 1"), sqlite_where=text("revision = 1")),
        Index("ix_retention_cleanup_subject", "tenant_id", "subject_user_id", "batch_id", "revision"),
        CheckConstraint("revision BETWEEN 1 AND 32 AND batch_sequence BETWEEN 1 AND 1000000", name="ck_retention_cleanup_revision"),
        CheckConstraint("state IN ('claimed','dispatched','verifying','completed','not_executed','needs_attention')", name="ck_retention_cleanup_state"),
        CheckConstraint("length(CAST(payload AS TEXT)) <= 131072", name="ck_retention_cleanup_payload"),
        {"comment": "独立维护者的批次计划/派发/验证/事实回执追加历史；不授予实际清理许可。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, comment="事件随机ID。")
    tenant_id: Mapped[UUID] = mapped_column(comment="原租户，不设待擦除业务外键。")
    subject_user_id: Mapped[UUID] = mapped_column(comment="原主体，不保存个人资料。")
    job_id: Mapped[UUID] = mapped_column(comment="可信维护作业ID，不由模型派生。")
    batch_id: Mapped[UUID] = mapped_column(comment="精确资源批次ID，派发事实永久保留。")
    batch_sequence: Mapped[int] = mapped_column(Integer, comment="作业内连续批次序号，前批完成才可推进。")
    revision: Mapped[int] = mapped_column(Integer, comment="批次追加事件序号，最多32。")
    state: Mapped[str] = mapped_column(String(24), comment="事件当时状态，不通过覆盖旧行更新。")
    plan_digest: Mapped[str] = mapped_column(String(64), comment="固定精确计划摘要。")
    event_digest: Mapped[str] = mapped_column(String(64), comment="包含前摘要的完整事件SHA256。")
    previous_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="前事件摘要；首条为空。")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="封闭计划/短租约/状态/验证及事实引用，无正文或秘密。")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="实际追加时间，不补造历史。")


class RetentionPropagationEvent(Base):
    """删除传播只读核对的最小追加历史，不保存原定位或执行许可。"""

    __tablename__ = "retention_propagation_events"
    __table_args__ = (
        UniqueConstraint("request_id", "revision", name="uq_retention_propagation_revision"),
        Index("ix_retention_propagation_subject", "tenant_id", "subject_user_id", "recorded_at", "id"),
        CheckConstraint("revision BETWEEN 1 AND 18", name="ck_retention_propagation_revision"),
        CheckConstraint("event_type IN ('inspection_started','target_observed','inspection_finished')",
            name="ck_retention_propagation_event"),
        CheckConstraint("length(CAST(payload AS TEXT)) <= 8192", name="ck_retention_propagation_payload"),
        {"comment": "删除传播只读核对的独立追加审计，不证明实际擦除完成。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, comment="核对审计随机事件ID。")
    tenant_id: Mapped[UUID] = mapped_column(comment="原事实租户，无业务外键。")
    subject_user_id: Mapped[UUID] = mapped_column(comment="原事实主体，无个人资料。")
    request_id: Mapped[UUID] = mapped_column(comment="固定传播核对请求ID。")
    request_digest: Mapped[str] = mapped_column(String(64), comment="完整原请求摘要，不保存原定位。")
    revision: Mapped[int] = mapped_column(Integer, comment="连续追加序号，最多18条。")
    event_type: Mapped[str] = mapped_column(String(24), comment="开始、逐项目标观察或结束。")
    audit_digest: Mapped[str] = mapped_column(String(64), comment="原封闭审计消息摘要。")
    previous_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="前持久记录摘要，首条为空。")
    record_digest: Mapped[str] = mapped_column(String(64), comment="含前摘要和实际记录时间的完整摘要。")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
        comment="最小核对摘要与结果，无内容、目标地址或秘密。")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="实际追加时间。")


class RestoreKnowledgeEvent(Base):
    """恢复知识隔离的唯一意图与最小追加证明；不授予恢复开放许可。"""

    __tablename__ = "restore_knowledge_events"
    __table_args__ = (
        UniqueConstraint("request_id", "revision", name="uq_restore_knowledge_revision"),
        Index("ix_restore_knowledge_subject", "tenant_id", "subject_user_id", "restore_id", "recorded_at", "id"),
        CheckConstraint("revision BETWEEN 1 AND 8", name="ck_restore_knowledge_revision"),
        CheckConstraint("event_type IN ('quarantine_requested','commit_observed','quarantine_verified',"
            "'target_not_observed','quarantine_not_confirmed')", name="ck_restore_knowledge_event"),
        CheckConstraint("length(CAST(payload AS TEXT)) <= 8192", name="ck_restore_knowledge_payload"),
        {"comment": "恢复知识隔离的唯一意图和最小追加审计，不代表擦除或恢复开放。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, comment="恢复审计随机事件ID。")
    tenant_id: Mapped[UUID] = mapped_column(comment="原事实租户，无业务外键。")
    subject_user_id: Mapped[UUID] = mapped_column(comment="原事实主体，无个人资料。")
    restore_id: Mapped[UUID] = mapped_column(comment="原恢复操作ID。")
    request_id: Mapped[UUID] = mapped_column(comment="原固定重施请求ID，不可重新领取。")
    request_digest: Mapped[str] = mapped_column(String(64), comment="完整恢复请求摘要。")
    revision: Mapped[int] = mapped_column(Integer, comment="连续追加序号，最多8条。")
    event_type: Mapped[str] = mapped_column(String(32), comment="唯一意图、提交观察或只读结果。")
    audit_digest: Mapped[str] = mapped_column(String(64), comment="原最小审计消息摘要。")
    previous_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="前持久记录摘要；首条为空。")
    record_digest: Mapped[str] = mapped_column(String(64), comment="原主体、事件、序号及前摘要链。")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
        comment="最小恢复证明，无定位、正文、角色或秘密。")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="实际追加时间。")


class RestoreCredentialEvent(Base):
    """恢复凭据修订隔离的唯一意图与最小追加证明；不授予秘密撤销或恢复开放许可。"""

    __tablename__ = "restore_credential_events"
    __table_args__ = (
        UniqueConstraint("request_id", "revision", name="uq_restore_credential_revision"),
        Index("ix_restore_credential_subject", "tenant_id", "subject_user_id", "restore_id", "recorded_at", "id"),
        CheckConstraint("revision BETWEEN 1 AND 8", name="ck_restore_credential_revision"),
        CheckConstraint("event_type IN ('quarantine_requested','commit_observed','quarantine_verified',"
            "'target_not_observed','quarantine_not_confirmed')", name="ck_restore_credential_event"),
        CheckConstraint("(event_type <> 'quarantine_requested' OR revision = 1) AND "
            "(event_type <> 'commit_observed' OR revision = 2)", name="ck_restore_credential_intent"),
        CheckConstraint("octet_length(CAST(payload AS TEXT)) <= 8192", name="ck_restore_credential_payload"),
        {"comment": "恢复凭据隔离的唯一意图和最小追加审计，不代表擦除或恢复开放。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, comment="恢复审计随机事件ID。")
    tenant_id: Mapped[UUID] = mapped_column(comment="原事实租户，无业务外键。")
    subject_user_id: Mapped[UUID] = mapped_column(comment="原事实主体，无个人资料。")
    restore_id: Mapped[UUID] = mapped_column(comment="原恢复操作ID。")
    request_id: Mapped[UUID] = mapped_column(comment="原固定重施请求ID，不可重新领取。")
    request_digest: Mapped[str] = mapped_column(String(64), comment="完整恢复请求摘要。")
    revision: Mapped[int] = mapped_column(Integer, comment="连续追加序号，最多8条。")
    event_type: Mapped[str] = mapped_column(String(32), comment="唯一意图、提交观察或只读结果。")
    audit_digest: Mapped[str] = mapped_column(String(64), comment="原最小审计消息摘要。")
    previous_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="前持久记录摘要；首条为空。")
    record_digest: Mapped[str] = mapped_column(String(64), comment="原主体、事件、序号及前摘要链。")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
        comment="最小恢复证明，无定位、正文、角色或秘密。")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="实际追加时间。")


class RestoreSessionEvent(Base):
    """恢复登录会话隔离的唯一意图与最小追加证明；不授予恢复开放许可。"""

    __tablename__ = "restore_session_events"
    __table_args__ = (
        UniqueConstraint("request_id", "revision", name="uq_restore_session_revision"),
        Index("ix_restore_session_subject", "tenant_id", "subject_user_id", "restore_id", "recorded_at", "id"),
        CheckConstraint("revision BETWEEN 1 AND 8", name="ck_restore_session_revision"),
        CheckConstraint("event_type IN ('quarantine_requested','commit_observed','quarantine_verified',"
            "'target_not_observed')", name="ck_restore_session_event"),
        CheckConstraint("(event_type <> 'quarantine_requested' OR revision = 1) AND "
            "(event_type <> 'commit_observed' OR revision = 2)", name="ck_restore_session_intent"),
        CheckConstraint("octet_length(CAST(payload AS TEXT)) <= 8192", name="ck_restore_session_payload"),
        {"comment": "恢复登录会话隔离的唯一意图和最小追加审计，不代表擦除或恢复开放。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, comment="恢复审计随机事件ID。")
    tenant_id: Mapped[UUID] = mapped_column(comment="原事实租户，无业务外键。")
    subject_user_id: Mapped[UUID] = mapped_column(comment="原事实主体，无个人资料。")
    restore_id: Mapped[UUID] = mapped_column(comment="原恢复操作ID。")
    request_id: Mapped[UUID] = mapped_column(comment="原固定重施请求ID，不可重新领取。")
    request_digest: Mapped[str] = mapped_column(String(64), comment="完整恢复请求摘要。")
    revision: Mapped[int] = mapped_column(Integer, comment="连续追加序号，最多8条。")
    event_type: Mapped[str] = mapped_column(String(32), comment="唯一意图、提交观察或只读结果。")
    audit_digest: Mapped[str] = mapped_column(String(64), comment="原最小审计消息摘要。")
    previous_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="前持久记录摘要；首条为空。")
    record_digest: Mapped[str] = mapped_column(String(64), comment="原主体、事件、序号及前摘要链。")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
        comment="最小恢复证明，无定位、正文、角色或秘密。")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="实际追加时间。")


class KnowledgeIngestionJob(Base):
    """固定导入范围与可围栏执行状态；不代表来源已批准。"""

    __tablename__ = "knowledge_ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "requested_by", "idempotency_key", name="uq_ingestion_job_request"),
        Index("ix_ingestion_job_owner", "tenant_id", "requested_by", "created_at", "id"),
        CheckConstraint("revision >= 1 AND generation >= attempts AND attempts BETWEEN 0 AND 3", name="ck_ingestion_job_counters"),
        CheckConstraint("status IN ('queued','running','retry_wait','completed','failed','cancelled')", name="ck_ingestion_job_status"),
        CheckConstraint("(status = 'running' AND owner_id IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status <> 'running' AND owner_id IS NULL AND lease_expires_at IS NULL)", name="ck_ingestion_job_owner"),
        CheckConstraint("deadline_at > created_at", name="ck_ingestion_job_deadline"),
        {"comment": "显式批准范围的有限导入任务；没有默认来源或后台领取入口。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, comment="服务器生成的任务ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="可信申请租户。")
    source_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_sources.id"), comment="固定知识来源。")
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="实际申请人，其他管理员不能隐式代办。")
    idempotency_key: Mapped[str] = mapped_column(String(200), comment="同租户本人创建请求幂等键。")
    request_digest: Mapped[str] = mapped_column(String(64), comment="固定范围和预算请求摘要，不含随机ID或创建时间。")
    definition: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="严格任务定义，仅摘要/引用/预算，无正文或原始游标。")
    definition_digest: Mapped[str] = mapped_column(String(64), comment="不可变完整任务定义摘要。")
    state: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="严格执行状态、检查点和累计计数。")
    state_digest: Mapped[str] = mapped_column(String(64), comment="当前状态完整性摘要，不能授予执行许可。")
    status: Mapped[str] = mapped_column(String(32), comment="状态索引列，与严格状态JSON绑定。")
    revision: Mapped[int] = mapped_column(Integer, comment="每次状态变化递增的并发版本。")
    generation: Mapped[int] = mapped_column(Integer, comment="领取或取消递增的围栏代次。")
    attempts: Mapped[int] = mapped_column(Integer, comment="累计实际领取次数，重试不清零。")
    owner_id: Mapped[UUID | None] = mapped_column(nullable=True, comment="当前执行者随机ID，非用户授权令牌。")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="当前执行租约截止时间。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="服务器固定创建时间。")
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="服务器固定任务截止时间。")


class KnowledgeIngestionExecutionRequest(Base):
    """本人显式提交的单次Worker执行意图；领取后永久消费，不恢复或重派。"""

    __tablename__ = "knowledge_ingestion_execution_requests"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_ingestion_execution_request_job"),
        Index("ix_ingestion_execution_request_queue", "status", "created_at", "id"),
        CheckConstraint("status IN ('queued','claimed','completed','rejected','unconfirmed')", name="ck_ingestion_execution_request_status"),
        CheckConstraint("revision >= 1 AND expected_job_revision >= 1 AND auth_generation >= 0", name="ck_ingestion_execution_request_revision"),
        CheckConstraint("expires_at > created_at", name="ck_ingestion_execution_request_window"),
        CheckConstraint("(status IN ('claimed','completed','unconfirmed') AND claim_id IS NOT NULL AND claimed_at IS NOT NULL) OR (status IN ('queued','rejected') AND claim_id IS NULL AND claimed_at IS NULL)", name="ck_ingestion_execution_request_claim"),
        CheckConstraint("(status IN ('completed','rejected','unconfirmed') AND finished_at IS NOT NULL) OR (status IN ('queued','claimed') AND finished_at IS NULL)", name="ck_ingestion_execution_request_finish"),
        CheckConstraint("(status IN ('rejected','unconfirmed') AND error_code IS NOT NULL) OR (status IN ('queued','claimed','completed') AND error_code IS NULL)", name="ck_ingestion_execution_request_error"),
        {"comment": "人工单次导入派发账本，不是定时更新或来源批准。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="一次执行意图ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="可信租户。")
    job_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_ingestion_jobs.id"), comment="本人原导入任务；全局唯一，不重派。")
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="原任务申请人。")
    auth_session_id: Mapped[UUID] = mapped_column(ForeignKey("auth_sessions.id"), comment="显式提交时会话引用，不保存令牌。")
    auth_generation: Mapped[int] = mapped_column(Integer, comment="原会话授权代次。")
    expected_job_revision: Mapped[int] = mapped_column(Integer, comment="本人明确确认的任务修订。")
    scope_digest: Mapped[str] = mapped_column(String(64), comment="原任务范围摘要。")
    record_digest: Mapped[str] = mapped_column(String(64), comment="不可变执行意图摘要，不产生授权。")
    status: Mapped[str] = mapped_column(String(16), comment="queued/claimed/completed/rejected/unconfirmed。")
    revision: Mapped[int] = mapped_column(Integer, comment="状态单向递增修订。")
    claim_id: Mapped[UUID | None] = mapped_column(nullable=True, comment="唯一领取随机值，旧执行者不可重领。")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="原领取提交时间。")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="本地执行意图终态时间，不替代原Job结果。")
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True, comment="固定拒绝/未确认原因，无原始异常。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="本人显式提交时间。")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="最多60秒且不超过原来源/任务/会话窗口。")


class KnowledgeIngestionCreationStop(Base):
    """本人显式封锁尚未生成任务的创建请求；只拒绝迟到创建，不授予执行许可。"""

    __tablename__ = "knowledge_ingestion_creation_stops"
    __table_args__ = (
        UniqueConstraint("tenant_id", "requested_by", "request_key", name="uq_ingestion_creation_stop_request"),
        CheckConstraint("length(request_key) BETWEEN 1 AND 200", name="ck_ingestion_creation_stop_key"),
        {"comment": "追加式创建请求停止事实，与当前主体锁串行，禁止迟到请求重新建任务。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="停止事实唯一标识。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="当前可信租户。")
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="当前本人，不允许管理员代办。")
    request_key: Mapped[str] = mapped_column(String(200), comment="本人原创建请求键，无来源路径或正文。")
    record_digest: Mapped[str] = mapped_column(String(64), comment="停止身份/请求/时间的完整性摘要。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="服务器实际保存停止事实的时间。")


class KnowledgeIngestionObject(Base):
    """跨Job稳定的来源对象到知识文档绑定，软删除后仍保留。"""

    __tablename__ = "knowledge_ingestion_objects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_id", "identity_policy_digest", "object_key", name="uq_ingestion_object_identity"),
        UniqueConstraint("document_id", name="uq_ingestion_object_document"),
        {"comment": "稳定来源对象绑定；不可删除或改绑，避免重复拉取创建第二文档。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="绑定ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="固定租户。")
    source_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_sources.id"), comment="固定来源。")
    identity_policy_digest: Mapped[str] = mapped_column(String(64), comment="来源身份规范化策略摘要。")
    object_key: Mapped[str] = mapped_column(String(64), comment="来源对象域隔离摘要，不保存路径或外部ID。")
    document_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_documents.id"), comment="唯一绑定的同租户同来源文档。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="首次绑定时间。")


class KnowledgeIngestionMaterialization(Base):
    """同内容/元数据/ACL/解析策略复用既有候选版本，不复用旧批准。"""

    __tablename__ = "knowledge_ingestion_materializations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_id", "draft_key", name="uq_ingestion_materialization_draft"),
        UniqueConstraint("version_id", name="uq_ingestion_materialization_version"),
        {"comment": "追加式草稿物化事实，不自动发布、审核或更改文档ACL。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="物化事实ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="固定租户。")
    source_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_sources.id"), comment="固定来源。")
    object_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_ingestion_objects.id"), comment="稳定对象绑定。")
    job_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_ingestion_jobs.id"), comment="首次物化任务。")
    draft_key: Mapped[str] = mapped_column(String(64), comment="内容/元数据/权限/解析策略的草稿意向键。")
    version_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_versions.id"), comment="首次创建的不可变候选知识版本。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="首次物化时间。")


class KnowledgeIngestionPage(Base):
    """已提交页面的不可变回执；失败尝试不占用页面幂等键。"""

    __tablename__ = "knowledge_ingestion_pages"
    __table_args__ = (
        UniqueConstraint("job_id", "page_key", name="uq_ingestion_page_key"),
        {"comment": "与草稿、物化、审计和检查点同事务提交的只含元数据页面回执。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="页面回执ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="固定租户。")
    job_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_ingestion_jobs.id"), comment="固定任务。")
    page_key: Mapped[str] = mapped_column(String(64), comment="同Job输入检查点的确定性键。")
    request_digest: Mapped[str] = mapped_column(String(64), comment="规范候选输入摘要，不含owner/revision/generation。")
    result_digest: Mapped[str] = mapped_column(String(64), comment="不可变完整页面结果摘要。")
    receipt: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="严格页面回执，仅目标ID/摘要/状态，无正文。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, comment="服务器提交时间。")


class ModelRoutingPolicyVersion(Base):
    """策略候选的不可变版本；摘要引用不代表独立批准已经生效。"""

    __tablename__ = "model_routing_policy_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "policy_id", "revision", name="uq_routing_policy_revision"),
        UniqueConstraint("tenant_id", "policy_id", "id", name="uq_routing_policy_scope"),
        ForeignKeyConstraint(["tenant_id", "policy_id", "previous_version_id"],
            ["model_routing_policy_versions.tenant_id", "model_routing_policy_versions.policy_id",
                "model_routing_policy_versions.id"], name="fk_routing_policy_previous"),
        CheckConstraint("revision >= 1", name="ck_routing_policy_revision"),
        CheckConstraint("(revision = 1 AND previous_version_id IS NULL AND previous_policy_digest IS NULL) OR "
            "(revision > 1 AND previous_version_id IS NOT NULL AND previous_policy_digest IS NOT NULL "
            "AND previous_version_id <> id)", name="ck_routing_policy_previous"),
        CheckConstraint("valid_until > created_at", name="ck_routing_policy_window"),
        Index("ix_routing_policy_tenant_created", "tenant_id", "created_at", "id"),
        {"comment": "追加式模型路由策略候选版本，无批准或执行开关。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, comment="原提交固定的版本ID，用于只读查证未知提交。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="可信租户。")
    policy_id: Mapped[UUID] = mapped_column(comment="同租户策略系列ID。")
    revision: Mapped[int] = mapped_column(Integer, comment="策略系列连续版本号。")
    previous_version_id: Mapped[UUID | None] = mapped_column(nullable=True, comment="同租户同策略前版。")
    previous_policy_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="前版完整规范摘要。")
    policy_digest: Mapped[str] = mapped_column(String(64), comment="包含服务器身份和时间的完整版本SHA-256。")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="封闭策略版本，无URL、秘密或规则代码。")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="实际保存版本的管理员。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="服务器保存时间。")
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="原版本期限，不允许原地延期。")


class ModelRoutingPolicyRevocation(Base):
    """策略版本的永久撤销事实，与原版本分开追加且不可清除。"""

    __tablename__ = "model_routing_policy_revocations"
    __table_args__ = (
        UniqueConstraint("policy_version_id", name="uq_routing_policy_revocation"),
        ForeignKeyConstraint(["tenant_id", "policy_id", "policy_version_id"],
            ["model_routing_policy_versions.tenant_id", "model_routing_policy_versions.policy_id",
                "model_routing_policy_versions.id"], name="fk_routing_policy_revoked_scope"),
        Index("ix_routing_policy_revoked_tenant", "tenant_id", "revoked_at", "id"),
        {"comment": "独立追加式策略撤销事实，不撤销或改写历史模型调用。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, comment="撤销事实ID。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="可信租户。")
    policy_id: Mapped[UUID] = mapped_column(comment="原策略系列ID。")
    policy_version_id: Mapped[UUID] = mapped_column(comment="原精确版本，唯一撤销。")
    policy_digest: Mapped[str] = mapped_column(String(64), comment="原策略完整摘要。")
    record_digest: Mapped[str] = mapped_column(String(64), comment="原撤销范围/人员/时间/原因的完整摘要。")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="封闭撤销事实，无任意说明正文。")
    revoked_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), comment="实际撤销管理员。")
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="真实撤销时间，不可改写。")


class ModelRoutingCircuitEvent(Base):
    """完整原范围的熔断观察/恢复领取；不提供模型调用许可。"""

    __tablename__ = "model_routing_circuit_events"
    __table_args__ = (
        UniqueConstraint("scope_digest", "sequence", name="uq_routing_circuit_sequence"),
        UniqueConstraint("tenant_id", "call_id", "kind", name="uq_routing_circuit_call_kind"),
        CheckConstraint("sequence BETWEEN 1 AND 256", name="ck_routing_circuit_sequence"),
        CheckConstraint("kind IN ('observation','recovery_claimed')", name="ck_routing_circuit_kind"),
        CheckConstraint("(sequence=1) = (previous_event_digest IS NULL)", name="ck_routing_circuit_previous"),
        Index("ix_routing_circuit_tenant_run", "tenant_id", "run_id", "created_at"),
        {"comment": "不可变熔断观察与恢复领取历史；领取不授予模型派发许可。"},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, comment="原操作固定ID；未知提交仅查原ID。")
    scope_digest: Mapped[str] = mapped_column(String(64), comment="租户/原策略/完整候选/控制摘要。")
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), comment="原调用租户。")
    policy_version_id: Mapped[UUID] = mapped_column(ForeignKey("model_routing_policy_versions.id"), comment="不可变原策略。")
    candidate_id: Mapped[UUID] = mapped_column(comment="原完整候选ID。")
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), comment="产生观察或领取的原Run。")
    call_id: Mapped[UUID] = mapped_column(ForeignKey("model_call_ledger.id"), comment="原物理预留调用。")
    sequence: Mapped[int] = mapped_column(Integer, comment="同范围从1连续递增，最多256条。")
    previous_event_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="直接前条事件摘要，首条为空。")
    kind: Mapped[str] = mapped_column(String(30), comment="observation或recovery_claimed。")
    event_digest: Mapped[str] = mapped_column(String(64), comment="事件规范摘要，供后条引用。")
    record_digest: Mapped[str] = mapped_column(String(64), comment="完整范围/控制/事件/批准摘要。")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), comment="封闭元数据，无模型正文或凭据。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="首次持久化时间。")


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
