"""原路由领取的一次SDK执行及真实费用收敛；无默认注册或产品结果采纳。"""

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_config import SupportAnswer
from supportops_core.config import Settings
from supportops_core.credential_services import resolve_credential_secret
from supportops_core.model_routing_accounting_contracts import RoutingAccountingView
from supportops_core.model_routing_circuit_store import RoutingCircuitAuthority
from supportops_core.model_routing_dispatch_contracts import RoutingDispatchConsumption, RoutingDispatchRecord, RoutingDispatchRequest
from supportops_core.model_routing_dispatch_history import read_routing_dispatch_history
from supportops_core.model_routing_dispatch_services import _current, _transaction, _unconsumed, consume_routing_dispatch
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader
from supportops_core.model_routing_execution_contracts import RoutingSDKObservationRecord
from supportops_core.model_routing_execution_source import capture_and_settle_routing_sdk, describe_routing_sdk_observation
from supportops_core.model_routing_request_contracts import RoutingPreparedProviderRequest, bounded_request_json
from supportops_core.model_routing_request_services import (
    PreparedRoutingDispatchAuthority, RoutingLogicalInputReader, RoutingPreparedApprovalReader,
)
from supportops_core.model_routing_reservation_contracts import RoutingReservationRecord
from supportops_core.model_routing_reservations import RoutingReservationAuthority, _authorization, _current_context
from supportops_core.model_services import model_version_digest
from supportops_core.models import ModelEndpointVersion
from supportops_core.outbound_http import OutboundHttpPolicy, _destination
from supportops_core.provider_sdk_transport import ProviderSDKObservation, assert_provider_sdk_supported, invoke_provider_sdk_once
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.secrets import SecretProvider

# 原调用取消后仍保留真实费用事务；进程崩溃不保证内存观察完成持久化。
_ACCOUNTING: set[asyncio.Task[RoutingAccountingView | None]] = set()


