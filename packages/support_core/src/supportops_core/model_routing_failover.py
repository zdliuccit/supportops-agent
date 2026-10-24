"""有界回退建议与熔断历史重放；纯算法，无数据库、模型、等待、自动探测或重试。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from supportops_core.model_routing_accounting_contracts import routing_usage_cost
from supportops_core.model_routing_contracts import RoutingCandidateFilterReport
from supportops_core.model_routing_failover_contracts import (
    CandidateFailoverReason, CircuitPhase, CircuitReason, FailoverReason, FailoverStatus,
    RoutingCircuitEvent, RoutingCircuitScope, RoutingCircuitSnapshot,
    RoutingFailoverAttempt, RoutingFailoverCandidate, RoutingFailoverControls, RoutingFailoverDecision,
)
from supportops_core.model_routing_policy_contracts import RoutingPolicyVersionRecord
from supportops_core.model_routing_reservation_contracts import RoutingCallPlan
from supportops_core.release_manifest_services import manifest_digest


class RoutingFailoverDenied(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("原路由回退或熔断历史当前不可确认")
        self.code = code


def _time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RoutingFailoverDenied("ROUTING_FAILOVER_TIME_INVALID")
    return value.astimezone(UTC)


def replay_routing_circuit(*, scope: RoutingCircuitScope, controls: RoutingFailoverControls,
    events: tuple[RoutingCircuitEvent, ...], now: datetime,
) -> RoutingCircuitSnapshot:
    """可信存储须提供原范围完整有序历史并证明空历史；摘要/重放不认证来源。

    未来存储必须在同范围锁下验证前摘要、原物理调用唯一性及完整真实费用；恢复领取需
    原子唯一提交后才可派发。此纯函数不写领取，不把冷却到期当作成功恢复。
    """
    now = _time(now)
    if len(events) > 256 or scope.controls_digest != manifest_digest(controls.model_dump(mode="json")):
        raise RoutingFailoverDenied("ROUTING_CIRCUIT_SCOPE_INVALID")
    scope_digest = manifest_digest(scope.model_dump(mode="json"))
    phase: CircuitPhase = "closed"
    reason: CircuitReason | None = None
    failures = 0
    previous: str | None = None
    open_until: datetime | None = None
    recovery_call: UUID | None = None
    recovery_until: datetime | None = None
    event_ids: set[UUID] = set()
    observed_calls: set[UUID] = set()
    recovery_calls: set[UUID] = set()
    last_time: datetime | None = None
    for index, original in enumerate(events, 1):
        event = RoutingCircuitEvent.model_validate_json(original.model_dump_json())
        when = _time(event.recorded_at)
        if (event.scope_digest != scope_digest or event.sequence != index or event.previous_event_digest != previous
            or event.event_id in event_ids or when > now or last_time is not None and when < last_time
            or phase == "quarantined"):
            raise RoutingFailoverDenied("ROUTING_CIRCUIT_HISTORY_INVALID")
        event_ids.add(event.event_id)
        if event.kind == "recovery_claimed":
            if (phase != "open" or open_until is None or when < open_until
                or event.call_id in recovery_calls | observed_calls):
                raise RoutingFailoverDenied("ROUTING_CIRCUIT_RECOVERY_ALREADY_CLAIMED")
            phase, reason = "half_open", "recovery_unconfirmed"
            recovery_calls.add(event.call_id)
            recovery_call, recovery_until = event.call_id, when + timedelta(seconds=controls.recovery_window_seconds)
            open_until = None
        else:
            receipt = event.receipt
            if (receipt is None or event.call_id in observed_calls or receipt.tenant_id != scope.tenant_id
                or receipt.actual_model_version_id != scope.candidate.model_version_id
                or receipt.credential_revision != scope.candidate.credential_revision
                or last_time is not None and receipt.observed_at < last_time):
                raise RoutingFailoverDenied("ROUTING_CIRCUIT_OBSERVATION_MISMATCH")
            observed_calls.add(event.call_id)
            complete = (receipt.usage is not None and receipt.finished_at is not None
                and event.accounting_digest is not None and event.accounting_within_bounds is True)
            trial_matches = (phase == "half_open" and event.call_id == recovery_call and recovery_until is not None
                and last_time is not None and receipt.started_at >= last_time and when < recovery_until)
            if phase == "open" or phase == "half_open" and not trial_matches:
                phase, reason = "quarantined", "recovery_unconfirmed"
            elif not complete or event.outcome not in {"completed", "transient_failure", "rate_limited"}:
                phase, reason = "quarantined", "unsafe_outcome"
            elif event.outcome == "completed":
                phase, reason, failures = "closed", None, 0
            else:
                failures = min(controls.failure_threshold, failures + 1)
                if phase == "half_open" or failures >= controls.failure_threshold:
                    reason = "recovery_failed" if phase == "half_open" else "failure_threshold"
                    phase, open_until = "open", when + timedelta(seconds=controls.cooldown_seconds)
            recovery_call, recovery_until = None, None
            if phase != "open":
                open_until = None
        previous, last_time = manifest_digest(event.model_dump(mode="json")), when
    if phase == "half_open" and recovery_until is not None and now >= recovery_until:
        phase, reason = "quarantined", "recovery_unconfirmed"
        recovery_call, recovery_until = None, None
    if len(events) == 256:
        phase, reason, open_until = "quarantined", "history_full", None
        recovery_call, recovery_until = None, None
    return RoutingCircuitSnapshot(scope=scope, scope_digest=scope_digest, phase=phase, reason=reason,
        consecutive_failures=failures, event_count=len(events), last_event_digest=previous, open_until=open_until,
        recovery_call_id=recovery_call, recovery_until=recovery_until, observed_at=now)


def propose_routing_failover(*, plan: RoutingCallPlan, policy: RoutingPolicyVersionRecord,
    controls: RoutingFailoverControls, eligibility: RoutingCandidateFilterReport,
    circuits: tuple[RoutingCircuitSnapshot, ...], attempts: tuple[RoutingFailoverAttempt, ...], now: datetime,
) -> RoutingFailoverDecision:
    """只消费可信当前原记录的有限观察并给出建议，不预留/领取/换主模型/发请求。

    真实提供者还须读持久原策略/撤销、完整尝试/事件/账本、当前过滤和熔断历史，证明
    没有遗漏在途调用或工具/正文。DTO不能上传自证。建议后必须重新授权和独立预留。
    """
    now = _time(now)
    if len(attempts) > 3 or len(circuits) > 8:
        raise RoutingFailoverDenied("ROUTING_FAILOVER_INPUT_TOO_LARGE")
    plan_digest = manifest_digest(plan.model_dump(mode="json"))
    controls_digest = manifest_digest(controls.model_dump(mode="json"))

    def decision(status: FailoverStatus, reason: FailoverReason | None = None, *,
        candidates: tuple[RoutingFailoverCandidate, ...] | list[RoutingFailoverCandidate] = (),
        candidate_id: UUID | None = None, trial: bool = False,
    ) -> RoutingFailoverDecision:
        return RoutingFailoverDecision(plan_digest=plan_digest, controls_digest=controls_digest, status=status,
            reason=reason, attempts_consumed=len(attempts), candidate_id=candidate_id,
            recovery_trial_required=trial, candidates=tuple(candidates), observed_at=now)

    ordered = tuple(sorted(plan.candidates, key=lambda row: (row.priority, str(row.candidate_id))))
    candidate_set_digest = manifest_digest([row.model_dump(mode="json") for row in ordered])
    if not plan.created_at <= now < min(plan.expires_at, policy.definition.valid_until):
        return decision("blocked", "policy_expired")
    if (policy.tenant_id != plan.requirements.tenant_id or policy.version_id != plan.requirements.policy_version_id
        or manifest_digest(policy.model_dump(mode="json")) != plan.requirements.policy_digest
        or not policy.created_at <= plan.created_at < plan.expires_at <= policy.definition.valid_until
        or plan.purpose not in policy.definition.allowed_purposes
        or not set(plan.requirements.allowed_regions) <= set(policy.definition.allowed_regions)
        or plan.requirements.data_classification not in policy.definition.allowed_data_classifications
        or policy.definition.fallback_policy_digest != controls_digest
        or tuple(sorted(policy.definition.candidates, key=lambda row: (row.priority, str(row.candidate_id)))) != ordered
        or eligibility.tenant_id != plan.requirements.tenant_id
        or eligibility.agent_version_id != plan.requirements.agent_version_id
        or eligibility.policy_version_id != policy.version_id or eligibility.policy_digest != plan.requirements.policy_digest
        or eligibility.requirements_digest != manifest_digest(plan.requirements.model_dump(mode="json"))
        or eligibility.candidate_set_digest != candidate_set_digest
        or len({row.candidate_id for row in eligibility.candidates}) != len(ordered)
        or {row.candidate_id for row in eligibility.candidates} != {row.candidate_id for row in ordered}):
        return decision("blocked", "scope_mismatch")
    if not 0 <= (now - _time(eligibility.observed_at)).total_seconds() <= controls.observation_max_age_seconds:
        return decision("blocked", "observations_stale")
    tried: set[UUID] = set()
    calls: set[UUID] = set()
    last_finish: datetime | None = None
    last_call_number = 0
    original_model_version: UUID | None = None
    for ordinal, row in enumerate(attempts, 1):
        view, record = row.accounting, row.accounting.reservation
        receipt, accounting = view.accounting.receipt, view.accounting
        candidate = next((item for item in ordered if item.candidate_id == record.candidate_id), None)
        if (row.ordinal != ordinal or row.root_call_id != plan.call_id or record.call_id in calls
            or ordinal == 1 and record.call_id != plan.call_id or candidate is None
            or record.candidate_id in tried or record.tenant_id != plan.requirements.tenant_id
            or record.run_id != plan.run_id or record.conversation_id != plan.conversation_id
            or record.attempt_id != plan.attempt_id or record.generation != plan.generation
            or record.policy_version_id != policy.version_id or record.policy_digest != plan.requirements.policy_digest
            or record.agent_version_id != plan.requirements.agent_version_id
            or record.model_input_digest != plan.model_input_digest or record.context_snapshot_digest != plan.context_snapshot_digest
            or record.actual_model_version_id != candidate.model_version_id or record.credential_revision != candidate.credential_revision
            or record.candidate_digest != manifest_digest(candidate.model_dump(mode="json"))
            or record.candidate_set_digest != candidate_set_digest or record.requirements_digest != eligibility.requirements_digest
            or record.plan_digest != manifest_digest(plan.model_copy(update={"call_id": record.call_id}).model_dump(mode="json"))
            or record.reserved_input_tokens != plan.requirements.reserved_input_tokens
            or record.reserved_output_tokens != plan.requirements.reserved_output_tokens
            or record.call_number <= last_call_number
            or original_model_version is not None and record.run_original_model_version_id != original_model_version
            or accounting.reservation_digest != manifest_digest(record.model_dump(mode="json"))
            or accounting.receipt_digest != manifest_digest(receipt.model_dump(mode="json"))
            or receipt.reservation_digest != accounting.reservation_digest or receipt.call_id != record.call_id
            or receipt.tenant_id != record.tenant_id or receipt.run_id != record.run_id
            or receipt.attempt_id != record.attempt_id or receipt.generation != record.generation
            or receipt.actual_model_version_id != record.actual_model_version_id
            or receipt.credential_revision != record.credential_revision or receipt.model_input_digest != record.model_input_digest
            or not plan.created_at <= _time(record.recorded_at) <= receipt.started_at <= receipt.observed_at <= accounting.recorded_at <= now
            or last_finish is not None and receipt.started_at < last_finish):
            return decision("blocked", "history_invalid")
        price = view.pricing
        if (view.accounting_digest != manifest_digest(accounting.model_dump(mode="json"))
            or accounting.pricing_snapshot_digest != manifest_digest(price.model_dump(mode="json"))
            or price.call_id != record.call_id or price.tenant_id != record.tenant_id
            or price.actual_model_version_id != record.actual_model_version_id
            or price.candidate_digest != record.candidate_digest
            or price.source_pricing_digest != manifest_digest(price.source_pricing)
            or price.source_pricing_digest != candidate.pricing_digest):
            return decision("blocked", "history_invalid")
        calls.add(record.call_id)
        tried.add(record.candidate_id)
        last_call_number, original_model_version = record.call_number, record.run_original_model_version_id
        if row.public_text_emitted:
            return decision("blocked", "public_text_started")
        if row.business_tool_calls_started or row.tool_outcome != "none":
            return decision("blocked", "tool_started")
        if (receipt.usage is None or receipt.finished_at is None or accounting.status != "settled"
            or accounting.reservation_exceeded or accounting.error_code is not None):
            return decision("blocked", "usage_unknown")
        try:
            reserved = routing_usage_cost(price, input_tokens=record.reserved_input_tokens,
                output_tokens=record.reserved_output_tokens)
            consumed = routing_usage_cost(price, input_tokens=receipt.usage.input_tokens,
                output_tokens=receipt.usage.output_tokens)
        except ValueError:
            return decision("blocked", "usage_unknown")
        if (reserved != record.reserved_cost_microusd or reserved > plan.requirements.max_call_cost_microusd
            or consumed != accounting.settled_cost_microusd
            or consumed > reserved or receipt.usage.input_tokens > record.reserved_input_tokens
            or receipt.usage.output_tokens > record.reserved_output_tokens):
            return decision("blocked", "history_invalid")
        last_finish = receipt.finished_at
        if row.outcome == "completed" and receipt.outcome == "completed":
            return decision("completed") if ordinal == len(attempts) else decision("blocked", "history_invalid")
        if row.outcome not in {"transient_failure", "rate_limited"} or receipt.outcome != "failed":
            return decision("blocked", "unsafe_outcome")
    if len(attempts) >= controls.max_physical_calls:
        return decision("blocked", "call_limit")
    assessed = {row.candidate_id: row for row in eligibility.candidates}
    indexed = {row.scope.candidate.candidate_id: row for row in circuits}
    if len(indexed) != len(circuits) or set(indexed) - {row.candidate_id for row in ordered}:
        return decision("blocked", "scope_mismatch")
    rows: list[RoutingFailoverCandidate] = []
    selected: UUID | None = None
    trial = False
    for candidate in ordered:
        result, circuit = assessed[candidate.candidate_id], indexed.get(candidate.candidate_id)
        reason: CandidateFailoverReason | None = None
        if result.model_version_id != candidate.model_version_id or result.priority != candidate.priority:
            reason = "scope_mismatch"
        elif (result.status != "eligible" or result.latest_probe_id is None or result.estimated_cost_microusd is None
            or result.estimated_cost_microusd > plan.requirements.max_call_cost_microusd):
            reason = "ineligible"
        elif candidate.candidate_id in tried:
            reason = "already_attempted"
        elif circuit is None:
            reason = "circuit_missing"
        elif (circuit.scope.tenant_id != plan.requirements.tenant_id or circuit.scope.policy_version_id != policy.version_id
            or circuit.scope.policy_digest != plan.requirements.policy_digest or circuit.scope.candidate != candidate
            or circuit.scope.controls_digest != controls_digest):
            reason = "scope_mismatch"
        elif not 0 <= (now - _time(circuit.observed_at)).total_seconds() <= controls.observation_max_age_seconds:
            reason = "observation_stale"
        elif circuit.phase == "quarantined":
            reason = "circuit_quarantined"
        elif circuit.phase == "half_open":
            reason = "recovery_inflight"
        elif circuit.phase == "open" and (circuit.open_until is None or now < circuit.open_until):
            reason = "circuit_open"
        if reason is None and selected is None:
            selected, trial = candidate.candidate_id, circuit is not None and circuit.phase == "open"
        rows.append(RoutingFailoverCandidate(candidate_id=candidate.candidate_id, model_version_id=candidate.model_version_id,
            reason=reason))
    if selected is None:
        return decision("blocked", "no_candidate", candidates=rows)
    return decision("proposed", candidates=rows, candidate_id=selected, trial=trial)
