"""模型端点、不可变版本、只写凭据和安全能力探测服务。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.enums import (
    AuditAction,
    AuditResourceType,
    ModelApiProtocol,
    ModelEndpointStatus,
    ModelVerificationStatus,
)
from supportops_core.model_config import (
    ModelEndpointVersionConfig,
    redact_provider_error,
    resolve_and_validate_endpoint,
    validate_endpoint_url_policy,
)
from supportops_core.models import (
    AgentAuditEvent,
    ModelCredential,
    ModelCredentialRevision,
    ModelEndpoint,
    ModelEndpointVersion,
    utc_now,
)
from supportops_core.secrets import SecretProvider, masked_secret_hint


class ModelEndpointConflictError(ValueError):
    pass


class ModelEndpointValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    """不含业务数据与秘密的模型连接分项测试结果。"""

    status: ModelVerificationStatus
    checks: dict[str, str]
    latency_ms: int | None = None
    provider_status: int | None = None
    error_code: str | None = None


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


async def list_model_endpoints(session: AsyncSession, *, tenant_id: UUID) -> list[ModelEndpoint]:
    return list(
        (
            await session.scalars(
                select(ModelEndpoint)
                .where(ModelEndpoint.tenant_id == tenant_id)
                .order_by(ModelEndpoint.updated_at.desc(), ModelEndpoint.id)
            )
        ).all()
    )


async def list_model_endpoint_versions(
    session: AsyncSession, *, endpoint_id: UUID, tenant_id: UUID
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
            )
        ).all()
    )


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
        status=ModelEndpointStatus.DRAFT,
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
        defaults=config.defaults.model_dump(mode="json"),
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
    endpoint.active_version_id = version.id
    endpoint.status = ModelEndpointStatus.DRAFT
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
        endpoint.status = (
            ModelEndpointStatus.ACTIVE
            if result.status == ModelVerificationStatus.VERIFIED
            else ModelEndpointStatus.DRAFT
        )
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
        endpoint.status = ModelEndpointStatus.DRAFT
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
    if version.api_protocol == ModelApiProtocol.RESPONSES:
        return {
            "model": version.remote_model_name,
            "input": "Reply with OK.",
            "max_output_tokens": 8,
        }
    return {
        "model": version.remote_model_name,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 8,
    }


def _capability_probe_payload(version: ModelEndpointVersion, capability: str) -> dict[str, object]:
    payload = _basic_probe_payload(version)
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
    endpoint.status = ModelEndpointStatus.DISABLED
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
