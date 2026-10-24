"""显式模型middleware输入适配器；读取本次真实请求，不注册Worker或派发SDK。"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from langchain.agents.middleware import ModelRequest, ModelResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_config import SupportAnswer, SupportContext
from supportops_core.agent_runtime import _PLATFORM_DATA_POLICY
from supportops_core.config import Settings
from supportops_core.context_services import ContextSnapshot, fit_model_context
from supportops_core.enums import MessageRole, ResponseStrategy
from supportops_core.history_context_services import assert_history_context_authorized
from supportops_core.knowledge_retrieval import assert_attempt_knowledge_authorized
from supportops_core.model_context_boundary import record_model_input_context
from supportops_core.model_routing_config_v3 import AgentConfigV3, VersionedRoutingBindingV3, parse_agent_config_v3
from supportops_core.model_routing_contracts import RoutingCallRequirements
from supportops_core.model_routing_policy_services import assert_routing_policy_plan
from supportops_core.model_routing_request_contracts import RoutingLogicalInput, RoutingRuntimeStart
from supportops_core.model_routing_request_services import (
    RoutingRequestDenied, assert_routing_worker_entry, freeze_routing_logical_input,
    read_routing_runtime_start, routing_business_activity_exists, routing_outcome_event_exists,
)
from supportops_core.model_routing_reservation_contracts import RoutingCallPlan
from supportops_core.models import AgentVersion, Message, ModelCallLedger, RunEvent
from supportops_core.release_manifest_services import AgentReleaseManifestV2, manifest_digest, project_release_manifest
from supportops_core.run_attempt_services import OwnedRun, RunAttemptIdentity, current_run_identity, owned_run
from supportops_core.services import append_event
from supportops_core.tool_context_services import assert_tool_context_authorized


async def _version(session: AsyncSession, *, owned: OwnedRun) -> tuple[AgentConfigV3, AgentReleaseManifestV2]:
    version = await session.scalar(select(AgentVersion).where(AgentVersion.id == owned.run.agent_version_id,
        AgentVersion.tenant_id == owned.run.tenant_id, AgentVersion.agent_id == owned.run.agent_id)
        .execution_options(populate_existing=True))
    if (version is None or version.schema_version != "3" or version.withdrawn_at is not None
        or version.config_digest != owned.run.config_digest
        or version.model_endpoint_version_id != owned.run.model_endpoint_version_id
        or owned.conversation.agent_id != owned.run.agent_id
        or owned.conversation.agent_version_id != owned.run.agent_version_id):
        raise RoutingRequestDenied("ROUTING_RUNTIME_VERSION_UNAVAILABLE")
    status, manifest = project_release_manifest(version)
    config = parse_agent_config_v3(version.config)
    if (status != "recorded" or not isinstance(manifest, AgentReleaseManifestV2)
        or not isinstance(config.model, VersionedRoutingBindingV3)
        or config.runtime.response_strategy != ResponseStrategy.PROVIDER
        or config.runtime.max_input_tokens is None or config.runtime.max_cost_usd is None
        or any(tool.enabled for tool in config.tools) or config.model.generation.max_retries != 0
        or manifest.model_routing.policy_snapshot is None):
        raise RoutingRequestDenied("ROUTING_RUNTIME_ADAPTER_UNSUPPORTED")
    return config, manifest


def _request_scope(request: ModelRequest[SupportContext], *, attempt: RunAttemptIdentity, actor_user_id: UUID) -> None:
    context = request.runtime.context
    if (not isinstance(context, SupportContext) or context.tenant_id != attempt.tenant_id
        or context.conversation_id != attempt.conversation_id or context.run_id != attempt.run_id
        or context.attempt_id != attempt.attempt_id or context.execution_generation != attempt.generation
        or context.owner_id != attempt.owner_id or context.user_id != actor_user_id):
        raise RoutingRequestDenied("ROUTING_RUNTIME_SCOPE_MISMATCH")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class RoutingRuntimeInput:
    """仅在原execute或独立产品入口协程存活的实际输入reader，供准备authority显式注入。

    原计划/正文仅内存；close撤销读取并释放本实例引用，不声称清除进程内所有副本。
    后续物理计划只能更换call_id，真实预留/完整回退链/独立批准仍由相应服务核对。
    """

    def __init__(self, *, request: ModelRequest[SupportContext], attempt: RunAttemptIdentity,
        plan: RoutingCallPlan, logical: RoutingLogicalInput, manifest_digest_at_open: str,
        settings: Settings, deadline: float,
        independent_product: bool = False,
        worker_entry_id: UUID | None = None,
        runtime_start: RoutingRuntimeStart | None = None,
    ) -> None:
        self._request: ModelRequest[SupportContext] | None = request
        self._logical: RoutingLogicalInput | None = logical
        self._plan = plan
        self._attempt = attempt
        self._manifest_digest = manifest_digest_at_open
        self._settings = settings
        self._deadline = deadline
        self._task = asyncio.current_task()
        self._product_commit_entered = False
        self._checkpoint_capture_entered = False
        self._independent_product = independent_product is True
        self._worker_entry_id = worker_entry_id
        self._runtime_start = runtime_start

    def _live(self) -> None:
        if (self._request is None or self._logical is None or asyncio.current_task() is not self._task
            or asyncio.get_running_loop().time() >= self._deadline
            or not self._plan.created_at <= datetime.now(UTC) < self._plan.expires_at):
            raise RoutingRequestDenied("ROUTING_RUNTIME_INPUT_CLOSED")

    @property
    def plan(self) -> RoutingCallPlan:
        self._live()
        return self._plan

    def close(self) -> None:
        self._request = None
        self._logical = None
        self._task = None
        self._runtime_start = None

    def claim_product_commit(self) -> None:
        """原输入实例只允许进入一次独立产品终结，失败/取消/未知后不重新开放。"""
        self._live()
        if not self._independent_product:
            raise RoutingRequestDenied("ROUTING_RUNTIME_PRODUCT_MODE_REQUIRED")
        if self._product_commit_entered:
            raise RoutingRequestDenied("ROUTING_RUNTIME_PRODUCT_ALREADY_ENTERED")
        self._product_commit_entered = True

    def claim_checkpoint_capture(self) -> None:
        """不同最终图适配器也不能对同一原输入重领checkpoint写入。"""
        self._live()
        if not self._independent_product:
            raise RoutingRequestDenied("ROUTING_RUNTIME_PRODUCT_MODE_REQUIRED")
        if self._checkpoint_capture_entered:
            raise RoutingRequestDenied("ROUTING_RUNTIME_CHECKPOINT_ALREADY_ENTERED")
        self._checkpoint_capture_entered = True

    async def read(self, session: AsyncSession, *, plan: RoutingCallPlan) -> RoutingLogicalInput:
        """首尾复核活请求/原事务/持久上下文和当前授权，不提交调用方事务。"""
        try:
            self._live()
            if plan != self._plan.model_copy(update={"call_id": plan.call_id}):
                raise RoutingRequestDenied("ROUTING_RUNTIME_PLAN_CHANGED")
            transaction = session.get_transaction()
            if transaction is None or not transaction.is_active:
                raise RoutingRequestDenied("ROUTING_RUNTIME_TRANSACTION_REQUIRED")
            request, original = self._request, self._logical
            if request is None or original is None:
                raise RoutingRequestDenied("ROUTING_RUNTIME_INPUT_CLOSED")
            owned = await owned_run(session, self._attempt)
            await assert_routing_worker_entry(session, attempt=self._attempt, worker_entry_id=self._worker_entry_id)
            if self._independent_product and (owned.attempt.side_effect_state != "none"
                or owned.attempt.checkpoint_id is not None or await routing_outcome_event_exists(session,
                    tenant_id=self._attempt.tenant_id, run_id=self._attempt.run_id)
                or await routing_business_activity_exists(session,
                    tenant_id=self._attempt.tenant_id, run_id=self._attempt.run_id)):
                raise RoutingRequestDenied("ROUTING_RUNTIME_RUN_ALREADY_ENTERED")
            identity = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
            _request_scope(request, attempt=self._attempt, actor_user_id=identity.principal.user_id)
            config, manifest = await _version(session, owned=owned)
            if (owned.run.agent_version_id != plan.requirements.agent_version_id
                or manifest_digest(manifest.model_dump(mode="json")) != self._manifest_digest
                or owned.attempt.context_snapshot is None
                or manifest_digest(owned.attempt.context_snapshot) != original.context_snapshot_digest):
                raise RoutingRequestDenied("ROUTING_RUNTIME_CONTEXT_CHANGED")
            if self._independent_product:
                start = await read_routing_runtime_start(session, self._attempt)
                if (start is None or start != self._runtime_start
                    or start.root_call_id != self._plan.call_id
                    or start.root_plan_digest != manifest_digest(self._plan.model_dump(mode="json"))
                    or start.manifest_digest != self._manifest_digest
                    or start.model_input_digest != original.model_input_digest
                    or start.context_snapshot_digest != original.context_snapshot_digest
                    or start.agent_version_id != owned.run.agent_version_id
                    or start.actor_user_id != identity.principal.user_id or start.input_sequence != owned.run.input_sequence
                    or start.checkpoint_thread_id != owned.attempt.checkpoint_thread_id
                    or start.created_at != self._plan.created_at or start.expires_at != self._plan.expires_at
                    or owned.attempt.replay_policy != "manual"):
                    raise RoutingRequestDenied("ROUTING_RUNTIME_START_HISTORY_INVALID")
            snapshot = ContextSnapshot.model_validate(owned.attempt.context_snapshot)
            await assert_routing_policy_plan(session, plan=plan)
            await assert_history_context_authorized(session, snapshot=snapshot, identity=identity,
                conversation_id=self._attempt.conversation_id, run=owned.run)
            await assert_attempt_knowledge_authorized(session, identity=identity, attempt=self._attempt)
            await assert_tool_context_authorized(session, snapshot=snapshot, identity=identity,
                run=owned.run, settings=self._settings)
            current = freeze_routing_logical_input(request, attempt=self._attempt, context_snapshot=snapshot)
            if (current != original or request.system_prompt != _PLATFORM_DATA_POLICY + config.prompt.system_prompt
                or session.get_transaction() is not transaction or not transaction.is_active):
                raise RoutingRequestDenied("ROUTING_RUNTIME_INPUT_CHANGED")
            self._live()
            return current
        except RoutingRequestDenied:
            raise
        except Exception:
            raise RoutingRequestDenied("ROUTING_RUNTIME_INPUT_UNCONFIRMED") from None


RoutingInputExecutor = Callable[[RoutingRuntimeInput], Awaitable[ModelResponse[SupportAnswer]]]


class RoutingModelInputGateway:
    """兼容现有model_executor钩子的显式适配器；默认无executor即拒绝。

    同实例只接受一个原逻辑输入，失败或关闭后不重新开窗；原v2 Worker不注册本类。
    不调用传入的固定模型handler，不提供隐式重试。
    executor须另行完成当前启动/发布批准、共享预留、领取/消费、SDK及结果采纳。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, attempt: RunAttemptIdentity,
        settings: Settings, executor: RoutingInputExecutor | None = None, streaming: bool = True,
    ) -> None:
        if type(streaming) is not bool:
            raise RoutingRequestDenied("ROUTING_RUNTIME_STREAMING_INVALID")
        self._session_factory, self._attempt, self._settings = session_factory, attempt, settings
        self._executor, self._streaming = executor, streaming
        self._busy = False
        self._used = False
        self._worker_entry_id: UUID | None = None

    def bind_worker_entry(self, entry_id: UUID) -> None:
        if self._used or self._busy or self._worker_entry_id is not None or not isinstance(entry_id, UUID):
            raise RoutingRequestDenied("ROUTING_WORKER_BINDING_INVALID")
        self._worker_entry_id = entry_id

    async def _open(self, request: ModelRequest[SupportContext], *, independent_product: bool = False) -> RoutingRuntimeInput:
        started = datetime.now(UTC)
        loop = asyncio.get_running_loop()
        began = loop.time()
        record: RoutingRuntimeStart | None = None
        async with self._session_factory() as session, session.begin():
            owned = await owned_run(session, self._attempt)
            await assert_routing_worker_entry(session, attempt=self._attempt, worker_entry_id=self._worker_entry_id)
            identity = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
            _request_scope(request, attempt=self._attempt, actor_user_id=identity.principal.user_id)
            config, manifest = await _version(session, owned=owned)
            binding = config.model
            if (not isinstance(binding, VersionedRoutingBindingV3) or config.runtime.max_input_tokens is None
                or config.runtime.max_cost_usd is None or manifest.model_routing.policy_snapshot is None
                or request.system_prompt != _PLATFORM_DATA_POLICY + config.prompt.system_prompt
                or owned.attempt.context_snapshot is None):
                raise RoutingRequestDenied("ROUTING_RUNTIME_INPUT_UNAVAILABLE")
            snapshot = ContextSnapshot.model_validate(owned.attempt.context_snapshot)
            selected_ids: set[str] = set()
            planned, tokens, omitted = fit_model_context(request, input_limit=config.runtime.max_input_tokens,
                history_excerpts=snapshot.history_excerpts, on_selected_sources=selected_ids.update)
            # 先检查实际支持的请求形状，拒绝时不提交来源变化。
            freeze_routing_logical_input(planned, attempt=self._attempt, context_snapshot=snapshot)
            snapshot = await record_model_input_context(session, owned=owned, identity=identity,
                attempt=self._attempt, settings=self._settings, messages=planned.messages, selected_source_ids=selected_ids)
            if snapshot is None:
                raise RoutingRequestDenied("ROUTING_RUNTIME_CONTEXT_REQUIRED")
            logical = freeze_routing_logical_input(planned, attempt=self._attempt, context_snapshot=snapshot)
            if tokens != logical.reserved_input_tokens:
                raise RoutingRequestDenied("ROUTING_RUNTIME_INPUT_CHANGED")
            policy = manifest.model_routing.policy_snapshot
            expires = min(started + timedelta(seconds=min(30, binding.generation.timeout_seconds)),
                _aware(owned.attempt.lease_expires_at), policy.definition.valid_until)
            if datetime.now(UTC) >= expires:
                raise RoutingRequestDenied("ROUTING_RUNTIME_INPUT_EXPIRED")
            plan = RoutingCallPlan(call_id=uuid4(), run_id=self._attempt.run_id,
                conversation_id=self._attempt.conversation_id, attempt_id=self._attempt.attempt_id,
                generation=self._attempt.generation, owner_id=self._attempt.owner_id, purpose=binding.purpose,
                model_input_digest=logical.model_input_digest, context_snapshot_digest=logical.context_snapshot_digest,
                requirements=RoutingCallRequirements(tenant_id=self._attempt.tenant_id,
                    agent_version_id=owned.run.agent_version_id, policy_version_id=binding.policy_version_id,
                    policy_digest=binding.policy_digest, reserved_input_tokens=logical.reserved_input_tokens,
                    reserved_output_tokens=binding.generation.max_output_tokens, response_strategy="provider",
                    uses_tools=False, streaming=self._streaming, allowed_regions=binding.allowed_regions,
                    data_classification=binding.data_classification,
                    max_call_cost_microusd=int(Decimal(str(config.runtime.max_cost_usd)) * 1_000_000)),
                candidates=policy.definition.candidates, omitted_message_count=omitted, created_at=started, expires_at=expires)
            await assert_routing_policy_plan(session, plan=plan)
            if freeze_routing_logical_input(planned, attempt=self._attempt, context_snapshot=snapshot) != logical:
                raise RoutingRequestDenied("ROUTING_RUNTIME_INPUT_CHANGED")
            manifest_hash = manifest_digest(manifest.model_dump(mode="json"))
            if independent_product:
                # 与来源登记同一Run锁/事务永久领取；换实例/根/attempt也不能重开原Run。
                prior = await session.scalar(select(RunEvent.id).where(
                    RunEvent.tenant_id == owned.run.tenant_id, RunEvent.run_id == owned.run.id,
                    RunEvent.event_type == "routing.execution_started").limit(1))
                ledger = await session.scalar(select(ModelCallLedger.id).where(
                    ModelCallLedger.tenant_id == owned.run.tenant_id, ModelCallLedger.run_id == owned.run.id).limit(1))
                product = await session.scalar(select(Message.id).where(
                    Message.tenant_id == owned.run.tenant_id, Message.conversation_id == owned.conversation.id,
                    Message.turn_sequence == owned.run.input_sequence, Message.role == MessageRole.ASSISTANT).limit(1))
                business_activity = await routing_business_activity_exists(session,
                    tenant_id=owned.run.tenant_id, run_id=owned.run.id)
                if (prior is not None or ledger is not None or product is not None or business_activity
                    or await routing_outcome_event_exists(session, tenant_id=owned.run.tenant_id, run_id=owned.run.id)
                    or owned.run.input_sequence is None or owned.attempt.side_effect_state != "none"
                    or owned.attempt.checkpoint_id is not None):
                    raise RoutingRequestDenied("ROUTING_RUNTIME_RUN_ALREADY_ENTERED")
                record = RoutingRuntimeStart(operation_id=uuid5(owned.run.id, "routing-runtime-start-v1"),
                    tenant_id=owned.run.tenant_id, conversation_id=owned.conversation.id, run_id=owned.run.id,
                    attempt_id=owned.attempt.id, generation=self._attempt.generation,
                    agent_version_id=owned.run.agent_version_id, actor_user_id=identity.principal.user_id,
                    input_sequence=owned.run.input_sequence, root_call_id=plan.call_id,
                    root_plan_digest=manifest_digest(plan.model_dump(mode="json")), manifest_digest=manifest_hash,
                    model_input_digest=logical.model_input_digest, context_snapshot_digest=logical.context_snapshot_digest,
                    checkpoint_thread_id=owned.attempt.checkpoint_thread_id, created_at=plan.created_at, expires_at=plan.expires_at)
                owned.attempt.replay_policy = "manual"
                await append_event(session, run_id=owned.run.id, attempt_id=owned.attempt.id,
                    event_type="routing.execution_started", data={"record": record.model_dump(mode="json"),
                        "record_digest": manifest_digest(record.model_dump(mode="json"))})
                if await read_routing_runtime_start(session, self._attempt) != record:
                    raise RoutingRequestDenied("ROUTING_RUNTIME_START_HISTORY_INVALID")
        # 来源事务已明确提交，才把本次完整输入交给后续预留/准备；提交未知不会进入executor。
        scope = RoutingRuntimeInput(request=planned, attempt=self._attempt, plan=plan, logical=logical,
            manifest_digest_at_open=manifest_hash, settings=self._settings,
            deadline=began + (expires - started).total_seconds(), independent_product=independent_product,
            worker_entry_id=self._worker_entry_id, runtime_start=record)
        scope._live()
        return scope

    async def open_for_product(self, request: ModelRequest[SupportContext]) -> RoutingRuntimeInput:
        """独立产品执行器的一次输入入口；调用者负责finally关闭，不返回ModelResponse。"""
        if self._executor is not None or self._busy or self._used:
            raise RoutingRequestDenied("ROUTING_RUNTIME_INPUT_ALREADY_CLOSED")
        self._busy = True
        self._used = True
        try:
            async with asyncio.timeout(30):
                return await self._open(request, independent_product=True)
        finally:
            self._busy = False

    async def execute(self, request: ModelRequest[SupportContext],
        handler: Callable[[ModelRequest[SupportContext]], Awaitable[ModelResponse[SupportAnswer]]],
    ) -> ModelResponse[SupportAnswer]:
        if self._executor is None:
            raise RoutingRequestDenied("ROUTING_RUNTIME_EXECUTOR_UNAVAILABLE")
        if self._busy:
            raise RoutingRequestDenied("ROUTING_RUNTIME_INPUT_IN_USE")
        if self._used:
            raise RoutingRequestDenied("ROUTING_RUNTIME_INPUT_ALREADY_CLOSED")
        self._busy = True
        self._used = True
        scope: RoutingRuntimeInput | None = None
        try:
            async with asyncio.timeout(30):
                scope = await self._open(request)
                async with asyncio.timeout_at(scope._deadline):
                    response = await self._executor(scope)
                    # 回调返回不代表原输入/当前主体仍有效，产品采纳前再次读取同一真实请求。
                    async with self._session_factory() as session, session.begin():
                        await scope.read(session, plan=scope.plan)
                    return response
        except RoutingRequestDenied:
            raise
        except Exception:
            raise RoutingRequestDenied("ROUTING_RUNTIME_EXECUTION_UNCONFIRMED") from None
        finally:
            if scope is not None:
                scope.close()
            self._busy = False
