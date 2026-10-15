"""从持久化业务事实投影有界阶段 Trace；不复制正文、不额外计费。"""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from pydantic import Field
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_config import StrictModel
from supportops_core.diagnostic_contracts import DiagnosticStage
from supportops_core.models import AgentRun, RunAttempt, RunEvent, ApprovalRequest, ApprovalDecision, ApprovalConsumption, ActionAttempt, ActionReceipt


class BusinessTracePhase(StrictModel):
    span_id: str
    parent_span_id: str
    kind: Literal["execution", "diagnostic", "approval_wait", "action_wait", "recovery_wait", "action", "verification", "reconciliation", "receipt"]
    name: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    run_attempt_id: UUID | None = None
    action_id: UUID | None = None
    action_attempt_id: UUID | None = None
    approval_id: UUID | None = None
    receipt_id: UUID | None = None
    source: Literal["durable_business_facts_v1"] = "durable_business_facts_v1"


class BusinessTrace(StrictModel):
    convention_version: Literal["business_phases_v1"] = "business_phases_v1"
    root_span_id: str
    observed_at: datetime
    phases: list[BusinessTracePhase] = Field(default_factory=list)
    truncated: bool = False
    source_limit: Literal[100] = 100


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _phase(*, started_at: datetime, finished_at: datetime | None = None, **values) -> BusinessTracePhase:
    elapsed = int((_aware(finished_at)-_aware(started_at)).total_seconds()*1000) if finished_at is not None else None
    if elapsed is not None and elapsed < 0:
        elapsed = None
    return BusinessTracePhase(started_at=started_at, finished_at=finished_at, duration_ms=elapsed, **values)


async def project_business_trace(session: AsyncSession, *, run: AgentRun) -> BusinessTrace:
    """聚合详情，每类事实最多 100 条并明确截断，不是普通扁平列表。"""
    root = f"run:{run.id}"
    result = BusinessTrace(observed_at=datetime.now(UTC), root_span_id=root)
    attempts = (await session.execute(select(RunAttempt.id, RunAttempt.generation, RunAttempt.status,
        RunAttempt.started_at, RunAttempt.finished_at,
        RunAttempt.context_snapshot["diagnostic"]["stages"].label("diagnostic_stages"))
        .where(RunAttempt.tenant_id == run.tenant_id, RunAttempt.run_id == run.id)
        .order_by(RunAttempt.generation, RunAttempt.id).limit(101))).all()
    result.truncated = len(attempts) > 100
    previous = None
    for attempt in attempts[:100]:
        result.phases.append(_phase(span_id=f"agent:{attempt.id}", parent_span_id=root, kind="execution",
            name="run.attempt.execute", status=attempt.status, run_attempt_id=attempt.id,
            started_at=attempt.started_at, finished_at=attempt.finished_at))
        if previous is not None and previous.finished_at is not None:
            result.phases.append(_phase(span_id=f"recovery:{attempt.id}", parent_span_id=root, kind="recovery_wait",
                name="run.recovery.wait", status="completed", run_attempt_id=attempt.id,
                started_at=previous.finished_at, finished_at=attempt.started_at))
        for data in (attempt.diagnostic_stages or [])[:10]:
            stage = DiagnosticStage.model_validate(data)
            result.phases.append(_phase(span_id=f"diagnostic:{attempt.id}:{stage.name}", parent_span_id=f"agent:{attempt.id}",
                kind="diagnostic", name=f"diagnostic.{stage.name}", status=stage.status, run_attempt_id=attempt.id,
                started_at=stage.started_at, finished_at=stage.finished_at))
        previous = attempt
    approvals = (await session.execute(select(ApprovalRequest.id, ApprovalRequest.action_id, ApprovalRequest.status,
        ApprovalRequest.created_at, ApprovalRequest.finished_at, ApprovalDecision.created_at.label("decision_at"),
        ApprovalDecision.decision, ApprovalConsumption.consumed_at)
        .outerjoin(ApprovalDecision, (ApprovalDecision.request_id == ApprovalRequest.id) & (ApprovalDecision.tenant_id == run.tenant_id))
        .outerjoin(ApprovalConsumption, (ApprovalConsumption.request_id == ApprovalRequest.id) & (ApprovalConsumption.tenant_id == run.tenant_id))
        .where(ApprovalRequest.tenant_id == run.tenant_id, ApprovalRequest.run_id == run.id)
        .order_by(ApprovalRequest.created_at, ApprovalRequest.id).limit(101))).all()
    result.truncated |= len(approvals) > 100
    for approval in approvals[:100]:
        result.phases.append(_phase(span_id=f"approval:{approval.id}", parent_span_id=root, kind="approval_wait",
            name="approval.wait", status=approval.status, approval_id=approval.id, action_id=approval.action_id,
            started_at=approval.created_at, finished_at=approval.decision_at or approval.finished_at))
        if approval.decision == "approved" and approval.decision_at is not None:
            result.phases.append(_phase(span_id=f"approval_dispatch:{approval.id}", parent_span_id=root, kind="action_wait",
                name="action.wait_after_approval", status=approval.status, approval_id=approval.id, action_id=approval.action_id,
                started_at=approval.decision_at,
                finished_at=approval.consumed_at or (approval.finished_at if approval.status in {"expired", "invalidated", "cancelled"} else None)))
    # ActionAttempt 没有历史 Run FK；只按本 Run 追加的 started 事件绑定，不以可变 execution_run_id 猜测归属。
    events = (await session.scalars(select(RunEvent).where(RunEvent.tenant_id == run.tenant_id,
        RunEvent.run_id == run.id, RunEvent.event_type == "action.started")
        .order_by(RunEvent.sequence).limit(101))).all()
    result.truncated |= len(events) > 100
    pairs = [(UUID(event.data["action_id"]), event.data["generation"]) for event in events[:100]]
    action_attempts = list((await session.scalars(select(ActionAttempt).where(ActionAttempt.tenant_id == run.tenant_id,
        tuple_(ActionAttempt.action_id, ActionAttempt.generation).in_(pairs))
        .order_by(ActionAttempt.started_at, ActionAttempt.id))).all()) if pairs else []
    for attempt in action_attempts:
        kind = {"execute": "action", "verify": "verification", "reconcile": "reconciliation"}[attempt.kind]
        result.phases.append(_phase(span_id=f"action_attempt:{attempt.id}", parent_span_id=root, kind=kind,
            name=f"action.{attempt.kind}", status=attempt.status, action_id=attempt.action_id, action_attempt_id=attempt.id,
            started_at=attempt.started_at, finished_at=attempt.finished_at))
    receipts = list((await session.scalars(select(ActionReceipt).where(ActionReceipt.tenant_id == run.tenant_id,
        ActionReceipt.attempt_id.in_([attempt.id for attempt in action_attempts]))
        .order_by(ActionReceipt.observed_at, ActionReceipt.id).limit(101))).all()) if action_attempts else []
    result.truncated |= len(receipts) > 100
    for receipt in receipts[:100]:
        result.phases.append(_phase(span_id=f"receipt:{receipt.id}", parent_span_id=f"action_attempt:{receipt.attempt_id}",
            kind="receipt", name="action.receipt", status=receipt.status, action_id=receipt.action_id,
            action_attempt_id=receipt.attempt_id, receipt_id=receipt.id,
            started_at=receipt.observed_at, finished_at=receipt.observed_at))
    result.phases.sort(key=lambda item: (_aware(item.started_at), item.span_id))
    return result
