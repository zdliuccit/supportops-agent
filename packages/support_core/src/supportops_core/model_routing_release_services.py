"""显式保存v3不可变候选；不激活版本、不写旧草稿、不提交模型或Run。"""

import asyncio
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_services import append_agent_audit, ensure_agent_mutable, get_agent
from supportops_core.auth import IdentityContext
from supportops_core.config import Settings
from supportops_core.enums import AuditAction
from supportops_core.model_routing_config_v3 import (
    FixedModelBindingV3, RoutingCandidatePublishRequest, RoutingConfigResolution,
)
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader
from supportops_core.model_routing_policy_services import (
    RoutingPolicyConflict, read_routing_policy_version_by_id, require_routing_administrator,
)
from supportops_core.model_routing_release_history import assert_v3_release_history_guard
from supportops_core.model_routing_resolution import project_v3_model_validation, resolve_v3_config
from supportops_core.models import (
    AgentDraft, AgentVersion, ModelCredential, ModelCredentialRevision, ModelEndpoint,
    ModelEndpointModel, ModelEndpointVersion, ToolCatalogEntry, utc_now,
)
from supportops_core.release_manifest_services import (
    AgentReleaseManifestV2, ResolvedModelRoutingManifest, RoutingCandidateOrigin,
    ReleaseManifestUnavailable, build_release_manifest, manifest_digest, project_release_manifest,
)
from supportops_core.services import ResourceNotFoundError


def _scope_digest(resolution: RoutingConfigResolution) -> str:
    payload = resolution.model_dump(mode="json")
    payload.pop("observed_at")
    if payload["candidate_filter"] is not None:
        payload["candidate_filter"].pop("observed_at")
    return manifest_digest(payload)


async def _version(session: AsyncSession, *, tenant_id: UUID, agent_id: UUID, version_id: UUID) -> AgentVersion:
    row = await session.scalar(select(AgentVersion).where(AgentVersion.tenant_id == tenant_id,
        AgentVersion.agent_id == agent_id, AgentVersion.id == version_id, AgentVersion.schema_version == "3")
        .execution_options(populate_existing=True))
    if row is None:
        raise ResourceNotFoundError("v3候选版本不存在")
    return row


async def read_v3_candidate(session: AsyncSession, identity: IdentityContext, *,
    agent_id: UUID, version_id: UUID,
) -> AgentVersion:
    await require_routing_administrator(session, identity)
    row = await _version(session, tenant_id=identity.principal.tenant_id, agent_id=agent_id, version_id=version_id)
    await require_routing_administrator(session, identity)
    return row


