"""从当前真实ModelRequest准备固定路由Provider正文；只读，不取密、SDK或联网。"""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from langchain.agents.middleware import ModelRequest
from langchain.agents.structured_output import ProviderStrategy
from pydantic import BaseModel
from sqlalchemy import Text, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import GenerationConfig, SupportAnswer, SupportContext
from supportops_core.agent_runtime import _PLATFORM_DATA_POLICY
from supportops_core.config import Settings
from supportops_core.context_services import ContextSnapshot, conservative_input_tokens, serialized_model_context
from supportops_core.credential_services import assert_credential_revision_active
from supportops_core.enums import ModelApiProtocol, ResponseStrategy
from supportops_core.model_config import ModelRequestMetadata, validate_model_extensions
from supportops_core.model_routing_config_v3 import VersionedRoutingBindingV3, parse_agent_config_v3
from supportops_core.model_routing_dispatch_contracts import RoutingDispatchPermit, RoutingDispatchRequest
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader, filter_routing_candidates
from supportops_core.model_routing_request_contracts import (
    ROUTING_OUTCOME_EVENT_TYPES, RoutingLogicalInput, RoutingPreparedProviderRequest,
    RoutingRuntimeStart, RoutingWorkerStart, bounded_request_json,
)
from supportops_core.model_routing_reservation_contracts import RoutingCallPlan, RoutingReservationRecord
from supportops_core.model_routing_reservations import RoutingReservationAuthority, _authorization, _current_context
from supportops_core.model_services import model_version_digest
from supportops_core.models import AgentVersion, BusinessAction, ModelCredential, ModelEndpointVersion, RunEvent, ToolInvocation
from supportops_core.outbound_http import OutboundHttpPolicy, _destination
from supportops_core.provider_answer_requests import provider_answer_request
from supportops_core.redaction import redact_sensitive_text
from supportops_core.release_manifest_services import AgentReleaseManifestV2, manifest_digest, project_release_manifest
from supportops_core.run_attempt_services import RunAttemptIdentity


class RoutingRequestDenied(ValueError):
    def __init__(self, code: str = "ROUTING_REQUEST_UNAVAILABLE") -> None:
        super().__init__("原路由完整请求材料当前不可准备")
        self.code = code


def _digest(value: BaseModel) -> str:
    return manifest_digest(value.model_dump(mode="json"))


async def routing_business_activity_exists(session: AsyncSession, *, tenant_id: UUID, run_id: UUID) -> bool:
    """原Run锁内查询业务事实，只取ID；False不授予执行或证明外部从未产生副作用。"""
    tool = await session.scalar(select(ToolInvocation.id).where(ToolInvocation.tenant_id == tenant_id,
        ToolInvocation.run_id == run_id).limit(1))
    if tool is not None:
        return True
    action = await session.scalar(select(BusinessAction.id).where(BusinessAction.tenant_id == tenant_id,
        or_(BusinessAction.run_id == run_id, BusinessAction.execution_run_id == run_id)).limit(1))
    return action is not None


async def routing_outcome_event_exists(session: AsyncSession, *, tenant_id: UUID, run_id: UUID) -> bool:
    """原Run锁内只读查询全部产品/终态/隔离事实，不按attempt或提交前序号忽略冲突。"""
    return await session.scalar(select(RunEvent.id).where(RunEvent.tenant_id == tenant_id,
        RunEvent.run_id == run_id, RunEvent.event_type.in_(ROUTING_OUTCOME_EVENT_TYPES)).limit(1)) is not None


