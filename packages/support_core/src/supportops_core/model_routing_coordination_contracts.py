"""有限路由协调的完整物理历史观察；不接受客户端/模型上传或授予执行许可。"""

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.model_routing_contracts import Digest, RoutingFilterModel
from supportops_core.model_routing_failover_contracts import RoutingFailoverAttempt


class RoutingExecutionHistory(RoutingFilterModel):
    schema_version: Literal["routing_execution_history_v1"] = "routing_execution_history_v1"
    root_plan_digest: Digest
    source_reference_digest: Digest
    attempts: tuple[RoutingFailoverAttempt, ...] = Field(min_length=1, max_length=3)
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def bounded_complete_history(self) -> "RoutingExecutionHistory":
        if (not 0 < (self.expires_at - self.checked_at).total_seconds() <= 1
            or [row.ordinal for row in self.attempts] != list(range(1, len(self.attempts) + 1))
            or len({row.accounting.reservation.call_id for row in self.attempts}) != len(self.attempts)
            or len({row.accounting.reservation.candidate_id for row in self.attempts}) != len(self.attempts)
            or len({row.root_call_id for row in self.attempts}) != 1):
            raise ValueError("ROUTING_COORDINATION_HISTORY_INVALID")
        return self
