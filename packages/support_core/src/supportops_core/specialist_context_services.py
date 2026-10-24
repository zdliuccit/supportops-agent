"""从父尝试的显式知识证据构造隔离上下文；无模型、工具或预算派发。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import agent_config_digest, parse_agent_config
from supportops_core.agent_services import _grant_filter, get_usable_agent
from supportops_core.auth import IdentityContext
from supportops_core.config import Settings
from supportops_core.enums import KnowledgeDocumentStatus, KnowledgeVersionStatus
from supportops_core.knowledge_retrieval import (
    KnowledgeEvidenceError, lock_evidence_documents, read_knowledge_evidence,
)
from supportops_core.knowledge_services import _acl_digest, knowledge_access_allowed
from supportops_core.models import (
    AgentAccessGrant, AgentVersion, KnowledgeDocument, KnowledgeEvidence,
    KnowledgeSnapshot, KnowledgeVersion, ModelEndpoint, ModelEndpointVersion, ToolInvocation,
)
from supportops_core.redaction import redact_sensitive_text
from supportops_core.release_manifest_services import manifest_digest, project_release_manifest
from supportops_core.run_attempt_services import (
    OwnedRun, RunAttemptIdentity, current_run_identity, owned_run,
)
from supportops_core.specialist_context_contracts import (
    SpecialistKnowledgeContext, SpecialistKnowledgeExcerpt, SpecialistKnowledgeScope,
    knowledge_payload_bytes,
)
from supportops_core.specialist_task_contracts import (
    SpecialistEvidence, SpecialistParent, SpecialistResource, SpecialistTaskInput,
    SpecialistVersion, contract_digest,
)
from supportops_core.tool_policy import ToolPolicyError, resolve_tool_policy


class SpecialistContextDenied(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("专业子任务知识上下文当前不可读取")
        self.code = code


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _version_ref(version: AgentVersion) -> SpecialistVersion:
    status, manifest = project_release_manifest(version)
    if (status != "recorded" or manifest is None or version.release_manifest_digest is None
        or version.withdrawn_at is not None
        or version.schema_version != "2"):
        raise SpecialistContextDenied("SPECIALIST_VERSION_UNAVAILABLE")
    try:
        if agent_config_digest(parse_agent_config(version.config)) != version.config_digest:
            raise ValueError()
    except ValueError:
        raise SpecialistContextDenied("SPECIALIST_VERSION_CHANGED") from None
    return SpecialistVersion(agent_version_id=version.id,
        model_version_id=version.model_endpoint_version_id,
        release_manifest_digest=version.release_manifest_digest)


async def _specialist_parent_versions(session: AsyncSession, *, attempt: RunAttemptIdentity,
    child_version: SpecialistVersion,
) -> tuple[OwnedRun, IdentityContext, AgentVersion, AgentVersion]:
    """知识和业务观察共用当前父身份/固定版本核对，不投影任何父正文。"""
    owned = await owned_run(session, attempt)
    identity = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
    if owned.conversation.user_id != identity.user.id:
        raise SpecialistContextDenied("SPECIALIST_PARENT_SCOPE_CHANGED")
    versions = (await session.scalars(select(AgentVersion).where(
        AgentVersion.tenant_id == attempt.tenant_id,
        AgentVersion.id.in_({owned.run.agent_version_id, child_version.agent_version_id}),
    ).execution_options(populate_existing=True))).all()
    indexed_versions = {item.id: item for item in versions}
    parent_version = indexed_versions.get(owned.run.agent_version_id)
    child = indexed_versions.get(child_version.agent_version_id)
    if parent_version is None or child is None:
        raise SpecialistContextDenied("SPECIALIST_VERSION_UNAVAILABLE")
    parent_ref = _version_ref(parent_version)
    if (parent_version.agent_id != owned.run.agent_id
        or parent_version.config_digest != owned.run.config_digest
        or owned.conversation.agent_version_id != parent_version.id
        or parent_ref.model_version_id != owned.run.model_endpoint_version_id
        or _version_ref(child) != child_version):
        raise SpecialistContextDenied("SPECIALIST_VERSION_CHANGED")
    # 专业身份不替代原主体；目标Agent也必须命中该主体当前显式授权。
    await get_usable_agent(session, agent_id=child.agent_id, identity=identity)
    child_model = await session.scalar(select(ModelEndpointVersion.id).join(ModelEndpoint,
        ModelEndpoint.id == ModelEndpointVersion.endpoint_id).where(
        ModelEndpointVersion.id == child_version.model_version_id,
        ModelEndpointVersion.tenant_id == attempt.tenant_id,
        ModelEndpoint.tenant_id == attempt.tenant_id, ModelEndpoint.is_enabled.is_(True),
    ))
    if child_model is None:
        raise SpecialistContextDenied("SPECIALIST_VERSION_UNAVAILABLE")
    return owned, identity, parent_version, child


async def project_specialist_knowledge_scope(
    session: AsyncSession, *, attempt: RunAttemptIdentity, child_version: SpecialistVersion,
    evidence_ids: tuple[UUID, ...], expires_at: datetime | None = None,
) -> SpecialistKnowledgeScope:
    """可信父级选定证据后读取元数据，不扫描会话、检索新资料或读取知识正文。

    由调用者管理短事务；不提交/回滚。返回值不是可持久使用的授权凭证。
    expires_at仅用于收紧原窗口，不能续期；加载正文还须完整复检。
    """
    if (not 1 <= len(evidence_ids) <= 64 or len(set(evidence_ids)) != len(evidence_ids)
        or any(not isinstance(item, UUID) for item in evidence_ids)):
        raise SpecialistContextDenied("SPECIALIST_EVIDENCE_SCOPE_INVALID")
    started = datetime.now(UTC)
    owned, identity, parent_version, child = await _specialist_parent_versions(session,
        attempt=attempt, child_version=child_version)
    parent_ref = _version_ref(parent_version)
    try:
        knowledge_policy = await resolve_tool_policy(session, identity=identity,
            config=parse_agent_config(parent_version.config), allowed_tool_ids=parent_version.resolved_tool_ids,
            tool_id="search_knowledge", arguments={}, implementation_versions=parent_version.resolved_tool_versions)
    except (ValueError, ToolPolicyError):
        raise SpecialistContextDenied("SPECIALIST_AUTHORIZATION_CHANGED") from None
    if knowledge_policy.approval_required or knowledge_policy.risk_number > 1:
        raise SpecialistContextDenied("SPECIALIST_AUTHORIZATION_CHANGED")
    deadline = min(started + timedelta(seconds=30), _utc(owned.attempt.lease_expires_at))
    if expires_at is not None:
        if expires_at.tzinfo is None:
            raise SpecialistContextDenied("SPECIALIST_CONTEXT_WINDOW_INVALID")
        deadline = min(deadline, expires_at.astimezone(UTC))
    await lock_evidence_documents(session, evidence_ids=list(evidence_ids), identity=identity)
    # 只取无正文元数据，始终固定原Run/attempt、本人和实际成功的检索账本。
    rows = (await session.execute(select(
        KnowledgeEvidence, KnowledgeSnapshot.document_id, KnowledgeSnapshot.version_id,
        KnowledgeSnapshot.content_digest.label("snapshot_digest"),
        KnowledgeSnapshot.effective_until.label("snapshot_until"),
        KnowledgeVersion.effective_until.label("version_until"),
    ).join(ToolInvocation, ToolInvocation.id == KnowledgeEvidence.tool_invocation_id)
        .join(KnowledgeSnapshot, KnowledgeSnapshot.id == KnowledgeEvidence.snapshot_id)
        .join(KnowledgeVersion, KnowledgeVersion.id == KnowledgeSnapshot.version_id)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeSnapshot.document_id).where(
        KnowledgeEvidence.id.in_(evidence_ids), KnowledgeEvidence.tenant_id == attempt.tenant_id,
        KnowledgeEvidence.user_id == identity.user.id, KnowledgeEvidence.run_id == attempt.run_id,
        KnowledgeEvidence.attempt_id == attempt.attempt_id,
        ToolInvocation.tenant_id == attempt.tenant_id, ToolInvocation.run_id == attempt.run_id,
        ToolInvocation.attempt_id == attempt.attempt_id, ToolInvocation.tool_id == "search_knowledge",
        ToolInvocation.status == "succeeded", KnowledgeSnapshot.tenant_id == attempt.tenant_id,
        KnowledgeVersion.tenant_id == attempt.tenant_id, KnowledgeDocument.tenant_id == attempt.tenant_id,
        KnowledgeVersion.document_id == KnowledgeDocument.id,
        KnowledgeVersion.content_digest == KnowledgeSnapshot.content_digest,
        KnowledgeDocument.current_version_id == KnowledgeSnapshot.version_id,
        KnowledgeDocument.status != KnowledgeDocumentStatus.ARCHIVED,
        KnowledgeVersion.status == KnowledgeVersionStatus.PUBLISHED,
        KnowledgeVersion.effective_from <= started, KnowledgeSnapshot.effective_from <= started,
        or_(KnowledgeVersion.effective_until.is_(None), KnowledgeVersion.effective_until > started),
        or_(KnowledgeSnapshot.effective_until.is_(None), KnowledgeSnapshot.effective_until > started),
    ).order_by(KnowledgeEvidence.id).execution_options(populate_existing=True))).all()
    if len(rows) != len(evidence_ids):
        raise SpecialistContextDenied("SPECIALIST_EVIDENCE_UNAVAILABLE")
    documents = sorted({row.document_id for row in rows}, key=str)
    if len(documents) > 32:
        raise SpecialistContextDenied("SPECIALIST_RESOURCE_LIMIT")
    acl_digests: dict[str, str] = {}
    for document_id in documents:
        if not await knowledge_access_allowed(session, document_id=document_id,
            tenant_id=attempt.tenant_id, identity=identity):
            raise SpecialistContextDenied("SPECIALIST_AUTHORIZATION_CHANGED")
        acl_digests[str(document_id)] = await _acl_digest(session,
            document_id=document_id, tenant_id=attempt.tenant_id)
    grants = (await session.execute(select(AgentAccessGrant.id, AgentAccessGrant.agent_id,
        AgentAccessGrant.subject_type, AgentAccessGrant.subject_id).where(
        AgentAccessGrant.tenant_id == attempt.tenant_id,
        AgentAccessGrant.agent_id.in_({parent_version.agent_id, child.agent_id}),
        _grant_filter(identity),
    ).order_by(AgentAccessGrant.id))).all()
    resource_versions: dict[UUID, str] = {}
    for row in rows:
        for limit in (row.snapshot_until, row.version_until):
            if limit is not None:
                deadline = min(deadline, _utc(limit))
        if _utc(row.KnowledgeEvidence.retrieved_at) > started:
            raise SpecialistContextDenied("SPECIALIST_EVIDENCE_UNAVAILABLE")
        resource_versions[row.document_id] = manifest_digest({
            "schema_version": "specialist_knowledge_resource_v1", "document_id": str(row.document_id),
            "snapshot_id": str(row.KnowledgeEvidence.snapshot_id), "version_id": str(row.version_id),
            "content_digest": row.snapshot_digest,
        })
    scope_digest = manifest_digest({
        "schema_version": "specialist_knowledge_authorization_v1",
        "tenant_id": str(attempt.tenant_id), "subject_user_id": str(identity.user.id),
        "auth_generation": identity.user.auth_generation,
        "organization_unit_id": str(identity.user.organization_unit_id) if identity.user.organization_unit_id else None,
        "roles": sorted(identity.principal.roles), "run_id": str(attempt.run_id),
        "conversation_id": str(attempt.conversation_id), "owner_id": attempt.owner_id,
        "attempt_id": str(attempt.attempt_id), "generation": attempt.generation,
        "parent_config_digest": owned.run.config_digest, "parent_credential_revision": owned.run.credential_revision,
        "parent_version": parent_ref.model_dump(mode="json"),
        "child_version": child_version.model_dump(mode="json"),
        "knowledge_tool": {"implementation_version": knowledge_policy.implementation_version,
            "catalog_version": knowledge_policy.catalog_version},
        "agent_grants": [[str(row.id), str(row.agent_id), row.subject_type.value, row.subject_id] for row in grants],
        "knowledge_acls": acl_digests,
        "resources": {str(key): value for key, value in resource_versions.items()},
        "evidence": [[str(row.KnowledgeEvidence.id), row.KnowledgeEvidence.content_digest,
            str(row.KnowledgeEvidence.chunk_id)] for row in rows],
    })
    resources = tuple(SpecialistResource(tenant_id=attempt.tenant_id, resource_kind="knowledge_document",
        resource_id=key, resource_version_digest=resource_versions[key],
        authorization_scope_digest=scope_digest) for key in documents)
    by_document = {item.resource_id: item for item in resources}
    if datetime.now(UTC) >= deadline:
        raise SpecialistContextDenied("SPECIALIST_CONTEXT_EXPIRED")
    return SpecialistKnowledgeScope(parent=SpecialistParent(tenant_id=attempt.tenant_id,
        subject_user_id=identity.user.id, conversation_id=attempt.conversation_id,
        run_id=attempt.run_id, attempt_id=attempt.attempt_id, attempt_generation=attempt.generation,
        version=parent_ref, authorization_scope_digest=scope_digest, authorized_until=deadline),
        child_version=child_version, resources=resources,
        evidence=tuple(SpecialistEvidence(evidence_id=row.KnowledgeEvidence.id,
            resource=by_document[row.document_id], content_digest=row.KnowledgeEvidence.content_digest,
            observed_at=_utc(row.KnowledgeEvidence.retrieved_at), valid_until=deadline) for row in rows),
        checked_at=started, expires_at=deadline)


async def load_specialist_knowledge_context(
    session: AsyncSession, *, attempt: RunAttemptIdentity, task: SpecialistTaskInput,
    scope: SpecialistKnowledgeScope, settings: Settings, redaction_secrets: tuple[str, ...] = (),
) -> SpecialistKnowledgeContext:
    """只加载任务选择的原父级证据子集；每次消费前须再调用，不能缓存后当成许可。

    可信调用者须提供当前在内存中已知的模型秘密供遮蔽；本服务不取密。
    不把消息历史、checkpoint、Prompt、用户身份正文或未列出的证据放入子上下文。
    """
    now = datetime.now(UTC)
    if (task.specialist != "knowledge" or task.parent != scope.parent
        or task.child_version != scope.child_version or not task.evidence
        or not scope.checked_at <= task.created_at <= now < task.deadline <= scope.expires_at
        or any(item not in scope.resources for item in task.resources)
        or any(item not in scope.evidence for item in task.evidence)
        or {item.resource for item in task.evidence} != set(task.resources)):
        raise SpecialistContextDenied("SPECIALIST_CONTEXT_SCOPE_INVALID")
    fresh = await project_specialist_knowledge_scope(session, attempt=attempt,
        child_version=scope.child_version, evidence_ids=tuple(item.evidence_id for item in scope.evidence),
        expires_at=scope.expires_at)
    if (fresh.parent != scope.parent or fresh.resources != scope.resources or fresh.evidence != scope.evidence):
        raise SpecialistContextDenied("SPECIALIST_AUTHORIZATION_CHANGED")
    owned = await owned_run(session, attempt)
    identity = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
    secrets = (settings.jwt_secret, settings.secret_encryption_key, *redaction_secrets)
    question = redact_sensitive_text(task.question, secrets=secrets)
    excerpts: list[SpecialistKnowledgeExcerpt] = []
    for source in sorted(task.evidence, key=lambda item: str(item.evidence_id)):
        try:
            data = await read_knowledge_evidence(session, evidence_id=source.evidence_id,
                identity=identity, run_id=attempt.run_id, attempt_id=attempt.attempt_id, lock=False)
        except KnowledgeEvidenceError:
            raise SpecialistContextDenied("SPECIALIST_EVIDENCE_UNAVAILABLE") from None
        if data["content_digest"] != source.content_digest:
            raise SpecialistContextDenied("SPECIALIST_EVIDENCE_CHANGED")
        content, title, section_id, section_title = (data[key] for key in (
            "content", "title", "section_id", "section_title"))
        snapshot_id, version_id = data["snapshot_id"], data["version_id"]
        if (not isinstance(content, str) or not isinstance(title, str)
            or not isinstance(section_id, str) or not isinstance(section_title, str)
            or not isinstance(snapshot_id, str) or not isinstance(version_id, str)):
            raise SpecialistContextDenied("SPECIALIST_EVIDENCE_UNAVAILABLE")
        if redact_sensitive_text(section_id, secrets=secrets) != section_id:
            raise SpecialistContextDenied("SPECIALIST_EVIDENCE_UNAVAILABLE")
        rendered = redact_sensitive_text(content, secrets=secrets)
        if len(rendered.encode("utf-8")) > 16384:
            raise SpecialistContextDenied("SPECIALIST_CONTEXT_TOO_LARGE")
        excerpts.append(SpecialistKnowledgeExcerpt(evidence_id=source.evidence_id, resource=source.resource,
            snapshot_id=UUID(snapshot_id), version_id=UUID(version_id),
            original_content_digest=source.content_digest,
            rendered_content_digest=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            title=redact_sensitive_text(title, secrets=secrets), section_id=section_id,
            section_title=redact_sensitive_text(section_title, secrets=secrets),
            content=rendered, content_is_redacted=rendered != content))
    payload_bytes = knowledge_payload_bytes(question, tuple(excerpts))
    if len(question.encode("utf-8")) > 8192 or payload_bytes > 65536:
        raise SpecialistContextDenied("SPECIALIST_CONTEXT_TOO_LARGE")
    # 正文读取后再次复查完整父范围；当前观察不替代派发/结果采纳时的独立复检。
    final = await project_specialist_knowledge_scope(session, attempt=attempt,
        child_version=scope.child_version, evidence_ids=tuple(item.evidence_id for item in scope.evidence),
        expires_at=scope.expires_at)
    checked_at = datetime.now(UTC)
    if (final.parent != fresh.parent or final.resources != fresh.resources or final.evidence != fresh.evidence):
        raise SpecialistContextDenied("SPECIALIST_AUTHORIZATION_CHANGED")
    if checked_at >= task.deadline:
        raise SpecialistContextDenied("SPECIALIST_CONTEXT_EXPIRED")
    return SpecialistKnowledgeContext(task_id=task.task_id, input_digest=contract_digest(task),
        scope_digest=contract_digest(scope), budget_scope_digest=contract_digest(task.budget),
        checked_at=checked_at, expires_at=task.deadline, question=question,
        question_is_redacted=question != task.question, excerpts=tuple(excerpts), payload_bytes=payload_bytes)
