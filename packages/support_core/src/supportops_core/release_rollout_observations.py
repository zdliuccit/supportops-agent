"""仅读取当前灰度轮次的有界计量元数据，不读取答案、原始日志或客户端上报。"""
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Literal
from uuid import UUID
from sqlalchemy import String, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.enums import MessageRole, RunStatus
from supportops_core.models import AgentReleaseRollout, AgentRun, AgentRunObservation, Conversation, Message, ToolInvocation, utc_now
from supportops_core.release_rollout_contracts import (
    ReleaseRolloutObservation, ReleaseRolloutPolicy, RolloutCohortMetrics, RolloutObservationCheck,
)

_ACCESS_FAILURE_CODES = (
    "RUNTIME_AUTHORIZATION_DENIED", "TOOL_ACCESS_DENIED", "TOOL_REVOKED", "TOOL_DISABLED_OR_REVOKED",
    "TOOL_POLICY_CHANGED", "TOOL_NOT_BOUND", "TOOL_IMPLEMENTATION_MISMATCH", "TICKET_ACCESS_CHANGED",
    "MOCK_DOWNSTREAM_ACCESS_DENIED", "KNOWLEDGE_EVIDENCE_INVALID", "HISTORY_CONTEXT_AUTHORIZATION_CHANGED",
    "ACTION_RELEASE_WITHDRAWN", "MODEL_CREDENTIAL_REVOKED",
    "TOOL_CONTEXT_AUTHORIZATION_CHANGED",
)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _p95(values: list[int]) -> int | None:
    return sorted(values)[ceil(len(values) * .95) - 1] if len(values) >= 3 else None


async def _cohort(session: AsyncSession, *, rollout: AgentReleaseRollout, policy: ReleaseRolloutPolicy,
    selection: Literal["baseline", "candidate"], observed_at: datetime,
) -> RolloutCohortMetrics:
    version_id = rollout.baseline_version_id if selection == "baseline" else rollout.candidate_version_id
    conditions = (AgentRun.tenant_id == rollout.tenant_id, AgentRun.agent_id == rollout.agent_id,
        AgentRun.agent_version_id == version_id, Conversation.tenant_id == rollout.tenant_id,
        Conversation.agent_id == rollout.agent_id, Conversation.agent_version_id == version_id,
        Conversation.release_rollout_id == rollout.id, Conversation.release_rollout_epoch == rollout.epoch,
        Conversation.release_selection == selection, AgentRun.created_at < observed_at,
        AgentRun.created_at >= (rollout.epoch_started_at or observed_at))
    ids_query = select(AgentRun.id).join(Conversation, Conversation.id == AgentRun.conversation_id).where(*conditions)
    ids = list((await session.scalars(ids_query.order_by(AgentRun.created_at, AgentRun.id)
        .limit(policy.max_metadata_rows_per_version + 1).execution_options(include_deleted=True))).all())
    api_timings = (select(AgentRunObservation.run_id,
        case((func.count() == 1, func.max(AgentRunObservation.duration_ms)), else_=None).label("duration_ms"))
        .where(AgentRunObservation.tenant_id == rollout.tenant_id, AgentRunObservation.run_id.in_(ids),
            AgentRunObservation.kind == "api", AgentRunObservation.name == "api.first_committed_text_observed",
            AgentRunObservation.metadata_payload["source"].as_string() == "api_database_visibility_v1")
        .group_by(AgentRunObservation.run_id).subquery())
    result_exists = exists(select(Message.id).where(Message.tenant_id == rollout.tenant_id,
        Message.conversation_id == AgentRun.conversation_id, Message.turn_sequence == AgentRun.input_sequence,
        Message.role == MessageRole.ASSISTANT, Message.support_result["schema_version"].as_string() == "1",
        func.replace(Message.support_result["run_id"].as_string(), "-", "") == func.replace(AgentRun.id.cast(String), "-", ""),
        func.replace(Message.support_result["attempt_id"].as_string(), "-", "") == func.replace(AgentRun.current_attempt_id.cast(String), "-", "")))
    rows = (await session.execute(select(AgentRun.id, AgentRun.status, AgentRun.error_code, AgentRun.total_cost_microusd,
        AgentRun.cost_source, AgentRun.end_to_end_latency_ms, api_timings.c.duration_ms.label("api_first_text_ms"),
        result_exists.label("has_result"))
        .join(Conversation, Conversation.id == AgentRun.conversation_id)
        .outerjoin(api_timings, api_timings.c.run_id == AgentRun.id).where(*conditions, AgentRun.id.in_(ids))
        .order_by(AgentRun.created_at, AgentRun.id).limit(policy.max_metadata_rows_per_version + 1)
        .execution_options(include_deleted=True))).all()
    truncated = len(rows) > policy.max_metadata_rows_per_version
    completed = [row for row in rows if row.status == RunStatus.COMPLETED]
    terminal = [row for row in rows if row.status in {RunStatus.COMPLETED, RunStatus.FAILED}]
    failed = sum(row.status == RunStatus.FAILED for row in rows)
    pending = sum(row.status not in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED} for row in rows)
    cancelled = sum(row.status == RunStatus.CANCELLED for row in rows)
    missing_cost = sum(row.total_cost_microusd is None or row.total_cost_microusd < 0 or row.cost_source not in {"provider", "calculated"} for row in terminal)
    first = [row.api_first_text_ms for row in completed if row.api_first_text_ms is not None and row.api_first_text_ms >= 0]
    complete = [row.end_to_end_latency_ms for row in completed if row.end_to_end_latency_ms is not None and row.end_to_end_latency_ms >= 0]
    denials = int(await session.scalar(select(func.count()).select_from(ToolInvocation).where(
        ToolInvocation.tenant_id == rollout.tenant_id, ToolInvocation.run_id.in_(ids),
        or_(ToolInvocation.status == "denied", ToolInvocation.error_code.in_(_ACCESS_FAILURE_CODES)))
        .execution_options(include_deleted=True)) or 0)
    # 包含工具返回后或最终提交前的授权拒绝；不得只统计调用前denied状态。
    denials += sum(row.error_code in _ACCESS_FAILURE_CODES for row in rows)
    return RolloutCohortMetrics(selection=selection, version_id=version_id, run_count=len(rows),
        completed=len(completed), failed=failed, pending=pending, cancelled=cancelled,
        failure_rate=failed / len(terminal) if terminal else None,
        average_cost_usd=sum(row.total_cost_microusd for row in terminal) / len(terminal) / 1_000_000 if terminal and not missing_cost else None,
        p95_api_first_text_ms=_p95(first) if len(first) == len(completed) else None,
        p95_complete_ms=_p95(complete) if len(complete) == len(completed) else None,
        missing_cost_count=missing_cost, missing_first_text_count=len(completed)-len(first),
        missing_complete_count=len(completed)-len(complete), missing_result_count=sum(not row.has_result for row in completed),
        authorization_denial_count=denials, truncated=truncated)


