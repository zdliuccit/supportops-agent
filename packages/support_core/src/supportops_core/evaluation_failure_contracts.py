"""评测失败事实与人工处置契约；人工诊断不修改评分或授予发布许可。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from supportops_core.agent_config import StrictModel
from supportops_core.release_manifest_services import AgentReleaseComparison

FailureReason = Literal[
    "EXECUTION_INCOMPLETE", "FACT_INCORRECT", "CITATIONS_INVALID", "TOOL_ROUTING_INVALID",
    "ESCALATION_PACKAGE_INVALID", "TARGET_STATE_INVALID", "STRUCTURED_RESULT_INVALID",
    "USAGE_UNAVAILABLE", "COST_UNAVAILABLE", "TIMING_UNAVAILABLE", "SECURITY_OBSERVATION_MISSING",
    "SECURITY_CONTROL_MISMATCH", "SECURITY_CONTROL_FAILED", "UNAUTHORIZED_SIDE_EFFECT",
    "SENSITIVE_CANARY_LEAKED", "EXTERNAL_NETWORK_REQUEST",
]
CauseCode = Literal[
    "authorization", "evidence_validation", "output_schema", "tool_routing", "target_state",
    "dependency", "checkpoint", "product_commit", "streaming", "usage_accounting",
    "timing_instrumentation", "evaluation_fixture", "prompt_policy", "unknown",
]


class EvaluationFailure(StrictModel):
    case_id: str = Field(pattern=r"^(?:S(?:0[1-9]|1[0-9]|20)-(?:positive|boundary|dependency_failure)|SEC-(?:0[1-9]|1[0-8]))$")
    category: Literal["functional", "security"]
    execution_status: Literal["completed", "unknown", "cancelled", "failed"]
    reason_codes: list[FailureReason] = Field(min_length=1, max_length=16)
    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    assessment_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_failure(self) -> "EvaluationFailure":
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("失败原因不能重复")
        if self.case_id.startswith("SEC-") != (self.category == "security"):
            raise ValueError("失败类别与样本 ID 不匹配")
        return self


class EvaluationFailureReviewInput(StrictModel):
    expected_revision: int = Field(ge=0, le=512, strict=True)
    report_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    finding_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_user_id: UUID
    cause_status: Literal["unknown", "suspected", "confirmed"]
    cause_code: CauseCode
    evidence_digests: list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", max_length=64)]] = Field(
        default_factory=list, max_length=16)
    decision: Literal["investigate", "fix", "rollback", "block_release"]
    remediation_reference_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rollback_version_id: UUID | None = None

    @model_validator(mode="after")
    def valid_review(self) -> "EvaluationFailureReviewInput":
        import re

        if (len(set(self.evidence_digests)) != len(self.evidence_digests)
            or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in self.evidence_digests)):
            raise ValueError("诊断证据摘要无效或重复")
        if (self.cause_status == "unknown") != (self.cause_code == "unknown"):
            raise ValueError("未知根因须保持明确的 unknown 状态")
        if self.cause_status == "confirmed" and not self.evidence_digests:
            raise ValueError("确认根因必须附证据摘要")
        if (self.decision == "rollback") != (self.rollback_version_id is not None):
            raise ValueError("回滚决定必须指定同一 Agent 的固定版本")
        if self.decision == "fix" and self.remediation_reference_digest is None:
            raise ValueError("修复决定必须关联修复记录摘要")
        return self


class EvaluationFailureReview(StrictModel):
    schema_version: Literal["evaluation_failure_review_v1"] = "evaluation_failure_review_v1"
    evaluation_id: UUID
    case_id: str = Field(max_length=64)
    revision: int = Field(ge=1, le=512, strict=True)
    previous_digest: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    command: EvaluationFailureReviewInput
    reviewer_user_id: UUID
    reviewed_at: datetime
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationFailureDetail(StrictModel):
    finding: EvaluationFailure
    finding_digest: str
    review: EvaluationFailureReview | None = None


class EvaluationFailureDocument(StrictModel):
    schema_version: Literal["evaluation_failure_document_v1"] = "evaluation_failure_document_v1"
    evaluation_id: UUID
    execution_id: UUID
    agent_id: UUID
    version_id: UUID
    report_digest: str
    recorded_at: datetime
    findings_status: Literal["recorded", "legacy_unrecorded"]
    # 最多 78 条固定样本的完整报告组成部分，不是普通扁平资源列表。
    failures: list[EvaluationFailureDetail] = Field(max_length=78)
    version_comparison: AgentReleaseComparison
    diagnosis_is_release_approval: Literal[False] = False
