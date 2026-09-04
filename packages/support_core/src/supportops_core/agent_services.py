"""Agent 控制面生命周期、授权目录和发布期配置解析服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import (
    TOOL_REGISTRY,
    AgentConfigV2,
    agent_config_digest,
    normalize_agent_config,
    parse_agent_config,
)
from supportops_core.auth import IdentityContext
from supportops_core.config import Settings
from supportops_core.enums import (
    AGENT_USE_ROLES,
    ROLE_PLATFORM_ADMIN,
    AgentStatus,
    ApprovalPolicy,
    AuditAction,
    AuditResourceType,
    GrantSubjectType,
    ModelEndpointStatus,
    ModelVerificationStatus,
    ResponseStrategy,
)
from supportops_core.models import (
    Agent,
    AgentAccessGrant,
    AgentAuditEvent,
    AgentDraft,
    AgentVersion,
    ModelCredential,
    ModelEndpoint,
    ModelEndpointVersion,
    User,
    utc_now,
)
from supportops_core.services import ResourceNotFoundError


class PermissionDeniedError(PermissionError):
    pass


class AgentConflictError(ValueError):
    pass


class AgentRevisionConflictError(AgentConflictError):
    def __init__(self, current_revision: int) -> None:
        super().__init__("Agent 草稿已被其他管理员修改")
        self.current_revision = current_revision


class AgentConfigurationError(ValueError):
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        super().__init__("Agent 配置校验失败")
        self.issues = issues


@dataclass(frozen=True, slots=True)
class ResolvedAgentConfig:
    """发布校验后固定下来的模型版本、凭据 revision 和工具集合。"""

    config: AgentConfigV2
    model_version: ModelEndpointVersion
    credential_revision: int
    tool_ids: list[str]


def require_platform_admin(identity: IdentityContext) -> None:
    """在服务层再次强制平台管理员角色，避免路由遗漏授权。"""
    if ROLE_PLATFORM_ADMIN not in identity.principal.roles:
        raise PermissionDeniedError("需要平台管理员权限")


async def append_agent_audit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent_id: UUID,
    actor_user_id: UUID,
    action: AuditAction,
    correlation_id: str,
    version_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """追加不含秘密和完整 Prompt 的 Agent 控制面审计事件。"""
    session.add(
        AgentAuditEvent(
            tenant_id=tenant_id,
            resource_type=AuditResourceType.AGENT,
            agent_id=agent_id,
            action=action,
            actor_user_id=actor_user_id,
            version_id=version_id,
            metadata_payload=metadata or {},
            correlation_id=correlation_id,
        )
    )
    await session.flush()


async def get_agent(session: AsyncSession, *, agent_id: UUID, tenant_id: UUID) -> Agent:
    agent = await session.scalar(
        select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
    )
    if agent is None:
        raise ResourceNotFoundError("Agent 不存在")
    return agent


def ensure_agent_mutable(agent: Agent) -> None:
    if agent.read_only:
        raise AgentConflictError("历史迁移 Agent 为只读，不能修改、发布、授权或激活")


async def list_admin_agents(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    status: AgentStatus | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Agent]:
    query = select(Agent).where(Agent.tenant_id == tenant_id)
    if status is not None:
        query = query.where(Agent.status == status)
    return list(
        (
            await session.scalars(
                query.order_by(Agent.updated_at.desc(), Agent.id).limit(limit).offset(offset)
            )
        ).all()
    )


async def count_admin_agents(
    session: AsyncSession, *, tenant_id: UUID, status: AgentStatus | None = None
) -> int:
    query = select(func.count()).select_from(Agent).where(Agent.tenant_id == tenant_id)
    if status is not None:
        query = query.where(Agent.status == status)
    return int(await session.scalar(query) or 0)


async def create_agent(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    slug: str,
    name: str,
    logo_url: str | None,
    description: str,
    welcome_message: str,
    suggested_prompts: list[str],
    config: object,
    correlation_id: str,
) -> Agent:
    """原子创建稳定 Agent 身份和 revision 1 草稿。"""
    duplicate = await session.scalar(
        select(Agent.id).where(Agent.tenant_id == tenant_id, Agent.slug == slug)
    )
    if duplicate is not None:
        raise AgentConflictError("Agent slug 已存在")
    try:
        parsed = parse_agent_config(config)
    except ValidationError as exc:
        raise AgentConfigurationError(_pydantic_issues(exc)) from exc
    agent = Agent(
        tenant_id=tenant_id,
        slug=slug,
        name=name,
        logo_url=logo_url,
        description=description,
        welcome_message=welcome_message,
        suggested_prompts=suggested_prompts,
        status=AgentStatus.DRAFT,
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    session.add(agent)
    await session.flush()
    session.add(
        AgentDraft(
            agent_id=agent.id,
            tenant_id=tenant_id,
            revision=1,
            schema_version=parsed.schema_version,
            config=normalize_agent_config(parsed),
            updated_by=actor_user_id,
        )
    )
    await append_agent_audit(
        session,
        tenant_id=tenant_id,
        agent_id=agent.id,
        actor_user_id=actor_user_id,
        action=AuditAction.CREATED,
        correlation_id=correlation_id,
    )
    return agent


async def update_agent_profile(
    session: AsyncSession,
    *,
    agent_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    name: str,
    logo_url: str | None,
    description: str,
    welcome_message: str,
    suggested_prompts: list[str],
    correlation_id: str,
) -> Agent:
    """更新动态展示资料，不改变已经发布的运行版本。"""
    agent = await get_agent(session, agent_id=agent_id, tenant_id=tenant_id)
    ensure_agent_mutable(agent)
    agent.name = name
    agent.logo_url = logo_url
    agent.description = description
    agent.welcome_message = welcome_message
    agent.suggested_prompts = suggested_prompts
    agent.updated_by = actor_user_id
    agent.updated_at = utc_now()
    await append_agent_audit(
        session,
        tenant_id=tenant_id,
        agent_id=agent.id,
        actor_user_id=actor_user_id,
        action=AuditAction.PROFILE_UPDATED,
        correlation_id=correlation_id,
    )
    return agent


async def get_agent_draft(session: AsyncSession, *, agent_id: UUID, tenant_id: UUID) -> AgentDraft:
    draft = await session.scalar(
        select(AgentDraft).where(
            AgentDraft.agent_id == agent_id,
            AgentDraft.tenant_id == tenant_id,
        )
    )
    if draft is None:
        raise ResourceNotFoundError("Agent 草稿不存在")
    return draft


async def update_agent_draft(
    session: AsyncSession,
    *,
    agent_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    expected_revision: int,
    config: object,
    correlation_id: str,
) -> AgentDraft:
    """使用期望 revision 保存草稿，冲突时不覆盖服务端或本地内容。"""
    agent = await get_agent(session, agent_id=agent_id, tenant_id=tenant_id)
    ensure_agent_mutable(agent)
    try:
        parsed = parse_agent_config(config)
    except ValidationError as exc:
        raise AgentConfigurationError(_pydantic_issues(exc)) from exc
    result = await session.execute(
        update(AgentDraft)
        .where(
            AgentDraft.agent_id == agent_id,
            AgentDraft.tenant_id == tenant_id,
            AgentDraft.revision == expected_revision,
        )
        .values(
            revision=AgentDraft.revision + 1,
            schema_version=parsed.schema_version,
            config=normalize_agent_config(parsed),
            updated_by=actor_user_id,
            updated_at=utc_now(),
        )
    )
    if cast(CursorResult[Any], result).rowcount != 1:
        current = await get_agent_draft(session, agent_id=agent_id, tenant_id=tenant_id)
        raise AgentRevisionConflictError(current.revision)
    draft = await get_agent_draft(session, agent_id=agent_id, tenant_id=tenant_id)
    await append_agent_audit(
        session,
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_user_id=actor_user_id,
        action=AuditAction.DRAFT_UPDATED,
        correlation_id=correlation_id,
        metadata={"revision": draft.revision},
    )
    return draft


async def validate_agent_config(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    config: object,
    settings: Settings,
) -> ResolvedAgentConfig:
    """校验 Schema、平台限额、模型能力、凭据和工具并解析固定引用。"""
    try:
        parsed = parse_agent_config(config)
    except ValidationError as exc:
        raise AgentConfigurationError(_pydantic_issues(exc)) from exc
    issues: list[dict[str, Any]] = []
    runtime = parsed.runtime
    if runtime.model_call_limit > settings.agent_model_call_limit_max:
        issues.append(_issue("runtime.model_call_limit", "超过平台模型调用上限"))
    if runtime.tool_call_limit > settings.agent_tool_call_limit_max:
        issues.append(_issue("runtime.tool_call_limit", "超过平台工具调用上限"))
    if runtime.run_timeout_seconds > settings.agent_run_timeout_seconds_max:
        issues.append(_issue("runtime.run_timeout_seconds", "超过平台运行超时上限"))
    if runtime.max_parallel_tools > settings.agent_max_parallel_tools_max:
        issues.append(_issue("runtime.max_parallel_tools", "超过平台并行工具上限"))

    endpoint = await session.scalar(
        select(ModelEndpoint).where(
            ModelEndpoint.id == parsed.model.model_endpoint_id,
            ModelEndpoint.tenant_id == tenant_id,
        )
    )
    model_version: ModelEndpointVersion | None = None
    credential_revision = 0
    if endpoint is None or endpoint.status != ModelEndpointStatus.ACTIVE:
        issues.append(_issue("model.model_endpoint_id", "模型端点不存在或未启用"))
    elif endpoint.active_version_id is None:
        issues.append(_issue("model.model_endpoint_id", "模型端点没有活动版本"))
    else:
        model_version = await session.scalar(
            select(ModelEndpointVersion).where(
                ModelEndpointVersion.id == endpoint.active_version_id,
                ModelEndpointVersion.tenant_id == tenant_id,
                ModelEndpointVersion.endpoint_id == endpoint.id,
            )
        )
        if model_version is None or model_version.verification_status not in {
            ModelVerificationStatus.VERIFIED,
            ModelVerificationStatus.PARTIAL,
        }:
            issues.append(_issue("model.model_endpoint_id", "模型端点尚未通过连接测试"))
        elif model_version.verification_status == ModelVerificationStatus.PARTIAL:
            issues.append(_issue("model.model_endpoint_id", "模型端点能力验证不完整"))
        if model_version is not None:
            credential = await session.get(ModelCredential, model_version.credential_id)
            if credential is None or credential.tenant_id != tenant_id:
                issues.append(_issue("model.model_endpoint_id", "模型端点凭据不可用"))
            else:
                credential_revision = credential.revision
            capabilities = model_version.capabilities
            if parsed.runtime.response_strategy == ResponseStrategy.TOOL and not capabilities.get(
                "tool_calling", False
            ):
                issues.append(
                    _issue("runtime.response_strategy", "ToolStrategy 需要模型支持工具调用")
                )
            if any(binding.enabled for binding in parsed.tools) and not capabilities.get(
                "tool_calling", False
            ):
                issues.append(_issue("tools", "所选模型未验证工具调用能力"))
            if (
                parsed.runtime.response_strategy == ResponseStrategy.PROVIDER
                and not capabilities.get("structured_output", False)
            ):
                issues.append(_issue("runtime.response_strategy", "模型未验证原生结构化输出"))
            generation = parsed.model.generation
            defaults = model_version.defaults
            context_window = defaults.get("context_window_tokens")
            if (
                runtime.max_input_tokens is not None
                and isinstance(context_window, int)
                and runtime.max_input_tokens > context_window
            ):
                issues.append(_issue("runtime.max_input_tokens", "超过模型上下文窗口"))
            max_output = defaults.get("max_output_tokens")
            if isinstance(max_output, int) and generation.max_output_tokens > max_output:
                issues.append(_issue("model.generation.max_output_tokens", "超过模型输出上限"))
            if runtime.max_cost_usd is not None:
                pricing = model_version.pricing
                has_input_price = isinstance(pricing.get("input_per_million_tokens"), int | float)
                has_output_price = isinstance(pricing.get("output_per_million_tokens"), int | float)
                if not has_input_price or not has_output_price:
                    issues.append(
                        _issue("runtime.max_cost_usd", "模型缺少执行成本预算所需的定价信息")
                    )

    tool_ids: list[str] = []
    for index, binding in enumerate(parsed.tools):
        if not binding.enabled:
            continue
        tool = TOOL_REGISTRY.get(binding.tool_id)
        if tool is None:
            issues.append(_issue(f"tools.{index}.tool_id", "工具未在平台注册"))
            continue
        if tool.risk_level == "high" and binding.approval_policy != ApprovalPolicy.REQUIRED:
            issues.append(_issue(f"tools.{index}.approval_policy", "高风险工具必须人工审批"))
        tool_ids.append(tool.id)

    if issues or model_version is None:
        raise AgentConfigurationError(issues)
    return ResolvedAgentConfig(
        config=parsed,
        model_version=model_version,
        credential_revision=credential_revision,
        tool_ids=tool_ids,
    )


async def publish_agent_version(
    session: AsyncSession,
    *,
    agent_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    expected_revision: int,
    release_notes: str,
    settings: Settings,
    correlation_id: str,
) -> AgentVersion:
    """从已校验草稿创建不可变、单调递增的 AgentVersion。"""
    agent = await get_agent(session, agent_id=agent_id, tenant_id=tenant_id)
    ensure_agent_mutable(agent)
    draft = await get_agent_draft(session, agent_id=agent_id, tenant_id=tenant_id)
    if draft.revision != expected_revision:
        raise AgentRevisionConflictError(draft.revision)
    resolved = await validate_agent_config(
        session, tenant_id=tenant_id, config=draft.config, settings=settings
    )
    current_number = await session.scalar(
        select(func.coalesce(func.max(AgentVersion.version_number), 0)).where(
            AgentVersion.agent_id == agent.id
        )
    )
    version = AgentVersion(
        tenant_id=tenant_id,
        agent_id=agent.id,
        version_number=int(current_number or 0) + 1,
        schema_version=resolved.config.schema_version,
        config=normalize_agent_config(resolved.config),
        config_digest=agent_config_digest(resolved.config),
        model_endpoint_version_id=resolved.model_version.id,
        resolved_tool_ids=resolved.tool_ids,
        release_notes=release_notes,
        published_by=actor_user_id,
    )
    session.add(version)
    agent.updated_by = actor_user_id
    agent.updated_at = utc_now()
    await session.flush()
    await append_agent_audit(
        session,
        tenant_id=tenant_id,
        agent_id=agent.id,
        actor_user_id=actor_user_id,
        action=AuditAction.VERSION_PUBLISHED,
        correlation_id=correlation_id,
        version_id=version.id,
        metadata={"version_number": version.version_number, "config_digest": version.config_digest},
    )
    return version


async def list_agent_versions(
    session: AsyncSession, *, agent_id: UUID, tenant_id: UUID
) -> list[AgentVersion]:
    await get_agent(session, agent_id=agent_id, tenant_id=tenant_id)
    return list(
        (
            await session.scalars(
                select(AgentVersion)
                .where(AgentVersion.agent_id == agent_id, AgentVersion.tenant_id == tenant_id)
                .order_by(AgentVersion.version_number.desc())
            )
        ).all()
    )


async def activate_agent_version(
    session: AsyncSession,
    *,
    agent_id: UUID,
    version_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    correlation_id: str,
) -> Agent:
    """切换当前活动版本；历史会话仍保留原固定版本。"""
    agent = await get_agent(session, agent_id=agent_id, tenant_id=tenant_id)
    ensure_agent_mutable(agent)
    version = await session.scalar(
        select(AgentVersion).where(
            AgentVersion.id == version_id,
            AgentVersion.agent_id == agent.id,
            AgentVersion.tenant_id == tenant_id,
        )
    )
    if version is None:
        raise ResourceNotFoundError("Agent 版本不存在")
    previous_version_id = agent.active_version_id
    agent.active_version_id = version.id
    agent.status = AgentStatus.ACTIVE
    agent.updated_by = actor_user_id
    agent.updated_at = utc_now()
    previous_version = (
        await session.get(AgentVersion, previous_version_id) if previous_version_id else None
    )
    action = AuditAction.VERSION_ACTIVATED
    if previous_version is not None and version.version_number < previous_version.version_number:
        action = AuditAction.VERSION_ROLLED_BACK
    await append_agent_audit(
        session,
        tenant_id=tenant_id,
        agent_id=agent.id,
        actor_user_id=actor_user_id,
        action=action,
        correlation_id=correlation_id,
        version_id=version.id,
        metadata={"previous_version_id": str(previous_version_id) if previous_version_id else None},
    )
    return agent


async def disable_agent(
    session: AsyncSession,
    *,
    agent_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    correlation_id: str,
) -> Agent:
    agent = await get_agent(session, agent_id=agent_id, tenant_id=tenant_id)
    ensure_agent_mutable(agent)
    agent.status = AgentStatus.DISABLED
    agent.updated_by = actor_user_id
    agent.updated_at = utc_now()
    await append_agent_audit(
        session,
        tenant_id=tenant_id,
        agent_id=agent.id,
        actor_user_id=actor_user_id,
        action=AuditAction.DISABLED,
        correlation_id=correlation_id,
    )
    return agent


async def replace_agent_grants(
    session: AsyncSession,
    *,
    agent_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    grants: list[tuple[GrantSubjectType, str]],
    correlation_id: str,
) -> list[AgentAccessGrant]:
    """整体替换用户和角色授权；空列表恢复默认拒绝。"""
    agent = await get_agent(session, agent_id=agent_id, tenant_id=tenant_id)
    ensure_agent_mutable(agent)
    normalized = list(
        dict.fromkeys((subject_type, subject_id.strip()) for subject_type, subject_id in grants)
    )
    for subject_type, subject_id in normalized:
        if not subject_id:
            raise AgentConfigurationError([_issue("grants.subject_id", "授权主体不能为空")])
        if subject_type == GrantSubjectType.USER:
            try:
                user_id = UUID(subject_id)
            except ValueError as exc:
                raise AgentConfigurationError(
                    [_issue("grants.subject_id", "用户授权主体必须是用户 ID")]
                ) from exc
            user = await session.scalar(
                select(User.id).where(User.id == user_id, User.tenant_id == tenant_id)
            )
            if user is None:
                raise AgentConfigurationError([_issue("grants.subject_id", "租户用户不存在")])
        elif subject_id not in AGENT_USE_ROLES:
            raise AgentConfigurationError(
                [_issue("grants.subject_id", "角色不是平台注册的 Agent 使用角色")]
            )
    await session.execute(delete(AgentAccessGrant).where(AgentAccessGrant.agent_id == agent_id))
    records = [
        AgentAccessGrant(
            tenant_id=tenant_id,
            agent_id=agent_id,
            subject_type=subject_type,
            subject_id=subject_id,
            created_by=actor_user_id,
        )
        for subject_type, subject_id in normalized
    ]
    session.add_all(records)
    await session.flush()
    await append_agent_audit(
        session,
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_user_id=actor_user_id,
        action=AuditAction.GRANTS_REPLACED,
        correlation_id=correlation_id,
        metadata={"grant_count": len(records)},
    )
    return records


async def list_agent_grants(
    session: AsyncSession, *, agent_id: UUID, tenant_id: UUID
) -> list[AgentAccessGrant]:
    await get_agent(session, agent_id=agent_id, tenant_id=tenant_id)
    return list(
        (
            await session.scalars(
                select(AgentAccessGrant)
                .where(
                    AgentAccessGrant.agent_id == agent_id,
                    AgentAccessGrant.tenant_id == tenant_id,
                )
                .order_by(AgentAccessGrant.subject_type, AgentAccessGrant.subject_id)
            )
        ).all()
    )


async def list_agent_audit_events(
    session: AsyncSession, *, agent_id: UUID, tenant_id: UUID, limit: int = 100
) -> list[AgentAuditEvent]:
    await get_agent(session, agent_id=agent_id, tenant_id=tenant_id)
    return list(
        (
            await session.scalars(
                select(AgentAuditEvent)
                .where(
                    AgentAuditEvent.agent_id == agent_id,
                    AgentAuditEvent.tenant_id == tenant_id,
                )
                .order_by(AgentAuditEvent.created_at.desc(), AgentAuditEvent.id.desc())
                .limit(limit)
            )
        ).all()
    )


def _grant_filter(identity: IdentityContext) -> Any:
    role_values = list(identity.principal.roles)
    return or_(
        (AgentAccessGrant.subject_type == GrantSubjectType.USER)
        & (AgentAccessGrant.subject_id == str(identity.user.id)),
        (AgentAccessGrant.subject_type == GrantSubjectType.ROLE)
        & AgentAccessGrant.subject_id.in_(role_values),
    )


async def list_available_agents(session: AsyncSession, *, identity: IdentityContext) -> list[Agent]:
    """返回当前主体明确获权且已激活的租户内 Agent。"""
    return list(
        (
            await session.scalars(
                select(Agent)
                .join(AgentAccessGrant, AgentAccessGrant.agent_id == Agent.id)
                .where(
                    Agent.tenant_id == identity.principal.tenant_id,
                    Agent.status == AgentStatus.ACTIVE,
                    Agent.active_version_id.is_not(None),
                    AgentAccessGrant.tenant_id == identity.principal.tenant_id,
                    _grant_filter(identity),
                )
                .distinct()
                .order_by(Agent.name, Agent.id)
            )
        ).all()
    )


async def get_usable_agent(
    session: AsyncSession, *, agent_id: UUID, identity: IdentityContext
) -> Agent:
    """按资源隐藏策略读取可用 Agent，避免未授权 ID 枚举。"""
    agent = await session.scalar(
        select(Agent)
        .join(AgentAccessGrant, AgentAccessGrant.agent_id == Agent.id)
        .where(
            Agent.id == agent_id,
            Agent.tenant_id == identity.principal.tenant_id,
            Agent.status == AgentStatus.ACTIVE,
            Agent.active_version_id.is_not(None),
            AgentAccessGrant.tenant_id == identity.principal.tenant_id,
            _grant_filter(identity),
        )
        .distinct()
    )
    if agent is None:
        raise ResourceNotFoundError("Agent 不可用")
    return agent


async def ensure_conversation_agent_usable(
    session: AsyncSession, *, agent_id: UUID, identity: IdentityContext
) -> Agent:
    return await get_usable_agent(session, agent_id=agent_id, identity=identity)


def _pydantic_issues(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "code": error["type"],
        }
        for error in exc.errors(include_url=False, include_input=False)
    ]


def _issue(field: str, message: str, code: str = "invalid") -> dict[str, Any]:
    return {"field": field, "message": message, "code": code}
