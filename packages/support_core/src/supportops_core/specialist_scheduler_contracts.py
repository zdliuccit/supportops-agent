"""专业子任务调度边界；范围声明和调度报告不授予执行或业务提交许可。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.specialist_task_contracts import (
    Digest, SpecialistAggregate, SpecialistContract, SpecialistTaskInput, SpecialistTaskOutput, contract_digest,
)


class SpecialistScheduleLimits(SpecialistContract):
    max_tasks: int = Field(default=8, ge=1, le=32)
    max_depth: int = Field(default=2, ge=1, le=4)
    max_parallel: int = Field(default=3, ge=1, le=8)
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class SpecialistSchedule(SpecialistContract):
    schema_version: Literal["specialist_schedule_v1"] = "specialist_schedule_v1"
    batch_id: UUID
    tasks: tuple[SpecialistTaskInput, ...] = Field(min_length=1, max_length=32, repr=False)
    limits: SpecialistScheduleLimits = Field(default_factory=SpecialistScheduleLimits)
    created_at: AwareDatetime
    deadline: AwareDatetime
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def fixed_window(self) -> "SpecialistSchedule":
        if not 0 < (self.deadline - self.created_at).total_seconds() <= self.limits.timeout_seconds:
            raise ValueError("SPECIALIST_SCHEDULE_WINDOW_INVALID")
        return self


class SpecialistDispatchClaim(SpecialistContract):
    """仅可信持久化准入服务返回；调用方不能上传本结构绕过原授权/共享预算。"""

    schema_version: Literal["specialist_dispatch_claim_v1"] = "specialist_dispatch_claim_v1"
    dispatch_id: UUID
    task_id: UUID
    task_input_digest: Digest
    authorization_scope_digest: Digest
    budget_scope_digest: Digest
    budget_reservation_digest: Digest
    governance_decision_digest: Digest
    claimed_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def claim_window(self) -> "SpecialistDispatchClaim":
        if not self.claimed_at < self.expires_at:
            raise ValueError("SPECIALIST_DISPATCH_WINDOW_INVALID")
        return self


ScheduleIssueCode = Literal[
    "DEPENDENCIES_UNAVAILABLE", "COORDINATOR_BUSY", "TASK_LIMIT_EXCEEDED", "DEPTH_LIMIT_EXCEEDED",
    "DELEGATION_CYCLE", "ANCESTOR_VERSION_REPEATED", "PARENT_TASK_MISSING", "SCOPE_MISMATCH",
    "CONTRACT_REJECTED", "DEADLINE_EXCEEDED", "CALLBACK_UNCONFIRMED",
]


class SpecialistScheduleIssue(SpecialistContract):
    code: ScheduleIssueCode
    task_id: UUID | None = None


class SpecialistScheduleReport(SpecialistContract):
    schema_version: Literal["specialist_schedule_report_v1"] = "specialist_schedule_report_v1"
    batch_id: UUID
    schedule_digest: Digest
    status: Literal["blocked", "collected", "unconfirmed"]
    next_action: Literal["parent_review", "request_input", "human_attention"] = "human_attention"
    issues: tuple[SpecialistScheduleIssue, ...] = Field(default=(), max_length=64)
    results: tuple[SpecialistTaskOutput, ...] = Field(default=(), max_length=32, repr=False)
    aggregate: SpecialistAggregate | None = None
    executor_entered_task_ids: tuple[UUID, ...] = Field(default=(), max_length=32)
    unfinished_task_ids: tuple[UUID, ...] = Field(default=(), max_length=32)
    observed_at: AwareDatetime
    runtime_enabled: Literal[False] = False
    business_status_confirmed: Literal[False] = False
    execution_permit: Literal[False] = False

    @model_validator(mode="after")
    def bounded_report(self) -> "SpecialistScheduleReport":
        if self.status == "blocked":
            if (not self.issues or self.results or self.aggregate is not None
                or self.executor_entered_task_ids or self.unfinished_task_ids or self.next_action != "human_attention"):
                raise ValueError("SPECIALIST_BLOCKED_REPORT_INVALID")
            return self
        aggregate = self.aggregate
        ids = {row.task_id for row in self.results}
        if (aggregate is None or not self.results or len(ids) != len(self.results)
            or len(set(self.executor_entered_task_ids)) != len(self.executor_entered_task_ids)
            or len(set(self.unfinished_task_ids)) != len(self.unfinished_task_ids)
            or not set(self.executor_entered_task_ids) <= ids or not set(self.unfinished_task_ids) <= ids
            or (self.status == "unconfirmed") != bool(self.unfinished_task_ids)
            or any(row.parent != aggregate.parent for row in self.results)
            or aggregate.result_digests != tuple(contract_digest(row) for row in sorted(self.results, key=lambda item: str(item.task_id)))):
            raise ValueError("SPECIALIST_SCHEDULE_REPORT_BINDING_INVALID")
        expected = "human_attention" if self.status == "unconfirmed" else {
            "ready_for_parent_review": "parent_review", "needs_input": "request_input", "needs_attention": "human_attention",
        }[aggregate.status]
        if self.next_action != expected:
            raise ValueError("SPECIALIST_SCHEDULE_NEXT_ACTION_INVALID")
        return self
