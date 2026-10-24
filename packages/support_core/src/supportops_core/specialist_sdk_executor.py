"""固定只读子任务的一次SDK入口及持久结果适配；显式依赖，无Worker默认注册。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.credential_services import resolve_credential_secret
from supportops_core.model_routing_accounting_contracts import RoutingExecutionUsageReceipt
from supportops_core.model_services import model_version_digest
from supportops_core.models import ModelEndpointVersion
from supportops_core.outbound_http import OutboundHttpPolicy
from supportops_core.run_attempt_services import RunAttemptIdentity
from supportops_core.secrets import SecretProvider
from supportops_core.specialist_accounting_contracts import SpecialistAccountingRecord
from supportops_core.specialist_accounting_services import _history as accounting_history
from supportops_core.specialist_admission_contracts import SpecialistAdmissionBinding, SpecialistResultObservation
from supportops_core.specialist_budget_contracts import SpecialistBudgetReservation
from supportops_core.specialist_budget_services import SpecialistBudgetAuthority
from supportops_core.specialist_dispatch_contracts import SpecialistDispatchConsumption, SpecialistDispatchRecord
from supportops_core.specialist_dispatch_history import read_specialist_dispatch_history
from supportops_core.specialist_dispatch_services import (
    _current, _reservation, _transaction, _unconsumed, consume_specialist_dispatch,
)
from supportops_core.specialist_execution_cipher import SpecialistResponseCipher
from supportops_core.specialist_execution_history import assert_specialist_execution_history
from supportops_core.specialist_execution_store import (
    PostgresSpecialistExecutionSource, SpecialistExecutionStorageAuthority, capture_specialist_execution,
)
from supportops_core.specialist_material_contracts import material_matches_task
from supportops_core.specialist_response_contracts import SpecialistResultBinding
from supportops_core.specialist_response_services import SpecialistResultReader, convert_specialist_answer
from supportops_core.specialist_scheduler_contracts import SpecialistDispatchClaim
from supportops_core.specialist_sdk_transport import (
    SpecialistSDKMaterial, SpecialistSDKObservation, _invoke_specialist_sdk_once, assert_specialist_sdk_supported,
)
from supportops_core.specialist_task_contracts import SpecialistTaskInput, SpecialistTaskOutput, contract_digest

# 即使调用者放弃实例，尚未退出的传输/费用收敛仍有强引用；进程崩溃不保证收敛。
_BACKGROUND: set[asyncio.Task[None]] = set()


class SpecialistSDKDenied(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("原只读子任务执行或结果暂不可确认，勿重新派发")
        self.code = code


class PostgresSpecialistSDKExecutor:
    """同一原父尝试最多32份固定映射；同时适配executor/usage reader/result reader。

    可显式传给既有PG admission及协调器；不生成企业批准、存储许可或凭据身份。
    历史消费只能读取，永不从历史重新进入SDK。取消只协作停止，非OS终止证明。
    """

    _allowed_specialists: ClassVar[frozenset[str]] = frozenset({"knowledge", "diagnostic", "ticket_analysis"})

    def __init__(self, factory: async_sessionmaker[AsyncSession], *, attempt: RunAttemptIdentity,
        bindings: tuple[SpecialistAdmissionBinding, ...], settings: Settings,
        authority: SpecialistBudgetAuthority | None = None,
        storage_authority: SpecialistExecutionStorageAuthority | None = None,
        secret_provider: SecretProvider | None = None, cipher: SpecialistResponseCipher | None = None,
        policies: Mapping[UUID, OutboundHttpPolicy] | None = None, redaction_secrets: tuple[str, ...] = (),
    ) -> None:
        if authority is None or storage_authority is None or secret_provider is None or cipher is None or policies is None:
            raise SpecialistSDKDenied("SPECIALIST_SDK_DEPENDENCIES_UNAVAILABLE")
        bindings = tuple(SpecialistAdmissionBinding.model_validate_json(row.model_dump_json()) for row in bindings)
        if (not 1 <= len(bindings) <= 32 or len({row.task_id for row in bindings}) != len(bindings)
            or len({row.call_id for row in bindings}) != len(bindings)
            or set(policies) != {row.call_id for row in bindings}
            or any(not isinstance(policy, OutboundHttpPolicy) for policy in policies.values())):
            raise SpecialistSDKDenied("SPECIALIST_SDK_BINDINGS_INVALID")
        assert_specialist_sdk_supported()
        self._factory, self._attempt, self._settings = factory, attempt, settings
        self._authority, self._storage, self._secrets, self._cipher = authority, storage_authority, secret_provider, cipher
        self._bindings, self._policies = {row.task_id: row for row in bindings}, dict(policies)
        self._redaction = redaction_secrets
        self._source = PostgresSpecialistExecutionSource(attempt=attempt, authority=storage_authority, cipher=cipher)
        self._prepared: dict[UUID, SpecialistResultBinding] = {}
        self._entered: set[UUID] = set()
        self._unfinished: set[asyncio.Task[None]] = set()
        self._pending_by_task: dict[UUID, tuple[asyncio.Task[None], ...]] = {}
        self._blocked = False

    @property
    def unconfirmed_task_ids(self) -> tuple[UUID, ...]:
        """供批次收尾查询真实未退出传输/费用任务；无新派发或取消回调。"""
        return tuple(sorted((task_id for task_id, futures in self._pending_by_task.items()
            if any(not future.done() for future in futures)), key=str))

    def _binding(self, task: SpecialistTaskInput) -> SpecialistAdmissionBinding:
        binding, parent, attempt = self._bindings.get(task.task_id), task.parent, self._attempt
        if (binding is None or task.specialist not in self._allowed_specialists
            or not material_matches_task(task, binding.scope) or binding.task_input_digest != contract_digest(task)
            or parent.tenant_id != attempt.tenant_id or parent.conversation_id != attempt.conversation_id
            or parent.run_id != attempt.run_id or parent.attempt_id != attempt.attempt_id
            or parent.attempt_generation != attempt.generation):
            raise SpecialistSDKDenied("SPECIALIST_SDK_TASK_MISMATCH")
        return binding

    async def _material(self, task: SpecialistTaskInput, claim: SpecialistDispatchClaim,
        binding: SpecialistAdmissionBinding,
    ) -> tuple[SpecialistBudgetReservation, SpecialistDispatchRecord, SpecialistSDKMaterial]:
        async with _transaction(self._factory, self._attempt) as session:
            reservation, ledger = await _reservation(session, attempt=self._attempt, call_id=binding.call_id)
            _unconsumed(ledger)
            history = await read_specialist_dispatch_history(session, reservation=reservation)
            record = history.record
            if record is None or record.claim != claim or history.consumption is not None:
                raise SpecialistSDKDenied("SPECIALIST_SDK_CLAIM_MISMATCH")
            await assert_specialist_execution_history(session)
            prepared, _ = await _current(session, attempt=self._attempt, reservation=reservation, task=task,
                scope=binding.scope, settings=self._settings, authority=self._authority, redaction_secrets=self._redaction)
            model = await session.scalar(select(ModelEndpointVersion).where(
                ModelEndpointVersion.tenant_id == reservation.tenant_id,
                ModelEndpointVersion.id == reservation.child_version.model_version_id).execution_options(populate_existing=True))
            if (model is None or model_version_digest(model, reservation.credential_revision) != prepared.model_request_digest
                or contract_digest(prepared) != record.prepared_request_digest):
                raise SpecialistSDKDenied("SPECIALIST_SDK_REQUEST_CHANGED")
            key = await resolve_credential_secret(session, tenant_id=reservation.tenant_id, credential_id=model.credential_id,
                revision=reservation.credential_revision, provider=self._secrets, actor_user_id=task.parent.subject_user_id,
                endpoint_id=model.endpoint_id, purpose="model_run", correlation_id=str(binding.call_id),
                run_id=reservation.run_id, audit_session_factory=self._factory)
            final, _ = await _current(session, attempt=self._attempt, reservation=reservation, task=task,
                scope=binding.scope, settings=self._settings, authority=self._authority,
                redaction_secrets=(*self._redaction, key))
            if final != prepared or datetime.now(UTC) >= claim.expires_at:
                raise SpecialistSDKDenied("SPECIALIST_SDK_REQUEST_CHANGED")
            material = SpecialistSDKMaterial(prepared=prepared, base_url=model.base_url, api_key=SecretStr(key))
        return reservation, record, material

    def _track(self, task: asyncio.Task[None]) -> None:
        _BACKGROUND.add(task)

        def finished(value: asyncio.Task[None]) -> None:
            _BACKGROUND.discard(value)
            self._unfinished.discard(value)
            if not value.cancelled():
                value.exception()  # 固定异常只消费一次；迟到结果不回送父级。

        task.add_done_callback(finished)

    async def _capture(self, sdk: asyncio.Task[SpecialistSDKObservation], *, stopped: asyncio.Event,
        reservation: SpecialistBudgetReservation, consumption: SpecialistDispatchConsumption, material: SpecialistSDKMaterial,
    ) -> None:
        observation = await asyncio.shield(sdk)
        reply = observation.reply
        if (reply.usage is None or observation.started_at is None or observation.finished_at is None
            or reply.outcome == "unknown"):
            raise SpecialistSDKDenied("SPECIALIST_SDK_USAGE_UNKNOWN")
        receipt = RoutingExecutionUsageReceipt(observation_id=uuid4(), tenant_id=reservation.tenant_id,
            call_id=reservation.call_id, run_id=reservation.run_id, attempt_id=reservation.attempt_id,
            generation=reservation.generation, reservation_digest=contract_digest(reservation),
            actual_model_version_id=reservation.child_version.model_version_id, credential_revision=reservation.credential_revision,
            model_input_digest=reservation.model_input_digest, dispatch_id=consumption.record.claim.dispatch_id,
            dispatch_evidence_digest=contract_digest(consumption), outcome=reply.outcome, usage=reply.usage,
            started_at=observation.started_at, finished_at=observation.finished_at, observed_at=datetime.now(UTC))
        try:
            await capture_specialist_execution(self._factory, attempt=self._attempt, receipt=receipt, settings=self._settings,
                answer_json=None if stopped.is_set() else reply.answer_json, authority=self._storage, cipher=self._cipher,
                redaction_secrets=(*self._redaction, material.api_key.get_secret_value()))
        except BaseException:
            # 费用写入未知只读原call；本实例不再开始新调用，不盲目重试捕获事务。
            self._blocked = True
            raise SpecialistSDKDenied("SPECIALIST_SDK_CAPTURE_UNCONFIRMED") from None

    async def _result(self, *, task: SpecialistTaskInput, binding: SpecialistAdmissionBinding,
        consumption: SpecialistDispatchConsumption, material: SpecialistSDKMaterial,
    ) -> SpecialistTaskOutput:
        async with _transaction(self._factory, self._attempt) as session:
            reservation, ledger = await _reservation(session, attempt=self._attempt, call_id=binding.call_id)
            current, _ = await _current(session, attempt=self._attempt, reservation=reservation, task=task,
                scope=binding.scope, settings=self._settings, authority=self._authority,
                redaction_secrets=(*self._redaction, material.api_key.get_secret_value()))
            if current != material.prepared:
                raise SpecialistSDKDenied("SPECIALIST_SDK_REQUEST_CHANGED")
            accounting = await accounting_history(session, reservation=reservation, ledger=ledger)
            if accounting is None:
                raise SpecialistSDKDenied("SPECIALIST_SDK_ACCOUNTING_MISSING")
            observation = await self._source.read_answer(session, reservation=reservation, consumption=consumption, accounting=accounting)
            if observation is None:
                raise SpecialistSDKDenied("SPECIALIST_SDK_ANSWER_UNAVAILABLE")
            result = convert_specialist_answer(binding=self._prepared[binding.call_id], reservation=reservation,
                consumption=consumption, accounting=accounting, observation=observation,
                redaction_secrets=(self._settings.jwt_secret, self._settings.secret_encryption_key, *self._redaction)).result
            final, _ = await _current(session, attempt=self._attempt, reservation=reservation, task=task,
                scope=binding.scope, settings=self._settings, authority=self._authority,
                redaction_secrets=(*self._redaction, material.api_key.get_secret_value()))
            if final != material.prepared or datetime.now(UTC) >= consumption.expires_at:
                raise SpecialistSDKDenied("SPECIALIST_SDK_RESULT_EXPIRED")
        return result  # 协调器仍须走独立当前采纳，返回本身不授权或确认业务成功。

    async def execute(self, *, task: SpecialistTaskInput, claim: SpecialistDispatchClaim) -> SpecialistTaskOutput:
        binding = self._binding(task)
        if self._blocked or self._unfinished or binding.call_id in self._entered:
            raise SpecialistSDKDenied("SPECIALIST_SDK_INSTANCE_UNCONFIRMED")
        self._entered.add(binding.call_id)
        try:
            return await self._execute(task=task, claim=claim, binding=binding)
        except asyncio.CancelledError:
            raise
        except SpecialistSDKDenied:
            raise
        except Exception:
            raise SpecialistSDKDenied("SPECIALIST_SDK_EXECUTION_UNCONFIRMED") from None

    async def _execute(self, *, task: SpecialistTaskInput, claim: SpecialistDispatchClaim,
        binding: SpecialistAdmissionBinding,
    ) -> SpecialistTaskOutput:
        reservation, record, material = await self._material(task, claim, binding)
        self._prepared[binding.call_id] = SpecialistResultBinding(call_id=binding.call_id, task=task, prepared=material.prepared)
        consumption = await consume_specialist_dispatch(self._factory, attempt=self._attempt, record=record, task=task,
            scope=binding.scope, settings=self._settings, authority=self._authority,
            redaction_secrets=(*self._redaction, material.api_key.get_secret_value()))
        # 只允许当前局部消费成功后进入；没有从历史记录恢复/重发的路径。
        timeout = json.loads(material.prepared.payload_json)["model_settings"]["timeout"]
        expires_at = min(consumption.expires_at, datetime.now(UTC) + timedelta(seconds=timeout))
        loop, stopped, mutex = asyncio.get_running_loop(), asyncio.Event(), asyncio.Lock()
        end = loop.time() + max(0, (expires_at - datetime.now(UTC)).total_seconds())

        async def fence() -> None:
            async with mutex:
                if stopped.is_set() or self._blocked or self._unfinished or loop.time() >= end or datetime.now(UTC) >= expires_at:
                    raise SpecialistSDKDenied("SPECIALIST_SDK_WINDOW_CLOSED")
                async with _transaction(self._factory, self._attempt) as session:
                    original, _ = await _reservation(session, attempt=self._attempt, call_id=binding.call_id)
                    history = await read_specialist_dispatch_history(session, reservation=original)
                    if original != reservation or history.consumption != consumption:
                        raise SpecialistSDKDenied("SPECIALIST_SDK_CONSUMPTION_CHANGED")
                    await assert_specialist_execution_history(session)
                    prepared, _ = await _current(session, attempt=self._attempt, reservation=original, task=task,
                        scope=binding.scope, settings=self._settings, authority=self._authority,
                        redaction_secrets=(*self._redaction, material.api_key.get_secret_value()))
                    if prepared != material.prepared:
                        raise SpecialistSDKDenied("SPECIALIST_SDK_REQUEST_CHANGED")
                if stopped.is_set() or loop.time() >= end or datetime.now(UTC) >= expires_at:
                    raise SpecialistSDKDenied("SPECIALIST_SDK_WINDOW_CLOSED")

        async def monitor() -> None:
            while True:
                await fence()
                await asyncio.sleep(min(0.5, max(0, end - loop.time())))

        sdk = asyncio.create_task(_invoke_specialist_sdk_once(material=material, policy=self._policies[binding.call_id],
            fence=fence, expires_at=expires_at))
        collector = asyncio.create_task(self._capture(sdk, stopped=stopped, reservation=reservation,
            consumption=consumption, material=material))
        watcher = asyncio.create_task(monitor())
        self._track(collector)
        self._track(watcher)
        try:
            done, _ = await asyncio.wait((collector, watcher), timeout=max(0, end - loop.time()), return_when=asyncio.FIRST_COMPLETED)
            if watcher in done or collector not in done or loop.time() >= end or datetime.now(UTC) >= expires_at:
                raise SpecialistSDKDenied("SPECIALIST_SDK_WINDOW_CLOSED")
            await collector
        finally:
            stopped.set()
            watcher.cancel()
            if not sdk.done():
                sdk.cancel()
            try:
                # 不取消费用collector；原SDK若吞取消迟到，仍只补记原费用，不采纳结果。
                await asyncio.wait((collector, watcher), timeout=0.1)
            finally:
                for pending in (collector, watcher):
                    if not pending.done():
                        self._unfinished.add(pending)
                self._pending_by_task[task.task_id] = tuple(pending for pending in (collector, watcher) if not pending.done())
        if self._unfinished:
            raise SpecialistSDKDenied("SPECIALIST_SDK_STOP_UNCONFIRMED")
        return await self._result(task=task, binding=binding, consumption=consumption, material=material)

    async def read_usage(self, session: AsyncSession, *, reservation: SpecialistBudgetReservation,
    ) -> RoutingExecutionUsageReceipt | None:
        return await self._source.read_usage(session, reservation=reservation)

    async def read_result(self, session: AsyncSession, *, reservation: SpecialistBudgetReservation,
        consumption: SpecialistDispatchConsumption, accounting: SpecialistAccountingRecord,
    ) -> SpecialistResultObservation | None:
        binding = self._prepared.get(reservation.call_id)
        if binding is None:
            return None
        # 回答已由存储层用同一原密钥脱敏；不长期保留解封后的模型凭据。
        reader = SpecialistResultReader(bindings=(binding,), settings=self._settings,
            source=self._source, redaction_secrets=self._redaction)
        return await reader.read_result(session, reservation=reservation, consumption=consumption, accounting=accounting)


class PostgresSpecialistKnowledgeSDKExecutor(PostgresSpecialistSDKExecutor):
    """旧知识专用执行器不自动接收业务类型；通用运行须显式选择新类。"""

    _allowed_specialists = frozenset({"knowledge"})
