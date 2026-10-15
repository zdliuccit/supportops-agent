"""产品升级包；只保存有界问题和已授权 Mock 事实，读取重查来源/证据。"""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_config import StrictModel
from supportops_core.redaction import redact_sensitive_text
from supportops_core.auth import IdentityContext
from supportops_core.context_services import ContextSnapshot
from supportops_core.diagnostic_contracts import DiagnosticCandidate, DiagnosticFact, DiagnosticSource, DiagnosticStage
from supportops_core.enums import MessageRole
from supportops_core.knowledge_retrieval import KnowledgeEvidenceError, read_knowledge_evidence, read_delegated_knowledge_evidence
from supportops_core.models import AgentRun, BusinessAction, Message, RunAttempt, TicketEscalationPackage, utc_now
from supportops_core.services import ResourceNotFoundError, append_event

BUDGET_ESCALATION_ERRORS = frozenset({"AGENT_CONTEXT_BUDGET_EXCEEDED", "AGENT_MODEL_CALL_BUDGET_EXCEEDED",
    "AGENT_COST_BUDGET_EXCEEDED", "AGENT_RUN_TIME_BUDGET_EXCEEDED"})


class EscalationPackagePayload(StrictModel):
    schema_version: Literal["1"] = "1"
    data_environment: Literal["mock"] = "mock"
    question: str = Field(max_length=512)
    run_id: UUID
    attempt_id: UUID | None
    trace_id: str = Field(max_length=64)
    observed_facts: list[DiagnosticFact] = Field(default_factory=list, max_length=80)
    confirmed_causes: list[DiagnosticCandidate] = Field(default_factory=list, max_length=5)
    unconfirmed_candidates: list[DiagnosticCandidate] = Field(default_factory=list, max_length=5)
    sources: list[DiagnosticSource] = Field(default_factory=list, max_length=4)
    steps: list[DiagnosticStage] = Field(default_factory=list, max_length=10)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=6)
    unresolved_items: list[str] = Field(default_factory=list, max_length=20)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    captured_at: datetime


