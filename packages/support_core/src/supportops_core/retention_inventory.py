"""本人数据影响的固定元数据目录；关联计数不是可删除集合，不读正文/密文。"""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID
from sqlalchemy import Select, Table, func, or_, select, tuple_
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core import models as m
from supportops_core.enums import KnowledgeAclSubjectType, RunStatus
from supportops_core.retention_contracts import DataCategory, ErasureCategoryImpact, ErasureTableImpact


@dataclass(frozen=True)
class CountSpec:
    table: str
    statement: Select[tuple[int]]
    relation: Literal["subject_owned", "subject_participation", "shared_reference"] = "subject_owned"
    reason_code: str | None = None


def _count(table: Table, tenant_id: UUID, cutoff: datetime, *conditions: ColumnElement[bool],
    time_field: str | None = "created_at", relation: Literal["subject_owned", "subject_participation", "shared_reference"] = "subject_owned",
    reason_code: str | None = None,
) -> CountSpec:
    query = select(func.count()).select_from(table).where(table.c.tenant_id == tenant_id, *conditions)
    if time_field is not None:
        query = query.where(table.c[time_field] <= cutoff)
    return CountSpec(table.name, query.execution_options(include_deleted=True), relation, reason_code)


def inventory_queries(*, tenant_id: UUID, subject_id: UUID, cutoff: datetime) -> dict[DataCategory, list[CountSpec]]:
    conversation_filters = (m.Conversation.tenant_id == tenant_id,
        m.Conversation.user_id == subject_id, m.Conversation.created_at <= cutoff)
    conversations = select(m.Conversation.id).where(*conversation_filters)
    run_filters = (m.AgentRun.tenant_id == tenant_id,
        m.AgentRun.conversation_id.in_(conversations), m.AgentRun.created_at <= cutoff)
    runs = select(m.AgentRun.id).where(*run_filters)
    actions = select(m.BusinessAction.id).where(m.BusinessAction.tenant_id == tenant_id, m.BusinessAction.proposed_by == subject_id,
        m.BusinessAction.created_at <= cutoff)
    approvals = select(m.ApprovalRequest.id).where(m.ApprovalRequest.tenant_id == tenant_id,
        or_(m.ApprovalRequest.applicant_id == subject_id, m.ApprovalRequest.approver_id == subject_id), m.ApprovalRequest.created_at <= cutoff)
    agent_versions = select(m.Conversation.agent_version_id).where(*conversation_filters)
    model_versions = select(m.AgentVersion.model_endpoint_version_id).where(
        m.AgentVersion.tenant_id == tenant_id, m.AgentVersion.id.in_(agent_versions)).union(
            select(m.AgentRun.model_endpoint_version_id).where(*run_filters))
    credentials = select(m.ModelEndpointVersion.credential_id).where(
        m.ModelEndpointVersion.tenant_id == tenant_id, m.ModelEndpointVersion.id.in_(model_versions))
    # 凭据revision在Run创建时固定，不能把同端点所有历史revision当作本人的固定引用。
    credential_revisions = select(m.ModelEndpointVersion.credential_id, m.AgentRun.credential_revision).select_from(
        m.AgentRun).join(m.ModelEndpointVersion, m.ModelEndpointVersion.id == m.AgentRun.model_endpoint_version_id).where(
            *run_filters, m.ModelEndpointVersion.tenant_id == tenant_id)
    evidence_filters = (
        m.KnowledgeEvidence.tenant_id == tenant_id, m.KnowledgeEvidence.user_id == subject_id,
        m.KnowledgeEvidence.run_id.in_(runs), m.KnowledgeEvidence.retrieved_at <= cutoff)
    evidence_snapshots = select(m.KnowledgeEvidence.snapshot_id).where(*evidence_filters)
    evidence_chunks = select(m.KnowledgeEvidence.chunk_id).where(*evidence_filters)
    ingestion_jobs = select(m.KnowledgeIngestionJob.id).where(
        m.KnowledgeIngestionJob.tenant_id == tenant_id, m.KnowledgeIngestionJob.requested_by == subject_id,
        m.KnowledgeIngestionJob.created_at <= cutoff)
    ingestion_documents = select(m.KnowledgeDocument.id).where(m.KnowledgeDocument.tenant_id == tenant_id,
        or_(m.KnowledgeDocument.owner_user_id == subject_id, m.KnowledgeDocument.created_by == subject_id))
    def c(table: Table, *filters: ColumnElement[bool], time_field: str | None = "created_at",
        relation: Literal["subject_owned", "subject_participation", "shared_reference"] = "subject_owned",
        reason_code: str | None = None,
    ) -> CountSpec:
        return _count(table, tenant_id, cutoff, *filters, time_field=time_field, relation=relation,
            reason_code=reason_code)
    return {
        "identity": [c(m.User.__table__, m.User.id == subject_id)],
        "auth_sessions": [c(m.AuthSession.__table__, m.AuthSession.user_id == subject_id)],
        "conversations": [c(m.Conversation.__table__, m.Conversation.user_id == subject_id)],
        "messages": [c(m.Message.__table__, m.Message.user_id == subject_id)],
        "runs": [c(m.AgentRun.__table__, m.AgentRun.id.in_(runs)),
            c(m.RunAttempt.__table__, m.RunAttempt.run_id.in_(runs), time_field="started_at"),
            c(m.ModelCallLedger.__table__, m.ModelCallLedger.run_id.in_(runs)),
            c(m.ToolInvocation.__table__, m.ToolInvocation.run_id.in_(runs)),
            c(m.RunNotificationOutbox.__table__, m.RunNotificationOutbox.run_id.in_(runs))],
        "events": [c(m.RunEvent.__table__, m.RunEvent.run_id.in_(runs)),
            c(m.AgentErrorEvent.__table__, m.AgentErrorEvent.user_id == subject_id, time_field="occurred_at")],
        "observations": [c(m.AgentRunObservation.__table__, m.AgentRunObservation.run_id.in_(runs), time_field="started_at")],
        "knowledge": [c(m.KnowledgeEvidence.__table__, m.KnowledgeEvidence.user_id == subject_id,
                m.KnowledgeEvidence.run_id.in_(runs), time_field="retrieved_at"),
            c(m.KnowledgeSnapshot.__table__, m.KnowledgeSnapshot.id.in_(evidence_snapshots),
                relation="shared_reference", reason_code="FIXED_EVIDENCE_SNAPSHOT_REFERENCE"),
            # 分块无created_at，以已固定截止时间内的本人evidence限定，绝不猜测分块时间。
            c(m.KnowledgeChunk.__table__, m.KnowledgeChunk.id.in_(evidence_chunks), time_field=None,
                relation="shared_reference", reason_code="FIXED_EVIDENCE_CHUNK_REFERENCE"),
            c(m.KnowledgeDocument.__table__, or_(m.KnowledgeDocument.owner_user_id == subject_id, m.KnowledgeDocument.created_by == subject_id), relation="shared_reference"),
            c(m.KnowledgeVersion.__table__, m.KnowledgeVersion.created_by == subject_id, relation="shared_reference"),
            c(m.KnowledgeIngestionJob.__table__, m.KnowledgeIngestionJob.id.in_(ingestion_jobs)),
            c(m.KnowledgeIngestionExecutionRequest.__table__, m.KnowledgeIngestionExecutionRequest.requested_by == subject_id,
                m.KnowledgeIngestionExecutionRequest.job_id.in_(ingestion_jobs)),
            c(m.KnowledgeIngestionCreationStop.__table__, m.KnowledgeIngestionCreationStop.requested_by == subject_id),
            c(m.KnowledgeIngestionPage.__table__, m.KnowledgeIngestionPage.job_id.in_(ingestion_jobs)),
            c(m.KnowledgeIngestionMaterialization.__table__, m.KnowledgeIngestionMaterialization.job_id.in_(ingestion_jobs), relation="shared_reference"),
            c(m.KnowledgeIngestionObject.__table__, m.KnowledgeIngestionObject.document_id.in_(ingestion_documents), relation="shared_reference"),
            c(m.KnowledgeAclEntry.__table__, m.KnowledgeAclEntry.subject_type == KnowledgeAclSubjectType.USER,
                m.KnowledgeAclEntry.subject_id == str(subject_id), relation="shared_reference")],
        "credentials": [c(m.ModelCredential.__table__, m.ModelCredential.id.in_(credentials),
                relation="shared_reference", reason_code="FIXED_MODEL_CREDENTIAL_REFERENCE"),
            c(m.ModelCredentialRevision.__table__,
                tuple_(m.ModelCredentialRevision.credential_id, m.ModelCredentialRevision.revision).in_(credential_revisions),
                relation="shared_reference", reason_code="FIXED_RUN_CREDENTIAL_REVISION_REFERENCE"),
            c(m.AgentVersion.__table__, m.AgentVersion.id.in_(agent_versions),
                relation="shared_reference", reason_code="FIXED_CONVERSATION_AGENT_REFERENCE"),
            c(m.ModelEndpointVersion.__table__, m.ModelEndpointVersion.id.in_(model_versions),
                relation="shared_reference", reason_code="FIXED_MODEL_VERSION_REFERENCE")],
        "actions": [c(m.BusinessAction.__table__, m.BusinessAction.id.in_(actions)),
            c(m.ActionCompensationProposal.__table__, m.ActionCompensationProposal.subject_user_id == subject_id),
            c(m.ActionAttempt.__table__, m.ActionAttempt.action_id.in_(actions), time_field="started_at"),
            c(m.ActionReceipt.__table__, m.ActionReceipt.action_id.in_(actions), time_field="observed_at"),
            c(m.TicketEscalationPackage.__table__, m.TicketEscalationPackage.user_id == subject_id),
            c(m.MockSupportTicket.__table__, m.MockSupportTicket.owner_user_id == subject_id),
            c(m.MockRemoteOperation.__table__, m.MockRemoteOperation.owner_user_id == subject_id),
            c(m.MockSupportCase.__table__, or_(m.MockSupportCase.owner_user_id == subject_id, m.MockSupportCase.handler_user_id == subject_id), relation="subject_participation"),
            c(m.MockTicketCommand.__table__, m.MockTicketCommand.actor_user_id == subject_id, relation="subject_participation")],
        "approvals": [c(m.ApprovalRequest.__table__, m.ApprovalRequest.id.in_(approvals), relation="subject_participation"),
            c(m.ApprovalDecision.__table__, m.ApprovalDecision.request_id.in_(approvals), relation="subject_participation"),
            c(m.ApprovalConsumption.__table__, m.ApprovalConsumption.request_id.in_(approvals), time_field="consumed_at", relation="subject_participation"),
            c(m.MockResourceGrant.__table__, or_(m.MockResourceGrant.user_id == subject_id, m.MockResourceGrant.owner_user_id == subject_id), relation="subject_participation")],
        "governance_audit": [c(m.AgentAuditEvent.__table__, m.AgentAuditEvent.actor_user_id == subject_id, relation="subject_participation"),
            c(m.ToolDiscoveryRevision.__table__, m.ToolDiscoveryRevision.actor_user_id == subject_id, relation="subject_participation"),
            c(m.ToolConnectionProbeJob.__table__, m.ToolConnectionProbeJob.requested_by == subject_id, relation="subject_participation"),
            c(m.ToolConnectionEvent.__table__, m.ToolConnectionEvent.actor_user_id == subject_id, relation="subject_participation"),
            c(m.HttpMcpDispatch.__table__, m.HttpMcpDispatch.actor_user_id == subject_id, relation="subject_participation"),
            c(m.ErasureRequest.__table__, m.ErasureRequest.subject_user_id == subject_id),
            c(m.RetentionCleanupEvent.__table__, m.RetentionCleanupEvent.subject_user_id == subject_id,
                time_field="recorded_at", relation="shared_reference", reason_code="RETENTION_CLEANUP_HISTORY_REFERENCE"),
            c(m.RetentionPropagationEvent.__table__, m.RetentionPropagationEvent.subject_user_id == subject_id,
                time_field="recorded_at", relation="shared_reference", reason_code="RETENTION_PROPAGATION_HISTORY_REFERENCE"),
            c(m.RestoreKnowledgeEvent.__table__, m.RestoreKnowledgeEvent.subject_user_id == subject_id,
                time_field="recorded_at", relation="shared_reference", reason_code="RESTORE_KNOWLEDGE_HISTORY_REFERENCE")],
        "checkpoints": [], "memory": [], "backups": [], "external_copies": [],
    }


