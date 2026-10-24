"""固定候选的只读资格过滤；不选择模型、不获取秘密、不预留调用预算。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.credential_services import CredentialAccessDenied, assert_credential_revision_active
from supportops_core.enums import ModelTestStatus, ModelVerificationStatus
from supportops_core.model_routing_contracts import (
    RoutingDataBoundaryScope,
    RoutingCallRequirements,
    RoutingCandidateAssessment,
    RoutingCandidateFilterReport,
    RoutingCandidateReference,
    RoutingDataBoundary,
    RoutingExclusionReason,
)
from supportops_core.model_services import model_version_digest
from supportops_core.models import (
    AgentVersion,
    ModelCredential,
    ModelEndpoint,
    ModelEndpointModel,
    ModelEndpointTestRun,
    ModelEndpointVersion,
    utc_now,
)
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.services import ResourceNotFoundError


class RoutingDataBoundaryReader(Protocol):
    """可信治理服务须复查当前批准/撤销，不接受模型或客户端自称获准。"""

    async def read_boundary(
        self, *, requirements: RoutingCallRequirements, candidate: RoutingCandidateReference,
    ) -> RoutingDataBoundary | None: ...


def routing_data_boundary_scope_digest(scope: RoutingDataBoundaryScope) -> str:
    """仅固定原租户/策略版本/模型/处理范围；独立批准仍须当前读取和复核。"""
    canonical = scope.model_copy(update={
        "processing_regions": tuple(sorted(scope.processing_regions)),
        "allowed_data_classifications": tuple(sorted(scope.allowed_data_classifications)),
    })
    return manifest_digest(canonical.model_dump(mode="json"))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _token_limit(value: object) -> int | None:
    return value if type(value) is int and 1 <= value <= 2_000_000 else None


def _price(value: object) -> Decimal | None:
    if type(value) not in (int, float):
        return None
    try:
        price = Decimal(str(value))
    except InvalidOperation:
        return None
    # 与每次调用微美元上界一致；非法、无穷、负值及超限价格均为未知。
    return price if price.is_finite() and 0 <= price <= Decimal(2**63 - 1) else None


async def _data_reasons(
    *, requirements: RoutingCallRequirements, candidate: RoutingCandidateReference,
    reader: RoutingDataBoundaryReader | None,
) -> list[RoutingExclusionReason]:
    if reader is None:
        return ["DATA_BOUNDARY_UNCONFIRMED"]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2
    try:
        async with asyncio.timeout_at(deadline):
            boundary = await reader.read_boundary(requirements=requirements, candidate=candidate)
    except Exception:
        # 不输出治理提供者异常、URL或批准材料；取消继续向外传播。
        return ["DATA_BOUNDARY_UNCONFIRMED"]
    if loop.time() >= deadline or not isinstance(boundary, RoutingDataBoundary):
        return ["DATA_BOUNDARY_UNCONFIRMED"]
    scope = RoutingDataBoundaryScope.model_validate(boundary.model_dump(exclude={
        "schema_version", "policy_digest", "approved_at", "expires_at", "revoked_at",
    }))
    if (boundary.tenant_id != requirements.tenant_id
        or boundary.policy_version_id != requirements.policy_version_id
        or boundary.policy_digest != requirements.policy_digest
        or boundary.endpoint_id != candidate.endpoint_id
        or boundary.endpoint_model_id != candidate.endpoint_model_id
        or boundary.model_version_id != candidate.model_version_id
        or boundary.model_request_digest != candidate.model_request_digest
        or routing_data_boundary_scope_digest(scope) != candidate.data_boundary_digest):
        return ["DATA_BOUNDARY_MISMATCH"]
    reasons: list[RoutingExclusionReason] = []
    now = utc_now()
    if not boundary.approved_at <= now < boundary.expires_at:
        reasons.append("DATA_BOUNDARY_EXPIRED")
    if boundary.revoked_at is not None:
        reasons.append("DATA_BOUNDARY_REVOKED")
    # 全部实际处理地域须落在允许集合内，不能仅有一个地域命中就放行。
    if not set(boundary.processing_regions) <= set(requirements.allowed_regions):
        reasons.append("DATA_REGION_NOT_ALLOWED")
    if requirements.data_classification not in boundary.allowed_data_classifications:
        reasons.append("DATA_CLASSIFICATION_NOT_ALLOWED")
    return reasons


async def _assess_candidate(
    session: AsyncSession, *, requirements: RoutingCallRequirements, candidate: RoutingCandidateReference,
    reader: RoutingDataBoundaryReader | None,
) -> RoutingCandidateAssessment:
    reasons: list[RoutingExclusionReason] = []
    cost: int | None = None
    probe_id = None

    def assessment() -> RoutingCandidateAssessment:
        return RoutingCandidateAssessment(candidate_id=candidate.candidate_id,
            model_version_id=candidate.model_version_id, priority=candidate.priority,
            status="excluded" if reasons else "eligible", reasons=tuple(dict.fromkeys(reasons)),
            estimated_cost_microusd=cost, latest_probe_id=probe_id)

    # 精确父子与租户关联；读取凭据revision元数据，不加载凭据实体或密文。
    row = (await session.execute(select(ModelEndpointVersion,
        ModelEndpoint.is_enabled.label("endpoint_enabled"), ModelEndpoint.read_only.label("endpoint_read_only"),
        ModelEndpointModel.archived_at.label("model_archived_at"),
        ModelCredential.revision.label("credential_revision"),
    ).select_from(ModelEndpointVersion).join(ModelEndpoint,
        ModelEndpoint.id == ModelEndpointVersion.endpoint_id).join(ModelEndpointModel,
        ModelEndpointModel.id == ModelEndpointVersion.endpoint_model_id).join(ModelCredential,
        ModelCredential.id == ModelEndpointVersion.credential_id).where(
        ModelEndpointVersion.tenant_id == requirements.tenant_id,
        ModelEndpointVersion.id == candidate.model_version_id,
        ModelEndpointVersion.endpoint_id == candidate.endpoint_id,
        ModelEndpointVersion.endpoint_model_id == candidate.endpoint_model_id,
        ModelEndpoint.tenant_id == requirements.tenant_id, ModelEndpoint.id == candidate.endpoint_id,
        ModelEndpointModel.tenant_id == requirements.tenant_id,
        ModelEndpointModel.endpoint_id == candidate.endpoint_id,
        ModelCredential.tenant_id == requirements.tenant_id, ModelCredential.endpoint_id == candidate.endpoint_id,
        ModelEndpointVersion.deleted_at.is_(None), ModelEndpoint.deleted_at.is_(None),
        ModelEndpointModel.deleted_at.is_(None), ModelCredential.deleted_at.is_(None),
    ).execution_options(populate_existing=True))).one_or_none()
    if row is None:
        reasons.append("REFERENCE_UNAVAILABLE")
        return assessment()
    version = row[0]
    if not row.endpoint_enabled or row.endpoint_read_only:
        reasons.append("ENDPOINT_DISABLED")
    if row.model_archived_at is not None:
        reasons.append("MODEL_ARCHIVED")
    if row.credential_revision != candidate.credential_revision:
        reasons.append("CREDENTIAL_REVISION_CHANGED")
    try:
        await assert_credential_revision_active(session, tenant_id=requirements.tenant_id,
            credential_id=version.credential_id, revision=candidate.credential_revision)
    except CredentialAccessDenied as exc:
        reasons.append("CREDENTIAL_REVOKED" if exc.code == "MODEL_CREDENTIAL_REVOKED"
            else "CREDENTIAL_PROVIDER_UNAVAILABLE" if exc.code == "MODEL_CREDENTIAL_PROVIDER_UNAVAILABLE"
            else "CREDENTIAL_UNAVAILABLE")
    if model_version_digest(version, candidate.credential_revision) != candidate.model_request_digest:
        reasons.append("REQUEST_DIGEST_MISMATCH")
    try:
        capability_digest = manifest_digest(version.capabilities)
    except (TypeError, ValueError):
        capability_digest = None
    if capability_digest != candidate.capability_digest:
        reasons.append("CAPABILITY_DIGEST_MISMATCH")
    try:
        pricing_digest = manifest_digest(version.pricing)
    except (TypeError, ValueError):
        pricing_digest = None
    if pricing_digest != candidate.pricing_digest:
        reasons.append("PRICING_DIGEST_MISMATCH")

    # 按稳定模型取最新观察，再匹配原版本；不从旧版本历史挑选一次通过。
    probe = (await session.execute(select(ModelEndpointTestRun.id, ModelEndpointTestRun.endpoint_id,
        ModelEndpointTestRun.model_version_id, ModelEndpointTestRun.credential_revision,
        ModelEndpointTestRun.config_digest, ModelEndpointTestRun.status, ModelEndpointTestRun.completed_at,
    ).where(ModelEndpointTestRun.tenant_id == requirements.tenant_id,
        ModelEndpointTestRun.endpoint_model_id == candidate.endpoint_model_id,
        ModelEndpointTestRun.deleted_at.is_(None))
        .order_by(ModelEndpointTestRun.created_at.desc(), ModelEndpointTestRun.id.desc()).limit(1))).one_or_none()
    if probe is not None:
        probe_id = probe.id
    probe_matches = (probe is not None and probe.endpoint_id == candidate.endpoint_id
        and probe.model_version_id == candidate.model_version_id
        and probe.credential_revision == candidate.credential_revision
        and probe.config_digest == candidate.model_request_digest
        and probe.status == ModelTestStatus.PASSED and probe.completed_at is not None
        and _utc(probe.completed_at) <= utc_now()
        and version.verification_status == ModelVerificationStatus.VERIFIED)
    if not probe_matches:
        reasons.append("LATEST_PROBE_UNVERIFIED")

    required_capabilities = []
    if requirements.streaming:
        required_capabilities.append("streaming")
    if requirements.uses_tools or requirements.response_strategy == "tool":
        required_capabilities.append("tool_calling")
    if requirements.response_strategy == "provider":
        required_capabilities.append("structured_output")
    if requirements.parallel_tool_calls:
        required_capabilities.append("parallel_tool_calls")
    if requirements.vision:
        required_capabilities.append("vision")
    verification = version.verification_result
    checks = verification.get("checks") if isinstance(verification, dict) else None
    for capability in required_capabilities:
        if not isinstance(version.capabilities, dict) or version.capabilities.get(capability) is not True:
            reasons.append("CAPABILITY_UNSUPPORTED")
        elif (not probe_matches or not isinstance(checks, dict) or checks.get(capability) != "passed"
            or verification.get("test_run_id") != str(probe_id)):
            reasons.append("CAPABILITY_UNCONFIRMED")

    defaults = version.defaults if isinstance(version.defaults, dict) else {}
    context_limit = _token_limit(defaults.get("context_window_tokens"))
    if context_limit is None:
        reasons.append("CONTEXT_LIMIT_UNKNOWN")
    elif requirements.reserved_input_tokens + requirements.reserved_output_tokens > context_limit:
        reasons.append("CONTEXT_LIMIT_EXCEEDED")
    output_limit = _token_limit(defaults.get("max_output_tokens"))
    if output_limit is None:
        reasons.append("OUTPUT_LIMIT_UNKNOWN")
    elif requirements.reserved_output_tokens > output_limit:
        reasons.append("OUTPUT_LIMIT_EXCEEDED")

    pricing = version.pricing if isinstance(version.pricing, dict) else {}
    input_price, output_price = _price(pricing.get("input_per_million_tokens")), _price(pricing.get("output_per_million_tokens"))
    if pricing.get("currency") != "USD" or input_price is None or output_price is None:
        reasons.append("USD_PRICING_UNKNOWN")
    else:
        # USD/百万Token等于微美元/Token；十进制转有理数后向上取整，避免浮点或精度截断低估。
        exact_cost = (requirements.reserved_input_tokens * Fraction(input_price)
            + requirements.reserved_output_tokens * Fraction(output_price))
        estimated = -(-exact_cost.numerator // exact_cost.denominator)
        if estimated > 2**63 - 1:
            reasons.append("CALL_COST_EXCEEDED")
        else:
            cost = estimated
            if cost > requirements.max_call_cost_microusd:
                reasons.append("CALL_COST_EXCEEDED")
    reasons.extend(await _data_reasons(requirements=requirements, candidate=candidate, reader=reader))
    return assessment()


async def filter_routing_candidates(
    session: AsyncSession, *, requirements: RoutingCallRequirements,
    candidates: tuple[RoutingCandidateReference, ...], boundary_reader: RoutingDataBoundaryReader | None = None,
) -> RoutingCandidateFilterReport:
    """1–8个确切候选逐个解释性排除；不排序选胜者、不改变获批候选集。"""
    if not 1 <= len(candidates) <= 8:
        raise ValueError("路由候选必须为1至8个")
    if (len({item.candidate_id for item in candidates}) != len(candidates)
        or len({item.model_version_id for item in candidates}) != len(candidates)
        or len({item.priority for item in candidates}) != len(candidates)):
        raise ValueError("候选ID、模型版本与优先级不能重复")
    ordered = sorted(candidates, key=lambda item: (item.priority, str(item.candidate_id)))
    with session.no_autoflush:
        version_id = await session.scalar(select(AgentVersion.id).where(
            AgentVersion.id == requirements.agent_version_id, AgentVersion.tenant_id == requirements.tenant_id))
        if version_id is None:
            raise ResourceNotFoundError("Agent 版本不存在")
        assessments = []
        for candidate in ordered:
            assessments.append(await _assess_candidate(session, requirements=requirements, candidate=candidate,
                reader=boundary_reader))
    return RoutingCandidateFilterReport(tenant_id=requirements.tenant_id,
        agent_version_id=requirements.agent_version_id, policy_version_id=requirements.policy_version_id,
        policy_digest=requirements.policy_digest,
        requirements_digest=manifest_digest(requirements.model_dump(mode="json")),
        candidate_set_digest=manifest_digest([item.model_dump(mode="json") for item in ordered]),
        observed_at=utc_now(), candidates=tuple(assessments))
