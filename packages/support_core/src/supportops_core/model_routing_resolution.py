"""v3模型固定引用的只读解析；不保存草稿、清单、评测或执行许可。"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import TOOL_REGISTRY, AgentConfigV2, agent_config_digest, parse_agent_config
from supportops_core.agent_services import AgentConfigurationError, ResolvedAgentConfig, get_agent, validate_agent_config
from supportops_core.auth import IdentityContext
from supportops_core.config import Settings
from supportops_core.enums import ModelApiProtocol, ModelTestStatus
from supportops_core.model_config import validate_model_extensions
from supportops_core.model_routing_config_v3 import (
    AgentConfigV3, FixedModelBindingV3, ResolvedModelReferenceV3, RoutingConfigResolution,
    RoutingConfigResolutionView, RoutingConfigResolveRequest, RoutingResolutionIssue, VersionedRoutingBindingV3,
)
from supportops_core.model_routing_contracts import RoutingCallRequirements, RoutingCandidateFilterReport
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader, filter_routing_candidates
from supportops_core.model_routing_policy_services import (
    RoutingPolicyConflict, read_routing_policy_version_by_id, require_routing_administrator,
)
from supportops_core.model_services import model_version_digest
from supportops_core.models import AgentVersion, ModelEndpointTestRun, ModelEndpointVersion, utc_now
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.services import ResourceNotFoundError


class RoutingResolutionUnavailable(RuntimeError):
    code = "ROUTING_RESOLUTION_UNCONFIRMED"

    def __init__(self) -> None:
        super().__init__("配置引用暂不可确认，请保持原配置和版本标识，只读重新解析")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _base_version(session: AsyncSession, identity: IdentityContext,
    command: RoutingConfigResolveRequest,
) -> AgentVersion:
    await get_agent(session, agent_id=command.agent_id, tenant_id=identity.principal.tenant_id)
    row = await session.scalar(select(AgentVersion).where(AgentVersion.id == command.base_agent_version_id,
        AgentVersion.agent_id == command.agent_id, AgentVersion.tenant_id == identity.principal.tenant_id)
        .execution_options(populate_existing=True))
    if row is None:
        raise ResourceNotFoundError("原Agent版本不存在")
    try:
        if (row.schema_version != "2" or row.withdrawn_at is not None
            or row.config_digest != command.expected_base_config_digest
            or agent_config_digest(parse_agent_config(row.config)) != row.config_digest):
            raise ValueError()
    except (TypeError, ValueError, ValidationError):
        raise RoutingPolicyConflict("ROUTING_BASE_VERSION_CHANGED") from None
    return row


def project_v3_model_validation(config: AgentConfigV3, *, endpoint_id: UUID, endpoint_model_id: UUID) -> AgentConfigV2:
    # 只在内存中复用原模型/工具治理；不写回配置，也不把v3作为v2发布。
    payload = config.model_dump(mode="json", exclude={"schema_version", "model"})
    payload.update({"schema_version": "2", "model": {
        "model_endpoint_id": str(endpoint_id), "model_endpoint_model_id": str(endpoint_model_id),
        "generation": config.model.generation.model_dump(mode="json"),
    }})
    return parse_agent_config(payload)


def _generation_supported(config: AgentConfigV3, version: ModelEndpointVersion) -> bool:
    defaults = version.defaults
    generation = config.model.generation
    if not isinstance(defaults, dict):
        return False
    if (generation.reasoning_effort is not None and generation.reasoning_effort != defaults.get("reasoning_effort")
        or generation.verbosity is not None and (version.api_protocol != ModelApiProtocol.RESPONSES
            or generation.verbosity != defaults.get("verbosity"))):
        return False
    extensions = defaults.get("extension_options", {})
    if not isinstance(extensions, dict):
        return False
    try:
        validate_model_extensions(extensions)
    except ValueError:
        return False
    return True


def _reference(version: ModelEndpointVersion, revision: int, probe_id: UUID,
    candidate_id: UUID | None = None,
) -> ResolvedModelReferenceV3:
    return ResolvedModelReferenceV3(candidate_id=candidate_id, endpoint_id=version.endpoint_id,
        endpoint_model_id=version.endpoint_model_id, model_version_id=version.id,
        credential_revision_at_resolution=revision, model_request_digest=model_version_digest(version, revision),
        capability_digest=manifest_digest(version.capabilities), pricing_digest=manifest_digest(version.pricing),
        latest_probe_id=probe_id)


async def _fixed_probe(session: AsyncSession, *, tenant_id: UUID, version: ModelEndpointVersion,
    revision: int, config: AgentConfigV3,
) -> UUID | None:
    row = (await session.execute(select(ModelEndpointTestRun.id, ModelEndpointTestRun.endpoint_id,
        ModelEndpointTestRun.model_version_id, ModelEndpointTestRun.credential_revision,
        ModelEndpointTestRun.config_digest, ModelEndpointTestRun.status, ModelEndpointTestRun.completed_at)
        .where(ModelEndpointTestRun.tenant_id == tenant_id,
            ModelEndpointTestRun.endpoint_model_id == version.endpoint_model_id,
            ModelEndpointTestRun.deleted_at.is_(None))
        .order_by(ModelEndpointTestRun.created_at.desc(), ModelEndpointTestRun.id.desc()).limit(1))).one_or_none()
    if (row is None or row.endpoint_id != version.endpoint_id or row.model_version_id != version.id
        or row.credential_revision != revision or row.config_digest != model_version_digest(version, revision)
        or row.status != ModelTestStatus.PASSED or row.completed_at is None or _utc(row.completed_at) > utc_now()):
        return None
    verification = version.verification_result if isinstance(version.verification_result, dict) else {}
    checks = verification.get("checks")
    required = {"streaming", "tool_calling" if config.runtime.response_strategy.value == "tool" else "structured_output"}
    if any(tool.enabled for tool in config.tools):
        required.add("tool_calling")
    if (verification.get("test_run_id") != str(row.id) or not isinstance(checks, dict)
        or any(version.capabilities.get(name) is not True or checks.get(name) != "passed" for name in required)):
        return None
    return row.id


async def resolve_v3_config(session: AsyncSession, identity: IdentityContext, *,
    command: RoutingConfigResolveRequest, settings: Settings,
    boundary_reader: RoutingDataBoundaryReader | None = None,
) -> RoutingConfigResolutionView:
    """观察完整候选，不删掉失败项；默认API不注入数据批准reader，不产生发布许可。"""
    # 不共享调用方可变模型对象；报告绑定本次解析的完整规范输入。
    command = RoutingConfigResolveRequest.model_validate(command.model_dump(mode="json"))
    config = command.config
    async with asyncio.timeout(20):
        await require_routing_administrator(session, identity)
        base = await _base_version(session, identity, command)
        tenant_id = identity.principal.tenant_id
        issues: list[RoutingResolutionIssue] = []
        models: list[ResolvedModelReferenceV3] = []
        tools = {tool.tool_id: TOOL_REGISTRY[tool.tool_id].version for tool in config.tools
            if tool.enabled and tool.tool_id in TOOL_REGISTRY}
        runtime = config.runtime
        if (runtime.model_call_limit > settings.agent_model_call_limit_max
            or runtime.tool_call_limit > settings.agent_tool_call_limit_max
            or runtime.run_timeout_seconds > settings.agent_run_timeout_seconds_max
            or runtime.max_parallel_tools > settings.agent_max_parallel_tools_max):
            issues.append(RoutingResolutionIssue(code="PLATFORM_LIMIT_EXCEEDED"))
        policy_id: UUID | None = None
        policy_digest: str | None = None
        candidate_set_digest: str | None = None
        candidate_filter: RoutingCandidateFilterReport | None = None
        model = config.model
        if isinstance(model, FixedModelBindingV3):
            try:
                checked = await validate_agent_config(session, tenant_id=tenant_id,
                    config=project_v3_model_validation(config, endpoint_id=model.model_endpoint_id,
                        endpoint_model_id=model.model_endpoint_model_id).model_dump(mode="json"),
                    settings=settings, fixed_model_version_id=model.model_endpoint_version_id, fixed_tool_versions=tools)
            except AgentConfigurationError:
                issues.append(RoutingResolutionIssue(code="FIXED_MODEL_UNAVAILABLE"))
            else:
                probe_id = await _fixed_probe(session, tenant_id=tenant_id, version=checked.model_version,
                    revision=checked.credential_revision, config=config)
                if probe_id is None:
                    issues.append(RoutingResolutionIssue(code="LATEST_PROBE_UNVERIFIED"))
                if not _generation_supported(config, checked.model_version):
                    issues.append(RoutingResolutionIssue(code="GENERATION_OPTION_UNCONFIRMED"))
                if probe_id is not None:
                    models.append(_reference(checked.model_version, checked.credential_revision, probe_id))
        elif isinstance(model, VersionedRoutingBindingV3):
            policy_id, policy_digest = model.policy_version_id, model.policy_digest
            policy = await read_routing_policy_version_by_id(session, identity, version_id=policy_id)
            if policy.policy_digest != policy_digest:
                raise RoutingPolicyConflict("ROUTING_POLICY_SCOPE_MISMATCH")
            definition = policy.record.definition
            candidate_set_digest = manifest_digest([row.model_dump(mode="json") for row in definition.candidates])
            if policy.status != "candidate":
                issues.append(RoutingResolutionIssue(code="POLICY_NOT_USABLE"))
            if (model.purpose not in definition.allowed_purposes
                or not set(model.allowed_regions) <= set(definition.allowed_regions)
                or model.data_classification not in definition.allowed_data_classifications):
                issues.append(RoutingResolutionIssue(code="POLICY_SCOPE_MISMATCH"))
            if not issues:
                # 路由Schema要求两个上限非空；这里只转换规范输入，不推测实际Token或费用。
                if runtime.max_input_tokens is None or runtime.max_cost_usd is None:
                    raise RoutingPolicyConflict("ROUTING_CONFIG_INVALID")
                cost_limit = int(Decimal(str(runtime.max_cost_usd)) * 1_000_000)
                requirements = RoutingCallRequirements(tenant_id=tenant_id, agent_version_id=base.id,
                    policy_version_id=policy_id, policy_digest=policy_digest,
                    reserved_input_tokens=runtime.max_input_tokens,
                    reserved_output_tokens=model.generation.max_output_tokens,
                    response_strategy=runtime.response_strategy.value, uses_tools=any(tool.enabled for tool in config.tools),
                    streaming=True, allowed_regions=model.allowed_regions,
                    data_classification=model.data_classification, max_call_cost_microusd=cost_limit)
                checked_models: dict[UUID, ResolvedAgentConfig] = {}
                for candidate in definition.candidates:
                    try:
                        checked = await validate_agent_config(session, tenant_id=tenant_id,
                            config=project_v3_model_validation(config, endpoint_id=candidate.endpoint_id,
                                endpoint_model_id=candidate.endpoint_model_id).model_dump(mode="json"),
                            settings=settings, fixed_model_version_id=candidate.model_version_id, fixed_tool_versions=tools)
                    except AgentConfigurationError:
                        issues.append(RoutingResolutionIssue(code="CANDIDATE_CONFIG_INVALID", candidate_id=candidate.candidate_id))
                        continue
                    if checked.credential_revision != candidate.credential_revision:
                        issues.append(RoutingResolutionIssue(code="REFERENCES_CHANGED", candidate_id=candidate.candidate_id))
                    if not _generation_supported(config, checked.model_version):
                        issues.append(RoutingResolutionIssue(code="GENERATION_OPTION_UNCONFIRMED", candidate_id=candidate.candidate_id))
                    checked_models[candidate.candidate_id] = checked
                candidate_filter = await filter_routing_candidates(session, requirements=requirements,
                    candidates=definition.candidates, boundary_reader=boundary_reader)
                assessments = {row.candidate_id: row for row in candidate_filter.candidates}
                if any(row.status != "eligible" for row in candidate_filter.candidates):
                    issues.append(RoutingResolutionIssue(code="CANDIDATES_EXCLUDED"))
                if not issues:
                    for candidate in definition.candidates:
                        checked = checked_models[candidate.candidate_id]
                        assessment = assessments[candidate.candidate_id]
                        if assessment.latest_probe_id is None:
                            raise RoutingPolicyConflict("ROUTING_RESOLUTION_UNCONFIRMED")
                        models.append(_reference(checked.model_version, checked.credential_revision,
                            assessment.latest_probe_id, candidate.candidate_id))
                current = await read_routing_policy_version_by_id(session, identity, version_id=policy_id)
                if current.policy_digest != policy_digest or current.status != "candidate":
                    issues.append(RoutingResolutionIssue(code="REFERENCES_CHANGED"))
        await _base_version(session, identity, command)
        await require_routing_administrator(session, identity)
        resolution = RoutingConfigResolution(tenant_id=tenant_id, agent_id=command.agent_id,
            base_agent_version_id=base.id, base_config_digest=base.config_digest,
            config_digest=manifest_digest(config.model_dump(mode="json")), mode=model.mode,
            policy_version_id=policy_id, policy_digest=policy_digest, candidate_set_digest=candidate_set_digest,
            status="blocked" if issues else "resolved", issues=tuple(issues), candidate_filter=candidate_filter,
            resolved_models=None if issues else tuple(models), resolved_tool_versions={} if issues else tools,
            observed_at=utc_now())
        return RoutingConfigResolutionView(resolution=resolution,
            resolution_digest=manifest_digest(resolution.model_dump(mode="json")))
