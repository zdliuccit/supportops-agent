"""真实 PostgreSQL 上的运行期授权、工具策略与最后一次额度竞争。"""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

import pytest
from langchain.agents.middleware import ToolCallRequest
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from sqlalchemy import select
from supportops_core import worker_runtime
from supportops_core.agent_runtime import RUNTIME_TOOLS, AgentRuntimeSnapshot
from supportops_core.db import create_session_factory
from supportops_core.enums import AgentStatus, GrantSubjectType, MessageRole, RunStatus
from supportops_core.models import (
    Agent,
    AgentAccessGrant,
    AgentRun,
    Conversation,
    Message,
    ModelEndpoint,
    ModelEndpointVersion,
    RunEvent,
    Tenant,
    ToolCatalogEntry,
    ToolInvocation,
    User,
    utc_now,
)
from supportops_core.run_attempt_services import RunClaim, claim_run_attempt
from supportops_core.services import ResourceNotFoundError, create_message_and_run
from supportops_core.tool_gateway import ToolExecutionGateway
from supportops_core.tool_policy import ToolPolicyError
from supportops_core.tool_services import update_tool_catalog_entry

from tests.postgres.test_run_fencing import PostgresWorkers
from tests.postgres.test_run_fencing import workers as workers
from tests.worker.test_worker import seed_run

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1",
    reason="需显式启用隔离 PostgreSQL 测试库",
)


@pytest.mark.parametrize(
    "change,expected_code",
    [
        ("user", "RUNTIME_AUTHORIZATION_DENIED"),
        ("tenant", "RUNTIME_AUTHORIZATION_DENIED"),
        ("agent", "RUNTIME_AUTHORIZATION_DENIED"),
        ("grant", "RUNTIME_AUTHORIZATION_DENIED"),
        ("admin_without_grant", "RUNTIME_AUTHORIZATION_DENIED"),
        ("model_disabled", "MODEL_RESOURCE_UNAVAILABLE"),
        ("foreign_model", "MODEL_RESOURCE_UNAVAILABLE"),
        ("config_digest", "AGENT_VERSION_REFERENCE_INVALID"),
    ],
)
async def test_queued_run_rechecks_current_subject_and_fixed_resources_before_model(
    workers: PostgresWorkers,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    expected_code: str,
) -> None:
    run_id = await seed_run(workers.engines[0], workers.settings)
    factory = create_session_factory(workers.engines[0])
    foreign_run_id = (
        await seed_run(workers.engines[1], workers.settings) if change == "foreign_model" else None
    )
    async with factory() as session, session.begin():
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == RunStatus.QUEUED
        conversation = await session.get(Conversation, run.conversation_id)
        assert conversation is not None
        if change == "user":
            user = await session.get(User, conversation.user_id)
            assert user is not None
            user.status = "disabled"
        elif change == "tenant":
            tenant = await session.get(Tenant, run.tenant_id)
            assert tenant is not None
            tenant.status = "disabled"
        elif change == "agent":
            agent = await session.get(Agent, run.agent_id)
            assert agent is not None
            agent.status = AgentStatus.DISABLED
        elif change in {"grant", "admin_without_grant"}:
            if change == "admin_without_grant":
                user = await session.get(User, conversation.user_id)
                assert user is not None
                user.roles = ["platform_admin"]
            grants = (
                await session.scalars(
                    select(AgentAccessGrant).where(
                        AgentAccessGrant.agent_id == run.agent_id,
                    )
                )
            ).all()
            assert grants
            for grant in grants:
                grant.deleted_at = utc_now()
        elif change == "model_disabled":
            version = await session.get(ModelEndpointVersion, run.model_endpoint_version_id)
            assert version is not None
            endpoint = await session.get(ModelEndpoint, version.endpoint_id)
            assert endpoint is not None
            endpoint.is_enabled = False
        elif change == "foreign_model":
            foreign = await session.get(AgentRun, foreign_run_id)
            assert foreign is not None and foreign.tenant_id != run.tenant_id
            run.model_endpoint_version_id = foreign.model_endpoint_version_id
        else:
            run.config_digest = "0" * 64
    invoked = False

    async def forbidden_model(*args: object, **kwargs: object):
        nonlocal invoked
        invoked = True
        raise AssertionError("已失效授权或引用不得发起模型调用")

    monkeypatch.setattr(worker_runtime, "invoke_agent", forbidden_model)
    assert not await worker_runtime.execute_agent_run(factory, run_id, settings=workers.settings)
    assert not invoked
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert (
            run is not None and run.status == RunStatus.FAILED and run.error_code == expected_code
        )
        conversation = await session.get(Conversation, run.conversation_id)
        assert conversation is not None and conversation.active_run_id is None
        assert conversation.active_attempt_id is None
        assert not (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == conversation.id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
        ).all()
        events = (
            await session.scalars(
                select(RunEvent).where(
                    RunEvent.run_id == run_id,
                    RunEvent.event_type == "run.failed",
                )
            )
        ).all()
        assert len(events) == 1
        if foreign_run_id is not None:
            foreign = await session.get(AgentRun, foreign_run_id)
            assert foreign is not None and foreign.status == RunStatus.QUEUED
            assert foreign.current_attempt_id is None


