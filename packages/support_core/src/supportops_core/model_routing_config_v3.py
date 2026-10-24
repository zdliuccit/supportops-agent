"""独立的v3配置及解析观察契约；当前生产v2解析入口不接受此版本。"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.agent_config import (
    AgentKnowledgeConfig, AgentMemoryConfig, AgentPromptConfig, AgentRuntimeConfig,
    AgentToolBinding, GenerationConfig, StrictModel,
)
from supportops_core.model_routing_contracts import (
    BoundaryLabel, Digest, RoutingCandidateFilterReport, RoutingFilterModel,
)


class FixedModelBindingV3(StrictModel):
    mode: Literal["fixed_model_v1"]
    model_endpoint_id: UUID
    model_endpoint_model_id: UUID
    model_endpoint_version_id: UUID
    generation: GenerationConfig = Field(default_factory=GenerationConfig)


class RoutedGenerationConfig(GenerationConfig):
    # 回退/物理重试尚未接入；不能通过生成配置启用隐式SDK重试。
    max_retries: int = Field(default=0, ge=0, le=0, strict=True)


class VersionedRoutingBindingV3(StrictModel):
    mode: Literal["versioned_routing_v1"]
    policy_version_id: UUID
    policy_digest: Digest
    purpose: BoundaryLabel
    allowed_regions: tuple[BoundaryLabel, ...] = Field(min_length=1, max_length=16)
    data_classification: BoundaryLabel
    generation: RoutedGenerationConfig = Field(default_factory=RoutedGenerationConfig)

    @model_validator(mode="after")
    def unique_regions(self) -> "VersionedRoutingBindingV3":
        if len(set(self.allowed_regions)) != len(self.allowed_regions):
            raise ValueError("路由配置地域不能重复")
        return self


class AgentConfigV3(StrictModel):
    schema_version: Literal["3"]
    prompt: AgentPromptConfig
    model: Annotated[FixedModelBindingV3 | VersionedRoutingBindingV3, Field(discriminator="mode")]
    tools: list[AgentToolBinding] = Field(default_factory=list, max_length=30)
    runtime: AgentRuntimeConfig = Field(default_factory=AgentRuntimeConfig)
    knowledge: AgentKnowledgeConfig = Field(default_factory=AgentKnowledgeConfig)
    memory: AgentMemoryConfig = Field(default_factory=AgentMemoryConfig)

    @model_validator(mode="after")
    def bounded_configuration(self) -> "AgentConfigV3":
        if len({tool.tool_id for tool in self.tools}) != len(self.tools):
            raise ValueError("工具绑定不能重复")
        if isinstance(self.model, VersionedRoutingBindingV3) and (
            self.runtime.max_input_tokens is None or self.runtime.max_cost_usd is None
        ):
            raise ValueError("路由配置必须显式固定完整输入上限及Run费用上限")
        return self


def parse_agent_config_v3(value: object) -> AgentConfigV3:
    """只供显式v3解析观察调用；不替换v2解析、摘要或发布入口。"""
    return AgentConfigV3.model_validate(value)


class RoutingConfigResolveRequest(StrictModel):
    agent_id: UUID
    base_agent_version_id: UUID
    expected_base_config_digest: Digest
    config: AgentConfigV3


class RoutingCandidatePublishRequest(StrictModel):
    version_id: UUID
    expected_draft_revision: int = Field(ge=1, le=2**31 - 1, strict=True)
    expected_latest_version_number: int = Field(ge=0, le=2**31 - 2, strict=True)
    request: RoutingConfigResolveRequest
    release_notes: str = Field(default="", max_length=2000)


ResolutionIssueCode = Literal[
    "POLICY_NOT_USABLE", "POLICY_SCOPE_MISMATCH", "CANDIDATES_EXCLUDED",
    "CANDIDATE_CONFIG_INVALID", "FIXED_MODEL_UNAVAILABLE", "LATEST_PROBE_UNVERIFIED",
    "GENERATION_OPTION_UNCONFIRMED", "REFERENCES_CHANGED", "PLATFORM_LIMIT_EXCEEDED",
]


class RoutingResolutionIssue(RoutingFilterModel):
    code: ResolutionIssueCode
    candidate_id: UUID | None = None


class ResolvedModelReferenceV3(RoutingFilterModel):
    candidate_id: UUID | None = None
    endpoint_id: UUID
    endpoint_model_id: UUID
    model_version_id: UUID
    credential_revision_at_resolution: int = Field(ge=1, strict=True)
    model_request_digest: Digest
    capability_digest: Digest
    pricing_digest: Digest
    latest_probe_id: UUID
    credential_binding: Literal["recheck_before_run_and_dispatch"] = "recheck_before_run_and_dispatch"


class RoutingConfigResolution(RoutingFilterModel):
    schema_version: Literal["routing_config_resolution_v1"] = "routing_config_resolution_v1"
    tenant_id: UUID
    agent_id: UUID
    base_agent_version_id: UUID
    base_config_digest: Digest
    config_digest: Digest
    mode: Literal["fixed_model_v1", "versioned_routing_v1"]
    policy_version_id: UUID | None = None
    policy_digest: Digest | None = None
    candidate_set_digest: Digest | None = None
    status: Literal["resolved", "blocked"]
    issues: tuple[RoutingResolutionIssue, ...] = Field(max_length=64)
    candidate_filter: RoutingCandidateFilterReport | None = None
    resolved_models: tuple[ResolvedModelReferenceV3, ...] | None = Field(default=None, min_length=1, max_length=8)
    resolved_tool_versions: dict[str, int] = Field(default_factory=dict, max_length=30)
    observed_at: AwareDatetime
    approvals_verified: Literal[False] = False
    publication_permitted: Literal[False] = False
    routing_enabled: Literal[False] = False
    budget_reserved: Literal[False] = False
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def complete_set(self) -> "RoutingConfigResolution":
        if (self.status == "resolved") != (self.resolved_models is not None and not self.issues):
            raise ValueError("解析成功必须有完整固定引用且没有阻断项")
        if self.status == "blocked" and (self.resolved_models is not None or self.resolved_tool_versions):
            raise ValueError("阻断观察不得伪造可用解析集合")
        if self.mode == "fixed_model_v1":
            if any(value is not None for value in (
                self.policy_version_id, self.policy_digest, self.candidate_set_digest, self.candidate_filter,
            )) or self.resolved_models is not None and (
                len(self.resolved_models) != 1 or self.resolved_models[0].candidate_id is not None
            ):
                raise ValueError("固定模式只能返回一个原模型且不含策略观察")
        else:
            if self.policy_version_id is None or self.policy_digest is None or self.candidate_set_digest is None:
                raise ValueError("路由解析必须保留原策略及完整候选摘要")
            report = self.candidate_filter
            if report is not None and (
                report.tenant_id != self.tenant_id or report.agent_version_id != self.base_agent_version_id
                or report.policy_version_id != self.policy_version_id or report.policy_digest != self.policy_digest
                or report.candidate_set_digest != self.candidate_set_digest
            ):
                raise ValueError("候选过滤观察须绑定原租户、锚点和策略")
            if self.resolved_models is not None and (
                report is None or any(row.status != "eligible" for row in report.candidates)
                or [(row.candidate_id, row.model_version_id, row.latest_probe_id) for row in self.resolved_models]
                != [(row.candidate_id, row.model_version_id, row.latest_probe_id) for row in report.candidates]
            ):
                raise ValueError("路由成功不能删减或替换原候选过滤集合")
        return self


class RoutingConfigResolutionView(RoutingFilterModel):
    resolution: RoutingConfigResolution
    resolution_digest: Digest
