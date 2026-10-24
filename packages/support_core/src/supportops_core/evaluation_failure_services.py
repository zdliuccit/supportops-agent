"""同租户评测失败投影及追加人工诊断，不改写原评分或执行回滚。"""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_services import AgentConflictError, append_agent_audit, get_agent
from supportops_core.auth import IdentityContext
from supportops_core.enums import ROLE_PLATFORM_ADMIN, AuditAction, AuditResourceType
from supportops_core.evaluation_failure_contracts import (
    EvaluationFailure,
    EvaluationFailureDetail,
    EvaluationFailureDocument,
    EvaluationFailureReview,
    EvaluationFailureReviewInput,
)
from supportops_core.models import AgentAuditEvent, AgentReleaseEvaluation, AgentVersion, User, utc_now
from supportops_core.release_gate_services import ReleaseEvaluationReport
from supportops_core.release_manifest_services import compare_release_versions, manifest_digest
from supportops_core.services import ResourceNotFoundError

MAX_REVIEW_EVENTS = 512


async def _evaluation(
    session: AsyncSession, *, version: AgentVersion, evaluation_id: UUID, lock: bool = False,
) -> tuple[AgentReleaseEvaluation, ReleaseEvaluationReport]:
    query = select(AgentReleaseEvaluation).where(
        AgentReleaseEvaluation.id == evaluation_id,
        AgentReleaseEvaluation.tenant_id == version.tenant_id,
        AgentReleaseEvaluation.agent_id == version.agent_id,
        AgentReleaseEvaluation.agent_version_id == version.id,
    )
    if lock:
        query = query.execution_options(populate_existing=True).with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise ResourceNotFoundError("评测记录不存在")
    try:
        report = ReleaseEvaluationReport.model_validate(row.report)
        if (manifest_digest(row.report) != row.report_digest or report.execution_id != row.execution_id
            or report.manifest_digest != row.manifest_digest or report.scope_digest != row.scope_digest
            or report.policy_digest != row.policy_digest):
            raise ValueError("scope mismatch")
    except (ValidationError, TypeError, ValueError):
        raise AgentConflictError("评测报告完整性无法确认") from None
    return row, report


async def _reviews(
    session: AsyncSession, *, evaluation: AgentReleaseEvaluation, report: ReleaseEvaluationReport,
) -> dict[str, EvaluationFailureReview]:
    # 整份报告的追加历史有上限，避免为单个失败加载无界控制面事件。
    rows = (await session.scalars(select(AgentAuditEvent).where(
        AgentAuditEvent.tenant_id == evaluation.tenant_id,
        AgentAuditEvent.agent_id == evaluation.agent_id,
        AgentAuditEvent.version_id == evaluation.agent_version_id,
        AgentAuditEvent.resource_type == AuditResourceType.AGENT,
        AgentAuditEvent.action == AuditAction.RELEASE_FAILURE_REVIEWED,
        AgentAuditEvent.metadata_payload["evaluation_id"].as_string() == str(evaluation.id),
    ).order_by(AgentAuditEvent.id).limit(MAX_REVIEW_EVENTS + 1))).all()
    if len(rows) > MAX_REVIEW_EVENTS:
        raise AgentConflictError("评测失败诊断历史超过上限")
    findings = {finding.case_id: finding for finding in report.case_failures or []}
    latest: dict[str, EvaluationFailureReview] = {}
    try:
        for row in rows:
            review = EvaluationFailureReview.model_validate(row.metadata_payload)
            prior = latest.get(review.case_id)
            finding = findings.get(review.case_id)
            if (finding is None or review.evaluation_id != evaluation.id
                or review.reviewer_user_id != row.actor_user_id
                or review.command.report_digest != evaluation.report_digest
                or review.command.finding_digest != manifest_digest(finding.model_dump(mode="json"))
                or review.revision != (prior.revision + 1 if prior else 1)
                or review.command.expected_revision != review.revision - 1
                or review.previous_digest != (prior.review_digest if prior else None)
                or review.reviewed_at.tzinfo is None
                or review.review_digest != manifest_digest(review.model_dump(mode="json", exclude={"review_digest"}))):
                raise ValueError("review chain mismatch")
            latest[review.case_id] = review
    except (ValidationError, TypeError, ValueError):
        raise AgentConflictError("评测失败诊断历史完整性无法确认") from None
    return latest


async def _comparison_version(
    session: AsyncSession, *, version: AgentVersion, baseline_version_id: UUID | None,
) -> AgentVersion | None:
    if baseline_version_id is None:
        return None
    baseline = await session.scalar(select(AgentVersion).where(
        AgentVersion.id == baseline_version_id, AgentVersion.tenant_id == version.tenant_id,
        AgentVersion.agent_id == version.agent_id,
    ))
    if baseline is None:
        raise ResourceNotFoundError("比较版本不存在")
    return baseline