async def read_routing_worker_start(session: AsyncSession, *, attempt: RunAttemptIdentity) -> RoutingWorkerStart | None:
    """调用者提供可信原身份/事务；只查元数据，损坏/多条不能视为没有入口。"""
    payload = case((func.octet_length(cast(RunEvent.data, Text())) <= 16384,
        RunEvent.data), else_=None).label("data")
    rows = (await session.execute(select(RunEvent.attempt_id, RunEvent.sequence, RunEvent.created_at, payload)
        .where(RunEvent.tenant_id == attempt.tenant_id, RunEvent.run_id == attempt.run_id,
            RunEvent.event_type == "routing.worker_started").order_by(RunEvent.sequence).limit(2))).all()
    if not rows:
        return None
    if len(rows) != 1:
        raise RoutingRequestDenied("ROUTING_WORKER_START_HISTORY_INVALID")
    event = rows[0]
    if not isinstance(event.data, dict) or set(event.data) != {"record", "record_digest"}:
        raise RoutingRequestDenied("ROUTING_WORKER_START_HISTORY_INVALID")
    record = RoutingWorkerStart.model_validate_json(json.dumps(event.data["record"], allow_nan=False))
    if (record.tenant_id != attempt.tenant_id or record.conversation_id != attempt.conversation_id
        or record.run_id != attempt.run_id or record.attempt_id != attempt.attempt_id
        or record.generation != attempt.generation or event.attempt_id != attempt.attempt_id
        or _digest(record) != event.data["record_digest"]
        or not record.created_at <= event.created_at <= datetime.now(UTC)):
        raise RoutingRequestDenied("ROUTING_WORKER_START_HISTORY_INVALID")
    stage_payload = case((func.octet_length(cast(RunEvent.data, Text())) <= 4096,
        RunEvent.data), else_=None).label("data")
    stages = (await session.execute(select(RunEvent.attempt_id, RunEvent.sequence, RunEvent.created_at, stage_payload)
        .where(RunEvent.tenant_id == attempt.tenant_id, RunEvent.run_id == attempt.run_id,
            RunEvent.event_type == "stage.started").order_by(RunEvent.sequence).limit(2))).all()
    if len(stages) != 1:
        raise RoutingRequestDenied("ROUTING_WORKER_START_HISTORY_INVALID")
    stage = stages[0]
    if (not isinstance(stage.data, dict) or stage.data != {"stage": "agent.execute",
        "attempt_id": str(attempt.attempt_id), "generation": attempt.generation}
        or type(stage.data["generation"]) is not int or stage.attempt_id != attempt.attempt_id
        or stage.sequence != event.sequence + 1
        or not event.created_at <= stage.created_at <= datetime.now(UTC)):
        raise RoutingRequestDenied("ROUTING_WORKER_START_HISTORY_INVALID")
    return record


async def assert_routing_worker_entry(session: AsyncSession, *, attempt: RunAttemptIdentity,
    worker_entry_id: UUID | None,
) -> None:
    worker = await read_routing_worker_start(session, attempt=attempt)
    if (worker is None and worker_entry_id is not None
        or worker is not None and worker.entry_id != worker_entry_id):
        raise RoutingRequestDenied("ROUTING_WORKER_ALREADY_ENTERED")


async def read_routing_runtime_start(session: AsyncSession, identity: RunAttemptIdentity) -> RoutingRuntimeStart | None:
    """原Run锁内读取唯一真实输入入口；运行复检与停止查证共用，不授予执行许可。"""
    payload = case((func.octet_length(cast(RunEvent.data, Text())) <= 16384,
        RunEvent.data), else_=None).label("data")
    rows = (await session.execute(select(RunEvent.attempt_id, RunEvent.created_at, payload)
        .where(RunEvent.tenant_id == identity.tenant_id, RunEvent.run_id == identity.run_id,
            RunEvent.event_type == "routing.execution_started").order_by(RunEvent.sequence).limit(2))).all()
    if not rows:
        return None
    if len(rows) != 1:
        raise RoutingRequestDenied("ROUTING_RUNTIME_START_HISTORY_INVALID")
    event = rows[0]
    if not isinstance(event.data, dict) or set(event.data) != {"record", "record_digest"}:
        raise RoutingRequestDenied("ROUTING_RUNTIME_START_HISTORY_INVALID")
    record = RoutingRuntimeStart.model_validate_json(json.dumps(event.data["record"], allow_nan=False))
    if (record.tenant_id != identity.tenant_id or record.run_id != identity.run_id
        or record.conversation_id != identity.conversation_id or record.attempt_id != identity.attempt_id
        or record.generation != identity.generation or event.attempt_id != identity.attempt_id
        or manifest_digest(record.model_dump(mode="json")) != event.data["record_digest"]
        or not record.created_at <= event.created_at <= record.expires_at):
        raise RoutingRequestDenied("ROUTING_RUNTIME_START_HISTORY_INVALID")
    return record


