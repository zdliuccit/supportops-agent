"""模型端点、不可变版本、只写凭据和安全能力探测服务。"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.enums import (
    AuditAction,
    AuditResourceType,
    ModelApiProtocol,
    ModelProviderKind,
    ModelTestStatus,
    ModelVerificationStatus,
)
from supportops_core.model_config import (
    ModelEndpointVersionConfig,
    redact_provider_error,
    resolve_and_validate_endpoint,
    validate_endpoint_url_policy,
)
from supportops_core.models import (
    Agent,
    AgentAuditEvent,
    AgentRun,
    AgentVersion,
    ModelCredential,
    ModelCredentialRevision,
    ModelEndpoint,
    ModelEndpointModel,
    ModelEndpointTestRun,
    ModelEndpointVersion,
    utc_now,
)
from supportops_core.secrets import SecretProvider, SecretProviderError, masked_secret_hint


class ModelEndpointConflictError(ValueError):
    pass


class ModelEndpointValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    """不含业务数据与秘密的模型分项测试结果。"""

    status: ModelVerificationStatus
    checks: dict[str, str]
    latency_ms: int | None = None
    provider_status: int | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ModelEndpointSelection:
    """管理员在一套连接中保存的单个上游模型。"""

    id: UUID | None
    upstream_model_id: str
    extension_options: dict[str, Any] = field(default_factory=dict)


MODEL_PROVIDER_PRESETS: tuple[dict[str, str], ...] = (
    {"id": "openai", "name": "OpenAI 官方", "base_url": "https://api.openai.com/v1"},
    {"id": "deepseek", "name": "DeepSeek 官方", "base_url": "https://api.deepseek.com/v1"},
    {"id": "glm_cn", "name": "智谱 GLM 中国版", "base_url": "https://open.bigmodel.cn/api/paas/v4"},
    {"id": "glm_global", "name": "智谱 GLM 国际版", "base_url": "https://api.z.ai/api/paas/v4"},
    {"id": "moonshot", "name": "Moonshot Kimi", "base_url": "https://api.moonshot.cn/v1"},
    {"id": "kimi_coding", "name": "Kimi For Coding", "base_url": "https://api.kimi.com/coding/v1"},
    {"id": "minimax_cn", "name": "MiniMax 中国版", "base_url": "https://api.minimaxi.com/v1"},
    {"id": "minimax_global", "name": "MiniMax 国际版", "base_url": "https://api.minimax.io/v1"},
    {"id": "mimo", "name": "小米 MiMo", "base_url": "https://api.xiaomimimo.com/v1"},
    {
        "id": "mimo_plan",
        "name": "小米 MiMo Plan",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
    },
    {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
)


async def append_model_audit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    endpoint_id: UUID,
    actor_user_id: UUID,
    action: AuditAction,
    correlation_id: str,
    version_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        AgentAuditEvent(
            tenant_id=tenant_id,
            resource_type=AuditResourceType.MODEL_ENDPOINT,
            model_endpoint_id=endpoint_id,
            action=action,
            actor_user_id=actor_user_id,
            version_id=version_id,
            metadata_payload=metadata or {},
            correlation_id=correlation_id,
        )
    )
    await session.flush()


async def get_model_endpoint(
    session: AsyncSession, *, endpoint_id: UUID, tenant_id: UUID
) -> ModelEndpoint:
    endpoint = await session.scalar(
        select(ModelEndpoint).where(
            ModelEndpoint.id == endpoint_id,
            ModelEndpoint.tenant_id == tenant_id,
        )
    )
    if endpoint is None:
        from supportops_core.services import ResourceNotFoundError

        raise ResourceNotFoundError("模型端点不存在")
    return endpoint


def ensure_model_endpoint_mutable(endpoint: ModelEndpoint) -> None:
    if endpoint.read_only:
        raise ModelEndpointConflictError("历史迁移模型端点为只读，不能修改、测试或轮换凭据")


async def get_model_endpoint_version(
    session: AsyncSession,
    *,
    version_id: UUID,
    tenant_id: UUID,
) -> ModelEndpointVersion:
    version = await session.scalar(
        select(ModelEndpointVersion).where(
            ModelEndpointVersion.id == version_id,
            ModelEndpointVersion.tenant_id == tenant_id,
        )
    )
    if version is None:
        from supportops_core.services import ResourceNotFoundError

        raise ResourceNotFoundError("模型端点版本不存在")
    return version


async def list_model_endpoints(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    page_size: int = 20,
    offset: int = 0,
) -> list[ModelEndpoint]:
    return list(
        (
            await session.scalars(
                select(ModelEndpoint)
                .where(ModelEndpoint.tenant_id == tenant_id)
                .order_by(ModelEndpoint.updated_at.desc(), ModelEndpoint.id)
                .limit(page_size)
                .offset(offset)
            )
        ).all()
    )


async def count_model_endpoints(session: AsyncSession, *, tenant_id: UUID) -> int:
    total = await session.scalar(
        select(func.count()).select_from(ModelEndpoint).where(ModelEndpoint.tenant_id == tenant_id)
    )
    return int(total or 0)


async def list_model_endpoint_used_agents(
    session: AsyncSession,
    *,
    endpoint_id: UUID,
    tenant_id: UUID,
) -> list[tuple[UUID, str]]:
    """返回曾发布版本绑定当前模型的租户内 Agent，按名称稳定排序。"""
    rows = (
        await session.execute(
            select(Agent.id, Agent.name)
            .join(AgentVersion, AgentVersion.agent_id == Agent.id)
            .join(
                ModelEndpointVersion,
                ModelEndpointVersion.id == AgentVersion.model_endpoint_version_id,
            )
            .where(
                Agent.tenant_id == tenant_id,
                AgentVersion.tenant_id == tenant_id,
                ModelEndpointVersion.tenant_id == tenant_id,
                ModelEndpointVersion.endpoint_id == endpoint_id,
            )
            .distinct()
            .order_by(Agent.name, Agent.id)
        )
    ).all()
    return [(agent_id, agent_name) for agent_id, agent_name in rows]


async def list_model_endpoint_versions(
    session: AsyncSession,
    *,
    endpoint_id: UUID,
    tenant_id: UUID,
    page_size: int = 20,
    offset: int = 0,
) -> list[ModelEndpointVersion]:
    await get_model_endpoint(session, endpoint_id=endpoint_id, tenant_id=tenant_id)
    return list(
        (
            await session.scalars(
                select(ModelEndpointVersion)
                .where(
                    ModelEndpointVersion.endpoint_id == endpoint_id,
                    ModelEndpointVersion.tenant_id == tenant_id,
                )
                .order_by(ModelEndpointVersion.version_number.desc())
                .limit(page_size)
                .offset(offset)
            )
        ).all()
    )


async def count_model_endpoint_versions(
    session: AsyncSession, *, endpoint_id: UUID, tenant_id: UUID
) -> int:
    await get_model_endpoint(session, endpoint_id=endpoint_id, tenant_id=tenant_id)
    total = await session.scalar(
        select(func.count())
        .select_from(ModelEndpointVersion)
        .where(
            ModelEndpointVersion.endpoint_id == endpoint_id,
            ModelEndpointVersion.tenant_id == tenant_id,
        )
    )
    return int(total or 0)


async def create_model_endpoint(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    name: str,
    logo_url: str | None,
    version_config: ModelEndpointVersionConfig,
    api_key: str,
    secret_provider: SecretProvider,
    allow_private_networks: bool,
    allowed_hosts: list[str],
    correlation_id: str,
) -> ModelEndpoint:
    """原子创建稳定端点、首个不可变版本和加密凭据。"""
    duplicate = await session.scalar(
        select(ModelEndpoint.id).where(
            ModelEndpoint.tenant_id == tenant_id,
            ModelEndpoint.name == name,
        )
    )
    if duplicate is not None:
        raise ModelEndpointConflictError("模型端点名称已存在")
    normalized = validate_endpoint_url_policy(
        version_config.base_url,
        allow_private_networks=allow_private_networks,
        allowed_hosts=allowed_hosts,
    )
    endpoint = ModelEndpoint(
        tenant_id=tenant_id,
        name=name,
        logo_url=logo_url,
        is_enabled=False,
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    session.add(endpoint)
    await session.flush()
    credential = ModelCredential(
        tenant_id=tenant_id,
        endpoint_id=endpoint.id,
        provider=secret_provider.provider_name,
        encrypted_secret=secret_provider.seal(api_key),
        masked_hint=masked_secret_hint(api_key),
        revision=1,
    )
    session.add(credential)
    await session.flush()
    session.add(
        ModelCredentialRevision(
            tenant_id=tenant_id,
            credential_id=credential.id,
            revision=credential.revision,
            encrypted_secret=credential.encrypted_secret,
        )
    )
    version = _build_endpoint_version(
        endpoint=endpoint,
        actor_user_id=actor_user_id,
        credential_id=credential.id,
        version_number=1,
        config=version_config.model_copy(update={"base_url": normalized}),
    )
    session.add(version)
    await session.flush()
    endpoint_model = ModelEndpointModel(
        tenant_id=tenant_id,
        endpoint_id=endpoint.id,
        upstream_model_id=version.remote_model_name,
        display_name=version.remote_model_name,
        current_version_id=version.id,
    )
    session.add(endpoint_model)
    await session.flush()
    version.endpoint_model_id = endpoint_model.id
    endpoint.active_version_id = version.id
    await append_model_audit(
        session,
        tenant_id=tenant_id,
        endpoint_id=endpoint.id,
        actor_user_id=actor_user_id,
        action=AuditAction.CREATED,
        correlation_id=correlation_id,
        version_id=version.id,
        metadata={"provider_kind": version.provider_kind.value},
    )
    return endpoint


def _build_endpoint_version(
    *,
    endpoint: ModelEndpoint,
    actor_user_id: UUID,
    credential_id: UUID,
    version_number: int,
    config: ModelEndpointVersionConfig,
) -> ModelEndpointVersion:
    return ModelEndpointVersion(
        tenant_id=endpoint.tenant_id,
        endpoint_id=endpoint.id,
        version_number=version_number,
        provider_kind=config.provider_kind,
        api_protocol=config.api_protocol,
        base_url=config.base_url,
        remote_model_name=config.remote_model_name,
        capabilities=config.capabilities.model_dump(mode="json"),
        defaults=config.defaults.model_dump(mode="json", exclude_defaults=True, exclude_none=True),
        request_metadata=config.request_metadata.model_dump(mode="json"),
        pricing=config.pricing.model_dump(mode="json"),
        credential_id=credential_id,
        created_by=actor_user_id,
    )


async def create_model_endpoint_version(
    session: AsyncSession,
    *,
    endpoint_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    config: ModelEndpointVersionConfig,
    allow_private_networks: bool,
    allowed_hosts: list[str],
    correlation_id: str,
) -> ModelEndpointVersion:
    """保存新的不可变连接配置，并等待重新验证后激活。"""
    endpoint = await get_model_endpoint(session, endpoint_id=endpoint_id, tenant_id=tenant_id)
    ensure_model_endpoint_mutable(endpoint)
    credential = await session.scalar(
        select(ModelCredential).where(
            ModelCredential.endpoint_id == endpoint.id,
            ModelCredential.tenant_id == tenant_id,
        )
    )
    if credential is None:
        raise ModelEndpointValidationError("模型端点尚未配置密钥")
    normalized = validate_endpoint_url_policy(
        config.base_url,
        allow_private_networks=allow_private_networks,
        allowed_hosts=allowed_hosts,
    )
    current_number = await session.scalar(
        select(func.coalesce(func.max(ModelEndpointVersion.version_number), 0)).where(
            ModelEndpointVersion.endpoint_id == endpoint.id
        )
    )
    version = _build_endpoint_version(
        endpoint=endpoint,
        actor_user_id=actor_user_id,
        credential_id=credential.id,
        version_number=int(current_number or 0) + 1,
        config=config.model_copy(update={"base_url": normalized}),
    )
    session.add(version)
    await session.flush()
    endpoint_model = await session.scalar(
        select(ModelEndpointModel).where(
            ModelEndpointModel.endpoint_id == endpoint.id,
            ModelEndpointModel.upstream_model_id == version.remote_model_name,
        )
    )
    if endpoint_model is None:
        endpoint_model = ModelEndpointModel(
            tenant_id=tenant_id,
            endpoint_id=endpoint.id,
            upstream_model_id=version.remote_model_name,
            display_name=version.remote_model_name,
        )
        session.add(endpoint_model)
        await session.flush()
    endpoint_model.current_version_id = version.id
    endpoint_model.archived_at = None
    endpoint_model.updated_at = utc_now()
    version.endpoint_model_id = endpoint_model.id
    endpoint.active_version_id = version.id
    endpoint.is_enabled = False
    endpoint.updated_by = actor_user_id
    endpoint.updated_at = utc_now()
    await append_model_audit(
        session,
        tenant_id=tenant_id,
        endpoint_id=endpoint.id,
        actor_user_id=actor_user_id,
        action=AuditAction.VERSION_PUBLISHED,
        correlation_id=correlation_id,
        version_id=version.id,
    )
    return version


async def update_model_endpoint_profile(
    session: AsyncSession,
    *,
    endpoint_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    name: str,
    logo_url: str | None,
    correlation_id: str,
) -> ModelEndpoint:
    endpoint = await get_model_endpoint(session, endpoint_id=endpoint_id, tenant_id=tenant_id)
    ensure_model_endpoint_mutable(endpoint)
    if endpoint.is_enabled:
        raise ModelEndpointConflictError("启用状态不能编辑，请先停用模型")
    duplicate = await session.scalar(
        select(ModelEndpoint.id).where(
            ModelEndpoint.tenant_id == tenant_id,
            ModelEndpoint.name == name,
            ModelEndpoint.id != endpoint.id,
        )
    )
    if duplicate is not None:
        raise ModelEndpointConflictError("模型端点名称已存在")
    endpoint.name = name
    endpoint.logo_url = logo_url
    endpoint.updated_by = actor_user_id
    endpoint.updated_at = utc_now()
    await append_model_audit(
        session,
        tenant_id=tenant_id,
        endpoint_id=endpoint.id,
        actor_user_id=actor_user_id,
        action=AuditAction.PROFILE_UPDATED,
        correlation_id=correlation_id,
    )
    return endpoint


async def rotate_model_credential(
    session: AsyncSession,
    *,
    endpoint_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    api_key: str,
    secret_provider: SecretProvider,
    correlation_id: str,
) -> ModelCredential:
    """只写轮换密钥并保留历史 revision，任何响应都不返回明文。"""
    endpoint = await get_model_endpoint(session, endpoint_id=endpoint_id, tenant_id=tenant_id)
    ensure_model_endpoint_mutable(endpoint)
    credential = await session.scalar(
        select(ModelCredential)
        .where(
            ModelCredential.endpoint_id == endpoint_id,
            ModelCredential.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if credential is None:
        raise ModelEndpointValidationError("模型端点凭据不存在")
    credential.encrypted_secret = secret_provider.seal(api_key)
    credential.masked_hint = masked_secret_hint(api_key)
    credential.provider = secret_provider.provider_name
    credential.revision += 1
    credential.rotated_at = utc_now()
    session.add(
        ModelCredentialRevision(
            tenant_id=tenant_id,
            credential_id=credential.id,
            revision=credential.revision,
            encrypted_secret=credential.encrypted_secret,
        )
    )
    await append_model_audit(
        session,
        tenant_id=tenant_id,
        endpoint_id=endpoint_id,
        actor_user_id=actor_user_id,
        action=AuditAction.CREDENTIAL_ROTATED,
        correlation_id=correlation_id,
        metadata={"credential_revision": credential.revision},
    )
    return credential


async def list_model_endpoint_models(
    session: AsyncSession,
    *,
    endpoint_id: UUID,
    tenant_id: UUID,
    include_archived: bool = False,
) -> list[ModelEndpointModel]:
    """返回连接下的完整当前模型集合；该集合必须整体读取，因此不分页。"""
    await get_model_endpoint(session, endpoint_id=endpoint_id, tenant_id=tenant_id)
    statement = select(ModelEndpointModel).where(
        ModelEndpointModel.endpoint_id == endpoint_id,
        ModelEndpointModel.tenant_id == tenant_id,
    )
    if not include_archived:
        statement = statement.where(ModelEndpointModel.archived_at.is_(None))
    return list(
        (
            await session.scalars(
                statement.order_by(ModelEndpointModel.created_at, ModelEndpointModel.id)
            )
        ).all()
    )


async def get_model_endpoint_model(
    session: AsyncSession,
    *,
    endpoint_id: UUID,
    endpoint_model_id: UUID,
    tenant_id: UUID,
) -> ModelEndpointModel:
    """按租户和端点读取稳定模型，避免通过 UUID 枚举其他连接。"""
    model = await session.scalar(
        select(ModelEndpointModel).where(
            ModelEndpointModel.id == endpoint_model_id,
            ModelEndpointModel.endpoint_id == endpoint_id,
            ModelEndpointModel.tenant_id == tenant_id,
            ModelEndpointModel.archived_at.is_(None),
        )
    )
    if model is None:
        from supportops_core.services import ResourceNotFoundError

        raise ResourceNotFoundError("模型不存在")
    return model


def model_version_digest(version: ModelEndpointVersion, credential_revision: int) -> str:
    """计算测试结果绑定的模型配置摘要，展示字段变化不会令测试过期。"""
    payload = {
        "provider_kind": version.provider_kind.value,
        "api_protocol": version.api_protocol.value,
        "base_url": version.base_url,
        "remote_model_name": version.remote_model_name,
        "capabilities": version.capabilities,
        "defaults": version.defaults,
        "request_metadata": version.request_metadata,
        "credential_revision": credential_revision,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


async def latest_model_test_run(
    session: AsyncSession,
    *,
    endpoint_model_id: UUID,
    tenant_id: UUID,
) -> ModelEndpointTestRun | None:
    """读取单模型最新测试，调用方再判断它是否匹配当前配置。"""
    return cast(
        ModelEndpointTestRun | None,
        await session.scalar(
            select(ModelEndpointTestRun)
            .where(
                ModelEndpointTestRun.endpoint_model_id == endpoint_model_id,
                ModelEndpointTestRun.tenant_id == tenant_id,
            )
            .order_by(ModelEndpointTestRun.created_at.desc(), ModelEndpointTestRun.id.desc())
            .limit(1)
        ),
    )


async def model_test_state(
    session: AsyncSession,
    *,
    model: ModelEndpointModel,
    credential_revision: int,
) -> tuple[str, ModelEndpointTestRun | None]:
    """计算模型当前测试状态，旧通过记录在配置变化后显示为已过期。"""
    version = (
        await session.get(ModelEndpointVersion, model.current_version_id)
        if model.current_version_id is not None
        else None
    )
    run = await latest_model_test_run(
        session, endpoint_model_id=model.id, tenant_id=model.tenant_id
    )
    if run is None:
        # 兼容迁移前已经通过旧连接测试的端点；新测试均会生成逐模型记录。
        if version is not None and version.verification_status == ModelVerificationStatus.VERIFIED:
            return "passed", None
        return "untested", None
    if run.status in {ModelTestStatus.QUEUED, ModelTestStatus.RUNNING}:
        return "running", run
    if model.current_version_id is None:
        return "stale", run
    if version is None:
        return "stale", run
    current_digest = model_version_digest(version, credential_revision)
    if run.model_version_id != version.id or run.config_digest != current_digest:
        return "stale", run
    return run.status.value, run


async def save_model_endpoint_configuration(
    session: AsyncSession,
    *,
    endpoint_id: UUID | None,
    tenant_id: UUID,
    actor_user_id: UUID,
    name: str,
    provider_preset: str | None,
    provider_kind: ModelProviderKind,
    base_url: str,
    api_key: str | None,
    models: list[ModelEndpointSelection],
    request_enable: bool,
    secret_provider: SecretProvider,
    allow_private_networks: bool,
    allowed_hosts: list[str],
    correlation_id: str,
) -> tuple[ModelEndpoint, list[str]]:
    """整体保存连接和模型集合；没有草稿状态，启用只由测试结果决定。"""
    if not models:
        raise ModelEndpointValidationError("至少需要添加一个模型")
    normalized = validate_endpoint_url_policy(
        base_url,
        allow_private_networks=allow_private_networks,
        allowed_hosts=allowed_hosts,
    )
    duplicate = await session.scalar(
        select(ModelEndpoint.id).where(
            ModelEndpoint.tenant_id == tenant_id,
            ModelEndpoint.name == name,
            *([ModelEndpoint.id != endpoint_id] if endpoint_id is not None else []),
        )
    )
    if duplicate is not None:
        raise ModelEndpointConflictError("模型端点名称已存在")

    created = endpoint_id is None
    credential: ModelCredential
    if created:
        if not api_key:
            raise ModelEndpointValidationError("请输入 API Key")
        endpoint = ModelEndpoint(
            tenant_id=tenant_id,
            name=name,
            provider_preset=provider_preset,
            is_enabled=False,
            created_by=actor_user_id,
            updated_by=actor_user_id,
        )
        session.add(endpoint)
        await session.flush()
        credential = ModelCredential(
            tenant_id=tenant_id,
            endpoint_id=endpoint.id,
            provider=secret_provider.provider_name,
            encrypted_secret=secret_provider.seal(api_key),
            masked_hint=masked_secret_hint(api_key),
            revision=1,
        )
        session.add(credential)
        await session.flush()
        session.add(
            ModelCredentialRevision(
                tenant_id=tenant_id,
                credential_id=credential.id,
                revision=1,
                encrypted_secret=credential.encrypted_secret,
            )
        )
    else:
        assert endpoint_id is not None
        endpoint = await get_model_endpoint(session, endpoint_id=endpoint_id, tenant_id=tenant_id)
        ensure_model_endpoint_mutable(endpoint)
        if endpoint.is_enabled:
            raise ModelEndpointConflictError("启用状态不能编辑，请先停用模型")
        endpoint.name = name
        endpoint.provider_preset = provider_preset
        stored_credential = await session.scalar(
            select(ModelCredential).where(
                ModelCredential.endpoint_id == endpoint.id,
                ModelCredential.tenant_id == tenant_id,
            )
        )
        if stored_credential is None:
            raise ModelEndpointValidationError("模型端点凭据不存在")
        credential = stored_credential
        if api_key:
            await rotate_model_credential(
                session,
                endpoint_id=endpoint.id,
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                api_key=api_key,
                secret_provider=secret_provider,
                correlation_id=correlation_id,
            )

    existing = {
        item.id: item
        for item in await list_model_endpoint_models(
            session,
            endpoint_id=endpoint.id,
            tenant_id=tenant_id,
            include_archived=True,
        )
    }
    existing_by_upstream = {item.upstream_model_id: item for item in existing.values()}
    selected_ids: set[UUID] = set()
    next_number = int(
        await session.scalar(
            select(func.coalesce(func.max(ModelEndpointVersion.version_number), 0)).where(
                ModelEndpointVersion.endpoint_id == endpoint.id
            )
        )
        or 0
    )
    current_versions: list[ModelEndpointVersion] = []
    seen_upstream: set[str] = set()
    for item in models:
        upstream_model_id = item.upstream_model_id.strip()
        if not upstream_model_id or upstream_model_id in seen_upstream:
            raise ModelEndpointValidationError("模型 ID 不能为空或重复")
        seen_upstream.add(upstream_model_id)
        model = existing.get(item.id) if item.id is not None else None
        model = model or existing_by_upstream.get(upstream_model_id)
        if model is None:
            model = ModelEndpointModel(
                tenant_id=tenant_id,
                endpoint_id=endpoint.id,
                upstream_model_id=upstream_model_id,
            )
            session.add(model)
            await session.flush()
        elif model.endpoint_id != endpoint.id:
            raise ModelEndpointValidationError("模型不属于当前端点")
        model.upstream_model_id = upstream_model_id
        model.display_name = upstream_model_id
        model.badge = ""
        model.archived_at = None
        model.updated_at = utc_now()
        selected_ids.add(model.id)

        config = ModelEndpointVersionConfig.model_validate(
            {
                "provider_kind": provider_kind,
                "api_protocol": (
                    ModelApiProtocol.RESPONSES
                    if provider_kind == ModelProviderKind.OPENAI_OFFICIAL
                    else ModelApiProtocol.CHAT_COMPLETIONS
                ),
                "base_url": normalized,
                "remote_model_name": upstream_model_id,
                "capabilities": {"streaming": True, "tool_calling": True},
                "defaults": {"extension_options": item.extension_options},
            }
        )
        current = (
            await session.get(ModelEndpointVersion, model.current_version_id)
            if model.current_version_id is not None
            else None
        )
        wanted = config.model_dump(mode="json")
        current_payload = (
            {
                "provider_kind": current.provider_kind.value,
                "api_protocol": current.api_protocol.value,
                "base_url": current.base_url,
                "remote_model_name": current.remote_model_name,
                "capabilities": current.capabilities,
                "defaults": current.defaults,
                "request_metadata": current.request_metadata,
                "pricing": current.pricing,
            }
            if current is not None
            else None
        )
        if current_payload != wanted:
            next_number += 1
            current = _build_endpoint_version(
                endpoint=endpoint,
                actor_user_id=actor_user_id,
                credential_id=credential.id,
                version_number=next_number,
                config=config,
            )
            current.endpoint_model_id = model.id
            session.add(current)
            await session.flush()
            model.current_version_id = current.id
        assert current is not None
        current_versions.append(current)

    for existing_model in existing.values():
        if existing_model.id not in selected_ids and existing_model.archived_at is None:
            existing_model.archived_at = utc_now()
            existing_model.updated_at = utc_now()

    endpoint.active_version_id = current_versions[0].id
    endpoint.is_enabled = False
    endpoint.updated_by = actor_user_id
    endpoint.updated_at = utc_now()
    blockers = await model_endpoint_enable_blockers(
        session, endpoint=endpoint, credential_revision=credential.revision
    )
    if request_enable and not blockers:
        endpoint.is_enabled = True
    await append_model_audit(
        session,
        tenant_id=tenant_id,
        endpoint_id=endpoint.id,
        actor_user_id=actor_user_id,
        action=AuditAction.CREATED if created else AuditAction.VERSION_PUBLISHED,
        correlation_id=correlation_id,
        version_id=endpoint.active_version_id,
        metadata={
            "is_enabled": endpoint.is_enabled,
            "model_count": len(current_versions),
            "enable_blockers": blockers,
        },
    )
    return endpoint, blockers


async def model_endpoint_enable_blockers(
    session: AsyncSession,
    *,
    endpoint: ModelEndpoint,
    credential_revision: int | None = None,
) -> list[str]:
    """返回阻止当前连接启用的逐模型原因。"""
    credential = await session.scalar(
        select(ModelCredential).where(
            ModelCredential.endpoint_id == endpoint.id,
            ModelCredential.tenant_id == endpoint.tenant_id,
        )
    )
    revision = credential_revision or (credential.revision if credential is not None else 0)
    models = await list_model_endpoint_models(
        session, endpoint_id=endpoint.id, tenant_id=endpoint.tenant_id
    )
    if not models:
        return ["至少需要一个模型"]
    blockers: list[str] = []
    for model in models:
        state, _ = await model_test_state(session, model=model, credential_revision=revision)
        if state != ModelTestStatus.PASSED.value:
            blockers.append(f"{model.display_name or model.upstream_model_id}：{state}")
    return blockers


async def enable_model_endpoint(
    session: AsyncSession,
    *,
    endpoint_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    correlation_id: str,
) -> ModelEndpoint:
    """仅在所有当前模型测试通过时启用连接。"""
    endpoint = await get_model_endpoint(session, endpoint_id=endpoint_id, tenant_id=tenant_id)
    ensure_model_endpoint_mutable(endpoint)
    blockers = await model_endpoint_enable_blockers(session, endpoint=endpoint)
    if blockers:
        raise ModelEndpointValidationError("存在未通过当前配置测试的模型：" + "；".join(blockers))
    endpoint.is_enabled = True
    endpoint.updated_by = actor_user_id
    endpoint.updated_at = utc_now()
    await append_model_audit(
        session,
        tenant_id=tenant_id,
        endpoint_id=endpoint.id,
        actor_user_id=actor_user_id,
        action=AuditAction.VERSION_ACTIVATED,
        correlation_id=correlation_id,
        version_id=endpoint.active_version_id,
    )
    return endpoint


async def discover_remote_models(
    *,
    base_url: str,
    api_key: str,
    allow_private_networks: bool,
    allowed_hosts: list[str],
    timeout_seconds: float,
    max_response_bytes: int,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """显式读取供应商模型目录；结果仅用于选择，不代表连通性验证通过。"""
    normalized = validate_endpoint_url_policy(
        base_url,
        allow_private_networks=allow_private_networks,
        allowed_hosts=allowed_hosts,
    )
    await resolve_and_validate_endpoint(normalized, allow_private_networks=allow_private_networks)
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
    try:
        response_body = bytearray()
        async with active_client.stream(
            "GET",
            f"{normalized.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                response_body.extend(chunk)
                if len(response_body) > max_response_bytes:
                    raise ModelEndpointValidationError("模型列表响应超过平台大小限制")
        payload = json.loads(response_body)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ModelEndpointValidationError("供应商没有返回标准模型列表")
        model_ids = sorted(
            {
                str(item["id"]).strip()
                for item in data
                if isinstance(item, dict) and str(item.get("id", "")).strip()
            }
        )
        return model_ids[:500]
    except (httpx.HTTPError, SecretProviderError, ValueError) as exc:
        raise ModelEndpointValidationError(redact_provider_error(str(exc), (api_key,))) from exc
    finally:
        if owns_client:
            await active_client.aclose()


async def start_model_test_run(
    session: AsyncSession,
    *,
    endpoint_id: UUID,
    endpoint_model_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    correlation_id: str,
) -> ModelEndpointTestRun:
    """持久化排队测试；真实网络请求由独立后台会话执行。"""
    endpoint = await get_model_endpoint(session, endpoint_id=endpoint_id, tenant_id=tenant_id)
    ensure_model_endpoint_mutable(endpoint)
    model = await get_model_endpoint_model(
        session,
        endpoint_id=endpoint_id,
        endpoint_model_id=endpoint_model_id,
        tenant_id=tenant_id,
    )
    if model.current_version_id is None:
        raise ModelEndpointValidationError("模型没有可测试的当前配置")
    version = await get_model_endpoint_version(
        session, version_id=model.current_version_id, tenant_id=tenant_id
    )
    credential = await session.get(ModelCredential, version.credential_id)
    if credential is None or credential.tenant_id != tenant_id:
        raise ModelEndpointValidationError("模型端点凭据不存在")
    running = await session.scalar(
        select(ModelEndpointTestRun.id).where(
            ModelEndpointTestRun.endpoint_model_id == model.id,
            ModelEndpointTestRun.status.in_((ModelTestStatus.QUEUED, ModelTestStatus.RUNNING)),
        )
    )
    if running is not None:
        raise ModelEndpointConflictError("该模型已有正在执行的连通性测试")
    path = (
        "/responses" if version.api_protocol == ModelApiProtocol.RESPONSES else "/chat/completions"
    )
    test_run = ModelEndpointTestRun(
        tenant_id=tenant_id,
        endpoint_id=endpoint.id,
        endpoint_model_id=model.id,
        model_version_id=version.id,
        credential_revision=credential.revision,
        config_digest=model_version_digest(version, credential.revision),
        status=ModelTestStatus.QUEUED,
        stage="queued",
        request_host=urlsplit(version.base_url).hostname or "",
        request_path=f"{urlsplit(version.base_url).path.rstrip('/')}{path}" or path,
        milestones={"queued": True},
        correlation_id=correlation_id,
        created_by=actor_user_id,
    )
    session.add(test_run)
    await session.flush()
    return test_run


async def execute_model_test_run(
    session: AsyncSession,
    *,
    test_run_id: UUID,
    secret_provider: SecretProvider,
    allow_private_networks: bool,
    allowed_hosts: list[str],
    timeout_seconds: float,
    max_response_bytes: int,
    client: httpx.AsyncClient | None = None,
) -> None:
    """执行真实流式最小推理，并在收到响应头和首包时立即持久化进度。"""
    test_run = await session.get(ModelEndpointTestRun, test_run_id)
    if test_run is None or test_run.status != ModelTestStatus.QUEUED:
        return
    version = await session.get(ModelEndpointVersion, test_run.model_version_id)
    credential = (
        await session.scalar(
            select(ModelCredentialRevision).where(
                ModelCredentialRevision.credential_id == version.credential_id,
                ModelCredentialRevision.revision == test_run.credential_revision,
            )
        )
        if version is not None
        else None
    )
    if version is None or credential is None:
        test_run.status = ModelTestStatus.FAILED
        test_run.stage = "failed"
        test_run.error_code = "MODEL_ENDPOINT_REFERENCE_MISSING"
        test_run.error_message = "测试固定的模型配置或凭据不存在"
        test_run.completed_at = utc_now()
        await session.commit()
        return

    started = time.monotonic()
    test_run.status = ModelTestStatus.RUNNING
    test_run.stage = "request_sent"
    test_run.started_at = utc_now()
    test_run.milestones = {**test_run.milestones, "request_sent": True}
    await session.commit()

    path = "responses" if version.api_protocol == ModelApiProtocol.RESPONSES else "chat/completions"
    url = f"{version.base_url.rstrip('/')}/{path}"
    api_key = ""
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
    try:
        validate_endpoint_url_policy(
            version.base_url,
            allow_private_networks=allow_private_networks,
            allowed_hosts=allowed_hosts,
        )
        await resolve_and_validate_endpoint(
            version.base_url, allow_private_networks=allow_private_networks
        )
        api_key = secret_provider.open(credential.encrypted_secret)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        payload = {**_extension_options(version), **_basic_probe_payload(version), "stream": True}
        async with active_client.stream("POST", url, headers=headers, json=payload) as response:
            test_run.provider_status = response.status_code
            test_run.response_headers_ms = int((time.monotonic() - started) * 1000)
            test_run.stage = "response_headers"
            test_run.milestones = {
                **test_run.milestones,
                "response_headers": True,
                "provider_status": response.status_code,
            }
            await session.commit()
            response.raise_for_status()
            response_size = 0
            received_content = False
            response_text = ""
            stream_buffer = ""
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                response_size += len(chunk)
                if response_size > max_response_bytes:
                    raise ValueError("模型端点测试响应超过平台大小限制")
                stream_buffer += chunk.decode("utf-8", errors="replace")
                complete_lines = stream_buffer.splitlines(keepends=True)
                stream_buffer = ""
                if complete_lines and not complete_lines[-1].endswith(("\n", "\r")):
                    stream_buffer = complete_lines.pop()
                response_text += _extract_probe_text(
                    "".join(complete_lines), api_protocol=version.api_protocol
                )
                response_text = response_text[:4000]
                if not received_content:
                    received_content = True
                    test_run.first_content_ms = int((time.monotonic() - started) * 1000)
                    test_run.stage = "first_content"
                    test_run.milestones = {
                        **test_run.milestones,
                        "first_content": True,
                    }
                    await session.commit()
            if not received_content:
                raise ValueError("模型端点没有返回流式内容")
            if stream_buffer:
                response_text += _extract_probe_text(
                    stream_buffer, api_protocol=version.api_protocol
                )
            test_run.response_content = response_text[:4000].strip()
            if not test_run.response_content:
                raise ValueError("模型端点没有返回可展示的文本内容")
        test_run.status = ModelTestStatus.PASSED
        test_run.stage = "completed"
        test_run.total_ms = int((time.monotonic() - started) * 1000)
        test_run.completed_at = utc_now()
        test_run.milestones = {**test_run.milestones, "completed": True}
        version.verification_status = ModelVerificationStatus.VERIFIED
        version.verified_at = utc_now()
        version.verification_result = {
            "status": "verified",
            "checks": {
                "authentication": "passed",
                "model": "passed",
                "streaming": "passed",
            },
            "response_headers_ms": test_run.response_headers_ms,
            "first_content_ms": test_run.first_content_ms,
            "total_ms": test_run.total_ms,
            "test_run_id": str(test_run.id),
        }
    except (httpx.HTTPError, SecretProviderError, ValueError) as exc:
        provider_status = (
            exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        )
        test_run.provider_status = test_run.provider_status or provider_status
        test_run.status = ModelTestStatus.FAILED
        test_run.stage = "failed"
        test_run.total_ms = int((time.monotonic() - started) * 1000)
        test_run.completed_at = utc_now()
        test_run.error_code = (
            "MODEL_ENDPOINT_AUTH_FAILED"
            if test_run.provider_status in {401, 403}
            else "MODEL_ENDPOINT_TEST_FAILED"
        )
        test_run.error_message = redact_provider_error(str(exc), (api_key,))
        test_run.milestones = {**test_run.milestones, "failed": True}
        version.verification_status = ModelVerificationStatus.FAILED
        version.verified_at = utc_now()
        version.verification_result = {
            "status": "failed",
            "error_code": test_run.error_code,
            "test_run_id": str(test_run.id),
        }
    finally:
        if owns_client:
            await active_client.aclose()
    await append_model_audit(
        session,
        tenant_id=test_run.tenant_id,
        endpoint_id=test_run.endpoint_id,
        actor_user_id=test_run.created_by,
        action=AuditAction.CONNECTION_TESTED,
        correlation_id=test_run.correlation_id,
        version_id=test_run.model_version_id,
        metadata={
            "test_run_id": str(test_run.id),
            "model_id": str(test_run.endpoint_model_id),
            "status": test_run.status.value,
            "total_ms": test_run.total_ms,
            "error_code": test_run.error_code,
        },
    )
    await session.commit()


async def get_model_test_run(
    session: AsyncSession,
    *,
    test_run_id: UUID,
    tenant_id: UUID,
) -> ModelEndpointTestRun:
    """读取当前租户可见的单次测试进度。"""
    test_run = await session.scalar(
        select(ModelEndpointTestRun).where(
            ModelEndpointTestRun.id == test_run_id,
            ModelEndpointTestRun.tenant_id == tenant_id,
        )
    )
    if test_run is None:
        from supportops_core.services import ResourceNotFoundError

        raise ResourceNotFoundError("模型测试不存在")
    return test_run


async def list_model_test_runs(
    session: AsyncSession,
    *,
    endpoint_model_id: UUID,
    tenant_id: UUID,
    page_size: int,
    offset: int,
) -> list[ModelEndpointTestRun]:
    """按时间倒序分页返回逐模型测试历史。"""
    return list(
        (
            await session.scalars(
                select(ModelEndpointTestRun)
                .where(
                    ModelEndpointTestRun.endpoint_model_id == endpoint_model_id,
                    ModelEndpointTestRun.tenant_id == tenant_id,
                )
                .order_by(ModelEndpointTestRun.created_at.desc(), ModelEndpointTestRun.id.desc())
                .limit(page_size)
                .offset(offset)
            )
        ).all()
    )


async def count_model_test_runs(
    session: AsyncSession, *, endpoint_model_id: UUID, tenant_id: UUID
) -> int:
    """统计当前租户单模型的测试历史数量。"""
    total = await session.scalar(
        select(func.count())
        .select_from(ModelEndpointTestRun)
        .where(
            ModelEndpointTestRun.endpoint_model_id == endpoint_model_id,
            ModelEndpointTestRun.tenant_id == tenant_id,
        )
    )
    return int(total or 0)


async def test_model_endpoint(
    session: AsyncSession,
    *,
    endpoint_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    secret_provider: SecretProvider,
    allow_private_networks: bool,
    allowed_hosts: list[str],
    timeout_seconds: float,
    max_response_bytes: int,
    correlation_id: str,
    client: httpx.AsyncClient | None = None,
) -> ConnectionTestResult:
    """使用无业务数据探针分别验证基础、流式、工具和结构化能力。"""
    endpoint = await get_model_endpoint(session, endpoint_id=endpoint_id, tenant_id=tenant_id)
    ensure_model_endpoint_mutable(endpoint)
    if endpoint.active_version_id is None:
        raise ModelEndpointValidationError("模型端点没有可测试版本")
    version = await get_model_endpoint_version(
        session, version_id=endpoint.active_version_id, tenant_id=tenant_id
    )
    credential = await session.get(ModelCredential, version.credential_id)
    if credential is None or credential.tenant_id != tenant_id:
        raise ModelEndpointValidationError("模型端点凭据不存在")
    api_key = ""
    path = "responses" if version.api_protocol == ModelApiProtocol.RESPONSES else "chat/completions"
    url = f"{version.base_url.rstrip('/')}/{path}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    organization = version.request_metadata.get("organization_id")
    project = version.request_metadata.get("project_id")
    if organization:
        headers["OpenAI-Organization"] = str(organization)
    if project:
        headers["OpenAI-Project"] = str(project)
    payload = _basic_probe_payload(version)
    started = time.monotonic()
    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=False,
    )
    try:
        validate_endpoint_url_policy(
            version.base_url,
            allow_private_networks=allow_private_networks,
            allowed_hosts=allowed_hosts,
        )
        await resolve_and_validate_endpoint(
            version.base_url, allow_private_networks=allow_private_networks
        )
        api_key = secret_provider.open(credential.encrypted_secret)
        headers["Authorization"] = f"Bearer {api_key}"
        response = await _send_bounded_probe(
            active_client,
            url=url,
            headers=headers,
            payload=payload,
            max_response_bytes=max_response_bytes,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        checks = {
            "dns": "passed",
            "tls": "passed" if url.startswith("https://") else "development_only",
            "authentication": "passed",
            "model": "passed",
        }
        probe_errors: dict[str, str] = {}
        for capability, enabled in (
            ("streaming", bool(version.capabilities.get("streaming"))),
            ("tool_calling", bool(version.capabilities.get("tool_calling"))),
            ("structured_output", bool(version.capabilities.get("structured_output"))),
        ):
            if not enabled:
                checks[capability] = "disabled"
                continue
            try:
                await resolve_and_validate_endpoint(
                    version.base_url, allow_private_networks=allow_private_networks
                )
                await _send_bounded_probe(
                    active_client,
                    url=url,
                    headers=headers,
                    payload=_capability_probe_payload(version, capability),
                    max_response_bytes=max_response_bytes,
                )
                checks[capability] = "passed"
            except (httpx.HTTPError, ValueError) as exc:
                checks[capability] = "failed"
                probe_errors[capability] = redact_provider_error(str(exc), (api_key,))
        verification_status = (
            ModelVerificationStatus.VERIFIED
            if not probe_errors
            else ModelVerificationStatus.PARTIAL
        )
        result = ConnectionTestResult(
            status=verification_status,
            checks=checks,
            latency_ms=latency_ms,
            provider_status=response.status_code,
        )
        version.verification_status = result.status
        version.verified_at = utc_now()
        version.verification_result = {**_result_payload(result), "probe_errors": probe_errors}
    except (httpx.HTTPError, ValueError) as exc:
        provider_status = (
            exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        )
        authentication_failed = provider_status in {401, 403}
        result = ConnectionTestResult(
            status=ModelVerificationStatus.FAILED,
            checks={
                "authentication": "failed" if authentication_failed else "unknown",
                "model": "not_checked" if authentication_failed else "failed",
            },
            latency_ms=int((time.monotonic() - started) * 1000),
            provider_status=provider_status,
            error_code=(
                "MODEL_ENDPOINT_AUTH_FAILED"
                if authentication_failed
                else "MODEL_ENDPOINT_TEST_FAILED"
            ),
        )
        version.verification_status = result.status
        version.verified_at = utc_now()
        version.verification_result = {
            **_result_payload(result),
            "error": redact_provider_error(str(exc), (api_key,)),
        }
    finally:
        if owns_client:
            await active_client.aclose()
    endpoint.updated_by = actor_user_id
    endpoint.updated_at = utc_now()
    await append_model_audit(
        session,
        tenant_id=tenant_id,
        endpoint_id=endpoint.id,
        actor_user_id=actor_user_id,
        action=AuditAction.CONNECTION_TESTED,
        correlation_id=correlation_id,
        version_id=version.id,
        metadata=_result_payload(result),
    )
    return result


async def _send_bounded_probe(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, object],
    max_response_bytes: int,
) -> httpx.Response:
    """发送禁止重定向、限制超时与响应体大小的最小探针。"""
    request = client.build_request("POST", url, headers=headers, json=payload)
    response = await client.send(request, stream=True)
    try:
        response.raise_for_status()
        response_size = 0
        async for chunk in response.aiter_bytes():
            response_size += len(chunk)
            if response_size > max_response_bytes:
                raise ValueError("模型端点测试响应超过平台大小限制")
    finally:
        await response.aclose()
    return response


def _basic_probe_payload(version: ModelEndpointVersion) -> dict[str, object]:
    """发送一个固定、无业务数据的简单算术问题，并要求模型直接回答。"""
    if version.api_protocol == ModelApiProtocol.RESPONSES:
        return {
            "model": version.remote_model_name,
            "input": "请回答：1+1等于几？",
            "max_output_tokens": 8,
        }
    return {
        "model": version.remote_model_name,
        "messages": [{"role": "user", "content": "请回答：1+1等于几？"}],
        "max_tokens": 8,
    }


def _extract_probe_text(value: str, *, api_protocol: ModelApiProtocol) -> str:
    """从 Responses 或 Chat Completions 的 SSE 数据中提取模型文本。"""
    chunks: list[str] = []
    for line in value.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if api_protocol == ModelApiProtocol.RESPONSES:
            delta = event.get("delta")
            if isinstance(delta, str):
                chunks.append(delta)
            output_text = event.get("output_text")
            if isinstance(output_text, str):
                chunks.append(output_text)
            continue
        choices = event.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            delta = choices[0].get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                chunks.append(delta["content"])
    return "".join(chunks)


def _extension_options(version: ModelEndpointVersion) -> dict[str, object]:
    """读取管理员保存的 JSON 扩展对象；历史版本或异常值按空对象处理。"""
    value = version.defaults.get("extension_options", {})
    return dict(value) if isinstance(value, dict) else {}


def _capability_probe_payload(version: ModelEndpointVersion, capability: str) -> dict[str, object]:
    payload = {**_extension_options(version), **_basic_probe_payload(version)}
    if capability == "streaming":
        return {**payload, "stream": True}
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    if capability == "tool_calling":
        function = {
            "name": "supportops_probe",
            "description": "Return the probe value.",
            "parameters": schema,
        }
        if version.api_protocol == ModelApiProtocol.RESPONSES:
            return {
                **payload,
                "input": "Call supportops_probe with value OK.",
                "tools": [{"type": "function", **function}],
                "tool_choice": {"type": "function", "name": "supportops_probe"},
            }
        return {
            **payload,
            "messages": [{"role": "user", "content": "Call supportops_probe with value OK."}],
            "tools": [{"type": "function", "function": function}],
            "tool_choice": {"type": "function", "function": {"name": "supportops_probe"}},
        }
    if capability == "structured_output":
        if version.api_protocol == ModelApiProtocol.RESPONSES:
            return {
                **payload,
                "input": "Return JSON with value OK.",
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "supportops_probe",
                        "schema": schema,
                        "strict": True,
                    }
                },
            }
        return {
            **payload,
            "messages": [{"role": "user", "content": "Return JSON with value OK."}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "supportops_probe", "schema": schema, "strict": True},
            },
        }
    raise ValueError("未知的模型能力测试")


def _result_payload(result: ConnectionTestResult) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "checks": result.checks,
        "latency_ms": result.latency_ms,
        "provider_status": result.provider_status,
        "error_code": result.error_code,
    }


async def disable_model_endpoint(
    session: AsyncSession,
    *,
    endpoint_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    correlation_id: str,
) -> ModelEndpoint:
    endpoint = await get_model_endpoint(session, endpoint_id=endpoint_id, tenant_id=tenant_id)
    ensure_model_endpoint_mutable(endpoint)
    endpoint.is_enabled = False
    endpoint.updated_by = actor_user_id
    endpoint.updated_at = utc_now()
    await append_model_audit(
        session,
        tenant_id=tenant_id,
        endpoint_id=endpoint.id,
        actor_user_id=actor_user_id,
        action=AuditAction.DISABLED,
        correlation_id=correlation_id,
    )
    return endpoint


async def delete_model_endpoint(
    session: AsyncSession,
    *,
    endpoint_id: UUID,
    tenant_id: UUID,
) -> None:
    """删除已停用且未被 Agent 固定引用的模型及其测试、版本和凭据。"""
    endpoint = await get_model_endpoint(session, endpoint_id=endpoint_id, tenant_id=tenant_id)
    ensure_model_endpoint_mutable(endpoint)
    if endpoint.is_enabled:
        raise ModelEndpointConflictError("启用状态不能删除，请先停用模型")

    version_ids = select(ModelEndpointVersion.id).where(
        ModelEndpointVersion.endpoint_id == endpoint.id,
        ModelEndpointVersion.tenant_id == tenant_id,
    )
    agent_version_count = await session.scalar(
        select(func.count())
        .select_from(AgentVersion)
        .where(
            AgentVersion.tenant_id == tenant_id,
            AgentVersion.model_endpoint_version_id.in_(version_ids),
        )
    )
    run_count = await session.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.model_endpoint_version_id.in_(version_ids),
        )
    )
    if (agent_version_count or 0) > 0 or (run_count or 0) > 0:
        raise ModelEndpointConflictError("模型已被 Agent 版本或执行记录引用，不能删除")

    running_test = await session.scalar(
        select(ModelEndpointTestRun.id).where(
            ModelEndpointTestRun.endpoint_id == endpoint.id,
            ModelEndpointTestRun.status.in_((ModelTestStatus.QUEUED, ModelTestStatus.RUNNING)),
        )
    )
    if running_test is not None:
        raise ModelEndpointConflictError("模型存在进行中的测试，完成后再删除")

    # 审计记录保留，但解除对已删除端点的可选外键引用。
    await session.execute(
        update(AgentAuditEvent)
        .where(
            AgentAuditEvent.tenant_id == tenant_id,
            AgentAuditEvent.model_endpoint_id == endpoint.id,
        )
        .values(model_endpoint_id=None)
    )
    await session.execute(
        delete(ModelEndpointTestRun).where(
            ModelEndpointTestRun.endpoint_id == endpoint.id,
            ModelEndpointTestRun.tenant_id == tenant_id,
        )
    )
    endpoint.active_version_id = None
    await session.execute(
        update(ModelEndpointModel)
        .where(
            ModelEndpointModel.endpoint_id == endpoint.id,
            ModelEndpointModel.tenant_id == tenant_id,
        )
        .values(current_version_id=None)
    )
    await session.execute(
        update(ModelEndpointVersion)
        .where(
            ModelEndpointVersion.endpoint_id == endpoint.id,
            ModelEndpointVersion.tenant_id == tenant_id,
        )
        .values(endpoint_model_id=None)
    )
    # 版本仍通过 credential_id 引用凭据，必须先删除版本再删除凭据历史。
    await session.execute(
        delete(ModelEndpointVersion).where(
            ModelEndpointVersion.endpoint_id == endpoint.id,
            ModelEndpointVersion.tenant_id == tenant_id,
        )
    )
    await session.execute(
        delete(ModelEndpointModel).where(
            ModelEndpointModel.endpoint_id == endpoint.id,
            ModelEndpointModel.tenant_id == tenant_id,
        )
    )
    credential_ids = select(ModelCredential.id).where(
        ModelCredential.endpoint_id == endpoint.id,
        ModelCredential.tenant_id == tenant_id,
    )
    await session.execute(
        delete(ModelCredentialRevision).where(ModelCredentialRevision.credential_id.in_(credential_ids))
    )
    await session.execute(
        delete(ModelCredential).where(
            ModelCredential.endpoint_id == endpoint.id,
            ModelCredential.tenant_id == tenant_id,
        )
    )
    await session.delete(endpoint)
