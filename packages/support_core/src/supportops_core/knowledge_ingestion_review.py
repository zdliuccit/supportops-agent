"""本人导入草稿的受控只读比较；不读取来源，不产生审核或发布决定。"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from supportops_core.auth import IdentityContext
from supportops_core.enums import AuditAction, AuditResourceType, KnowledgeDocumentStatus, KnowledgeVersionStatus
from supportops_core.knowledge_content import knowledge_content_digest
from supportops_core.knowledge_ingestion_contracts import (
    Digest, IngestionContract, IngestionObjectReceipt, IngestionPageReceipt, ingestion_draft_key,
    ingestion_page_key, ingestion_page_result_digest, validate_ingestion_page,
)
from supportops_core.knowledge_ingestion_services import (
    IngestionConflict, _current_admin, _digest, _job_contracts, _utc, get_ingestion_job,
)
from supportops_core.knowledge_services import _acl_digest, knowledge_access_allowed
from supportops_core.models import (
    AgentAuditEvent, KnowledgeDocument, KnowledgeIngestionJob, KnowledgeIngestionMaterialization, KnowledgeIngestionObject,
    KnowledgeIngestionPage, KnowledgeVersion,
)
from supportops_core.services import ResourceNotFoundError


class IngestionDraftView(IngestionContract):
    """仅首次物化标识与时间；复用版本不重复列入新任务。"""

    id: UUID
    created_at: datetime


class IngestionComparedVersion(IngestionContract):
    id: UUID
    version_number: int = Field(ge=1)
    title: str = Field(max_length=500, repr=False)
    content_digest: Digest


class IngestionTextChange(IngestionContract):
    """共同首尾之外的完整替换区间，不声称最小编辑距离或语义冲突检测。"""

    content_changed: bool
    title_changed: bool
    common_prefix_lines: int = Field(ge=0)
    common_suffix_lines: int = Field(ge=0)
    removed_text: str = Field(max_length=65536, repr=False)
    added_text: str = Field(max_length=65536, repr=False)


class IngestionComparisonView(IngestionContract):
    schema_version: Literal["knowledge_ingestion_comparison_v1"] = "knowledge_ingestion_comparison_v1"
    job_id: UUID
    materialization_id: UUID
    status: Literal["available", "content_unavailable", "target_unavailable", "newer_candidate", "candidate_withdrawn",
        "scope_changed", "integrity_failed", "too_large"]
    document_id: UUID | None = None
    owner_user_id: UUID | None = None
    review_due_at: datetime | None = None
    candidate_status: Literal["draft", "in_review", "published"] | None = None
    review_decision: Literal["pending", "approved", "rejected"] | None = None
    candidate: IngestionComparedVersion | None = None
    baseline: IngestionComparedVersion | None = None
    change: IngestionTextChange | None = None
    automatic_publish_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_visibility(self) -> "IngestionComparisonView":
        visible = (self.document_id, self.owner_user_id, self.candidate_status,
            self.candidate, self.baseline, self.change)
        if self.status == "available":
            if any(value is None for value in visible):
                raise ValueError("可用比较必须有完整获准范围与差异")
        elif any(value is not None for value in (*visible, self.review_due_at, self.review_decision)):
            raise ValueError("受限比较不能包含目标范围或正文")
        return self


async def list_ingestion_drafts(session: AsyncSession, *, identity: IdentityContext, job_id: UUID,
    page_size: int, offset: int,
) -> tuple[list[IngestionDraftView], int]:
    job = await get_ingestion_job(session, identity=identity, job_id=job_id)
    if type(page_size) is not int or not 1 <= page_size <= 100 or type(offset) is not int or offset < 0:
        raise IngestionConflict("导入草稿分页无效")
    filters = [KnowledgeIngestionMaterialization.tenant_id == job.tenant_id,
        KnowledgeIngestionMaterialization.source_id == job.source_id,
        KnowledgeIngestionMaterialization.job_id == job.id]
    total = int(await session.scalar(select(func.count()).select_from(KnowledgeIngestionMaterialization)
        .where(*filters)) or 0)
    rows = (await session.execute(select(KnowledgeIngestionMaterialization.id,
        KnowledgeIngestionMaterialization.created_at).where(*filters)
        .order_by(KnowledgeIngestionMaterialization.created_at.desc(), KnowledgeIngestionMaterialization.id.desc())
        .offset(offset).limit(page_size))).all()
    return [IngestionDraftView(id=row.id, created_at=_utc(row.created_at)) for row in rows], total


def _text_change(before: str, after: str, *, title_changed: bool) -> IngestionTextChange | None:
    # UTF-8总量64KiB、每侧最多4000行；算法只做线性首尾扫描，避免重复行的二次复杂度。
    if len(before.encode("utf-8")) + len(after.encode("utf-8")) > 65536:
        return None
    left, right = before.splitlines(keepends=True), after.splitlines(keepends=True)
    if max(len(left), len(right)) > 4000:
        return None
    prefix = 0
    while prefix < min(len(left), len(right)) and left[prefix] == right[prefix]:
        prefix += 1
    suffix = 0
    while (suffix < min(len(left), len(right)) - prefix
        and left[len(left) - suffix - 1] == right[len(right) - suffix - 1]):
        suffix += 1
    return IngestionTextChange(content_changed=before != after, title_changed=title_changed,
        common_prefix_lines=prefix, common_suffix_lines=suffix,
        removed_text="".join(left[prefix:len(left) - suffix]),
        added_text="".join(right[prefix:len(right) - suffix]))


IngestionTargetFailure = Literal["target_unavailable", "newer_candidate", "integrity_failed", "candidate_withdrawn"]


@dataclass(frozen=True, slots=True)
class IngestionReviewTarget:
    """已核验的本人导入绑定；版本仅加载治理元数据，正文权限另行判断。"""

    job: KnowledgeIngestionJob
    materialization: KnowledgeIngestionMaterialization
    document: KnowledgeDocument
    candidate: KnowledgeVersion
    observation: IngestionObjectReceipt


async def load_ingestion_review_target(session: AsyncSession, *, identity: IdentityContext,
    job_id: UUID, materialization_id: UUID,
) -> IngestionReviewTarget | IngestionTargetFailure:
    job = await get_ingestion_job(session, identity=identity, job_id=job_id)
    definition, state = _job_contracts(job)
    materialization = await session.scalar(select(KnowledgeIngestionMaterialization).where(
        KnowledgeIngestionMaterialization.id == materialization_id,
        KnowledgeIngestionMaterialization.tenant_id == job.tenant_id,
        KnowledgeIngestionMaterialization.source_id == job.source_id,
        KnowledgeIngestionMaterialization.job_id == job.id))
    if materialization is None:
        raise ResourceNotFoundError("导入草稿不存在")

    binding = await session.scalar(select(KnowledgeIngestionObject).where(
        KnowledgeIngestionObject.id == materialization.object_id,
        KnowledgeIngestionObject.tenant_id == job.tenant_id,
        KnowledgeIngestionObject.source_id == job.source_id,
        KnowledgeIngestionObject.identity_policy_digest == definition.scope.identity_policy_digest))
    # 当前适配器固定单页。取至多两项以检测异常重复，不对损坏历史自行选择一个结果。
    pages = (await session.scalars(select(KnowledgeIngestionPage).where(
        KnowledgeIngestionPage.tenant_id == job.tenant_id, KnowledgeIngestionPage.job_id == job.id).limit(2))).all()
    if binding is None or len(pages) != 1:
        return "integrity_failed"
    try:
        page = IngestionPageReceipt.model_validate_json(json.dumps(pages[0].receipt))
        validate_ingestion_page(definition, page)
        matches = [item for item in page.objects if item.object_key == binding.object_key
            and item.document_id == binding.document_id and item.knowledge_version_id == materialization.version_id
            and item.outcome == "draft_created"]
        if (state.status != "completed" or page.input_cursor != definition.initial_cursor
            or page.output_cursor != state.cursor or page.generation != state.generation
            or page.expected_revision + 1 != state.revision
            or pages[0].page_key != ingestion_page_key(page)
            or pages[0].result_digest != ingestion_page_result_digest(page) or len(matches) != 1
            or materialization.draft_key != ingestion_draft_key(definition.scope, matches[0])):
            raise ValueError("receipt binding mismatch")
        observation = matches[0]
    except (ValueError, TypeError):
        return "integrity_failed"

    document = await session.scalar(select(KnowledgeDocument).where(
        KnowledgeDocument.id == binding.document_id, KnowledgeDocument.tenant_id == job.tenant_id)
        .execution_options(populate_existing=True).with_for_update())
    if document is None or document.source_id != job.source_id or document.status == KnowledgeDocumentStatus.ARCHIVED:
        return "target_unavailable"
    candidate = await session.scalar(select(KnowledgeVersion).options(load_only(
        KnowledgeVersion.id, KnowledgeVersion.version_number, KnowledgeVersion.title,
        KnowledgeVersion.content_digest, KnowledgeVersion.status, KnowledgeVersion.review_decision,
        KnowledgeVersion.created_by, KnowledgeVersion.published_at,
        KnowledgeVersion.review_revision, KnowledgeVersion.effective_from, KnowledgeVersion.effective_until))
        .where(KnowledgeVersion.id == materialization.version_id, KnowledgeVersion.document_id == document.id,
            KnowledgeVersion.tenant_id == job.tenant_id).execution_options(populate_existing=True))
    latest = (await session.execute(select(KnowledgeVersion.id, KnowledgeVersion.deleted_at).where(
        KnowledgeVersion.document_id == document.id, KnowledgeVersion.tenant_id == job.tenant_id)
        .order_by(KnowledgeVersion.version_number.desc()).limit(1).execution_options(include_deleted=True))).one_or_none()
    if candidate is None or latest is None or latest.deleted_at is not None:
        return "target_unavailable"
    if candidate.content_digest != observation.parsed_content_digest or candidate.created_by != job.requested_by:
        return "integrity_failed"
    if candidate.status == KnowledgeVersionStatus.RETIRED and candidate.published_at is None:
        # 仅真实撤回审计与当前版本修订匹配才显示该终态，不从旧退役状态补造处置事实。
        withdrawals = (await session.scalars(select(AgentAuditEvent.metadata_payload).where(
            AgentAuditEvent.tenant_id == job.tenant_id, AgentAuditEvent.actor_user_id == job.requested_by,
            AgentAuditEvent.resource_type == AuditResourceType.KNOWLEDGE_VERSION,
            AgentAuditEvent.action == AuditAction.KNOWLEDGE_RETIRED, AgentAuditEvent.version_id == candidate.id,
            AgentAuditEvent.metadata_payload["operation"].as_string() == "ingestion_candidate_withdrawn",
            AgentAuditEvent.metadata_payload["job_id"].as_string() == str(job.id),
            AgentAuditEvent.metadata_payload["materialization_id"].as_string() == str(materialization.id))
            .order_by(AgentAuditEvent.id.desc()).limit(2))).all()
        if withdrawals:
            if (len(withdrawals) != 1 or not isinstance(withdrawals[0], dict)
                or type(withdrawals[0].get("review_revision")) is not int
                or withdrawals[0]["review_revision"] != candidate.review_revision):
                return "integrity_failed"
            return "candidate_withdrawn"
    if latest.id != candidate.id or candidate.status in {KnowledgeVersionStatus.SUPERSEDED, KnowledgeVersionStatus.RETIRED}:
        return "newer_candidate"
    if (candidate.status not in {KnowledgeVersionStatus.DRAFT, KnowledgeVersionStatus.IN_REVIEW, KnowledgeVersionStatus.PUBLISHED}
        or candidate.review_decision not in {None, "pending", "approved", "rejected"}):
        return "integrity_failed"
    return IngestionReviewTarget(job=job, materialization=materialization, document=document,
        candidate=candidate, observation=observation)


async def read_ingestion_comparison(session: AsyncSession, *, identity: IdentityContext,
    job_id: UUID, materialization_id: UUID,
) -> IngestionComparisonView:
    def hidden(status: Literal["content_unavailable", "target_unavailable", "newer_candidate",
        "candidate_withdrawn", "scope_changed", "integrity_failed", "too_large"]) -> IngestionComparisonView:
        return IngestionComparisonView(job_id=job_id, materialization_id=materialization_id, status=status)

    target = await load_ingestion_review_target(session, identity=identity, job_id=job_id,
        materialization_id=materialization_id)
    if isinstance(target, str):
        return hidden(target)
    job, document, candidate, observation = target.job, target.document, target.candidate, target.observation
    # 在文档锁下复用当前ACL/发布有效期检查；管理员与本人Job都不授予正文权限。
    current = await _current_admin(session, identity)
    if not await knowledge_access_allowed(session, document_id=document.id, tenant_id=job.tenant_id, identity=current):
        return hidden("content_unavailable")
    metadata_digest = _digest({"title": candidate.title, "owner": str(document.owner_user_id),
        "target_acl_digest": await _acl_digest(session, document_id=document.id, tenant_id=job.tenant_id),
        "effective_policy": "first_import_no_expiry"})
    if metadata_digest != observation.document_metadata_digest:
        return hidden("scope_changed")
    sizes = (await session.scalars(select(func.length(KnowledgeVersion.content_markdown)).where(
        KnowledgeVersion.id.in_({candidate.id, document.current_version_id}),
        KnowledgeVersion.document_id == document.id, KnowledgeVersion.tenant_id == job.tenant_id))).all()
    if any(size is None or size < 1 for size in sizes):
        return hidden("integrity_failed")
    if sum(sizes) > 65536:
        return hidden("too_large")
    # 只有通过当前授权后才查询正文；单个版本仍受知识正文既有1MiB限制。
    versions = (await session.scalars(select(KnowledgeVersion).where(
        KnowledgeVersion.id.in_({candidate.id, document.current_version_id}),
        KnowledgeVersion.document_id == document.id, KnowledgeVersion.tenant_id == job.tenant_id)
        .execution_options(populate_existing=True))).all()
    by_id = {version.id: version for version in versions}
    incoming, baseline = by_id.get(candidate.id), by_id.get(document.current_version_id)
    if incoming is None or baseline is None or baseline.status != KnowledgeVersionStatus.PUBLISHED:
        return hidden("target_unavailable")
    try:
        if (incoming.content_digest != observation.parsed_content_digest
            or any(knowledge_content_digest(version.content_markdown) != version.content_digest for version in versions)):
            return hidden("integrity_failed")
        change = _text_change(baseline.content_markdown, incoming.content_markdown,
            title_changed=baseline.title != incoming.title)
    except (ValueError, UnicodeError):
        return hidden("integrity_failed")
    if change is None:
        return hidden("too_large")
    # 序列化前再次检查自然到期与会话；文档锁阻止并发生命周期/ACL变化。
    current = await _current_admin(session, identity)
    if not await knowledge_access_allowed(session, document_id=document.id, tenant_id=job.tenant_id, identity=current):
        return hidden("content_unavailable")

    def summary(version: KnowledgeVersion) -> IngestionComparedVersion:
        return IngestionComparedVersion(id=version.id, version_number=version.version_number,
            title=version.title, content_digest=version.content_digest)

    if incoming.status not in {KnowledgeVersionStatus.DRAFT, KnowledgeVersionStatus.IN_REVIEW, KnowledgeVersionStatus.PUBLISHED}:
        return hidden("integrity_failed")
    if incoming.review_decision not in {None, "pending", "approved", "rejected"}:
        return hidden("integrity_failed")
    try:
        return IngestionComparisonView(job_id=job.id, materialization_id=target.materialization.id, status="available",
            document_id=document.id, owner_user_id=document.owner_user_id, review_due_at=_utc(document.review_due_at),
            candidate_status=incoming.status.value, review_decision=incoming.review_decision,
            candidate=summary(incoming), baseline=summary(baseline), change=change)
    except ValueError:
        return hidden("integrity_failed")