@dataclass(frozen=True)
class GatewayContext:
    workers: PostgresWorkers
    snapshot: AgentRuntimeSnapshot
    claim: RunClaim

    def gateway(self, index: int = 0, **settings_overrides: object) -> ToolExecutionGateway:
        return ToolExecutionGateway(
            create_session_factory(self.workers.engines[index % 2]),
            settings=self.workers.settings.model_copy(update=settings_overrides),
            snapshot=self.snapshot,
            identity=self.claim.identity,
        )


@pytest.fixture
async def gateway_context(
    workers: PostgresWorkers,
    request: pytest.FixtureRequest,
) -> AsyncIterator[GatewayContext]:
    limits = getattr(request, "param", (2, 2))
    run_id = await seed_run(
        workers.engines[0],
        workers.settings,
        tool_ids=("current_identity_summary",),
        tool_call_limit=limits[0],
        max_calls_per_tool=limits[1],
    )
    factory = create_session_factory(workers.engines[0])
    async with factory() as session, session.begin():
        claim = await claim_run_attempt(session, run_id, owner_id="gateway-owner", lease_seconds=90)
        assert claim is not None
    async with factory() as session, session.begin():
        snapshot = await worker_runtime._load_snapshot(
            session,
            run_id=run_id,
            settings=workers.settings,
            attempt_identity=claim.identity,
            audit_session_factory=factory,
        )
    try:
        yield GatewayContext(workers, snapshot, claim)
    finally:
        await worker_runtime._cancel_owned_attempt(
            factory, claim.identity, reason="isolated_test_finished"
        )


def tool_request(context: GatewayContext, call_id: str, **arguments: object) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={
            "type": "tool_call",
            "name": "current_identity_summary",
            "id": call_id,
            "args": arguments,
        },
        tool=RUNTIME_TOOLS["current_identity_summary"],
        state={},
        runtime=ToolRuntime(
            state={},
            context=context.snapshot.context,
            config={},
            stream_writer=lambda _: None,
            tool_call_id=call_id,
            store=None,
        ),
    )


async def identity_handler(request: ToolCallRequest) -> ToolMessage:
    return ToolMessage(
        content=json.dumps({"summary": f"本人：{request.runtime.context.user_id}"}),
        name="current_identity_summary",
        tool_call_id=request.tool_call["id"],
    )


async def change_catalog(context: GatewayContext, change: str) -> None:
    async with create_session_factory(context.workers.engines[1])() as session, session.begin():
        entry = await session.scalar(
            select(ToolCatalogEntry).where(
                ToolCatalogEntry.tenant_id == context.claim.identity.tenant_id,
                ToolCatalogEntry.tool_id == "current_identity_summary",
            )
        )
        assert entry is not None
        await update_tool_catalog_entry(
            session,
            tenant_id=entry.tenant_id,
            actor_user_id=context.snapshot.context.user_id,
            tool_id=entry.tool_id,
            name=entry.name,
            description=entry.description + "（隔离策略变更）",
            required_roles=["platform_admin"] if change == "roles" else entry.required_roles,
            risk_level="medium" if change == "risk" else entry.risk_level,
            is_enabled=change != "disabled",
            correlation_id="isolated-tool-policy-change",
        )


