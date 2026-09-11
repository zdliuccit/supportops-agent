from typing import Literal, cast
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import IdentityContext
from supportops_core.enums import ModelApiProtocol
from supportops_core.model_services import (
    MODEL_PROVIDER_PRESETS,
    ModelEndpointSelection,
    count_model_endpoint_versions,
    count_model_endpoints,
    count_model_test_runs,
    create_model_endpoint,
    create_model_endpoint_version,
    delete_model_endpoint,
    disable_model_endpoint,
    discover_remote_models,
    enable_model_endpoint,
    execute_model_test_run,
    get_model_endpoint,
    get_model_test_run,
    list_model_endpoint_models,
    list_model_endpoint_used_agents,
    list_model_endpoint_versions,
    list_model_endpoints,
    list_model_test_runs,
    model_endpoint_enable_blockers,
    model_test_state,
    rotate_model_credential,
    save_model_endpoint_configuration,
    start_model_test_run,
    test_model_endpoint,
    update_model_endpoint_profile,
)
from supportops_core.models import ModelCredential, ModelEndpoint, ModelEndpointVersion
from supportops_core.secrets import SecretProvider

from supportops_api.dependencies import (
    current_identity,
    database_session,
    secret_provider_from,
    settings_from,
)
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import (
    ConnectionTestResponse,
    ModelCredentialRotate,
    ModelDiscoveryRequest,
    ModelDiscoveryResponse,
    ModelEndpointConfigurationSave,
    ModelEndpointCreate,
    ModelEndpointListResponse,
    ModelEndpointModelResponse,
    ModelEndpointProfileUpdate,
    ModelEndpointResponse,
    ModelEndpointVersionCreate,
    ModelEndpointVersionListResponse,
    ModelEndpointVersionResponse,
    ModelTestRunListResponse,
    ModelTestRunResponse,
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
    models = await list_model_endpoint_models(
        session, endpoint_id=endpoint.id, tenant_id=endpoint.tenant_id
    )
    model_responses: list[ModelEndpointModelResponse] = []
    for model in models:
        version = (
            await session.get(ModelEndpointVersion, model.current_version_id)
            if model.current_version_id is not None
            else None
        )
        test_status, latest_test = await model_test_state(
            session,
            model=model,
            credential_revision=credential.revision if credential else 0,
        )
        model_responses.append(
            ModelEndpointModelResponse(
                id=model.id,
                upstream_model_id=model.upstream_model_id,
                display_name=model.display_name,
                badge=model.badge,
                current_version_id=model.current_version_id,
                api_protocol=(
                    version.api_protocol
                    if version is not None
                    else ModelApiProtocol.CHAT_COMPLETIONS
                ),
                context_window_tokens=(
                    version.defaults.get("context_window_tokens")
                    if version is not None
                    and isinstance(version.defaults.get("context_window_tokens"), int)
                    else None
                ),
                extension_options=(
                    dict(version.defaults.get("extension_options", {}))
                    if version is not None
                    and isinstance(version.defaults.get("extension_options", {}), dict)
                    else {}
                ),
                test_status=cast(
                    Literal["untested", "running", "passed", "failed", "stale", "cancelled"],
                    test_status,
                ),
                latest_test=(
                    ModelTestRunResponse.model_validate(latest_test)
                    if latest_test is not None
                    else None
                ),
                created_at=model.created_at,
                updated_at=model.updated_at,
            )
        )
    blockers = await model_endpoint_enable_blockers(session, endpoint=endpoint)
    used_agents = await list_model_endpoint_used_agents(
        session,
        endpoint_id=endpoint.id,
        tenant_id=endpoint.tenant_id,
    )
    first_version = (
        await session.get(ModelEndpointVersion, models[0].current_version_id)
        if models and models[0].current_version_id is not None
        else None
    )
    return ModelEndpointResponse(
        id=endpoint.id,
        name=endpoint.name,
        logo_url=endpoint.logo_url,
        provider_preset=endpoint.provider_preset,
        provider_kind=first_version.provider_kind if first_version else None,
        base_url=first_version.base_url if first_version else None,
        is_enabled=endpoint.is_enabled,
        read_only=endpoint.read_only,
        active_version_id=endpoint.active_version_id,
        credential_masked_hint=credential.masked_hint if credential else None,
        credential_revision=credential.revision if credential else None,
        models=model_responses,
        used_agent_count=len(used_agents),
        used_agent_names=[agent_name for _, agent_name in used_agents],
        enable_blockers=blockers,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
    )


@router.get("", response_model=ModelEndpointListResponse)
async def read_model_endpoints(
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ModelEndpointListResponse:
    endpoints = await list_model_endpoints(
        session,
        tenant_id=identity.principal.tenant_id,
        page_size=pagination.page_size,
        offset=pagination.offset,
    )
    total = await count_model_endpoints(session, tenant_id=identity.principal.tenant_id)
    metadata = pagination_metadata(total, pagination)
    return ModelEndpointListResponse(
        items=[await _endpoint_response(session, endpoint) for endpoint in endpoints],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
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
        if payload.version is not None:
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
        else:
            assert payload.provider_kind is not None
            assert payload.base_url is not None
            endpoint, _ = await save_model_endpoint_configuration(
                session,
                endpoint_id=None,
                tenant_id=identity.principal.tenant_id,
                actor_user_id=identity.user.id,
                name=payload.name,
                provider_preset=payload.provider_preset,
                provider_kind=payload.provider_kind,
                base_url=payload.base_url,
                api_key=payload.api_key,
                models=[
                    ModelEndpointSelection(
                        id=item.id,
                        upstream_model_id=item.upstream_model_id,
                        extension_options=item.extension_options,
                    )
                    for item in payload.models
                ],
                request_enable=payload.is_enabled,
                secret_provider=secret_provider,
                allow_private_networks=settings.model_endpoint_allow_private_networks,
                allowed_hosts=settings.model_endpoint_allowed_hosts,
                correlation_id=request.state.correlation_id,
            )
    return await _endpoint_response(session, endpoint)


@router.get("/presets", response_model=list[dict[str, str]])
async def read_model_provider_presets(
    identity: IdentityContext = Depends(platform_admin_identity),
) -> list[dict[str, str]]:
    """返回受控供应商预设；自定义选项由前端固定展示为空配置。"""
    del identity
    return [dict(item) for item in MODEL_PROVIDER_PRESETS]


@router.post("/discover", response_model=ModelDiscoveryResponse)
async def discover_models(
    payload: ModelDiscoveryRequest,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
) -> ModelDiscoveryResponse:
    """使用请求中的 Base URL 和 API Key 调用通用 OpenAI-compatible 模型目录。"""
    del identity
    settings = settings_from(request)
    items = await discover_remote_models(
        base_url=payload.base_url,
        api_key=payload.api_key,
        allow_private_networks=settings.model_endpoint_allow_private_networks,
        allowed_hosts=settings.model_endpoint_allowed_hosts,
        timeout_seconds=settings.model_test_timeout_seconds,
        max_response_bytes=settings.model_test_max_response_bytes,
    )
    return ModelDiscoveryResponse(items=items)


@router.get("/test-runs/{test_run_id}", response_model=ModelTestRunResponse)
async def read_model_test_run(
    test_run_id: UUID,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ModelTestRunResponse:
    test_run = await get_model_test_run(
        session,
        test_run_id=test_run_id,
        tenant_id=identity.principal.tenant_id,
    )
    return ModelTestRunResponse.model_validate(test_run)


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


@router.put("/{endpoint_id}/configuration", response_model=ModelEndpointResponse)
async def save_model_endpoint(
    endpoint_id: UUID,
    payload: ModelEndpointConfigurationSave,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
    secret_provider: SecretProvider = Depends(secret_provider_from),
) -> ModelEndpointResponse:
    """整体保存连接配置；普通保存停用，保存并使用受测试结果约束。"""
    settings = settings_from(request)
    async with session.begin():
        endpoint, _ = await save_model_endpoint_configuration(
            session,
            endpoint_id=endpoint_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            name=payload.name,
            provider_preset=payload.provider_preset,
            provider_kind=payload.provider_kind,
            base_url=payload.base_url,
            api_key=payload.api_key,
            models=[
                ModelEndpointSelection(
                    id=item.id,
                    upstream_model_id=item.upstream_model_id,
                    extension_options=item.extension_options,
                )
                for item in payload.models
            ],
            request_enable=payload.is_enabled,
            secret_provider=secret_provider,
            allow_private_networks=settings.model_endpoint_allow_private_networks,
            allowed_hosts=settings.model_endpoint_allowed_hosts,
            correlation_id=request.state.correlation_id,
        )
    return await _endpoint_response(session, endpoint)


@router.post(
    "/{endpoint_id}/models/{endpoint_model_id}/tests",
    response_model=ModelTestRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_model_test(
    endpoint_id: UUID,
    endpoint_model_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
    secret_provider: SecretProvider = Depends(secret_provider_from),
) -> ModelTestRunResponse:
    """创建逐模型测试并在独立数据库会话中执行真实流式探针。"""
    settings = settings_from(request)
    async with session.begin():
        test_run = await start_model_test_run(
            session,
            endpoint_id=endpoint_id,
            endpoint_model_id=endpoint_model_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            correlation_id=request.state.correlation_id,
        )

    session_factory = request.app.state.session_factory

    async def execute() -> None:
        async with session_factory() as background_session:
            await execute_model_test_run(
                background_session,
                test_run_id=test_run.id,
                secret_provider=secret_provider,
                allow_private_networks=settings.model_endpoint_allow_private_networks,
                allowed_hosts=settings.model_endpoint_allowed_hosts,
                timeout_seconds=settings.model_test_timeout_seconds,
                max_response_bytes=settings.model_test_max_response_bytes,
            )

    background_tasks.add_task(execute)
    return ModelTestRunResponse.model_validate(test_run)


@router.get(
    "/{endpoint_id}/models/{endpoint_model_id}/tests",
    response_model=ModelTestRunListResponse,
)
async def read_model_test_history(
    endpoint_id: UUID,
    endpoint_model_id: UUID,
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ModelTestRunListResponse:
    await get_model_endpoint(
        session, endpoint_id=endpoint_id, tenant_id=identity.principal.tenant_id
    )
    models = await list_model_endpoint_models(
        session, endpoint_id=endpoint_id, tenant_id=identity.principal.tenant_id
    )
    if endpoint_model_id not in {model.id for model in models}:
        from supportops_core.services import ResourceNotFoundError

        raise ResourceNotFoundError("模型不存在")
    runs = await list_model_test_runs(
        session,
        endpoint_model_id=endpoint_model_id,
        tenant_id=identity.principal.tenant_id,
        page_size=pagination.page_size,
        offset=pagination.offset,
    )
    total = await count_model_test_runs(
        session,
        endpoint_model_id=endpoint_model_id,
        tenant_id=identity.principal.tenant_id,
    )
    metadata = pagination_metadata(total, pagination)
    return ModelTestRunListResponse(
        items=[ModelTestRunResponse.model_validate(item) for item in runs],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.post("/{endpoint_id}/enable", response_model=ModelEndpointResponse)
async def turn_on_model_endpoint(
    endpoint_id: UUID,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ModelEndpointResponse:
    async with session.begin():
        endpoint = await enable_model_endpoint(
            session,
            endpoint_id=endpoint_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            correlation_id=request.state.correlation_id,
        )
    return await _endpoint_response(session, endpoint)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_model_endpoint(
    endpoint_id: UUID,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> None:
    """删除已停用且未被 Agent 运行版本引用的模型。"""
    async with session.begin():
        await delete_model_endpoint(
            session,
            endpoint_id=endpoint_id,
            tenant_id=identity.principal.tenant_id,
        )


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
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ModelEndpointVersionListResponse:
    versions = await list_model_endpoint_versions(
        session,
        endpoint_id=endpoint_id,
        tenant_id=identity.principal.tenant_id,
        page_size=pagination.page_size,
        offset=pagination.offset,
    )
    total = await count_model_endpoint_versions(
        session, endpoint_id=endpoint_id, tenant_id=identity.principal.tenant_id
    )
    metadata = pagination_metadata(total, pagination)
    return ModelEndpointVersionListResponse(
        items=[ModelEndpointVersionResponse.model_validate(item) for item in versions],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
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