async def ensure_escalation_package(session: AsyncSession, *, action: BusinessAction, run: AgentRun,
    identity: IdentityContext,
) -> TicketEscalationPackage:
    # 调用方持有 Run 与 Action 锁；来源变化追加快照，不覆盖早期提案或历史事实。
    message = await session.scalar(select(Message).where(Message.id == run.input_message_id, Message.tenant_id == run.tenant_id,
        Message.conversation_id == run.conversation_id, Message.user_id == identity.user.id, Message.role == MessageRole.USER))
    if message is None:
        raise ResourceNotFoundError("升级问题来源不存在")
    question = redact_sensitive_text(message.content)
    payload = EscalationPackagePayload(question=question[:512], run_id=run.id, attempt_id=run.current_attempt_id,
        trace_id=run.correlation_id, captured_at=utc_now(), unresolved_items=["未取得完整诊断证据，需要人工复核。"],
        reason_codes=[run.error_code or "DIAGNOSTIC_CONTEXT_MISSING"])
    attempt = await session.get(RunAttempt, run.current_attempt_id) if run.current_attempt_id is not None else None
    context = ContextSnapshot.model_validate(attempt.context_snapshot) if attempt is not None and attempt.context_snapshot else None
    if context is not None and context.diagnostic is not None:
        from supportops_core.support_result_services import project_diagnostic_report
        report = context.diagnostic
        evidence_available = True
        for evidence_id in report.evidence_ids:
            try:
                await read_knowledge_evidence(session, evidence_id=evidence_id, identity=identity, run_id=run.id, lock=False)
            except KnowledgeEvidenceError:
                evidence_available = False
        report = await project_diagnostic_report(session, report=report, identity=identity, evidence_available=evidence_available)
        payload = payload.model_copy(update={"observed_facts": report.facts,
            "confirmed_causes": [item for item in report.candidates if item.confirmation == "confirmed_mock"],
            "unconfirmed_candidates": [item for item in report.candidates if item.confirmation == "unconfirmed"],
            "sources": report.sources, "steps": report.stages, "evidence_ids": report.evidence_ids,
            "unresolved_items": report.unresolved_items, "reason_codes": report.reason_codes})
    if run.error_code is not None:
        payload = payload.model_copy(update={"reason_codes": list(dict.fromkeys([run.error_code, *payload.reason_codes]))[:20],
            "unresolved_items": ["运行已停止，尚未确认建单；请在新的有效授权上下文中确认后续动作。", *payload.unresolved_items][:20]})
    data = payload.model_dump(mode="json")
    source = {key: value for key, value in data.items() if key != "captured_at"}
    source_digest = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    existing = await session.scalar(select(TicketEscalationPackage).where(TicketEscalationPackage.tenant_id == run.tenant_id,
        TicketEscalationPackage.action_id == action.id, TicketEscalationPackage.run_id == run.id,
        TicketEscalationPackage.source_digest == source_digest))
    if existing is not None:
        return existing
    row = TicketEscalationPackage(tenant_id=run.tenant_id, user_id=identity.user.id, action_id=action.id,
        run_id=run.id, attempt_id=run.current_attempt_id, payload=data, source_digest=source_digest,
        payload_digest=hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    session.add(row)
    await session.flush()
    await append_event(session, run_id=run.id, event_type="escalation.saved",
        data={"action_id": str(action.id), "package_id": str(row.id), "data_environment": "mock"})
    return row


async def project_escalation_package(session: AsyncSession, *, package: TicketEscalationPackage,
    identity: IdentityContext,
) -> EscalationPackagePayload:
    # 复用服务也必须先挡跨租户，不能依赖某个路由已做过 SQL 过滤。
    if package.tenant_id != identity.principal.tenant_id:
        raise ResourceNotFoundError("升级包不存在或当前不可访问")
    from supportops_core.action_proposal_services import get_action_for_user
    from supportops_core.approval_services import live_identity, require_mock_staff
    source_identity = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=package.user_id)
    if package.user_id != identity.user.id:
        for permission in ("ticket_handle", "support_context_read"):
            await require_mock_staff(session, tenant_id=identity.principal.tenant_id,
                owner_user_id=package.user_id, user_id=identity.user.id, permission=permission)
    await get_action_for_user(session, action_id=package.action_id, identity=source_identity, context_run_id=package.run_id)
    payload = EscalationPackagePayload.model_validate(package.payload)
    digest = hashlib.sha256(json.dumps(package.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != package.payload_digest or payload.run_id != package.run_id or payload.attempt_id != package.attempt_id:
        raise ResourceNotFoundError("升级包已失效或不可访问")
    # 来源权限与证据不是快照权限。撤权后不向当前主体复制旧正文或旧事实。
    from supportops_core.support_result_services import project_diagnostic_report
    from supportops_core.diagnostic_contracts import DiagnosticReport
    if payload.attempt_id is not None:
        report = DiagnosticReport(run_id=payload.run_id, attempt_id=payload.attempt_id, sources=payload.sources,
            facts=payload.observed_facts, candidates=[*payload.confirmed_causes, *payload.unconfirmed_candidates], evidence_ids=payload.evidence_ids)
        evidence_available = True
        for evidence_id in payload.evidence_ids:
            try:
                await read_delegated_knowledge_evidence(session, evidence_id=evidence_id, identity=identity,
                    source_identity=source_identity, run_id=package.run_id)
            except KnowledgeEvidenceError:
                evidence_available = False
        report = await project_diagnostic_report(session, report=report, identity=source_identity, evidence_available=evidence_available)
        if report.status == "unavailable":
            return payload.model_copy(update={"question": "原来源已失效或无权读取。", "observed_facts": [], "confirmed_causes": [],
                "unconfirmed_candidates": [], "sources": [], "steps": [], "evidence_ids": [], "reason_codes": ["ESCALATION_SOURCE_UNAVAILABLE"],
                "unresolved_items": ["需重新授权或重新排查，历史事实不能直接复用。"]})
    return payload
