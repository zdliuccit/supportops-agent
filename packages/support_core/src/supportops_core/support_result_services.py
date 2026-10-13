"""产品答案保存可信证据；读取投影隐藏当前不可读的引用。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import AgentConfigV2, StrictModel, SupportAnswer
from supportops_core.auth import IdentityContext
from supportops_core.diagnostic_mock_services import MOCK_DIAGNOSTIC_TOOLS
from supportops_core.diagnostic_contracts import DiagnosticReport
from supportops_core.diagnostic_workflow import answer_from_report, authorize_report_sources
from supportops_core.context_services import ContextSnapshot
from supportops_core.knowledge_retrieval import (
    KnowledgeEvidenceError, lock_evidence_documents, read_knowledge_evidence,
)
from supportops_core.run_attempt_services import RunAttemptIdentity, RuntimeAuthorizationDenied, current_run_identity
from supportops_core.tool_policy import ToolPolicyError
from supportops_core.models import AgentRun, AgentVersion, Conversation, KnowledgeEvidence, RunAttempt, ToolInvocation
from supportops_core.models import BusinessAction, RunEvent
from supportops_core.action_proposal_services import get_action_for_user
from supportops_core.action_contracts import ActionStatus
from supportops_core.services import ResourceNotFoundError


class ResultCitation(StrictModel):
    evidence_id: UUID
    status: Literal["available", "unavailable"] = "available"
    snapshot_id: UUID | None = None
    version_id: UUID | None = None
    title: str | None = None
    section_id: str | None = None
    section_title: str | None = None
    content_digest: str | None = None
    retrieved_at: datetime | None = None


class SuggestedAction(StrictModel):
    description: str = Field(min_length=1, max_length=5000)
    status: Literal["proposed"] = "proposed"


class ResultEvidenceConflict(StrictModel):
    evidence_ids: list[UUID]
    status: Literal["available", "unavailable"] = "available"
    reason: str | None = None


class ResultActionReference(StrictModel):
    action_id: UUID
    status: ActionStatus | None = None
    availability: Literal["available", "unavailable"] = "available"
    data_environment: Literal["mock"] = "mock"
    risk_level: str | None = None


class SupportResult(StrictModel):
    schema_version: Literal["1"] = "1"
    run_id: UUID
    attempt_id: UUID
    answer: str
    summary: str | None = None
    citations: list[ResultCitation] = Field(default_factory=list)
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
    escalation_status: Literal["recommended", "not_requested"] = "not_requested"
    evidence_conflicts: list[ResultEvidenceConflict] = Field(default_factory=list)
    diagnostic_data_environment: Literal["mock"] | None = None
    diagnostic: DiagnosticReport | None = None
    business_actions: list[ResultActionReference] = Field(default_factory=list, max_length=20)


async def build_support_result(
    session: AsyncSession, *, answer: SupportAnswer, identity: IdentityContext, attempt: RunAttemptIdentity,
) -> SupportResult:
    attempt_row = await session.scalar(select(RunAttempt).where(RunAttempt.id == attempt.attempt_id,
        RunAttempt.tenant_id == attempt.tenant_id, RunAttempt.run_id == attempt.run_id))
    context = ContextSnapshot.model_validate(attempt_row.context_snapshot) if attempt_row is not None and attempt_row.context_snapshot else None
    if context is not None and context.diagnostic is not None:
        report = context.diagnostic
        if report.finished_at is None or report.status == "running":
            raise KnowledgeEvidenceError("诊断工作流尚未完成，不能提交产品答案")
        run = await session.get(AgentRun, attempt.run_id)
        version = await session.get(AgentVersion, run.agent_version_id) if run is not None else None
        if run is None or version is None or version.tenant_id != attempt.tenant_id:
            raise KnowledgeEvidenceError("诊断固定版本不存在")
        await authorize_report_sources(session, report=report, run=run, identity=identity,
            config=AgentConfigV2.model_validate(version.config), tool_ids=version.resolved_tool_ids,
            implementation_versions=version.resolved_tool_versions)
        canonical = answer_from_report(report)
        await lock_evidence_documents(session, evidence_ids=report.evidence_ids, identity=identity)
        citations = []
        for evidence_id in report.evidence_ids:
            evidence = await read_knowledge_evidence(session, evidence_id=evidence_id, identity=identity,
                run_id=attempt.run_id, attempt_id=attempt.attempt_id)
            if report.status == "confirmed_mock" and not any(
                f"supportops_429_rule_v1:{candidate.code}" in str(evidence["content"]) for candidate in report.candidates
            ):
                raise KnowledgeEvidenceError("诊断规则引用与固定判断不一致")
            citations.append(ResultCitation.model_validate({key: value for key, value in evidence.items() if key != "content"}))
        return SupportResult(run_id=attempt.run_id, attempt_id=attempt.attempt_id,
            answer="【模拟诊断】以下使用隔离模拟业务数据，不能证明真实生产状态。\n\n" + canonical.answer,
            summary=canonical.summary, citations=citations, escalation_status="recommended" if report.escalation else "not_requested",
            diagnostic_data_environment="mock", diagnostic=report)
    result = await _build_support_result(session, answer=answer, identity=identity, attempt=attempt)
    action_ids = (await session.scalars(select(ToolInvocation.business_action_id).where(
        ToolInvocation.tenant_id == attempt.tenant_id, ToolInvocation.run_id == attempt.run_id,
        ToolInvocation.attempt_id == attempt.attempt_id, ToolInvocation.status == "succeeded",
        ToolInvocation.business_action_id.is_not(None)).distinct())).all()
    references = []
    for action_id in action_ids:
        action = await session.scalar(select(BusinessAction).where(BusinessAction.id == action_id,
            BusinessAction.tenant_id == attempt.tenant_id, BusinessAction.proposed_by == identity.user.id))
        if action is not None:
            references.append(ResultActionReference(action_id=action.id, status=action.status, risk_level=action.risk_level))
    if references:
        result = result.model_copy(update={"business_actions": references, "diagnostic_data_environment": "mock",
            "answer": "已记录隔离模拟建单提案；提案或排队状态不表示工单已经创建，实际状态以动作详情和独立复核回执为准。",
            "summary": "建单提案已记录，执行结果需要独立复核。"})
    used_mock = bool(await session.scalar(select(exists().where(
        ToolInvocation.tenant_id == attempt.tenant_id, ToolInvocation.run_id == attempt.run_id,
        ToolInvocation.attempt_id == attempt.attempt_id, ToolInvocation.tool_id.in_(MOCK_DIAGNOSTIC_TOOLS),
        ToolInvocation.status.in_(("succeeded", "failed")),
    ))))
    if used_mock:
        result = result.model_copy(update={"diagnostic_data_environment": "mock",
            "answer": "【模拟诊断】以下使用隔离模拟业务数据，不能证明真实生产状态。\n\n" + result.answer})
    return result


async def _build_support_result(
    session: AsyncSession, *, answer: SupportAnswer, identity: IdentityContext, attempt: RunAttemptIdentity,
) -> SupportResult:
    """与助手消息同事务校验证据。自造 URL/旧 attempt 引用一律拒绝提交。"""
    if any(c.evidence_id is None or c.url is not None for c in answer.citations):
        raise KnowledgeEvidenceError("引用必须使用本次检索的 evidence ID，不能提供外部 URL")
    evidence_ids = list(dict.fromkeys(c.evidence_id for c in answer.citations if c.evidence_id is not None))
    for conflict in answer.evidence_conflicts:
        if len(set(conflict.evidence_ids)) < 2:
            raise KnowledgeEvidenceError("冲突必须关联两份不同的本次证据")
        evidence_ids = list(dict.fromkeys([*evidence_ids, *conflict.evidence_ids]))
    searched = bool(await session.scalar(select(exists().where(
        ToolInvocation.tenant_id == attempt.tenant_id, ToolInvocation.attempt_id == attempt.attempt_id,
        ToolInvocation.run_id == attempt.run_id, ToolInvocation.tool_id == "search_knowledge", ToolInvocation.status.in_(("succeeded", "failed")),
    ))))
    found_evidence = bool(await session.scalar(select(exists(select(KnowledgeEvidence.id).join(ToolInvocation,
        ToolInvocation.id == KnowledgeEvidence.tool_invocation_id).where(
        KnowledgeEvidence.tenant_id == attempt.tenant_id, KnowledgeEvidence.attempt_id == attempt.attempt_id,
        KnowledgeEvidence.run_id == attempt.run_id,
        ToolInvocation.status == "succeeded", ToolInvocation.tenant_id == attempt.tenant_id,
    )))))
    if searched and not found_evidence:
        # 服务端明确降级，不依赖模型是否遵循“证据不足请追问”的 Prompt。
        return SupportResult(run_id=attempt.run_id, attempt_id=attempt.attempt_id,
            answer="目前未取得可用的获准知识证据，尚不能确认该问题的原因。请补充产品名称、错误码和发生时间，或转人工处理。",
            summary="知识证据不足，结论未确认。", escalation_status="recommended")
    if searched and found_evidence and not evidence_ids:
        raise KnowledgeEvidenceError("检索过知识的回答必须引用本次获准证据")
    await lock_evidence_documents(session, evidence_ids=evidence_ids, identity=identity)
    citations = []
    for evidence_id in evidence_ids:
        evidence = await read_knowledge_evidence(session, evidence_id=evidence_id, identity=identity,
                                                run_id=attempt.run_id, attempt_id=attempt.attempt_id)
        citations.append(ResultCitation.model_validate({key: value for key, value in evidence.items() if key != "content"}))
    if answer.evidence_conflicts:
        conflicts = [ResultEvidenceConflict(evidence_ids=list(dict.fromkeys(conflict.evidence_ids)), reason=conflict.reason)
                     for conflict in answer.evidence_conflicts]
        return SupportResult(run_id=attempt.run_id, attempt_id=attempt.attempt_id,
            answer="当前检索证据存在不一致，尚不能确认问题原因。请补充请求 ID、发生时间和产品信息，或由人工复核这些来源。",
            summary="知识证据存在冲突，结论未确认。", citations=citations, evidence_conflicts=conflicts, escalation_status="recommended")
    return SupportResult(run_id=attempt.run_id, attempt_id=attempt.attempt_id, answer=answer.answer, summary=answer.summary,
        citations=citations, suggested_actions=[SuggestedAction(description=text) for text in answer.suggested_actions],
        escalation_status="recommended" if answer.escalation_recommended else "not_requested")


async def project_support_result(session: AsyncSession, *, stored: dict[str, object] | None, identity: IdentityContext) -> SupportResult | None:
    """旧消息无结构化结果；撤权时不返回历史引用标题、快照或摘要。"""
    if stored is None:
        return None
    result = SupportResult.model_validate(stored)
    citations = []
    for citation in result.citations:
        try:
            await read_knowledge_evidence(session, evidence_id=citation.evidence_id, identity=identity,
                                         run_id=result.run_id, lock=False)
        except KnowledgeEvidenceError:
            citations.append(ResultCitation(evidence_id=citation.evidence_id, status="unavailable"))
        else:
            citations.append(citation)
    available_ids = {citation.evidence_id for citation in citations if citation.status == "available"}
    conflicts = [conflict if set(conflict.evidence_ids).issubset(available_ids)
                 else ResultEvidenceConflict(evidence_ids=conflict.evidence_ids, status="unavailable")
                 for conflict in result.evidence_conflicts]
    diagnostic = result.diagnostic
    if diagnostic is not None:
        diagnostic = await project_diagnostic_report(session, report=diagnostic, identity=identity,
            evidence_available=set(diagnostic.evidence_ids).issubset(available_ids))
    actions = []
    reference_ids = list(dict.fromkeys(reference.action_id for reference in result.business_actions))
    linked_ids = (await session.scalars(select(RunEvent.data["action_id"].as_string()).where(
        RunEvent.tenant_id == identity.principal.tenant_id, RunEvent.run_id == result.run_id,
        RunEvent.event_type == "action.proposed").distinct().limit(20))).all()
    reference_ids = list(dict.fromkeys([*reference_ids, *(UUID(value) for value in linked_ids if value is not None)]))[:20]
    for action_id in reference_ids:
        try:
            action = await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=result.run_id)
        except (ResourceNotFoundError, RuntimeAuthorizationDenied, ToolPolicyError):
            actions.append(ResultActionReference(action_id=action_id, availability="unavailable"))
        else:
            actions.append(ResultActionReference(action_id=action.id, status=action.status, risk_level=action.risk_level))
    return result.model_copy(update={"citations": citations, "evidence_conflicts": conflicts, "diagnostic": diagnostic, "business_actions": actions})


async def project_diagnostic_report(session: AsyncSession, *, report: DiagnosticReport, identity: IdentityContext,
    evidence_available: bool = True,
) -> DiagnosticReport:
    """当前权限下投影完整报告；来源撤销后隐藏事实、候选和接管包。"""
    run = await session.scalar(select(AgentRun).where(AgentRun.id == report.run_id, AgentRun.tenant_id == identity.principal.tenant_id))
    version = await session.get(AgentVersion, run.agent_version_id) if run is not None else None
    try:
        if run is None or version is None or version.tenant_id != run.tenant_id:
            raise KnowledgeEvidenceError("诊断固定版本不存在")
        conversation = await session.get(Conversation, run.conversation_id)
        if conversation is None or conversation.user_id != identity.user.id:
            raise KnowledgeEvidenceError("诊断不属于当前主体")
        current_identity = await current_run_identity(session, run=run, conversation=conversation)
        await authorize_report_sources(session, report=report, run=run, identity=current_identity,
            config=AgentConfigV2.model_validate(version.config), tool_ids=version.resolved_tool_ids,
            implementation_versions=version.resolved_tool_versions)
        if not evidence_available:
            raise KnowledgeEvidenceError("诊断引用已失效或撤权")
    except (KnowledgeEvidenceError, ToolPolicyError, RuntimeAuthorizationDenied):
        return report.model_copy(update={"status": "unavailable", "request_id": None, "sources": [], "facts": [],
            "candidates": [], "evidence_ids": [], "escalation": None,
            "reason_codes": ["DIAGNOSTIC_SOURCE_UNAVAILABLE"], "unresolved_items": ["诊断来源已失效或无权读取，详情已隐藏。"]})
    return report
