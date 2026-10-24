"""路由策略候选版本管理；不提供批准、启用或模型调用入口。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.auth import IdentityContext
from supportops_core.model_routing_policy_contracts import (
    RoutingPolicyRevoke, RoutingPolicySave, RoutingPolicyVersionView,
)
from supportops_core.model_routing_policy_services import (
    RoutingPolicyUnavailable, list_routing_policy_versions, read_routing_policy_version,
    revoke_routing_policy_version, save_routing_policy_version,
)
from supportops_core.services import ResourceNotFoundError

from supportops_core.model_routing_config_v3 import (
    RoutingCandidatePublishRequest, RoutingConfigResolveRequest, RoutingConfigResolutionView,
)
from supportops_core.model_routing_resolution import RoutingResolutionUnavailable, resolve_v3_config
from supportops_core.model_routing_release_services import publish_v3_candidate, read_v3_candidate
from supportops_core.models import AgentVersion
from supportops_core.release_manifest_services import AgentReleaseManifestV2, project_release_manifest

from supportops_api.dependencies import current_identity, database_session, settings_from
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import AgentVersionResponse, PaginatedListResponse

router = APIRouter(prefix="/v1/admin/model-routing-policies", tags=["admin-model-routing"])


class RoutingPolicyVersionListResponse(PaginatedListResponse):
    items: list[RoutingPolicyVersionView]


class V3CandidateVersionView(BaseModel):
    version: AgentVersionResponse
    submission_digest: str | None = None
    candidate_only: Literal[True] = True
    runtime_supported: Literal[False] = False
    activation_allowed: Literal[False] = False


def _candidate_view(row: AgentVersion) -> V3CandidateVersionView:
    status, manifest = project_release_manifest(row)
    # 与普通版本读取相同：先核完整性，再投影；损坏JSON不得先进入响应模型。
    metadata = AgentVersionResponse(id=row.id, agent_id=row.agent_id, version_number=row.version_number,
        schema_version=row.schema_version, config_digest=row.config_digest,
        model_endpoint_version_id=row.model_endpoint_version_id, resolved_tool_ids=row.resolved_tool_ids,
        resolved_tool_versions=row.resolved_tool_versions, release_manifest=manifest,
        release_manifest_digest=row.release_manifest_digest if status == "recorded" else None,
        release_manifest_status=status, withdrawn_at=row.withdrawn_at, withdrawn_by=row.withdrawn_by,
        release_notes=row.release_notes, published_at=row.published_at)
    digest = manifest.candidate_origin.submission_digest if isinstance(manifest, AgentReleaseManifestV2) else None
    return V3CandidateVersionView(version=metadata, submission_digest=digest)


@asynccontextmanager
async def _transaction(session: AsyncSession, *, read_only: bool = False) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(10), session.begin():
            if session.get_bind().dialect.name == "postgresql":
                if read_only:
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
            yield
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="资源不存在或当前不可访问",
            headers={"Cache-Control": "no-store"}) from None
    except (TimeoutError, SQLAlchemyError):
        if read_only:
            raise RoutingResolutionUnavailable() from None
        # 未知提交只查询原version_id，不能自动创建新版本或重发撤销。
        raise RoutingPolicyUnavailable() from None


@router.post("/resolve-config", response_model=RoutingConfigResolutionView)
async def resolve_configuration(command: RoutingConfigResolveRequest, request: Request, response: Response,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> RoutingConfigResolutionView:
    """完整有界的只读配置解析（最多8候选），非资源列表，不分页，不运行模型。"""
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session, read_only=True):
        # 不从请求读取治理reader或批准布尔值；默认缺提供者保持候选未确认。
        result = await resolve_v3_config(session, identity, command=command, settings=settings_from(request))
    return result


@router.post("/publish-candidate", response_model=V3CandidateVersionView, status_code=201)
async def save_v3_candidate(command: RoutingCandidatePublishRequest, request: Request, response: Response,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> V3CandidateVersionView:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        row = await publish_v3_candidate(session, identity, command=command, settings=settings_from(request),
            correlation_id=request.state.correlation_id)
        result = _candidate_view(row)
    return result


@router.get("/candidates/{agent_id}/{version_id}", response_model=V3CandidateVersionView)
async def read_candidate(agent_id: UUID, version_id: UUID, response: Response,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> V3CandidateVersionView:
    """单个完整有界候选详情；原提交未知时只读查证，不重发发布。"""
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session, read_only=True):
        row = await read_v3_candidate(session, identity, agent_id=agent_id, version_id=version_id)
        result = _candidate_view(row)
    return result


@router.get("/versions", response_model=RoutingPolicyVersionListResponse)
async def read_versions(response: Response, policy_id: UUID | None = None,
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> RoutingPolicyVersionListResponse:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        items, total = await list_routing_policy_versions(session, identity, policy_id=policy_id,
            page_size=pagination.page_size, offset=pagination.offset)
    metadata = pagination_metadata(total, pagination)
    return RoutingPolicyVersionListResponse(items=items, total=metadata.total, page=metadata.page,
        page_size=metadata.page_size, pages=metadata.pages)


@router.get("/{policy_id}/versions/{version_id}", response_model=RoutingPolicyVersionView)
async def read_version(policy_id: UUID, version_id: UUID, response: Response,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> RoutingPolicyVersionView:
    """单版本完整有界快照，最多8个候选；非普通列表，不分页。"""
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        result = await read_routing_policy_version(session, identity, policy_id=policy_id, version_id=version_id)
    return result


@router.post("/{policy_id}/versions", response_model=RoutingPolicyVersionView, status_code=201)
async def save_version(policy_id: UUID, command: RoutingPolicySave, request: Request, response: Response,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> RoutingPolicyVersionView:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        result = await save_routing_policy_version(session, identity, policy_id=policy_id, command=command,
            correlation_id=request.state.correlation_id)
    return result


@router.post("/{policy_id}/versions/{version_id}/revoke", response_model=RoutingPolicyVersionView)
async def revoke_version(policy_id: UUID, version_id: UUID, command: RoutingPolicyRevoke,
    request: Request, response: Response,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> RoutingPolicyVersionView:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        result = await revoke_routing_policy_version(session, identity, policy_id=policy_id, version_id=version_id,
            command=command, correlation_id=request.state.correlation_id)
    return result