async def observe_release_rollout(session: AsyncSession, *, rollout: AgentReleaseRollout,
    policy: ReleaseRolloutPolicy,
) -> ReleaseRolloutObservation:
    now = utc_now()
    baseline = await _cohort(session, rollout=rollout, policy=policy, selection="baseline", observed_at=now)
    candidate = await _cohort(session, rollout=rollout, policy=policy, selection="candidate", observed_at=now)
    earliest = aware(rollout.epoch_started_at) + timedelta(seconds=policy.observation_seconds) if rollout.epoch_started_at else None
    elapsed = earliest is not None and now >= earliest
    checks = [RolloutObservationCheck(code="OBSERVATION_WINDOW", status="passed" if elapsed else "waiting",
        message="每次启动/恢复/扩大须经过完整观察时长；旧轮次不充当本轮样本")]
    for metric in (baseline, candidate):
        prefix = metric.selection.upper()
        enough = metric.completed + metric.failed >= policy.minimum_runs_per_version
        checks.extend([
            RolloutObservationCheck(code=prefix+"_METADATA_BOUNDED", status="failed" if metric.truncated else "passed", message="元数据超限停止新增灰度，不截断后批准扩大"),
            RolloutObservationCheck(code=prefix+"_SAMPLES", status="passed" if enough else "missing", message="基线和候选分别需要足够的本轮终态样本"),
            RolloutObservationCheck(code=prefix+"_PENDING", status="passed" if metric.pending == 0 else "waiting", message="观察范围仍有执行/审批/动作等待时不能扩大"),
            RolloutObservationCheck(code=prefix+"_CANCELLATIONS", status="passed" if metric.cancelled == 0 else "missing", message="取消样本不能从费用和延迟中静默排除后批准扩大；本轮需排查后重新观察"),
            RolloutObservationCheck(code=prefix+"_AUTHORIZATION", status="passed" if metric.authorization_denial_count == 0 else "failed", message="授权拒绝须排查，不能将其视为业务成功或忽略后扩大"),
            RolloutObservationCheck(code=prefix+"_PRODUCT_RESULT", status="passed" if metric.missing_result_count == 0 else "failed", message="已完成Run须有绑定本轮的结构化产品结果，存在结果不证明语义正确"),
        ])
        values = (("FAILURE_RATE", metric.failure_rate, policy.max_failure_rate),
            ("AVERAGE_COST_USD", metric.average_cost_usd, policy.max_average_cost_usd),
            ("P95_API_FIRST_TEXT", metric.p95_api_first_text_ms, policy.max_p95_api_first_text_ms),
            ("P95_COMPLETE", metric.p95_complete_ms, policy.max_p95_complete_ms))
        status: Literal["passed", "failed", "missing", "waiting"]
        for name, value, limit in values:
            status = "missing" if value is None else "failed" if enough and value > limit else "passed" if enough else "missing"
            checks.append(RolloutObservationCheck(code=prefix+"_"+name, status=status,
                message="已知完整计量须满足批准策略，缺失不填零；API首正文上界不证明客户端延迟"))
    for name, candidate_value, baseline_value, ratio in (
        ("COST_REGRESSION", candidate.average_cost_usd, baseline.average_cost_usd, policy.max_cost_increase_ratio),
        ("LATENCY_REGRESSION", candidate.p95_complete_ms, baseline.p95_complete_ms, policy.max_latency_increase_ratio)):
        complete = candidate_value is not None and baseline_value is not None
        enough = all(item.completed + item.failed >= policy.minimum_runs_per_version for item in (baseline, candidate))
        status = "missing" if not complete or not enough else "passed" if candidate_value <= baseline_value * ratio else "failed"
        checks.append(RolloutObservationCheck(code=name, status=status, message="候选相对基线费用/完整响应耗时不得超过批准退化比例"))
    passed = all(check.status == "passed" for check in checks)
    unsafe = any(check.status == "failed" for check in checks)
    overdue_missing = elapsed and any(check.status == "missing" for check in checks)
    return ReleaseRolloutObservation(observed_at=now, epoch=rollout.epoch, started_at=rollout.epoch_started_at,
        earliest_expansion_at=earliest, baseline=baseline, candidate=candidate, checks=checks,
        can_expand=rollout.status == "active" and rollout.candidate_bps < policy.max_candidate_bps and passed,
        pause_recommended=unsafe or overdue_missing)
