"""W26 候选子任务的静态契约；不注册 Agent、不调用模型、不授予执行许可。"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
FactKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{0,63}$")]
Count = Annotated[int, Field(ge=0, le=1_000_000_000)]
FactValue = Annotated[str, Field(max_length=1024)] | bool | int
FailureCode = Literal[
    "AUTHORIZATION_CHANGED", "VERSION_CHANGED", "EVIDENCE_UNAVAILABLE", "DEPENDENCY_FAILED",
    "BUDGET_UNAVAILABLE", "DEADLINE_EXCEEDED", "CANCELLED", "CONTRACT_REJECTED",
]


class SpecialistContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    @field_validator("*", mode="after")
    @classmethod
    def normalized_time(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        return value


def contract_digest(value: SpecialistContract) -> str:
    """只对封闭契约计算规范摘要；摘要匹配不表示当前授权有效。"""
    encoded = json.dumps(value.model_dump(mode="json"), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SpecialistVersion(SpecialistContract):
    agent_version_id: UUID
    model_version_id: UUID
    release_manifest_digest: Digest


class SpecialistParent(SpecialistContract):
    tenant_id: UUID
    subject_user_id: UUID
    conversation_id: UUID
    run_id: UUID
    attempt_id: UUID
    attempt_generation: int = Field(ge=1)
    version: SpecialistVersion
    authorization_scope_digest: Digest
    authorized_until: AwareDatetime


class SpecialistTraceBinding(SpecialistContract):
    """委派关联标识；未来执行器须另行创建真实 Span，不补造已发出的 Trace。"""

    schema_version: Literal["specialist_trace_binding_v1"] = "specialist_trace_binding_v1"
    parent_run_id: UUID
    parent_attempt_id: UUID
    parent_task_id: UUID | None = None
    child_task_id: UUID
    trace_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    parent_span_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    child_span_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    child_span_emitted: Literal[False] = False

    @model_validator(mode="after")
    def distinct_spans(self) -> "SpecialistTraceBinding":
        if self.parent_span_id == self.child_span_id:
            raise ValueError("SPECIALIST_TRACE_BINDING_INVALID")
        return self


class SpecialistResource(SpecialistContract):
    """仅由父级服务端投影当前获准资源，不接受专业身份或角色自行扩权。"""

    tenant_id: UUID
    resource_kind: Literal["knowledge_document", "diagnostic_request", "support_ticket"]
    resource_id: UUID
    resource_version_digest: Digest
    authorization_scope_digest: Digest
    access: Literal["read"] = "read"


class SpecialistEvidence(SpecialistContract):
    evidence_id: UUID
    resource: SpecialistResource
    content_digest: Digest
    observed_at: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def observation_window(self) -> "SpecialistEvidence":
        if self.observed_at >= self.valid_until:
            raise ValueError("SPECIALIST_EVIDENCE_WINDOW_INVALID")
        return self


class SpecialistBudgetBinding(SpecialistContract):
    """父级共享账本范围与上限声明；不是预留、消费或恢复许可。"""

    parent_run_id: UUID
    ledger_scope_digest: Digest
    ledger_revision: int = Field(ge=1)
    model_calls_limit: Count
    tool_calls_limit: Count
    tokens_limit: Count
    cost_microusd_limit: Count
    deadline: AwareDatetime
    reservation_granted: Literal[False] = False


class SpecialistTaskInput(SpecialistContract):
    schema_version: Literal["specialist_task_input_v1"] = "specialist_task_input_v1"
    task_id: UUID
    parent: SpecialistParent
    child_version: SpecialistVersion
    specialist: Literal["knowledge", "diagnostic", "ticket_analysis"]
    trace: SpecialistTraceBinding
    budget: SpecialistBudgetBinding
    created_at: AwareDatetime
    deadline: AwareDatetime
    question: str = Field(min_length=1, max_length=8192, repr=False)
    resources: tuple[SpecialistResource, ...] = Field(max_length=32)
    evidence: tuple[SpecialistEvidence, ...] = Field(max_length=64, repr=False)
    expected_fact_keys: tuple[FactKey, ...] = Field(max_length=32)
    execution_enabled: Literal[False] = False
    write_enabled: Literal[False] = False
    retry_policy: Literal["no_automatic_retry"] = "no_automatic_retry"

    @model_validator(mode="after")
    def bound_context(self) -> "SpecialistTaskInput":
        if (self.trace.parent_run_id != self.parent.run_id
            or self.trace.parent_attempt_id != self.parent.attempt_id
            or self.trace.child_task_id != self.task_id or self.trace.parent_task_id == self.task_id
            or self.budget.parent_run_id != self.parent.run_id):
            raise ValueError("SPECIALIST_PARENT_BINDING_INVALID")
        if (not self.created_at < self.deadline <= self.parent.authorized_until
            or self.deadline > self.budget.deadline):
            raise ValueError("SPECIALIST_TASK_WINDOW_INVALID")
        if not self.question.strip() or len(self.question.encode("utf-8")) > 8192:
            raise ValueError("SPECIALIST_QUESTION_INVALID")
        resources = {(item.resource_kind, item.resource_id): item for item in self.resources}
        if (len(resources) != len(self.resources)
            or any(item.tenant_id != self.parent.tenant_id
                or item.authorization_scope_digest != self.parent.authorization_scope_digest
                for item in self.resources)
            or len(set(self.expected_fact_keys)) != len(self.expected_fact_keys)
            or len({item.evidence_id for item in self.evidence}) != len(self.evidence)):
            raise ValueError("SPECIALIST_RESOURCE_SCOPE_INVALID")
        for item in self.evidence:
            if (resources.get((item.resource.resource_kind, item.resource.resource_id)) != item.resource
                or item.observed_at > self.created_at or item.valid_until < self.deadline):
                raise ValueError("SPECIALIST_EVIDENCE_SCOPE_INVALID")
        return self


class SpecialistFact(SpecialistContract):
    """结构化观察仍是不可信数据；键相同才比较冲突，不做语义推断。"""

    resource_kind: Literal["knowledge_document", "diagnostic_request", "support_ticket"]
    resource_id: UUID
    key: FactKey
    value: FactValue = Field(repr=False)
    evidence_ids: tuple[UUID, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_evidence(self) -> "SpecialistFact":
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("SPECIALIST_EVIDENCE_DUPLICATED")
        if isinstance(self.value, int) and not isinstance(self.value, bool) and abs(self.value) > 2**53 - 1:
            raise ValueError("SPECIALIST_FACT_INTEGER_INVALID")
        return self


class SpecialistUsage(SpecialistContract):
    """未观察到的用量保留空值，不转成零或释放父级账本预留。"""

    model_calls: Count | None = None
    tool_calls: Count | None = None
    tokens: Count | None = None
    cost_microusd: Count | None = None


class SpecialistTaskOutput(SpecialistContract):
    schema_version: Literal["specialist_task_output_v1"] = "specialist_task_output_v1"
    task_id: UUID
    input_digest: Digest
    parent: SpecialistParent
    child_version: SpecialistVersion
    trace: SpecialistTraceBinding
    budget_scope_digest: Digest
    status: Literal["completed", "needs_input", "failed", "cancelled"]
    finished_at: AwareDatetime
    facts: tuple[SpecialistFact, ...] = Field(default=(), max_length=32, repr=False)
    analysis: str = Field(default="", max_length=4096, repr=False)
    missing_fact_keys: tuple[FactKey, ...] = Field(default=(), max_length=32)
    failure_code: FailureCode | None = None
    usage: SpecialistUsage
    business_status_confirmed: Literal[False] = False
    execution_permit: Literal[False] = False

    @model_validator(mode="after")
    def consistent_status(self) -> "SpecialistTaskOutput":
        if self.status in {"completed", "needs_input"}:
            if self.failure_code is not None or bool(self.missing_fact_keys) != (self.status == "needs_input"):
                raise ValueError("SPECIALIST_RESULT_STATUS_INVALID")
        elif (self.failure_code is None or self.facts or self.analysis or self.missing_fact_keys
            or (self.status == "cancelled") != (self.failure_code == "CANCELLED")):
            raise ValueError("SPECIALIST_RESULT_STATUS_INVALID")
        if len(set(self.missing_fact_keys)) != len(self.missing_fact_keys):
            raise ValueError("SPECIALIST_FACT_KEY_DUPLICATED")
        return self


def validate_specialist_result(task: SpecialistTaskInput, result: SpecialistTaskOutput) -> None:
    """纯契约绑定检查；未来执行/提交边界仍须重新查当前权限、版本和账本。"""
    if (result.task_id != task.task_id or result.input_digest != contract_digest(task)
        or result.parent != task.parent or result.child_version != task.child_version
        or result.trace != task.trace or result.budget_scope_digest != contract_digest(task.budget)
        or result.finished_at < task.created_at):
        raise ValueError("SPECIALIST_RESULT_BINDING_INVALID")
    if result.finished_at > task.deadline and result.status in {"completed", "needs_input"}:
        raise ValueError("SPECIALIST_RESULT_DEADLINE_INVALID")
    evidence = {item.evidence_id: item for item in task.evidence}
    keys: set[tuple[str, UUID, str]] = set()
    for fact in result.facts:
        key = (fact.resource_kind, fact.resource_id, fact.key)
        if key in keys or fact.key not in task.expected_fact_keys:
            raise ValueError("SPECIALIST_RESULT_FACT_INVALID")
        keys.add(key)
        for evidence_id in fact.evidence_ids:
            item = evidence.get(evidence_id)
            if item is None or (item.resource.resource_kind, item.resource.resource_id) != key[:2]:
                raise ValueError("SPECIALIST_RESULT_EVIDENCE_INVALID")
    if not set(result.missing_fact_keys) <= set(task.expected_fact_keys):
        raise ValueError("SPECIALIST_RESULT_FACT_INVALID")
    if result.status in {"completed", "needs_input"}:
        observed = {fact.key for fact in result.facts}
        missing = set(result.missing_fact_keys)
        if observed & missing or observed | missing != set(task.expected_fact_keys):
            raise ValueError("SPECIALIST_RESULT_FACT_COVERAGE_INVALID")
    for name in ("model_calls", "tool_calls", "tokens", "cost_microusd"):
        usage = getattr(result.usage, name)
        if usage is not None and usage > getattr(task.budget, name + "_limit"):
            raise ValueError("SPECIALIST_RESULT_BUDGET_INVALID")


class SpecialistFailure(SpecialistContract):
    task_id: UUID
    status: Literal["needs_input", "failed", "cancelled", "missing"]
    failure_code: FailureCode | None = None
    missing_fact_keys: tuple[FactKey, ...] = Field(default=(), max_length=32)


class SpecialistConflict(SpecialistContract):
    resource_kind: Literal["knowledge_document", "diagnostic_request", "support_ticket"]
    resource_id: UUID
    fact_key: FactKey
    task_ids: tuple[UUID, ...] = Field(min_length=2, max_length=32)


class SpecialistAggregate(SpecialistContract):
    schema_version: Literal["specialist_task_aggregate_v1"] = "specialist_task_aggregate_v1"
    parent: SpecialistParent
    status: Literal["ready_for_parent_review", "needs_input", "needs_attention"]
    task_input_digests: tuple[Digest, ...] = Field(min_length=1, max_length=32)
    result_digests: tuple[Digest, ...] = Field(max_length=32)
    failures: tuple[SpecialistFailure, ...] = Field(max_length=32)
    conflicts: tuple[SpecialistConflict, ...] = Field(max_length=1024)
    business_status_confirmed: Literal[False] = False
    execution_permit: Literal[False] = False


def aggregate_specialist_results(tasks: tuple[SpecialistTaskInput, ...],
    results: tuple[SpecialistTaskOutput, ...],
) -> SpecialistAggregate:
    """确定排序、保留缺失/失败及显式事实冲突，不投票、不选择获胜结论。"""
    if not 1 <= len(tasks) <= 32 or len(results) > len(tasks):
        raise ValueError("SPECIALIST_AGGREGATE_SIZE_INVALID")
    ordered = sorted(tasks, key=lambda item: str(item.task_id))
    parent = ordered[0].parent
    task_map = {item.task_id: item for item in ordered}
    result_map = {item.task_id: item for item in results}
    if (len(task_map) != len(tasks) or len(result_map) != len(results)
        or not result_map.keys() <= task_map.keys() or any(item.parent != parent for item in tasks)
        or len({contract_digest(item.budget) for item in tasks}) != 1
        or len({item.trace.trace_id for item in tasks}) != 1
        or len({item.trace.child_span_id for item in tasks}) != len(tasks)):
        raise ValueError("SPECIALIST_AGGREGATE_SCOPE_INVALID")
    failures: list[SpecialistFailure] = []
    facts: dict[tuple[str, UUID, str], list[tuple[UUID, str]]] = {}
    for task in ordered:
        result = result_map.get(task.task_id)
        if result is None:
            failures.append(SpecialistFailure(task_id=task.task_id, status="missing"))
            continue
        validate_specialist_result(task, result)
        if result.status != "completed":
            failures.append(SpecialistFailure(task_id=task.task_id, status=result.status,
                failure_code=result.failure_code, missing_fact_keys=result.missing_fact_keys))
        for fact in result.facts:
            key = (fact.resource_kind, fact.resource_id, fact.key)
            value = json.dumps(fact.value, ensure_ascii=False, separators=(",", ":"))
            facts.setdefault(key, []).append((task.task_id, value))
    conflicts = tuple(SpecialistConflict(resource_kind=key[0], resource_id=key[1], fact_key=key[2],
        task_ids=tuple(item[0] for item in values))
        for key, values in sorted(facts.items(), key=lambda item: (item[0][0], str(item[0][1]), item[0][2]))
        if len({item[1] for item in values}) > 1)
    status: Literal["ready_for_parent_review", "needs_input", "needs_attention"] = "ready_for_parent_review"
    if conflicts or any(item.status != "needs_input" for item in failures):
        status = "needs_attention"
    elif failures:
        status = "needs_input"
    return SpecialistAggregate(parent=parent, status=status,
        task_input_digests=tuple(contract_digest(item) for item in ordered),
        result_digests=tuple(contract_digest(result_map[item.task_id]) for item in ordered if item.task_id in result_map),
        failures=tuple(failures), conflicts=conflicts)