@pytest.mark.parametrize(
    "change,expected",
    [
        ("disabled", "TOOL_DISABLED_OR_REVOKED"),
        ("roles", "TOOL_ACCESS_DENIED"),
        ("risk", "TOOL_APPROVAL_REQUIRED"),
    ],
)
async def test_catalog_change_after_snapshot_blocks_new_tool_invocation(
    gateway_context: GatewayContext,
    change: str,
    expected: str,
) -> None:
    context = gateway_context
    await change_catalog(context, change)
    called = False

    async def blocked(request: ToolCallRequest) -> ToolMessage:
        nonlocal called
        called = True
        return await identity_handler(request)

    with pytest.raises(ToolPolicyError) as error:
        await context.gateway().execute(tool_request(context, "after-change"), blocked)
    assert error.value.code == expected and not called
    async with create_session_factory(context.workers.engines[0])() as session:
        invocation = await session.scalar(
            select(ToolInvocation).where(
                ToolInvocation.run_id == context.claim.identity.run_id,
            )
        )
        assert invocation is not None and invocation.status == "denied"
        assert invocation.error_code == expected and invocation.result_digest is None


@pytest.mark.parametrize("change", ["version", "disabled", "roles", "risk"])
async def test_catalog_change_during_tool_call_rejects_late_result(
    gateway_context: GatewayContext,
    change: str,
) -> None:
    context = gateway_context

    async def late_result(request: ToolCallRequest) -> ToolMessage:
        await change_catalog(context, change)
        return await identity_handler(request)

    with pytest.raises(ToolPolicyError):
        await context.gateway().execute(tool_request(context, "late-result"), late_result)
    async with create_session_factory(context.workers.engines[0])() as session:
        invocation = await session.scalar(
            select(ToolInvocation).where(
                ToolInvocation.run_id == context.claim.identity.run_id,
            )
        )
        assert invocation is not None and invocation.status == "failed"
        assert invocation.error_code in {
            "TOOL_DISABLED_OR_REVOKED",
            "TOOL_ACCESS_DENIED",
            "TOOL_POLICY_CHANGED",
        }
        assert invocation.result_digest is None


@pytest.mark.parametrize("argument", ["tenant_id", "user_id", "roles"])
async def test_client_arguments_cannot_override_trusted_scope(
    gateway_context: GatewayContext,
    argument: str,
) -> None:
    context = gateway_context
    with pytest.raises(ToolPolicyError) as error:
        await context.gateway().execute(
            tool_request(context, "spoofed-scope", **{argument: "foreign"}), identity_handler
        )
    assert error.value.code == "TOOL_INPUT_INVALID"


async def test_cross_tenant_gateway_identity_cannot_create_invocation_in_foreign_run(
    gateway_context: GatewayContext,
) -> None:
    context = gateway_context
    foreign_run_id = await seed_run(context.workers.engines[1], context.workers.settings)
    async with create_session_factory(context.workers.engines[1])() as session:
        foreign = await session.get(AgentRun, foreign_run_id)
        assert foreign is not None
        foreign_tenant = foreign.tenant_id
    gateway = context.gateway()
    gateway.identity = replace(context.claim.identity, tenant_id=foreign_tenant)
    with pytest.raises(ResourceNotFoundError):
        await gateway.execute(tool_request(context, "foreign-identity"), identity_handler)
    async with create_session_factory(context.workers.engines[0])() as session:
        assert not (
            await session.scalars(
                select(ToolInvocation).where(
                    ToolInvocation.run_id.in_((foreign_run_id, context.claim.identity.run_id)),
                )
            )
        ).all()