def freeze_routing_logical_input(request: ModelRequest[SupportContext], *, attempt: RunAttemptIdentity,
    context_snapshot: ContextSnapshot,
) -> RoutingLogicalInput:
    """冻结可信运行时实际构造的新请求；不能从历史元数据或checkpoint补造已派发材料。"""
    try:
        response_format = request.response_format
        if (not isinstance(response_format, ProviderStrategy) or response_format.schema is not SupportAnswer
            or response_format.schema_spec.strict is not True
            or response_format.schema_spec.json_schema != SupportAnswer.model_json_schema()
            or request.tool_choice is not None):
            raise ValueError("ROUTING_REQUEST_ADAPTER_UNSUPPORTED")
        body = serialized_model_context(request)
        return RoutingLogicalInput(attempt_id=attempt.attempt_id, generation=attempt.generation,
            context_snapshot_digest=_digest(context_snapshot), payload_json=body,
            model_input_digest=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            reserved_input_tokens=conservative_input_tokens(request))
    except Exception:
        raise RoutingRequestDenied("ROUTING_REQUEST_INPUT_UNSUPPORTED") from None


class RoutingLogicalInputReader(Protocol):
    """当前可信模型middleware提供本次真实完整输入，不接受客户端/模型上传自证。

    须证明原Run/owner/attempt/generation、完整裁剪后历史/知识/工具上下文和原输入摘要，
    不能由问题或ContextSnapshot元数据重建/估算消息，不遗漏额外系统/会话材料；不读秘密、
    网络、提交或替换调用者事务。首个适配器只接受无工具/纯文本/无额外model_settings。
    """

    async def read(self, session: AsyncSession, *, plan: RoutingCallPlan) -> RoutingLogicalInput: ...


class RoutingPreparedApprovalReader(Protocol):
    """独立当前W24.1/W19/W23与原一次派发批准，不由本适配器生成或自批。

    必须核实实际prepared正文/目标/出站范围、完整逻辑请求与物理历史、无遗漏在途派发，
    回退前无正文/业务工具；仅元数据准备不是执行批准。提供者只读事务，不取密或联网。
    """

    async def authorize(self, session: AsyncSession, *, request: RoutingDispatchRequest,
        reservation: RoutingReservationRecord, prepared: RoutingPreparedProviderRequest,
    ) -> RoutingDispatchPermit: ...


def _parts(logical: RoutingLogicalInput) -> tuple[str, tuple[tuple[Literal["user", "assistant"], str], ...]]:
    payload = bounded_request_json(logical.payload_json)
    system, messages = payload["system"], payload["messages"]
    system_text = system.get("content") if isinstance(system, dict) else None
    if not isinstance(system_text, str) or not isinstance(messages, list):
        raise RoutingRequestDenied("ROUTING_REQUEST_INPUT_INVALID")
    items: list[tuple[Literal["user", "assistant"], str]] = []
    for row in messages:
        if not isinstance(row, dict):
            raise RoutingRequestDenied("ROUTING_REQUEST_INPUT_INVALID")
        content = row.get("content")
        if not isinstance(content, str):
            raise RoutingRequestDenied("ROUTING_REQUEST_INPUT_INVALID")
        role: Literal["user", "assistant"] = "user" if row.get("type") == "human" else "assistant"
        items.append((role, content))
    return system_text, tuple(items)