async def _lock_references(session: AsyncSession, identity: IdentityContext, command: RoutingCandidatePublishRequest) -> None:
    tenant_id = identity.principal.tenant_id
    model = command.request.config.model
    expected_revisions: dict[UUID, int] = {}
    if isinstance(model, FixedModelBindingV3):
        endpoint_ids, model_ids, version_ids = {model.model_endpoint_id}, {model.model_endpoint_model_id}, {model.model_endpoint_version_id}
    else:
        policy = await read_routing_policy_version_by_id(session, identity, version_id=model.policy_version_id)
        if policy.status != "candidate" or policy.policy_digest != model.policy_digest:
            raise RoutingPolicyConflict("ROUTING_POLICY_SCOPE_MISMATCH")
        candidates = policy.record.definition.candidates
        endpoint_ids = {row.endpoint_id for row in candidates}
        model_ids = {row.endpoint_model_id for row in candidates}
        version_ids = {row.model_version_id for row in candidates}
        expected_revisions = {row.model_version_id: row.credential_revision for row in candidates}
    # 按父资源及唯一ID顺序锁住当前元数据；不加载密文、SDK或网络资源。
    for entity, ids in ((ModelEndpoint, endpoint_ids), (ModelEndpointModel, model_ids), (ModelEndpointVersion, version_ids)):
        found = (await session.scalars(select(entity.id).where(entity.tenant_id == tenant_id,
            entity.id.in_(ids), entity.deleted_at.is_(None)).order_by(entity.id).with_for_update(read=True))).all()
        if len(found) != len(ids):
            raise RoutingPolicyConflict("ROUTING_CANDIDATE_REFERENCES_UNAVAILABLE")
    versions = (await session.execute(select(ModelEndpointVersion.id, ModelEndpointVersion.credential_id).where(
        ModelEndpointVersion.tenant_id == tenant_id, ModelEndpointVersion.id.in_(version_ids)))).all()
    credential_ids = {row.credential_id for row in versions}
    credentials = (await session.execute(select(ModelCredential.id, ModelCredential.revision).where(
        ModelCredential.tenant_id == tenant_id, ModelCredential.id.in_(credential_ids), ModelCredential.deleted_at.is_(None))
        .order_by(ModelCredential.id).with_for_update(read=True))).all()
    revisions = {row.id: row.revision for row in credentials}
    if len(revisions) != len(credential_ids):
        raise RoutingPolicyConflict("ROUTING_CANDIDATE_REFERENCES_UNAVAILABLE")
    pairs = set()
    for version in versions:
        revision = revisions[version.credential_id]
        if version.id in expected_revisions and expected_revisions[version.id] != revision:
            raise RoutingPolicyConflict("ROUTING_CANDIDATE_REFERENCES_CHANGED")
        pairs.add((version.credential_id, revision))
    revision_ids = (await session.scalars(select(ModelCredentialRevision.id).where(
        ModelCredentialRevision.tenant_id == tenant_id,
        tuple_(ModelCredentialRevision.credential_id, ModelCredentialRevision.revision).in_(pairs),
        ModelCredentialRevision.deleted_at.is_(None)).order_by(ModelCredentialRevision.id).with_for_update(read=True))).all()
    if len(revision_ids) != len(pairs):
        raise RoutingPolicyConflict("ROUTING_CANDIDATE_REFERENCES_UNAVAILABLE")
    tool_ids = [row.tool_id for row in command.request.config.tools if row.enabled]
    (await session.scalars(select(ToolCatalogEntry).where(ToolCatalogEntry.tenant_id == tenant_id,
        ToolCatalogEntry.tool_id.in_(tool_ids)).order_by(ToolCatalogEntry.tool_id).with_for_update(read=True))).all()