@pytest.mark.parametrize(
    "gateway_context", [(2, 4), (8, 2)], indirect=True, ids=["run_total", "single_tool"]
)
@pytest.mark.parametrize("first_result", ["succeeded", "failed", "cancelled"])
async def test_independent_gateways_compete_for_only_one_remaining_tool_permit(
    gateway_context: GatewayContext,
    first_result: str,
) -> None:
    context = gateway_context

    async def consume_first(request: ToolCallRequest) -> ToolMessage:
        if first_result == "failed":
            raise RuntimeError("isolated tool failure after permit consumption")
        if first_result == "cancelled":
            raise asyncio.CancelledError("isolated tool cancellation after permit consumption")
        return await identity_handler(request)

    if first_result == "succeeded":
        await context.gateway().execute(tool_request(context, "first-consumed"), consume_first)
    else:
        with pytest.raises(ToolPolicyError if first_result == "failed" else asyncio.CancelledError):
            await context.gateway().execute(tool_request(context, "first-consumed"), consume_first)
    calls = 0
    start = asyncio.Event()

    async def counted(request: ToolCallRequest) -> ToolMessage:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return await identity_handler(request)

    async def compete(index: int):
        await start.wait()
        return await context.gateway(index).execute(
            tool_request(context, f"competing-{index}"), counted
        )

    tasks = [asyncio.create_task(compete(index)) for index in range(6)]
    start.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)
    assert sum(isinstance(result, ToolMessage) for result in results) == calls == 1
    errors = [result for result in results if isinstance(result, ToolPolicyError)]
    assert len(errors) == 5 and all(error.code == "TOOL_CALL_QUOTA_EXCEEDED" for error in errors)
    async with create_session_factory(context.workers.engines[0])() as session:
        invocations = (
            await session.scalars(
                select(ToolInvocation).where(
                    ToolInvocation.run_id == context.claim.identity.run_id,
                )
            )
        ).all()
        assert sum(item.status != "denied" for item in invocations) == 2
        assert (
            next(item for item in invocations if item.tool_call_id == "first-consumed").status
            == first_result
        )
        assert sum(item.status == "denied" for item in invocations) == 5
        assert not any(item.status == "running" for item in invocations)


async def test_tool_handler_receives_current_database_roles_not_snapshot_roles(
    gateway_context: GatewayContext,
) -> None:
    context = gateway_context
    assert context.snapshot.context.roles == ("customer",)
    async with create_session_factory(context.workers.engines[1])() as session, session.begin():
        user = await session.get(User, context.snapshot.context.user_id)
        assert user is not None
        user.roles = ["employee"]
        session.add(
            AgentAccessGrant(
                tenant_id=user.tenant_id,
                agent_id=context.claim.run.agent_id,
                subject_type=GrantSubjectType.USER,
                subject_id=str(user.id),
                created_by=user.id,
            )
        )

    async def current_identity(request: ToolCallRequest) -> ToolMessage:
        assert request.runtime.context.roles == ("employee",)
        assert request.runtime.context.user_id == context.snapshot.context.user_id
        return await identity_handler(request)

    result = await context.gateway().execute(
        tool_request(context, "current-roles"), current_identity
    )
    assert isinstance(result, ToolMessage)


async def test_two_conversations_compete_for_tenant_daily_last_permit(
    gateway_context: GatewayContext,
) -> None:
    context = gateway_context
    factory = create_session_factory(context.workers.engines[1])
    async with factory() as session, session.begin():
        original = await session.get(Conversation, context.claim.identity.conversation_id)
        assert original is not None
        conversation = Conversation(
            tenant_id=original.tenant_id,
            user_id=original.user_id,
            agent_id=original.agent_id,
            agent_version_id=original.agent_version_id,
        )
        session.add(conversation)
        await session.flush()
        accepted = await create_message_and_run(
            session,
            conversation=conversation,
            user_id=conversation.user_id,
            identity_roles=context.snapshot.context.roles,
            content="另一会话",
            idempotency_key="other-conversation",
            correlation_id="tenant-quota",
        )
        run_id = accepted.run.id
    async with factory() as session, session.begin():
        claim = await claim_run_attempt(session, run_id, owner_id="other-owner", lease_seconds=90)
        assert claim is not None
    async with factory() as session, session.begin():
        snapshot = await worker_runtime._load_snapshot(
            session,
            run_id=run_id,
            settings=context.workers.settings,
            attempt_identity=claim.identity,
            audit_session_factory=factory,
        )
    other = GatewayContext(context.workers, snapshot, claim)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    value.gateway(index, tenant_tool_limit_per_day=1).execute(
                        tool_request(value, "last-daily-permit"), identity_handler
                    )
                    for index, value in enumerate((context, other))
                ),
                return_exceptions=True,
            ),
            timeout=5,
        )
        assert sum(isinstance(result, ToolMessage) for result in results) == 1
        errors = [result for result in results if isinstance(result, ToolPolicyError)]
        assert len(errors) == 1 and errors[0].code == "TENANT_TOOL_DAILY_QUOTA_EXCEEDED"
    finally:
        await worker_runtime._cancel_owned_attempt(
            factory, claim.identity, reason="isolated_test_finished"
        )
