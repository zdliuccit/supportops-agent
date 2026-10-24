"""路由完整缓存结果的独立产品批准、实际checkpoint证明与最小提交记录。"""

from typing import Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.model_routing_contracts import Digest, RoutingFilterModel
from supportops_core.model_routing_reservation_contracts import RoutingCallPlan
from supportops_core.model_routing_result_services import RoutingPreparedResultBinding
from supportops_core.release_manifest_services import manifest_digest


class RoutingProductCheckpoint(RoutingFilterModel):
    """可信reader查证的实际完成checkpoint；不接受客户端/模型上传或自行补造。"""

    root_plan_digest: Digest
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1, le=2**31 - 1, strict=True)
    checkpoint_thread_id: str = Field(min_length=1, max_length=300)
    checkpoint_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    input_sequence: int = Field(ge=1, strict=True)
    model_input_digest: Digest
    context_snapshot_digest: Digest
    canonical_answer_digest: Digest
    support_result_digest: Digest
    history_source_digest: Digest
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def bounded_checkpoint(self) -> "RoutingProductCheckpoint":
        if not 0 < (self.expires_at - self.checked_at).total_seconds() <= 1:
            raise ValueError("ROUTING_PRODUCT_CHECKPOINT_WINDOW_INVALID")
        return self


class RoutingProductCommitRequest(RoutingFilterModel):
    operation_id: UUID
    message_id: UUID
    plan: RoutingCallPlan = Field(repr=False)
    prepared: RoutingPreparedResultBinding
    checkpoint: RoutingProductCheckpoint

    @model_validator(mode="after")
    def exact_product(self) -> "RoutingProductCommitRequest":
        plan, prepared, checkpoint = self.plan, self.prepared, self.checkpoint
        digest = manifest_digest(plan.model_dump(mode="json"))
        if (self.operation_id != uuid5(plan.call_id, "routing-product-commit-v1")
            or self.message_id != uuid5(plan.call_id, "routing-product-message-v1")
            or prepared.root_call_id != plan.call_id or prepared.root_plan_digest != digest
            or checkpoint.root_plan_digest != digest or checkpoint.run_id != plan.run_id
            or checkpoint.attempt_id != plan.attempt_id or checkpoint.generation != plan.generation
            or checkpoint.model_input_digest != plan.model_input_digest
            or checkpoint.context_snapshot_digest != plan.context_snapshot_digest
            or checkpoint.canonical_answer_digest != prepared.canonical_answer_digest
            or checkpoint.support_result_digest != prepared.support_result_digest
            or checkpoint.history_source_digest != prepared.history_source_digest):
            raise ValueError("ROUTING_PRODUCT_REQUEST_INVALID")
        return self


class RoutingProductCommitPermit(RoutingFilterModel):
    request_digest: Digest
    actor_user_id: UUID
    governance_reference_digest: Digest
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def bounded_permit(self) -> "RoutingProductCommitPermit":
        if not 0 < (self.expires_at - self.checked_at).total_seconds() <= 1:
            raise ValueError("ROUTING_PRODUCT_APPROVAL_WINDOW_INVALID")
        return self


class RoutingProductCommitRecord(RoutingFilterModel):
    """仅已提交事务可读取；recorded_at不冒充精确数据库commit时间。"""

    schema_version: Literal["routing_product_commit_v1"] = "routing_product_commit_v1"
    operation_id: UUID
    message_id: UUID
    tenant_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1, strict=True)
    root_call_id: UUID
    final_call_id: UUID
    actual_model_version_id: UUID
    actor_user_id: UUID
    root_plan_digest: Digest
    prepared_digest: Digest
    support_result_digest: Digest
    checkpoint_digest: Digest
    checkpoint_thread_id: str = Field(min_length=1, max_length=300)
    checkpoint_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    request_digest: Digest
    permit_digest: Digest
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def original_ids(self) -> "RoutingProductCommitRecord":
        if (self.operation_id != uuid5(self.root_call_id, "routing-product-commit-v1")
            or self.message_id != uuid5(self.root_call_id, "routing-product-message-v1")):
            raise ValueError("ROUTING_PRODUCT_RECORD_INVALID")
        return self