async def get_evaluation_failures(
    session: AsyncSession, *, version: AgentVersion, evaluation_id: UUID,
    baseline_version_id: UUID | None = None,
) -> EvaluationFailureDocument:
    """固定评测的有界完整报告文档；不读取答案、Prompt、工具正文或凭据。"""
    row, report = await _evaluation(session, version=version, evaluation_id=evaluation_id)
    reviews = await _reviews(session, evaluation=row, report=report)
    baseline = await _comparison_version(session, version=version, baseline_version_id=baseline_version_id)
    return EvaluationFailureDocument(
        evaluation_id=row.id, execution_id=row.execution_id, agent_id=version.agent_id, version_id=version.id,
        report_digest=row.report_digest, recorded_at=row.recorded_at,
        findings_status="legacy_unrecorded" if report.case_failures is None else "recorded",
        failures=[EvaluationFailureDetail(finding=finding,
            finding_digest=manifest_digest(finding.model_dump(mode="json")), review=reviews.get(finding.case_id))
            for finding in report.case_failures or []],
        version_comparison=compare_release_versions(version, baseline),
    )


async def record_evaluation_failure_review(
    session: AsyncSession, *, version: AgentVersion, evaluation_id: UUID, case_id: str,
    identity: IdentityContext, command: EvaluationFailureReviewInput, correlation_id: str,
) -> EvaluationFailureReview:
    """由当前管理员在事务内追加人工判断；同内容重放返回原审计事实。"""
    from supportops_core.agent_services import require_platform_admin

    require_platform_admin(identity)
    if identity.principal.tenant_id != version.tenant_id:
        raise ResourceNotFoundError("评测记录不存在")
    await get_agent(session, agent_id=version.agent_id, tenant_id=version.tenant_id, lock=True)
    evaluation, report = await _evaluation(session, version=version, evaluation_id=evaluation_id, lock=True)
    finding: EvaluationFailure | None = next(
        (item for item in report.case_failures or [] if item.case_id == case_id), None)
    if finding is None:
        raise ResourceNotFoundError("评测失败记录不存在")
    if (command.report_digest != evaluation.report_digest
        or command.finding_digest != manifest_digest(finding.model_dump(mode="json"))):
        raise AgentConflictError("评测报告或失败观察已变化，请重新读取")
    # 在共同 Agent 锁后复查真实当前人员；管理员不能把其他租户人员写作负责人。
    personnel = (await session.execute(select(User.id, User.roles).where(
        User.tenant_id == version.tenant_id,
        User.id.in_({identity.principal.user_id, command.owner_user_id}),
        User.status == "active",
    ))).all()
    by_id = {user.id: user for user in personnel}
    reviewer = by_id.get(identity.principal.user_id)
    if (reviewer is None or ROLE_PLATFORM_ADMIN not in reviewer.roles or command.owner_user_id not in by_id):
        raise AgentConflictError("当前管理员或负责人不可用")
    reviews = await _reviews(session, evaluation=evaluation, report=report)
    prior = reviews.get(case_id)
    if (prior is not None and prior.command == command
        and prior.reviewer_user_id == identity.principal.user_id):
        return prior
    if command.expected_revision != (prior.revision if prior else 0):
        raise AgentConflictError("诊断记录已更新，请重新读取")
    if command.rollback_version_id is not None:
        if command.rollback_version_id == version.id:
            raise AgentConflictError("回滚目标须为另一个固定版本")
        await _comparison_version(session, version=version, baseline_version_id=command.rollback_version_id)
    # 总数限制与本次追加共用 Agent/评测锁，不根据每个 case 的数量低估上限。
    event_count = await session.scalar(select(func.count()).select_from(AgentAuditEvent).where(
        AgentAuditEvent.tenant_id == version.tenant_id, AgentAuditEvent.agent_id == version.agent_id,
        AgentAuditEvent.version_id == version.id,
        AgentAuditEvent.action == AuditAction.RELEASE_FAILURE_REVIEWED,
        AgentAuditEvent.metadata_payload["evaluation_id"].as_string() == str(evaluation.id)))
    if int(event_count or 0) >= MAX_REVIEW_EVENTS:
        raise AgentConflictError("评测失败诊断历史已达到上限")
    payload = {
        "schema_version": "evaluation_failure_review_v1", "evaluation_id": str(evaluation.id),
        "case_id": case_id, "revision": command.expected_revision + 1,
        "previous_digest": prior.review_digest if prior else None,
        "command": command.model_dump(mode="json"), "reviewer_user_id": str(identity.principal.user_id),
        "reviewed_at": utc_now().isoformat(),
    }
    review = EvaluationFailureReview.model_validate({**payload, "review_digest": "0" * 64})
    review = review.model_copy(update={"review_digest": manifest_digest(
        review.model_dump(mode="json", exclude={"review_digest"}))})
    await append_agent_audit(session, tenant_id=version.tenant_id, agent_id=version.agent_id,
        version_id=version.id, actor_user_id=identity.principal.user_id,
        action=AuditAction.RELEASE_FAILURE_REVIEWED, correlation_id=correlation_id,
        metadata=review.model_dump(mode="json"))
    return review