class RoutingSDKDenied(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("原路由SDK执行或结果暂不可确认，勿重新派发")
        self.code = code


@dataclass(frozen=True, slots=True)
class RoutingSDKResult:
    reservation: RoutingReservationRecord
    consumption: RoutingDispatchConsumption
    accounting: RoutingAccountingView | None
    outcome: Literal["completed", "failed", "unknown", "contract_failure"]
    answer: SupportAnswer | None = field(repr=False)
    io_stop_confirmed: bool
    product_adoption_permitted: Literal[False] = False


@dataclass(frozen=True, slots=True)
class _Material:
    prepared: RoutingPreparedProviderRequest = field(repr=False)
    base_url: str = field(repr=False)
    key: SecretStr = field(repr=False)


class PostgresRoutingSDKExecutor:
    """显式依赖/单实例单物理调用；既有领取和预留必须先存在。

    本方法始终在原输入协程运行，I/O等待中的围栏也由原协程读取真实输入。
    无history消费重放入口；未知提交、取消、失败均不能再用本实例或自动重试。
    返回回答仍是不可信观察，W24.6及原产品引用/结果校验不得被绕过。
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession], *, settings: Settings,
        input_reader: RoutingLogicalInputReader | None = None, authority: RoutingReservationAuthority | None = None,
        approval_reader: RoutingPreparedApprovalReader | None = None, boundary_reader: RoutingDataBoundaryReader | None = None,
        circuit_authority: RoutingCircuitAuthority | None = None, secret_provider: SecretProvider | None = None,
        policy: OutboundHttpPolicy | None = None, redaction_secrets: tuple[str, ...] = (),
    ) -> None:
        if (input_reader is None or authority is None or approval_reader is None or boundary_reader is None
            or circuit_authority is None or secret_provider is None or not isinstance(policy, OutboundHttpPolicy)):
            raise RoutingSDKDenied("ROUTING_SDK_DEPENDENCIES_UNAVAILABLE")
        assert_provider_sdk_supported("ROUTING_SDK")
        self._factory, self._settings, self._input, self._authority = factory, settings, input_reader, authority
        self._approval, self._boundary, self._circuit = approval_reader, boundary_reader, circuit_authority
        self._provider, self._policy, self._redaction = secret_provider, policy, redaction_secrets
        self._entered = False
        self._consumed = False
        self._accounting: asyncio.Task[RoutingAccountingView | None] | None = None

    @property
    def accounting_unconfirmed(self) -> bool:
        task = self._accounting
        return self._consumed and (task is None or not task.done() or task.cancelled() or task.exception() is not None or task.result() is None)

    def _dispatch_authority(self, request: RoutingDispatchRequest, key: str | None = None) -> PreparedRoutingDispatchAuthority:
        return PreparedRoutingDispatchAuthority(settings=self._settings, authority=self._authority,
            input_reader=self._input, approval_reader=self._approval, boundary_reader=self._boundary,
            policies={request.plan.call_id: self._policy},
            redaction_secrets=(*self._redaction, *((key,) if key is not None else ())))

    async def _material(self, request: RoutingDispatchRequest, record: RoutingDispatchRecord) -> _Material:
        if (record.request_digest != manifest_digest(request.model_dump(mode="json"))
            or record.reservation.plan_digest != manifest_digest(request.plan.model_dump(mode="json"))
            or datetime.now(UTC) >= record.expires_at):
            raise RoutingSDKDenied("ROUTING_SDK_CLAIM_CHANGED")
        dispatch = self._dispatch_authority(request)
        async with _transaction(self._factory, request) as scope:
            _unconsumed(scope.ledger)
            history = await read_routing_dispatch_history(scope.session, reservation=scope.reservation)
            if history.record != record or history.consumption is not None:
                raise RoutingSDKDenied("ROUTING_SDK_CLAIM_CHANGED")
            current = await _current(scope, request, settings=self._settings, authority=self._authority,
                dispatch_authority=dispatch, boundary_reader=self._boundary, circuit_authority=self._circuit)
            prepared = await dispatch.prepare(scope.session, request=request, reservation=scope.reservation)
            if (manifest_digest(prepared.model_dump(mode="json")) != record.prepared_request_digest
                or current.prepared_digest != record.prepared_request_digest):
                raise RoutingSDKDenied("ROUTING_SDK_REQUEST_CHANGED")
        # 解封审计独立提交；此处不持租户FOR UPDATE锁，避免审计外键锁与自身相互等待。
        async with self._factory() as session, session.begin():
            authorization = await _authorization(session, plan=request.plan, authority=self._authority, settings=self._settings)
            if authorization.approval_reference_digest != record.reservation.approval_reference_digest:
                raise RoutingSDKDenied("ROUTING_SDK_APPROVAL_CHANGED")
            await _current_context(session, plan=request.plan, settings=self._settings, authorization=authorization)
            model = await session.scalar(select(ModelEndpointVersion).where(ModelEndpointVersion.id == prepared.model_version_id,
                ModelEndpointVersion.tenant_id == record.reservation.tenant_id).execution_options(populate_existing=True))
            if model is None or model_version_digest(model, prepared.credential_revision) != prepared.model_request_digest:
                raise RoutingSDKDenied("ROUTING_SDK_MODEL_CHANGED")
            key = await resolve_credential_secret(session, tenant_id=record.reservation.tenant_id,
                credential_id=model.credential_id, revision=prepared.credential_revision, provider=self._provider,
                actor_user_id=authorization.actor_user_id, endpoint_id=model.endpoint_id, purpose="model_run",
                correlation_id=str(request.plan.call_id), run_id=request.plan.run_id, audit_session_factory=self._factory)
            final = await self._dispatch_authority(request, key).prepare(session, request=request, reservation=record.reservation)
            target, _ = _destination(model.base_url.rstrip("/") + "/" + prepared.path, self._policy)
            if (final != prepared or hashlib.sha256(str(target).encode("utf-8")).hexdigest() != prepared.target_digest
                or datetime.now(UTC) >= record.expires_at):
                raise RoutingSDKDenied("ROUTING_SDK_REQUEST_CHANGED")
            return _Material(prepared, model.base_url, SecretStr(key))

    def _capture(self, observation: RoutingSDKObservationRecord, *, consumption: RoutingDispatchConsumption) -> None:
        task = asyncio.create_task(capture_and_settle_routing_sdk(self._factory, consumption=consumption, observation=observation))
        self._accounting = task
        _ACCOUNTING.add(task)

        def finished(value: asyncio.Task[RoutingAccountingView | None]) -> None:
            _ACCOUNTING.discard(value)
            if not value.cancelled():
                value.exception()  # 不输出原异常或重试未知写入，调用者只能查原call。

        task.add_done_callback(finished)

    async def execute(self, *, request: RoutingDispatchRequest, record: RoutingDispatchRecord) -> RoutingSDKResult:
        if self._entered:
            raise RoutingSDKDenied("ROUTING_SDK_INSTANCE_ALREADY_ENTERED")
        self._entered = True
        try:
            request = RoutingDispatchRequest.model_validate_json(request.model_dump_json())
            record = RoutingDispatchRecord.model_validate_json(record.model_dump_json())
            remaining = (min(record.expires_at, request.plan.expires_at) - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                raise RoutingSDKDenied("ROUTING_SDK_WINDOW_EXPIRED")
            async with asyncio.timeout(remaining):
                return await self._execute(request, record)
        except asyncio.CancelledError:
            raise
        except RoutingSDKDenied:
            raise
        except Exception:
            raise RoutingSDKDenied("ROUTING_SDK_EXECUTION_UNCONFIRMED") from None

    async def _execute(self, request: RoutingDispatchRequest, record: RoutingDispatchRecord) -> RoutingSDKResult:
        material = await self._material(request, record)
        dispatch = self._dispatch_authority(request, material.key.get_secret_value())
        consumption = await consume_routing_dispatch(self._factory, request=request, record=record,
            settings=self._settings, authority=self._authority, dispatch_authority=dispatch,
            boundary_reader=self._boundary, circuit_authority=self._circuit)
        self._consumed = True
        if datetime.now(UTC) >= consumption.expires_at:
            raise RoutingSDKDenied("ROUTING_SDK_ENTRY_EXPIRED")
        # 消费的≤1秒是入口新鲜界限；已进入的一次调用仍受原record窗口/模型超时约束。
        expires = min(record.expires_at, datetime.now(UTC) + timedelta(seconds=material.prepared.timeout_seconds))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0, (expires - datetime.now(UTC)).total_seconds())

        async def fence() -> None:
            if loop.time() >= deadline or datetime.now(UTC) >= expires:
                raise RoutingSDKDenied("ROUTING_SDK_WINDOW_EXPIRED")
            async with _transaction(self._factory, request) as scope:
                history = await read_routing_dispatch_history(scope.session, reservation=scope.reservation)
                if scope.reservation != record.reservation or history.consumption != consumption:
                    raise RoutingSDKDenied("ROUTING_SDK_CONSUMPTION_CHANGED")
                current = await _current(scope, request, settings=self._settings, authority=self._authority,
                    dispatch_authority=dispatch, boundary_reader=self._boundary, circuit_authority=self._circuit)
                if (current.prepared_digest != record.prepared_request_digest
                    or current.recovery_digest != record.recovery_record_digest
                    or current.circuit.last_event_digest != consumption.confirmation_circuit_digest):
                    raise RoutingSDKDenied("ROUTING_SDK_CURRENT_BOUNDARY_CHANGED")
            if loop.time() >= deadline or datetime.now(UTC) >= expires:
                raise RoutingSDKDenied("ROUTING_SDK_WINDOW_EXPIRED")

        body = bounded_request_json(material.prepared.payload_json)
        model = body.get("model")
        if not isinstance(model, str):
            raise RoutingSDKDenied("ROUTING_SDK_REQUEST_INVALID")
        observations: list[ProviderSDKObservation] = []
        captured: RoutingSDKObservationRecord | None = None
        answer: SupportAnswer | None = None
        try:
            await invoke_provider_sdk_once(base_url=material.base_url, path=material.prepared.path,
                body=material.prepared.payload_json.encode("utf-8"), key=material.key, organization=material.prepared.organization,
                streaming=material.prepared.streaming, policy=self._policy, fence=fence, expires_at=expires,
                prefix="ROUTING_SDK", continuous_fence=True, observe=observations.append,
                entry_expires_at=consumption.expires_at)
        finally:
            if observations:
                observed = observations[0]
                captured, answer = describe_routing_sdk_observation(observed, consumption=consumption,
                    responses=material.prepared.path == "responses", streaming=material.prepared.streaming, model=model,
                    secrets=(self._settings.jwt_secret, self._settings.secret_encryption_key, *self._redaction, material.key.get_secret_value()))
                self._capture(captured, consumption=consumption)
        if not observations or not observations[0].io_stop_confirmed:
            raise RoutingSDKDenied("ROUTING_SDK_STOP_UNCONFIRMED")
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            raise asyncio.CancelledError()
        accounting = await asyncio.shield(self._accounting) if self._accounting is not None else None
        await fence()
        outcome: Literal["completed", "failed", "unknown", "contract_failure"] = "unknown"
        if captured is not None and captured.receipt is not None:
            if captured.classification == "contract_failure" and captured.receipt.outcome == "completed":
                outcome = "contract_failure"
            elif captured.receipt.outcome == "completed":
                outcome = "completed"
            elif captured.receipt.outcome == "failed":
                outcome = "failed"
        if accounting is None or accounting.accounting.status != "settled" or accounting.accounting.reservation_exceeded:
            answer = None
        if loop.time() >= deadline or datetime.now(UTC) >= expires:
            raise RoutingSDKDenied("ROUTING_SDK_RESULT_EXPIRED")
        return RoutingSDKResult(record.reservation, consumption, accounting, outcome, answer,
            observations[0].io_stop_confirmed)