async def _prepare_routing_provider_request(session: AsyncSession, *, request: RoutingDispatchRequest,
    reservation: RoutingReservationRecord, settings: Settings, authority: RoutingReservationAuthority,
    input_reader: RoutingLogicalInputReader, boundary_reader: RoutingDataBoundaryReader, policy: OutboundHttpPolicy,
    redaction_secrets: tuple[str, ...] = (),
) -> RoutingPreparedProviderRequest:
    """只读同事务准备；调用者持原Run/租户锁或一致快照。没有实际输入reader则不可使用。"""
    plan = request.plan
    if (reservation.call_id != plan.call_id or _digest(reservation) != request.reservation_digest
        or reservation.plan_digest != _digest(plan)):
        raise RoutingRequestDenied("ROUTING_REQUEST_RESERVATION_MISMATCH")
    approval = await _authorization(session, plan=plan, authority=authority, settings=settings)
    if approval.approval_reference_digest != reservation.approval_reference_digest:
        raise RoutingRequestDenied("ROUTING_REQUEST_APPROVAL_CHANGED")
    await _current_context(session, plan=plan, settings=settings, authorization=approval)
    version = await session.scalar(select(AgentVersion).where(AgentVersion.id == plan.requirements.agent_version_id,
        AgentVersion.tenant_id == plan.requirements.tenant_id).execution_options(populate_existing=True))
    if version is None or version.schema_version != "3" or version.withdrawn_at is not None:
        raise RoutingRequestDenied("ROUTING_REQUEST_VERSION_UNAVAILABLE")
    status, manifest = project_release_manifest(version)
    config = parse_agent_config_v3(version.config)
    binding = config.model
    if (status != "recorded" or not isinstance(manifest, AgentReleaseManifestV2)
        or not isinstance(binding, VersionedRoutingBindingV3)
        or binding.policy_version_id != plan.requirements.policy_version_id or binding.policy_digest != plan.requirements.policy_digest
        or binding.purpose != plan.purpose or set(binding.allowed_regions) != set(plan.requirements.allowed_regions)
        or binding.data_classification != plan.requirements.data_classification
        or config.runtime.response_strategy != ResponseStrategy.PROVIDER or plan.requirements.response_strategy != "provider"
        or any(tool.enabled for tool in config.tools) or plan.requirements.uses_tools or plan.requirements.parallel_tool_calls
        or plan.requirements.vision or binding.generation.max_retries != 0
        or manifest.model_routing.policy_snapshot is None
        or tuple(manifest.model_routing.policy_snapshot.definition.candidates) != tuple(sorted(plan.candidates, key=lambda row: (row.priority, str(row.candidate_id))))):
        raise RoutingRequestDenied("ROUTING_REQUEST_ADAPTER_UNSUPPORTED")
    candidate = next((row for row in plan.candidates if row.candidate_id == reservation.candidate_id), None)
    if candidate is None or _digest(candidate) != reservation.candidate_digest:
        raise RoutingRequestDenied("ROUTING_REQUEST_CANDIDATE_MISMATCH")
    model = await session.scalar(select(ModelEndpointVersion).where(ModelEndpointVersion.id == candidate.model_version_id,
        ModelEndpointVersion.tenant_id == reservation.tenant_id).execution_options(populate_existing=True))
    revision = await session.scalar(select(ModelCredential.revision).where(ModelCredential.id == model.credential_id,
        ModelCredential.tenant_id == reservation.tenant_id)) if model is not None else None
    if (model is None or revision is None or revision != reservation.credential_revision or revision != candidate.credential_revision
        or model_version_digest(model, revision) != candidate.model_request_digest
        or not isinstance(model.defaults, dict) or not isinstance(model.request_metadata, dict)
        or model.defaults.get("extension_options", {}) != {}):
        raise RoutingRequestDenied("ROUTING_REQUEST_MODEL_CHANGED")
    await assert_credential_revision_active(session, tenant_id=reservation.tenant_id, credential_id=model.credential_id, revision=revision)
    validate_model_extensions({})
    transaction = session.get_transaction()
    async with asyncio.timeout(1):
        logical = await input_reader.read(session, plan=plan)
    if (transaction is None or session.get_transaction() is not transaction or not transaction.is_active
        or not isinstance(logical, RoutingLogicalInput)):
        raise RoutingRequestDenied("ROUTING_REQUEST_INPUT_UNCONFIRMED")
    logical = RoutingLogicalInput.model_validate_json(logical.model_dump_json())
    if (logical.attempt_id != plan.attempt_id or logical.generation != plan.generation
        or logical.context_snapshot_digest != plan.context_snapshot_digest or logical.model_input_digest != plan.model_input_digest
        or logical.reserved_input_tokens != plan.requirements.reserved_input_tokens):
        raise RoutingRequestDenied("ROUTING_REQUEST_INPUT_CHANGED")
    system, messages = _parts(logical)
    if system != _PLATFORM_DATA_POLICY + config.prompt.system_prompt:
        raise RoutingRequestDenied("ROUTING_REQUEST_SYSTEM_CHANGED")
    values = binding.generation.model_dump(mode="json")
    for name in ("reasoning_effort", "verbosity"):
        if values[name] is None and model.defaults.get(name) is not None:
            values[name] = model.defaults[name]
    generation = GenerationConfig.model_validate(values)
    metadata = ModelRequestMetadata.model_validate(model.request_metadata, strict=True)
    if model.request_metadata.get("organization_id") != metadata.organization_id:
        raise RoutingRequestDenied("ROUTING_REQUEST_FIXED_METADATA_CHANGED")
    if generation.max_output_tokens != plan.requirements.reserved_output_tokens:
        raise RoutingRequestDenied("ROUTING_REQUEST_OUTPUT_CHANGED")
    if config.runtime.max_input_tokens is None or logical.reserved_input_tokens > config.runtime.max_input_tokens:
        raise RoutingRequestDenied("ROUTING_REQUEST_INPUT_BOUND_EXCEEDED")
    report = await filter_routing_candidates(session, requirements=plan.requirements, candidates=plan.candidates, boundary_reader=boundary_reader)
    selected = next((row for row in report.candidates if row.candidate_id == candidate.candidate_id), None)
    if selected is None or selected.status != "eligible" or selected.estimated_cost_microusd != reservation.reserved_cost_microusd:
        raise RoutingRequestDenied("ROUTING_REQUEST_CANDIDATE_EXCLUDED")
    if model.api_protocol == ModelApiProtocol.RESPONSES:
        protocol: Literal["responses", "chat_completions"] = "responses"
    elif model.api_protocol == ModelApiProtocol.CHAT_COMPLETIONS:
        protocol = "chat_completions"
    else:
        raise RoutingRequestDenied("ROUTING_REQUEST_PROTOCOL_UNSUPPORTED")
    path, wire = provider_answer_request(api_protocol=protocol, model=model.remote_model_name,
        system=system, messages=messages, temperature=generation.temperature, max_output_tokens=generation.max_output_tokens,
        reasoning_effort=generation.reasoning_effort, verbosity=generation.verbosity, streaming=plan.requirements.streaming,
        reserved_input_tokens=logical.reserved_input_tokens)
    target = model.base_url.rstrip("/") + "/" + path
    destination, _ = _destination(target, policy)  # 纯URL/白名单校验，无DNS或网络。
    policy_digest = manifest_digest({"hosts": sorted(policy.allowed_hosts), "paths": sorted(policy.allowed_paths),
        "ports": sorted(policy.allowed_ports), "ips": sorted(policy.allowed_ips), "private_ips": sorted(policy.private_ips)})
    payload_json = wire.decode("utf-8")
    secrets = (settings.jwt_secret, settings.secret_encryption_key, *redaction_secrets)
    for value in (logical.payload_json, payload_json, model.remote_model_name, metadata.organization_id or "", str(destination)):
        if redact_sensitive_text(value, secrets=secrets) != value:
            raise RoutingRequestDenied("ROUTING_REQUEST_MATERIAL_REQUIRES_REDACTION")
    await _current_context(session, plan=plan, settings=settings, authorization=approval)
    return RoutingPreparedProviderRequest(call_id=plan.call_id, reservation_digest=_digest(reservation),
        logical_input_digest=logical.model_input_digest, model_version_id=model.id, credential_revision=revision,
        model_request_digest=candidate.model_request_digest, outbound_policy_digest=policy_digest,
        target_digest=hashlib.sha256(str(destination).encode("utf-8")).hexdigest(), path=path,
        payload_json=payload_json, body_digest=hashlib.sha256(wire).hexdigest(), organization=metadata.organization_id,
        timeout_seconds=min(float(generation.timeout_seconds), (plan.expires_at - plan.created_at).total_seconds()),
        streaming=plan.requirements.streaming, reserved_input_tokens=logical.reserved_input_tokens,
        reserved_output_tokens=generation.max_output_tokens)


