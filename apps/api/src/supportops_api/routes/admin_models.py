from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import IdentityContext
from supportops_core.model_services import (
    create_model_endpoint,
    create_model_endpoint_version,
    disable_model_endpoint,
    get_model_endpoint,
    list_model_endpoint_versions,
    list_model_endpoints,
    rotate_model_credential,
    test_model_endpoint,
    update_model_endpoint_profile,
)
from supportops_core.models import ModelCredential, ModelEndpoint
from supportops_core.secrets import SecretProvider

from supportops_api.dependencies import (
    current_identity,
    database_session,
    secret_provider_from,
    settings_from,
)
from supportops_api.schemas import (
    ConnectionTestResponse,
    ModelCredentialRotate,
    ModelEndpointCreate,
    ModelEndpointListResponse,
    ModelEndpointProfileUpdate,
    ModelEndpointResponse,
    ModelEndpointVersionCreate,
    ModelEndpointVersionListResponse,
    ModelEndpointVersionResponse,
)

router = APIRouter(prefix="/v1/admin/model-endpoints", tags=["admin-model-endpoints"])


async def platform_admin_identity(
    identity: IdentityContext = Depends(current_identity),
) -> IdentityContext:
    require_platform_admin(identity)
    return identity


async def _endpoint_response(
    session: AsyncSession, endpoint: ModelEndpoint
) -> ModelEndpointResponse:
    credential = await session.scalar(
        select(ModelCredential).where(ModelCredential.endpoint_id == endpoint.id)
    )
    return ModelEndpointResponse(
        id=endpoint.id,
        name=endpoint.name,
        logo_url=endpoint.logo_url,
        status=endpoint.status,
        read_only=endpoint.read_only,
        active_version_id=endpoint.active_version_id,
        credential_masked_hint=credential.masked_hint if credential else None,
        credential_revision=credential.revision if credential else None,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
    )


@router.get("", response_model=ModelEndpointListResponse)
async def read_model_endpoints(
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ModelEndpointListResponse:
    endpoints = await list_model_endpoints(session, tenant_id=identity.principal.tenant_id)
    return ModelEndpointListResponse(
        items=[await _endpoint_response(session, endpoint) for endpoint in endpoints]
    )


@router.post("", response_model=ModelEndpointResponse, status_code=status.HTTP_201_CREATED)
async def add_model_endpoint(
    payload: ModelEndpointCreate,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
    secret_provider: SecretProvider = Depends(secret_provider_from),
) -> ModelEndpointResponse:
    settings = settings_from(request)
    async with session.begin():
        endpoint = await create_model_endpoint(
            session,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            name=payload.name,
            logo_url=payload.logo_url,
            version_config=payload.version,
            api_key=payload.api_key,
            secret_provider=secret_provider,
            allow_private_networks=settings.model_endpoint_allow_private_networks,
            allowed_hosts=settings.model_endpoint_allowed_hosts,
            correlation_id=request.state.correlation_id,
        )
    return await _endpoint_response(session, endpoint)


@router.get("/{endpoint_id}", response_model=ModelEndpointResponse)
async def read_model_endpoint(
    endpoint_id: UUID,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ModelEndpointResponse:
    endpoint = await get_model_endpoint(
        session, endpoint_id=endpoint_id, tenant_id=identity.principal.tenant_id
    )
    return await _endpoint_response(session, endpoint)


@router.patch("/{endpoint_id}", response_model=ModelEndpointResponse)
async def change_model_endpoint(
    endpoint_id: UUID,
    payload: ModelEndpointProfileUpdate,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ModelEndpointResponse:
    async with session.begin():
        endpoint = await update_model_endpoint_profile(
            session,
            endpoint_id=endpoint_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            name=payload.name,
            logo_url=payload.logo_url,
            correlation_id=request.state.correlation_id,
        )
    return await _endpoint_response(session, endpoint)


@router.get("/{endpoint_id}/versions", response_model=ModelEndpointVersionListResponse)
async def read_model_endpoint_versions(
    endpoint_id: UUID,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ModelEndpointVersionListResponse:
    versions = await list_model_endpoint_versions(
        session, endpoint_id=endpoint_id, tenant_id=identity.principal.tenant_id
    )
    return ModelEndpointVersionListResponse(
        items=[ModelEndpointVersionResponse.model_validate(item) for item in versions]
    )


@router.post(
    "/{endpoint_id}/versions",
    response_model=ModelEndpointVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_model_endpoint_version(
    endpoint_id: UUID,
    payload: ModelEndpointVersionCreate,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ModelEndpointVersionResponse:
    settings = settings_from(request)
    async with session.begin():
        version = await create_model_endpoint_version(
            session,
            endpoint_id=endpoint_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            config=payload.version,
            allow_private_networks=settings.model_endpoint_allow_private_networks,
            allowed_hosts=settings.model_endpoint_allowed_hosts,
            correlation_id=request.state.correlation_id,
        )
    return ModelEndpointVersionResponse.model_validate(version)


@router.put("/{endpoint_id}/credential", response_model=ModelEndpointResponse)
async def replace_model_credential(
    endpoint_id: UUID,
    payload: ModelCredentialRotate,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
    secret_provider: SecretProvider = Depends(secret_provider_from),
) -> ModelEndpointResponse:
    async with session.begin():
        await rotate_model_credential(
            session,
            endpoint_id=endpoint_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            api_key=payload.api_key,
            secret_provider=secret_provider,
            correlation_id=request.state.correlation_id,
        )
        endpoint = await get_model_endpoint(
            session, endpoint_id=endpoint_id, tenant_id=identity.principal.tenant_id
        )
    return await _endpoint_response(session, endpoint)


@router.post("/{endpoint_id}/test", response_model=ConnectionTestResponse)
async def check_model_endpoint(
    endpoint_id: UUID,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
    secret_provider: SecretProvider = Depends(secret_provider_from),
) -> ConnectionTestResponse:
    settings = settings_from(request)
    async with session.begin():
        result = await test_model_endpoint(
            session,
            endpoint_id=endpoint_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            secret_provider=secret_provider,
            allow_private_networks=settings.model_endpoint_allow_private_networks,
            allowed_hosts=settings.model_endpoint_allowed_hosts,
            timeout_seconds=settings.model_test_timeout_seconds,
            max_response_bytes=settings.model_test_max_response_bytes,
            correlation_id=request.state.correlation_id,
        )
    return ConnectionTestResponse(
        status=result.status,
        checks=result.checks,
        latency_ms=result.latency_ms,
        provider_status=result.provider_status,
        error_code=result.error_code,
    )


@router.post("/{endpoint_id}/disable", response_model=ModelEndpointResponse)
async def turn_off_model_endpoint(
    endpoint_id: UUID,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ModelEndpointResponse:
    async with session.begin():
        endpoint = await disable_model_endpoint(
            session,
            endpoint_id=endpoint_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            correlation_id=request.state.correlation_id,
        )
    return await _endpoint_response(session, endpoint)