async def publish_v3_candidate(session: AsyncSession, identity: IdentityContext, *,
    command: RoutingCandidatePublishRequest, settings: Settings, correlation_id: str,
    boundary_reader: RoutingDataBoundaryReader | None = None,
) -> AgentVersion:
    """调用方持有事务；管理员写授权独立复检，不接受上传预览结果或activate字段。"""
    command = RoutingCandidatePublishRequest.model_validate(command.model_dump(mode="json"))
    submission_digest = manifest_digest(command.model_dump(mode="json"))
    async with asyncio.timeout(30):
        current = await require_routing_administrator(session, identity, lock=True)
        tenant_id, agent_id = current.principal.tenant_id, command.request.agent_id
        agent = await get_agent(session, agent_id=agent_id, tenant_id=tenant_id, lock=True)
        existing = await session.scalar(select(AgentVersion).where(AgentVersion.tenant_id == tenant_id,
            AgentVersion.agent_id == agent_id, AgentVersion.id == command.version_id))
        if existing is not None:
            status, manifest = project_release_manifest(existing)
            if (status != "recorded" or not isinstance(manifest, AgentReleaseManifestV2)
                or existing.published_by != current.principal.user_id
                or manifest.candidate_origin.submission_digest != submission_digest):
                raise RoutingPolicyConflict("ROUTING_CANDIDATE_ID_CONFLICT")
            return existing
        ensure_agent_mutable(agent)
        await assert_v3_release_history_guard(session)
        draft = await session.scalar(select(AgentDraft).where(AgentDraft.tenant_id == tenant_id, AgentDraft.agent_id == agent_id)
            .execution_options(populate_existing=True).with_for_update())
        if draft is None or draft.revision != command.expected_draft_revision:
            raise RoutingPolicyConflict("ROUTING_CANDIDATE_DRAFT_CHANGED")
        latest = int(await session.scalar(select(func.max(AgentVersion.version_number)).where(
            AgentVersion.tenant_id == tenant_id, AgentVersion.agent_id == agent_id)) or 0)
        if latest != command.expected_latest_version_number:
            raise RoutingPolicyConflict("ROUTING_CANDIDATE_HEAD_CHANGED")
        await _lock_references(session, current, command)
        preview = await resolve_v3_config(session, current, command=command.request, settings=settings,
            boundary_reader=boundary_reader)
        resolution = preview.resolution
        if resolution.status != "resolved" or resolution.resolved_models is None:
            raise RoutingPolicyConflict("ROUTING_CANDIDATE_NOT_RESOLVED")
        policy_snapshot = None
        primary_candidate_id = None
        config = command.request.config
        if not isinstance(config.model, FixedModelBindingV3):
            policy = await read_routing_policy_version_by_id(session, current, version_id=config.model.policy_version_id)
            policy_snapshot = policy.record
            primary_candidate_id = policy.record.definition.primary_candidate_id
        primary = next(row for row in resolution.resolved_models if row.candidate_id == primary_candidate_id)
        version = await session.scalar(select(ModelEndpointVersion).where(ModelEndpointVersion.tenant_id == tenant_id,
            ModelEndpointVersion.id == primary.model_version_id).execution_options(populate_existing=True))
        if version is None:
            raise RoutingPolicyConflict("ROUTING_CANDIDATE_REFERENCES_CHANGED")
        projection = project_v3_model_validation(config, endpoint_id=primary.endpoint_id,
            endpoint_model_id=primary.endpoint_model_id)
        try:
            common = await build_release_manifest(session, tenant_id=tenant_id, agent_id=agent_id,
                agent_version_id=command.version_id, config=projection, model=version,
                credential_revision=primary.credential_revision_at_resolution,
                tool_ids=sorted(resolution.resolved_tool_versions), settings=settings)
        except ReleaseManifestUnavailable:
            raise RoutingPolicyConflict("ROUTING_CANDIDATE_MANIFEST_UNAVAILABLE") from None
        snapshot = ResolvedModelRoutingManifest(binding_digest=manifest_digest(config.model.model_dump(mode="json")),
            primary_candidate_id=primary_candidate_id, policy_snapshot=policy_snapshot, resolution=resolution)
        payload = common.model_dump(mode="json", exclude={"schema_version"})
        payload.update({"config_digest": resolution.config_digest,
            "graph_policy_digest": manifest_digest({"engine": config.runtime.engine,
                "runtime": config.runtime.model_dump(mode="json"), "sources": common.source_digests,
                "model_binding": snapshot.binding_digest, "model_references": _scope_digest(resolution)}),
            "model_routing": snapshot.model_dump(mode="json"),
            "candidate_origin": RoutingCandidateOrigin(submission_digest=submission_digest,
                expected_draft_revision=command.expected_draft_revision,
                expected_latest_version_number=latest).model_dump(mode="json")})
        manifest = AgentReleaseManifestV2.model_validate(payload)
        saved_at = utc_now()
        row = AgentVersion(id=command.version_id, tenant_id=tenant_id, agent_id=agent_id,
            version_number=latest + 1, schema_version="3", config=config.model_dump(mode="json"),
            config_digest=resolution.config_digest, model_endpoint_version_id=primary.model_version_id,
            resolved_tool_ids=sorted(resolution.resolved_tool_versions),
            resolved_tool_versions=resolution.resolved_tool_versions,
            release_manifest=manifest.model_dump(mode="json"),
            release_manifest_digest=manifest_digest(manifest.model_dump(mode="json")),
            release_notes=command.release_notes, published_by=current.principal.user_id, published_at=saved_at)
        session.add(row)
        if project_release_manifest(row)[0] != "recorded":
            raise RoutingPolicyConflict("ROUTING_CANDIDATE_MANIFEST_INVALID")
        agent.updated_by, agent.updated_at = current.principal.user_id, saved_at
        await append_agent_audit(session, tenant_id=tenant_id, agent_id=agent_id, actor_user_id=current.principal.user_id,
            action=AuditAction.VERSION_PUBLISHED, correlation_id=correlation_id, version_id=row.id,
            metadata={"schema_version": "3", "candidate_only": True, "runtime_supported": False,
                "version_number": row.version_number, "submission_digest": submission_digest,
                "config_digest": row.config_digest, "release_manifest_digest": row.release_manifest_digest})
        # 清单构建或异步治理等待后复查原集合；未知/变化整笔回滚，不补造通过。
        checked = await resolve_v3_config(session, current, command=command.request, settings=settings,
            boundary_reader=boundary_reader)
        if checked.resolution.status != "resolved" or _scope_digest(checked.resolution) != _scope_digest(resolution):
            raise RoutingPolicyConflict("ROUTING_CANDIDATE_REFERENCES_CHANGED")
        await require_routing_administrator(session, current, lock=True)
        await assert_v3_release_history_guard(session)
        return row