async def prepare_routing_provider_request(session: AsyncSession, *, request: RoutingDispatchRequest,
    reservation: RoutingReservationRecord, settings: Settings, authority: RoutingReservationAuthority,
    input_reader: RoutingLogicalInputReader, boundary_reader: RoutingDataBoundaryReader, policy: OutboundHttpPolicy,
    redaction_secrets: tuple[str, ...] = (),
) -> RoutingPreparedProviderRequest:
    """封闭外部输入/提供者错误，不回显实际正文、配置或原异常链；取消继续传播。"""
    try:
        return await _prepare_routing_provider_request(session, request=request, reservation=reservation,
            settings=settings, authority=authority, input_reader=input_reader, boundary_reader=boundary_reader,
            policy=policy, redaction_secrets=redaction_secrets)
    except RoutingRequestDenied:
        raise
    except Exception:
        raise RoutingRequestDenied("ROUTING_REQUEST_PREPARATION_UNCONFIRMED") from None


class PreparedRoutingDispatchAuthority:
    """显式适配既有领取/消费authority；不生成企业批准或注册到Worker。"""

    def __init__(self, *, settings: Settings, authority: RoutingReservationAuthority,
        input_reader: RoutingLogicalInputReader, approval_reader: RoutingPreparedApprovalReader,
        boundary_reader: RoutingDataBoundaryReader, policies: Mapping[UUID, OutboundHttpPolicy],
        redaction_secrets: tuple[str, ...] = (),
    ) -> None:
        if (any(value is None for value in (authority, input_reader, approval_reader, boundary_reader))
            or not 1 <= len(policies) <= 3 or any(not isinstance(key, UUID) or not isinstance(value, OutboundHttpPolicy)
                for key, value in policies.items())):
            raise RoutingRequestDenied("ROUTING_REQUEST_DEPENDENCIES_UNAVAILABLE")
        self.settings, self.authority, self.input_reader, self.approval_reader = settings, authority, input_reader, approval_reader
        self.boundary_reader, self.policies, self.redaction = boundary_reader, dict(policies), redaction_secrets

    async def prepare(self, session: AsyncSession, *, request: RoutingDispatchRequest,
        reservation: RoutingReservationRecord,
    ) -> RoutingPreparedProviderRequest:
        policy = self.policies.get(request.plan.call_id)
        if policy is None:
            raise RoutingRequestDenied("ROUTING_REQUEST_POLICY_UNAVAILABLE")
        return await prepare_routing_provider_request(session, request=request, reservation=reservation,
            settings=self.settings, authority=self.authority, input_reader=self.input_reader,
            boundary_reader=self.boundary_reader, policy=policy, redaction_secrets=self.redaction)

    async def _authorize(self, session: AsyncSession, *, request: RoutingDispatchRequest,
        reservation: RoutingReservationRecord,
    ) -> RoutingDispatchPermit:
        prepared = await self.prepare(session, request=request, reservation=reservation)
        transaction = session.get_transaction()
        started = datetime.now(UTC)
        async with asyncio.timeout(1):
            permit = await self.approval_reader.authorize(session, request=request, reservation=reservation, prepared=prepared)
        if (transaction is None or session.get_transaction() is not transaction or not transaction.is_active
            or not isinstance(permit, RoutingDispatchPermit)):
            raise RoutingRequestDenied("ROUTING_REQUEST_APPROVAL_UNCONFIRMED")
        permit = RoutingDispatchPermit.model_validate_json(permit.model_dump_json())
        if (permit.request_digest != _digest(request) or permit.reservation_digest != _digest(reservation)
            or permit.prepared_request_digest != _digest(prepared) or permit.model_input_digest != request.plan.model_input_digest
            or not started <= permit.checked_at <= datetime.now(UTC) < permit.expires_at <= request.plan.expires_at):
            raise RoutingRequestDenied("ROUTING_REQUEST_APPROVAL_MISMATCH")
        final = await self.prepare(session, request=request, reservation=reservation)
        if final != prepared or datetime.now(UTC) >= permit.expires_at:
            raise RoutingRequestDenied("ROUTING_REQUEST_CHANGED")
        return permit

    async def authorize(self, session: AsyncSession, *, request: RoutingDispatchRequest,
        reservation: RoutingReservationRecord,
    ) -> RoutingDispatchPermit:
        try:
            return await self._authorize(session, request=request, reservation=reservation)
        except RoutingRequestDenied:
            raise
        except Exception:
            raise RoutingRequestDenied("ROUTING_REQUEST_APPROVAL_UNCONFIRMED") from None
