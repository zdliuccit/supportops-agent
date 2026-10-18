"""本人导入候选的负责人/复核日期管理；只有控制面元数据，不授予正文或发布权限。"""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.auth import IdentityContext
from supportops_core.enums import AuditAction, AuditResourceType, KnowledgeVersionStatus
from supportops_core.knowledge_ingestion_contracts import Digest, IngestionContract
from supportops_core.knowledge_ingestion_review import IngestionReviewTarget, load_ingestion_review_target
from supportops_core.knowledge_ingestion_services import (
    IngestionConflict, _current_admin, _digest, _utc, set_ingestion_transaction_limits,
)
from supportops_core.knowledge_services import (
    KnowledgeConflictError, _acl_digest, append_knowledge_audit, update_knowledge_document,
    withdraw_knowledge_candidate,
)
from supportops_core.models import User, utc_now


class IngestionGovernanceCommand(BaseModel):
    # HTTP JSON的UUID/时间按标准格式解析，范围摘要和三个必填字段构成完整替换。
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_governance_digest: Digest = Field(description="最后读取的治理范围摘要，变化时拒绝覆盖。")
    owner_user_id: UUID = Field(description="同租户当前有效的文档负责人，不授予知识权限。")
    review_due_at: AwareDatetime | None = Field(description="带时区的复核日期；显式null清除配置。")

    @field_validator("review_due_at", mode="before")
    @classmethod
    def validate_time_input(cls, value: object) -> object:
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("复核日期必须是带时区的ISO时间或null")
        return value

    @field_validator("review_due_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        try:
            return value.astimezone(UTC) if value else None
        except (OverflowError, ValueError):
            raise ValueError("复核日期超出支持范围") from None


IngestionWithdrawalReason = Literal["source_scope_changed", "manual_resolution_required", "no_longer_needed"]


class IngestionWithdrawalCommand(IngestionContract):
    expected_governance_digest: Digest = Field(description="最后读取的当前治理范围摘要。")
    reason_code: IngestionWithdrawalReason = Field(description="本人明确选择的撤回原因，不代表系统已验证来源冲突。")


class IngestionGovernanceView(IngestionContract):
    schema_version: Literal["knowledge_ingestion_governance_v1"] = "knowledge_ingestion_governance_v1"
    job_id: UUID
    materialization_id: UUID
    status: Literal["available", "target_unavailable", "newer_candidate", "integrity_failed", "candidate_withdrawn"]
    observed_at: datetime
    document_id: UUID | None = None
    owner_user_id: UUID | None = None
    owner_display_name: str | None = Field(default=None, max_length=200)
    owner_active: bool | None = None
    review_due_at: datetime | None = None
    review_timing: Literal["unscheduled", "scheduled", "due"] | None = None
    candidate_status: Literal["draft", "in_review", "published"] | None = None
    review_decision: Literal["pending", "approved", "rejected"] | None = None
    governance_digest: Digest | None = None
    update_available: bool = False
    withdraw_available: bool = False
    automatic_publish_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_scope(self) -> "IngestionGovernanceView":
        required = (self.document_id, self.owner_user_id, self.owner_active,
            self.review_timing, self.candidate_status, self.governance_digest)
        if self.status == "available":
            if any(value is None for value in required) or not self.update_available:
                raise ValueError("可用治理视图必须绑定完整当前范围")
            timing = "unscheduled" if self.review_due_at is None else "due" if self.review_due_at <= self.observed_at else "scheduled"
            if self.review_timing != timing:
                raise ValueError("复核日期与服务端到期判断不一致")
        elif (self.update_available or self.withdraw_available or any(value is not None for value in (*required,
            self.owner_display_name, self.review_due_at, self.review_decision))):
            raise ValueError("不可用目标不能返回治理范围或写入条件")
        return self


class IngestionOwnerOption(IngestionContract):
    id: UUID
    display_name: str = Field(max_length=200)


async def list_ingestion_owners(session: AsyncSession, *, identity: IdentityContext, job_id: UUID,
    materialization_id: UUID, keywords: str, page_size: int, offset: int,
) -> tuple[list[IngestionOwnerOption], int]:
    if (type(page_size) is not int or not 1 <= page_size <= 100 or type(offset) is not int or offset < 0
        or not isinstance(keywords, str) or len(keywords) > 200):
        raise IngestionConflict("负责人查询条件或分页无效")
    target = await load_ingestion_review_target(session, identity=identity, job_id=job_id,
        materialization_id=materialization_id)
    if isinstance(target, str):
        raise IngestionConflict("当前候选已不可配置，请重新读取")
    filters = [User.tenant_id == target.job.tenant_id, User.status == "active"]
    if keywords.strip():
        filters.append(User.display_name.icontains(keywords.strip(), autoescape=True))
    total = int(await session.scalar(select(func.count()).select_from(User).where(*filters)) or 0)
    rows = (await session.execute(select(User.id, User.display_name).where(*filters)
        .order_by(User.display_name, User.id).offset(offset).limit(page_size))).all()
    return [IngestionOwnerOption(id=row.id, display_name=row.display_name) for row in rows], total


async def _governance_digest(session: AsyncSession, target: IngestionReviewTarget) -> str:
    document, candidate = target.document, target.candidate
    return _digest(["knowledge_ingestion_governance_v1", str(target.job.tenant_id), str(target.job.requested_by),
        str(target.job.id), target.job.definition_digest, target.job.state_digest,
        str(target.materialization.id), target.materialization.draft_key,
        str(document.id), str(document.source_id), document.status.value,
        _utc(document.updated_at).isoformat(), str(document.owner_user_id),
        _utc(document.review_due_at).isoformat() if document.review_due_at else None,
        str(document.current_version_id) if document.current_version_id else None,
        str(candidate.id), candidate.status.value, candidate.review_revision, candidate.review_decision,
        str(candidate.created_by), _utc(candidate.published_at).isoformat() if candidate.published_at else None,
        candidate.content_digest, candidate.title,
        _utc(candidate.effective_from).isoformat(),
        _utc(candidate.effective_until).isoformat() if candidate.effective_until else None,
        await _acl_digest(session, document_id=document.id, tenant_id=document.tenant_id)])


async def _view(session: AsyncSession, target: IngestionReviewTarget) -> IngestionGovernanceView:
    owner = (await session.execute(select(User.display_name, User.status).where(
        User.tenant_id == target.job.tenant_id, User.id == target.document.owner_user_id))).one_or_none()
    now, due = utc_now(), _utc(target.document.review_due_at)
    return IngestionGovernanceView(job_id=target.job.id, materialization_id=target.materialization.id,
        status="available", observed_at=now, document_id=target.document.id,
        owner_user_id=target.document.owner_user_id, owner_display_name=owner.display_name if owner else None,
        owner_active=owner is not None and owner.status == "active", review_due_at=due,
        review_timing="unscheduled" if due is None else "due" if due <= now else "scheduled",
        candidate_status=target.candidate.status.value, review_decision=target.candidate.review_decision,
        governance_digest=await _governance_digest(session, target), update_available=True,
        withdraw_available=(target.candidate.created_by == target.job.requested_by
            and target.candidate.status in {KnowledgeVersionStatus.DRAFT, KnowledgeVersionStatus.IN_REVIEW}
            and target.candidate.published_at is None and target.document.current_version_id != target.candidate.id))


async def read_ingestion_governance(session: AsyncSession, *, identity: IdentityContext,
    job_id: UUID, materialization_id: UUID,
) -> IngestionGovernanceView:
    target = await load_ingestion_review_target(session, identity=identity, job_id=job_id,
        materialization_id=materialization_id)
    if isinstance(target, str):
        return IngestionGovernanceView(job_id=job_id, materialization_id=materialization_id,
            status=target, observed_at=utc_now())
    await _current_admin(session, identity)
    return await _view(session, target)


async def update_ingestion_governance(session: AsyncSession, *, identity: IdentityContext,
    job_id: UUID, materialization_id: UUID, command: IngestionGovernanceCommand, correlation_id: str,
) -> IngestionGovernanceView:
    # 在当前身份锁/文档锁之前按UUID锁定申请人与目标负责人，防止两个管理员互相分配时反向取锁。
    # API调用方须开启并提交独立业务事务；此处不执行来源、正文或审批操作。
    await set_ingestion_transaction_limits(session)
    owners = (await session.execute(select(User.id, User.status).where(User.tenant_id == identity.principal.tenant_id,
        User.id.in_({identity.principal.user_id, command.owner_user_id})).order_by(User.id)
        .with_for_update())).all()
    target = await load_ingestion_review_target(session, identity=identity, job_id=job_id,
        materialization_id=materialization_id)
    if isinstance(target, str):
        raise IngestionConflict("导入目标已不可编辑，请刷新后复核最新候选")
    previous_digest = await _governance_digest(session, target)
    if previous_digest != command.expected_governance_digest:
        raise IngestionConflict("负责人、复核日期或候选治理范围已变化，请重新读取后确认")
    owner = next((user for user in owners if user.id == command.owner_user_id), None)
    if owner is None or owner.status != "active":
        raise IngestionConflict("负责人必须是同租户当前有效用户，请重新选择")
    due = command.review_due_at.astimezone(UTC) if command.review_due_at else None
    if target.document.owner_user_id == owner.id and _utc(target.document.review_due_at) == due:
        return await _view(session, target)
    await _current_admin(session, identity)
    await update_knowledge_document(session, document_id=target.document.id, tenant_id=target.job.tenant_id,
        actor_user_id=identity.principal.user_id, owner_user_id=owner.id, review_due_at=due,
        correlation_id=correlation_id)
    result = await _view(session, target)
    await append_knowledge_audit(session, tenant_id=target.job.tenant_id, actor_user_id=identity.principal.user_id,
        resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT, action=AuditAction.KNOWLEDGE_UPDATED,
        correlation_id=correlation_id, metadata={"operation": "ingestion_review_configured",
            "document_id": str(target.document.id), "ingestion_job_id": str(target.job.id),
            "materialization_id": str(target.materialization.id), "before_digest": previous_digest,
            "after_digest": result.governance_digest})
    await _current_admin(session, identity)
    return result


async def withdraw_ingestion_candidate(session: AsyncSession, *, identity: IdentityContext,
    job_id: UUID, materialization_id: UUID, command: IngestionWithdrawalCommand, correlation_id: str,
) -> IngestionGovernanceView:
    """调用方提交独立业务事务；状态、失效修订和两类最小审计原子保存。"""
    target = await load_ingestion_review_target(session, identity=identity, job_id=job_id,
        materialization_id=materialization_id)
    if isinstance(target, str):
        raise IngestionConflict("候选已不可撤回，请重新读取当前状态")
    current_digest = await _governance_digest(session, target)
    if current_digest != command.expected_governance_digest:
        raise IngestionConflict("候选、正式版本或治理范围已变化，请刷新后重新确认撤回")
    await _current_admin(session, identity)
    try:
        version = await withdraw_knowledge_candidate(session, version_id=target.candidate.id,
            tenant_id=target.job.tenant_id, actor_user_id=identity.principal.user_id,
            expected_review_revision=target.candidate.review_revision,
            expected_content_digest=target.candidate.content_digest, reason_code=command.reason_code,
            correlation_id=correlation_id)
    except KnowledgeConflictError:
        raise IngestionConflict("候选已发布、已被取代或审核范围已变化，不能撤回") from None
    await append_knowledge_audit(session, tenant_id=target.job.tenant_id, actor_user_id=identity.principal.user_id,
        resource_type=AuditResourceType.KNOWLEDGE_VERSION, action=AuditAction.KNOWLEDGE_RETIRED,
        correlation_id=correlation_id, version_id=version.id,
        metadata={"operation": "ingestion_candidate_withdrawn", "job_id": str(target.job.id),
            "materialization_id": str(target.materialization.id), "reason_code": command.reason_code,
            "review_revision": version.review_revision, "before_governance_digest": current_digest})
    await _current_admin(session, identity)
    result = await read_ingestion_governance(session, identity=identity, job_id=job_id,
        materialization_id=materialization_id)
    if result.status != "candidate_withdrawn":
        raise IngestionConflict("撤回状态与审计绑定无法确认，本次事务不提交")
    return result
