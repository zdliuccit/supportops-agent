"""由业务事实派生有界告警；不读取正文、不修改执行状态、不发送外部通知。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID
from collections.abc import Mapping, Sequence
from sqlalchemy.engine import Row
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from supportops_core.config import Settings
from supportops_core.enums import RunStatus, AuditAction, AuditResourceType
from supportops_core.models import (AgentRun, AgentRunObservation, AgentAuditEvent, BusinessAction,
    RunAttempt, RunEvent, RuntimeAlert, RuntimeAlertScanState)
from supportops_core.redaction import redact_sensitive_text

ALERT_RULES = {
    "BUDGET_EXHAUSTED": ("warning", "预算或调用额度耗尽", "已触发实际预算/额度门禁，不能通过恢复清零。"),
    "LEASE_EXPIRED": ("warning", "当前执行租约过期", "当前许可已过期，需等待恢复或确认；告警不授予新的执行许可。"),
    "RECOVERY_ACTIVE": ("info", "运行正在恢复", "运行存在恢复尝试，次数和旧尝试事实保留。"),
    "RECOVERY_NEEDS_ATTENTION": ("warning", "恢复需要人工处理", "恢复额度耗尽或旧执行/副作用未获证明，不能直接重跑。"),
    "ACTION_RECONCILIATION_REQUIRED": ("warning", "业务动作需要对账", "结果未知或存在冲突，需要独立查询；不能直接判定失败或重复执行。"),
    "AUTHORIZATION_DENIED": ("warning", "权限或凭据门禁拒绝", "该运行记录中存在权限/凭据拒绝事实，请查看安全审计；不作为当前权限可用性探测。"),
    "FIRST_OUTPUT_SLOW": ("warning", "模型首输出超过配置阈值", "以 SDK 首个非空输出块计时，不使用未信任客户端数据推断网络耗时。"),
    "OBSERVATION_MISSING": ("info", "部分模型观测不可用", "缺失值保留未知，不填零，也不据此判定业务失败。"),
}
AUTHORIZATION_CODES = frozenset({"RUNTIME_AUTHORIZATION_DENIED", "TOOL_ACCESS_DENIED", "RESOURCE_ACCESS_DENIED",
    "TOOL_RESOURCE_ACCESS_DENIED", "TOOL_REVOKED", "TOOL_DISABLED_OR_REVOKED", "TOOL_APPROVAL_REQUIRED",
    "TOOL_ACTION_WORKFLOW_UNAVAILABLE", "MOCK_RESOURCE_ACCESS_DENIED", "APPROVAL_SCOPE_INVALID"})
RECOVERY_CODES = frozenset({"ACTION_RECONCILIATION_REQUIRED", "LEGACY_EXECUTOR_CONFIRMATION_REQUIRED", "RUN_NOTIFICATION_EXHAUSTED"})
METADATA_KEYS = frozenset({"source_status", "error_code", "generation", "recovery_count", "model_call_count",
    "denied_call_count", "reconciliation_count", "reconciliation_limit", "first_output_ms", "threshold_ms", "missing_fields"})
MISSING_FIELD_NAMES = frozenset({"model_call_count", "provider_first_output_at", "first_output_duration_ms",
    "input_tokens", "output_tokens", "total_cost_microusd"})
AlertMetadataValue = str | int | bool | None | list[str]
RunAlertSource = Row[tuple[UUID, UUID, RunStatus, str | None, UUID | None, int, str | None, int,
    int | None, datetime | None, int | None, int | None, int | None]]


class RuntimeAlertConflict(ValueError):
    pass


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def safe_alert_metadata(data: Mapping[str, object]) -> dict[str, AlertMetadataValue]:
    result: dict[str, AlertMetadataValue] = {}
    for key, value in data.items():
        if key not in METADATA_KEYS:
            continue
        if isinstance(value, str):
            result[key] = redact_sensitive_text(value)[:200]
        elif value is None or isinstance(value, (int, bool)):
            result[key] = value
        elif key == "missing_fields" and isinstance(value, list) and len(value) <= 10:
            names = [item for item in value if isinstance(item, str) and item in MISSING_FIELD_NAMES]
            if len(names) == len(value):
                result[key] = names
    return result


async def _sync(session: AsyncSession, *, tenant_id: UUID, scope_kind: str, scope_id: UUID, run_id: UUID,
    generation: int, rule: str, hit: bool, metadata: Mapping[str, object], now: datetime,
) -> None:
    alert = await session.scalar(select(RuntimeAlert).where(RuntimeAlert.tenant_id == tenant_id,
        RuntimeAlert.scope_kind == scope_kind, RuntimeAlert.scope_id == scope_id, RuntimeAlert.rule_code == rule)
        .execution_options(populate_existing=True).with_for_update(key_share=True))
    if alert is None and not hit:
        return
    data = safe_alert_metadata(metadata)
    if alert is None:
        session.add(RuntimeAlert(tenant_id=tenant_id, scope_kind=scope_kind, scope_id=scope_id, run_id=run_id,
            rule_code=rule, severity=ALERT_RULES[rule][0], is_active=True, source_generation=generation,
            metadata_payload=data, revision=1, first_observed_at=now, last_observed_at=now))
        return
    changed = alert.is_active != hit or alert.source_generation != generation or alert.metadata_payload != data
    if hit and (not alert.is_active or alert.source_generation != generation):
        alert.acknowledged_at = alert.acknowledged_by = None
    if changed:
        alert.revision += 1
    alert.is_active, alert.source_generation, alert.metadata_payload = hit, generation, data
    alert.last_observed_at = now
    if hit:
        alert.resolved_at = None
    elif alert.resolved_at is None:
        alert.resolved_at = now


async def _scan_runs(session: AsyncSession, runs: Sequence[RunAlertSource], *, settings: Settings, now: datetime) -> None:
    ids = [run.id for run in runs]
    if not ids:
        return
    attempts = {row.id: row for row in (await session.execute(select(RunAttempt.id, RunAttempt.tenant_id,
        RunAttempt.run_id, RunAttempt.status, RunAttempt.lease_expires_at).where(RunAttempt.run_id.in_(ids)))).all()}
    ranked = select(AgentRunObservation.run_id, AgentRunObservation.started_at, AgentRunObservation.first_output_at,
        func.row_number().over(partition_by=AgentRunObservation.run_id,
            order_by=(AgentRunObservation.first_output_at.asc().nulls_last(), AgentRunObservation.id)).label("rank"))
    ranked = ranked.join(AgentRun, (AgentRun.id == AgentRunObservation.run_id) & (AgentRun.tenant_id == AgentRunObservation.tenant_id))
    ranked = ranked.where(AgentRunObservation.run_id.in_(ids), AgentRunObservation.kind == "llm").subquery()
    first_outputs = {row.run_id: row for row in (await session.execute(select(ranked).where(ranked.c.rank == 1))).all()}
    # 复用同一 JSON 表达式/绑定参数，避免 PG 将 SELECT 与 GROUP BY 视作不同列。
    denial_code = RunEvent.data["code"].as_string()
    denial_rows = (await session.execute(select(RunEvent.run_id, denial_code.label("code"),
        func.count().label("denial_count")).join(AgentRun, (AgentRun.id == RunEvent.run_id) & (AgentRun.tenant_id == RunEvent.tenant_id))
        .where(RunEvent.run_id.in_(ids), RunEvent.event_type == "tool.denied")
        .group_by(RunEvent.run_id, denial_code))).all()
    denials: dict[UUID, dict[str, int]] = {}
    for row in denial_rows:
        if row.code:
            denials.setdefault(row.run_id, {})[row.code] = row.denial_count
    for run in runs:
        codes = {run.error_code, *denials.get(run.id, {}).keys()}
        codes.discard(None)
        budget = any(code.endswith(("BUDGET_EXCEEDED", "QUOTA_EXCEEDED")) or code == "AGENT_RUN_TIMEOUT" for code in codes)
        auth = any(code in AUTHORIZATION_CODES or code.startswith("MODEL_CREDENTIAL_") for code in codes)
        attempt = attempts.get(run.current_attempt_id)
        expired = (run.status in {RunStatus.RUNNING, RunStatus.CANCELLING} and attempt is not None
            and attempt.tenant_id == run.tenant_id and attempt.run_id == run.id and attempt.status == "running"
            and aware(attempt.lease_expires_at) <= now)
        recovery = run.recovery_count > 0 and run.status in {RunStatus.QUEUED, RunStatus.RUNNING}
        attention = run.recovery_reason in RECOVERY_CODES or run.error_code in RECOVERY_CODES or (
            run.status == RunStatus.FAILED and run.recovery_count >= settings.run_attempt_max_recoveries and run.recovery_count > 0)
        first = first_outputs.get(run.id)
        first_ms = int((aware(first.first_output_at)-aware(first.started_at)).total_seconds()*1000) if first is not None and first.first_output_at is not None else None
        if first_ms is not None and first_ms < 0:
            first_ms = None
        slow = settings.runtime_alert_first_output_ms is not None and first_ms is not None and first_ms > settings.runtime_alert_first_output_ms
        missing = []
        if run.status == RunStatus.COMPLETED and run.finished_at is not None and (
            now-aware(run.finished_at)).total_seconds() >= settings.runtime_alert_observation_grace_seconds:
            if run.model_call_count is None:
                missing.append("model_call_count")
            elif run.model_call_count > 0:
                if first is None or first.first_output_at is None:
                    missing.append("provider_first_output_at")
                elif first_ms is None:
                    missing.append("first_output_duration_ms")
                for field in ("input_tokens", "output_tokens", "total_cost_microusd"):
                    if getattr(run, field) is None:
                        missing.append(field)
        data = {"source_status": run.status.value, "error_code": run.error_code, "generation": run.execution_generation,
            "recovery_count": run.recovery_count, "model_call_count": run.model_call_count,
            "denied_call_count": sum(denials.get(run.id, {}).values())}
        rules = {"BUDGET_EXHAUSTED": budget, "LEASE_EXPIRED": bool(expired), "RECOVERY_ACTIVE": recovery,
            "RECOVERY_NEEDS_ATTENTION": attention, "AUTHORIZATION_DENIED": auth,
            "FIRST_OUTPUT_SLOW": slow, "OBSERVATION_MISSING": bool(missing)}
        for rule, hit in rules.items():
            details = {**data, **({"first_output_ms": first_ms, "threshold_ms": settings.runtime_alert_first_output_ms} if rule == "FIRST_OUTPUT_SLOW" else {}),
                **({"missing_fields": missing} if rule == "OBSERVATION_MISSING" else {})}
            await _sync(session, tenant_id=run.tenant_id, scope_kind="run", scope_id=run.id, run_id=run.id,
                generation=run.execution_generation, rule=rule, hit=hit, metadata=details, now=now)


async def scan_runtime_alerts(session_factory: async_sessionmaker[AsyncSession], *, settings: Settings) -> None:
    """独立周期任务，各类来源单独短事务；失败不会回滚业务执行或回执。"""
    for kind in ("run", "action"):
        async with session_factory() as session, session.begin():
            now = datetime.now(UTC)
            dialect = session.get_bind().dialect.name
            insert = sqlite_insert if dialect == "sqlite" else pg_insert
            await session.execute(insert(RuntimeAlertScanState).values(source_kind=kind, updated_at=now)
                .on_conflict_do_nothing(index_elements=["source_kind"]))
            cursor = await session.scalar(select(RuntimeAlertScanState).where(RuntimeAlertScanState.source_kind == kind)
                .with_for_update(key_share=True))
            if cursor is None:
                raise RuntimeError("运行告警扫描游标不可用")
            start = now-timedelta(hours=settings.runtime_alert_scan_window_hours)
            if kind == "run":
                # 扫描只取指标和许可元数据，不加载问题、回答、配置快照或凭据。
                run_query = select(AgentRun.id, AgentRun.tenant_id, AgentRun.status, AgentRun.error_code,
                    AgentRun.current_attempt_id, AgentRun.recovery_count, AgentRun.recovery_reason,
                    AgentRun.execution_generation, AgentRun.model_call_count, AgentRun.finished_at,
                    AgentRun.input_tokens, AgentRun.output_tokens, AgentRun.total_cost_microusd).where(or_(AgentRun.created_at >= start, AgentRun.status.in_(
                    [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.WAITING_APPROVAL, RunStatus.WAITING_ACTION])))
                if cursor.last_source_id is not None:
                    run_query = run_query.where(AgentRun.id > cursor.last_source_id)
                run_rows = list((await session.execute(run_query.order_by(AgentRun.id).limit(settings.runtime_alert_scan_batch_size))).all())
                await _scan_runs(session, run_rows, settings=settings, now=now)
                cursor.last_source_id = run_rows[-1].id if run_rows else None
            else:
                action_query = select(BusinessAction.id, BusinessAction.tenant_id, BusinessAction.run_id, BusinessAction.status,
                    BusinessAction.generation, BusinessAction.error_code, BusinessAction.reconciliation_count, BusinessAction.reconciliation_limit)
                action_query = action_query.where(or_(BusinessAction.created_at >= start, BusinessAction.status.in_(["unknown", "reconciling", "needs_attention"])))
                if cursor.last_source_id is not None:
                    action_query = action_query.where(BusinessAction.id > cursor.last_source_id)
                action_rows = list((await session.execute(action_query.order_by(BusinessAction.id).limit(settings.runtime_alert_scan_batch_size))).all())
                for row in action_rows:
                    await _sync(session, tenant_id=row.tenant_id, scope_kind="action", scope_id=row.id, run_id=row.run_id,
                        generation=row.generation, rule="ACTION_RECONCILIATION_REQUIRED",
                        hit=row.status in {"unknown", "reconciling", "needs_attention"}, now=now,
                        metadata={"source_status": row.status, "error_code": row.error_code, "generation": row.generation,
                            "reconciliation_count": row.reconciliation_count, "reconciliation_limit": row.reconciliation_limit})
                cursor.last_source_id = action_rows[-1].id if action_rows else None
            cursor.updated_at = now


async def acknowledge_runtime_alert(session: AsyncSession, *, tenant_id: UUID, alert_id: UUID,
    expected_revision: int, actor_user_id: UUID, correlation_id: str,
) -> RuntimeAlert:
    from supportops_core.services import ResourceNotFoundError
    alert = await session.scalar(select(RuntimeAlert).where(RuntimeAlert.tenant_id == tenant_id, RuntimeAlert.id == alert_id)
        .execution_options(populate_existing=True).with_for_update(key_share=True))
    if alert is None:
        raise ResourceNotFoundError("告警不存在")
    if alert.acknowledged_at is not None and alert.acknowledged_by == actor_user_id:
        return alert
    if not alert.is_active or alert.revision != expected_revision or alert.acknowledged_at is not None:
        raise RuntimeAlertConflict("告警状态或版本已变化，请刷新后重新确认")
    alert.acknowledged_at, alert.acknowledged_by = datetime.now(UTC), actor_user_id
    alert.revision += 1
    session.add(AgentAuditEvent(tenant_id=tenant_id, actor_user_id=actor_user_id, resource_type=AuditResourceType.RUNTIME_ALERT,
        action=AuditAction.RUNTIME_ALERT_ACKNOWLEDGED, correlation_id=correlation_id,
        metadata_payload={"alert_id": str(alert.id), "rule_code": alert.rule_code, "scope_kind": alert.scope_kind,
            "scope_id": str(alert.scope_id), "revision": alert.revision}))
    return alert