async def category_impact(session: AsyncSession, *, category: DataCategory, queries: list[CountSpec]) -> ErasureCategoryImpact:
    tables = []
    for item in queries:
        value = await session.scalar(item.statement)
        if value is None:
            raise RuntimeError("数据影响计数缺少聚合结果")
        tables.append(ErasureTableImpact(table=item.table, row_count=int(value), relation=item.relation,
            reason_code=item.reason_code))
    # 只输出已声明的本人/参与记录计数。共享引用和外部副本未完整枚举，绝不据此生成删除清单。
    tables.append(ErasureTableImpact(table="unresolved_dependencies", row_count=None, relation="unresolved",
        reason_code="DEPENDENCY_INVENTORY_REQUIRED" if queries else "CATEGORY_ADAPTER_NOT_IMPLEMENTED"))
    blockers = ["LEGAL_HOLD_REVIEW_REQUIRED", "DEPENDENCY_INVENTORY_REQUIRED"]
    if category in {"knowledge", "credentials", "actions", "approvals", "governance_audit"}:
        blockers.append("SHARED_OR_AUDIT_REFERENCE_REVIEW_REQUIRED")
    if category in {"checkpoints", "memory", "backups", "external_copies"}:
        blockers.append("CATEGORY_ADAPTER_NOT_IMPLEMENTED")
    if any(item.reason_code is not None and item.relation == "shared_reference"
        and item.row_count is not None and item.row_count > 0 for item in tables):
        blockers.append("FIXED_REFERENCES_PRESENT")
    return ErasureCategoryImpact(category=category, known_related_rows=sum(item.row_count for item in tables if item.row_count is not None),
        unknown_table_count=sum(item.row_count is None for item in tables), tables=tables, blockers=blockers)


async def live_reference_counts(session: AsyncSession, *, tenant_id: UUID, subject_id: UUID) -> tuple[int, int]:
    conversations = select(m.Conversation.id).where(m.Conversation.tenant_id == tenant_id, m.Conversation.user_id == subject_id)
    active = await session.scalar(select(func.count()).select_from(m.AgentRun).where(m.AgentRun.tenant_id == tenant_id,
        m.AgentRun.conversation_id.in_(conversations), m.AgentRun.status.not_in((RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)))
        .execution_options(include_deleted=True))
    actions = await session.scalar(select(func.count()).select_from(m.BusinessAction).where(m.BusinessAction.tenant_id == tenant_id,
        m.BusinessAction.proposed_by == subject_id, m.BusinessAction.status.not_in(("succeeded", "failed", "cancelled")))
        .execution_options(include_deleted=True))
    if active is None or actions is None:
        raise RuntimeError("在途引用计数缺少聚合结果")
    return int(active), int(actions)
