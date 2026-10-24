"""真实路由结果的固定最终图与有界PG核验；不调用setup、模型或工具。"""

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from typing import Literal, Protocol, TypedDict
from urllib.parse import urlsplit
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackManager
from langgraph.checkpoint.base import LATEST_VERSION
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_runtime import _psycopg_url
from supportops_core.config import Settings
from supportops_core.model_routing_contracts import Digest, RoutingFilterModel
from supportops_core.model_routing_coordinator import RoutingCoordinationResult
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader
from supportops_core.model_routing_failover_contracts import RoutingFailoverControls
from supportops_core.model_routing_product_contracts import RoutingProductCheckpoint
from supportops_core.model_routing_reservations import RoutingReservationAuthority, _identity
from supportops_core.model_routing_result_services import (
    PreparedRoutingSupportResult, RoutingResultDenied, prepare_routing_support_result,
)
from supportops_core.model_routing_runtime_input import RoutingRuntimeInput
from supportops_core.redaction import redact_sensitive_text
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import owned_run
from supportops_core.specialist_response_services import _json_object

PACKET_BYTES_MAX = 1_048_576
SERIALIZED_BYTES_MAX = 4_194_304


def _scope(prepared: PreparedRoutingSupportResult) -> str:
    values = prepared.binding.model_dump(mode="json")
    for key in ("checked_at", "expires_at"):
        values.pop(key)
    return manifest_digest(values)


def _json(value: object, *, limit: int) -> str:
    """只序列化JSON原生值；拒绝自定义对象/tuple，绝不借通用serde恢复Python对象。"""
    pending: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 16 or nodes > 4096 or type(item) not in (dict, list, str, int, float, bool, type(None)):
            raise RoutingResultDenied("ROUTING_CHECKPOINT_JSON_INVALID")
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                raise RoutingResultDenied("ROUTING_CHECKPOINT_JSON_INVALID")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    result = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(result.encode("utf-8")) > limit:
        raise RoutingResultDenied("ROUTING_CHECKPOINT_TOO_LARGE")
    return result


class _JSONSerializer:
    def dumps_typed(self, obj: object) -> tuple[str, bytes]:
        return "routing_json_v1", _json({"value": obj}, limit=SERIALIZED_BYTES_MAX).encode("utf-8")

    def loads_typed(self, data: tuple[str, bytes]) -> object:
        if data[0] != "routing_json_v1":
            raise RoutingResultDenied("ROUTING_CHECKPOINT_SERIALIZER_UNSUPPORTED")
        value = _json_object(data[1].decode("utf-8"), bytes_limit=SERIALIZED_BYTES_MAX)
        if set(value) != {"value"}:
            raise RoutingResultDenied("ROUTING_CHECKPOINT_JSON_INVALID")
        _json(value, limit=SERIALIZED_BYTES_MAX)
        return value["value"]


class RoutingCheckpointStorageRequest(RoutingFilterModel):
    purpose: Literal["capture", "read"]
    tenant_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1, le=2**31 - 1, strict=True)
    root_call_id: UUID
    root_plan_digest: Digest
    actor_user_id: UUID
    checkpoint_thread_id: str = Field(min_length=1, max_length=300)
    database_scope_digest: Digest
    prepared_scope_digest: Digest
    packet_digest: Digest
    packet_bytes: int = Field(ge=1, le=PACKET_BYTES_MAX, strict=True)


class RoutingCheckpointStoragePermit(RoutingFilterModel):
    request_digest: Digest
    actor_user_id: UUID
    retention_policy_digest: Digest
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def short_storage_permit(self) -> "RoutingCheckpointStoragePermit":
        if not 0 < (self.expires_at - self.checked_at).total_seconds() <= 5:
            raise ValueError("ROUTING_CHECKPOINT_STORAGE_WINDOW_INVALID")
        return self


class RoutingCheckpointStorageAuthority(Protocol):
    """独立确认W21的完整真实输入/正文存储、处理地域/目标库和当前保留/读取许可。

    捕获还须批准独占原attempt的最终保存图并核实此前没有其他在途图执行者。
    不能从包/摘要/角色自批；只读调用者事务，不取密/联网/提交或修改事务。
    """

    async def authorize(self, session: AsyncSession, *, request: RoutingCheckpointStorageRequest,
    ) -> RoutingCheckpointStoragePermit: ...


class _FinalState(TypedDict):
    packet: str


class PostgresRoutingFinalCheckpoint:
    """单实例一次实际最终图，同时实现原RoutingProductCheckpointReader。

    必须在独立路由执行中使用；原generic Agent图正在运行时禁止重用其thread。
    不创建schema/表，不清理或恢复旧checkpoint，不注册到Worker或自动生成批准。
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession], *, settings: Settings,
        authority: RoutingCheckpointStorageAuthority | None = None,
    ) -> None:
        schema = settings.langgraph_checkpoint_schema
        if (authority is None or not settings.database_url.startswith("postgresql")
            or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None
            or schema in {"public", "information_schema"} or schema.startswith("pg_")):
            raise RoutingResultDenied("ROUTING_CHECKPOINT_DEPENDENCIES_UNAVAILABLE")
        parsed = urlsplit(settings.database_url)
        self._database_scope = manifest_digest({"schema": schema, "host": parsed.hostname,
            "port": parsed.port, "database": parsed.path, "role": parsed.username,
            "scheme": parsed.scheme, "options": parsed.query})
        self._factory, self._settings, self._authority = factory, settings, authority
        self._schema = '"' + schema + '"'
        self._entered, self._finished = False, False
        self._input: RoutingRuntimeInput | None = None
        self._request: RoutingCheckpointStorageRequest | None = None
        self._policy: str | None = None
        self._checkpoint_id: str | None = None
        self._graph_started_at: datetime | None = None

    def close(self) -> None:
        self._input = None
        self._finished = False
        # entered不重置；未知或关闭后不重新执行图。

    async def _permit(self, session: AsyncSession, request: RoutingCheckpointStorageRequest,
        *, expires_at: datetime,
    ) -> RoutingCheckpointStoragePermit:
        transaction, started = session.get_transaction(), datetime.now(UTC)
        async with asyncio.timeout(0.25):
            permit = await self._authority.authorize(session, request=request)
        if (transaction is None or session.get_transaction() is not transaction or not transaction.is_active
            or not isinstance(permit, RoutingCheckpointStoragePermit)
            or permit.request_digest != manifest_digest(request.model_dump(mode="json"))
            or permit.actor_user_id != request.actor_user_id
            or not started <= permit.checked_at <= datetime.now(UTC) < permit.expires_at <= expires_at
            or request.purpose == "read" and (permit.expires_at - permit.checked_at).total_seconds() > 1
            or self._policy is not None and permit.retention_policy_digest != self._policy):
            raise RoutingResultDenied("ROUTING_CHECKPOINT_APPROVAL_UNCONFIRMED")
        return RoutingCheckpointStoragePermit.model_validate_json(permit.model_dump_json())

    async def _presence(self, session: AsyncSession, thread: str) -> bool:
        row = (await session.execute(text(f"""
            SELECT EXISTS(SELECT 1 FROM {self._schema}.checkpoints WHERE thread_id=:thread)
                OR EXISTS(SELECT 1 FROM {self._schema}.checkpoint_blobs WHERE thread_id=:thread)
                OR EXISTS(SELECT 1 FROM {self._schema}.checkpoint_writes WHERE thread_id=:thread)
        """), {"thread": thread})).scalar_one()
        return row is not False

    async def _stored(self, session: AsyncSession, request: RoutingCheckpointStorageRequest) -> tuple[str, dict[str, object]]:
        # SQL先限制每份JSON文本的大小；读取只解析JSON，不加载blob或调用Saver反序列化。
        rows = (await session.execute(text(f"""
            SELECT checkpoint_ns,checkpoint_id,parent_checkpoint_id,
                CASE WHEN octet_length(checkpoint::text)<=:limit THEN checkpoint::text END AS payload,
                CASE WHEN octet_length(metadata::text)<=16384 THEN metadata::text END AS metadata
            FROM {self._schema}.checkpoints WHERE thread_id=:thread ORDER BY checkpoint_id LIMIT 4
        """), {"thread": request.checkpoint_thread_id, "limit": SERIALIZED_BYTES_MAX})).mappings().all()
        if len(rows) != 3 or any(row["checkpoint_ns"] != "" or row["payload"] is None or row["metadata"] is None for row in rows):
            raise RoutingResultDenied("ROUTING_CHECKPOINT_HISTORY_UNCONFIRMED")
        final = rows[-1]
        data = _json_object(final["payload"], bytes_limit=SERIALIZED_BYTES_MAX)
        metadata = _json_object(final["metadata"], bytes_limit=16384)
        timestamp = data.get("ts")
        if (not isinstance(timestamp, str) or self._graph_started_at is None
            or not self._graph_started_at <= datetime.fromisoformat(timestamp) <= datetime.now(UTC)):
            raise RoutingResultDenied("ROUTING_CHECKPOINT_TIMESTAMP_INVALID")
        versions, seen, values = data.get("channel_versions"), data.get("versions_seen"), data.get("channel_values")
        if (type(data.get("v")) is not int or data.get("v") != LATEST_VERSION or data.get("id") != final["checkpoint_id"]
            or metadata.get("source") != "loop" or type(metadata.get("step")) is not int or metadata.get("step") != 1
            or not isinstance(versions, dict) or set(versions) != {"__start__", "packet", "branch:to:seal"}
            or not isinstance(seen, dict) or not isinstance(seen.get("seal"), dict)
            or seen["seal"].get("branch:to:seal") != versions["branch:to:seal"]
            or not isinstance(seen.get("__start__"), dict) or seen["__start__"].get("__start__") != versions["__start__"]
            or data.get("pending_sends") not in (None, []) or not isinstance(values, dict) or set(values) != {"packet"}
            or not isinstance(values["packet"], str)
            or rows[0]["parent_checkpoint_id"] is not None
            or any(row["parent_checkpoint_id"] != previous["checkpoint_id"] for previous, row in zip(rows, rows[1:]))):
            raise RoutingResultDenied("ROUTING_CHECKPOINT_FINAL_STATE_INVALID")
        packet = _json_object(values["packet"], bytes_limit=PACKET_BYTES_MAX)
        if (len(values["packet"].encode("utf-8")) != request.packet_bytes
            or manifest_digest(packet) != request.packet_digest):
            raise RoutingResultDenied("ROUTING_CHECKPOINT_PACKET_CHANGED")
        pending = await session.scalar(text(f"""
            SELECT EXISTS(SELECT 1 FROM {self._schema}.checkpoint_writes
                WHERE thread_id=:thread AND (checkpoint_ns<>'' OR checkpoint_id=:checkpoint))
                OR EXISTS(SELECT 1 FROM {self._schema}.checkpoint_blobs
                    WHERE thread_id=:thread AND (checkpoint_ns<>'' OR channel<>'__start__'
                        OR type NOT IN ('routing_json_v1','empty')))
        """), {"thread": request.checkpoint_thread_id, "checkpoint": final["checkpoint_id"]})
        if pending is not False:
            raise RoutingResultDenied("ROUTING_CHECKPOINT_PENDING_WORK_PRESENT")
        return final["checkpoint_id"], packet

    async def capture(self, *, input: RoutingRuntimeInput, coordination: RoutingCoordinationResult,
        controls: RoutingFailoverControls, reservation_authority: RoutingReservationAuthority,
        boundary_reader: RoutingDataBoundaryReader, redaction_secrets: tuple[str, ...] = (),
    ) -> None:
        if self._entered:
            raise RoutingResultDenied("ROUTING_CHECKPOINT_ALREADY_ENTERED")
        self._entered = True
        self._input = input
        root = input.plan
        input.claim_checkpoint_capture()
        if (version("langgraph") != "1.2.11" or version("langgraph-checkpoint") != "4.2.0"
            or version("langgraph-checkpoint-postgres") != "3.1.2"):
            raise RoutingResultDenied("ROUTING_CHECKPOINT_VERSION_UNSUPPORTED")
        try:
            async with asyncio.timeout(min(8, max(0, (root.expires_at - datetime.now(UTC)).total_seconds()))):
                async with self._factory() as session, session.begin():
                    prepared = await prepare_routing_support_result(session, input=input, coordination=coordination,
                        controls=controls, settings=self._settings, authority=reservation_authority,
                        boundary_reader=boundary_reader, redaction_secrets=redaction_secrets)
                    logical = await input.read(session, plan=root)
                    owned = await owned_run(session, _identity(root))
                    packet = {"schema_version": "routing_final_checkpoint_packet_v1", "root_call_id": str(root.call_id),
                        "root_plan_digest": manifest_digest(root.model_dump(mode="json")),
                        "prepared_scope_digest": _scope(prepared), "logical_input": logical.model_dump(mode="json"),
                        "canonical_answer": prepared.answer.model_dump(mode="json"),
                        "support_result": prepared.support_result.model_dump(mode="json")}
                    payload = _json(packet, limit=PACKET_BYTES_MAX)
                    if redact_sensitive_text(payload, secrets=(self._settings.jwt_secret,
                        self._settings.secret_encryption_key, *redaction_secrets)) != payload:
                        raise RoutingResultDenied("ROUTING_CHECKPOINT_REDACTION_REQUIRED")
                    request = RoutingCheckpointStorageRequest(purpose="capture", root_call_id=root.call_id,
                        tenant_id=root.requirements.tenant_id, run_id=root.run_id, attempt_id=root.attempt_id,
                        generation=root.generation,
                        root_plan_digest=manifest_digest(root.model_dump(mode="json")), actor_user_id=prepared.binding.actor_user_id,
                        checkpoint_thread_id=owned.attempt.checkpoint_thread_id, database_scope_digest=self._database_scope,
                        prepared_scope_digest=_scope(prepared), packet_digest=manifest_digest(packet),
                        packet_bytes=len(payload.encode("utf-8")))
                    if await self._presence(session, request.checkpoint_thread_id):
                        raise RoutingResultDenied("ROUTING_CHECKPOINT_THREAD_ALREADY_USED")
                    permit = await self._permit(session, request, expires_at=root.expires_at)
                    await input.read(session, plan=root)
                if datetime.now(UTC) >= permit.expires_at:
                    raise RoutingResultDenied("ROUTING_CHECKPOINT_APPROVAL_EXPIRED")
                self._request, self._policy = request, permit.retention_policy_digest
                self._graph_started_at = datetime.now(UTC)

                async def seal(state: _FinalState) -> _FinalState:
                    if state != {"packet": payload} or datetime.now(UTC) >= permit.expires_at:
                        raise RoutingResultDenied("ROUTING_CHECKPOINT_INPUT_CHANGED")
                    return {"packet": payload}

                async with asyncio.timeout(max(0, (permit.expires_at - datetime.now(UTC)).total_seconds())):
                    # 独立的固定最终图真实运行；沿用已有Saver，明确不调用setup或执行DDL。
                    async with AsyncPostgresSaver.from_conn_string(_psycopg_url(self._settings.database_url,
                        self._settings.langgraph_checkpoint_schema), serde=_JSONSerializer()) as saver:
                        builder = StateGraph(_FinalState)
                        builder.add_node("seal", seal)
                        builder.add_edge(START, "seal")
                        builder.add_edge("seal", END)
                        graph = builder.compile(checkpointer=saver)
                        # 空callbacks不能禁用环境自动追踪；显式隔离父追踪，并拒绝自动处理器。
                        with tracing_context(enabled=False, parent=False):
                            callbacks = AsyncCallbackManager.configure(
                                inheritable_callbacks=[], local_callbacks=[], verbose=False)
                            if callbacks.handlers or callbacks.inheritable_handlers:
                                raise RoutingResultDenied("ROUTING_CHECKPOINT_CALLBACKS_UNSUPPORTED")
                            result = await graph.ainvoke({"packet": payload}, config={"configurable": {
                                "thread_id": request.checkpoint_thread_id, "checkpoint_ns": ""},
                                "callbacks": callbacks, "recursion_limit": 3}, durability="sync")
                        if result != {"packet": payload}:
                            raise RoutingResultDenied("ROUTING_CHECKPOINT_RESULT_CHANGED")
                # ainvoke及Saver连接均已退出，才登记本实例已结束；未知绝不重跑或取旧head替代。
                async with self._factory() as session, session.begin():
                    await input.read(session, plan=root)
                    checkpoint_id, _ = await self._stored(session, request)
                    if datetime.now(UTC) >= permit.expires_at or input.plan != root:
                        raise RoutingResultDenied("ROUTING_CHECKPOINT_APPROVAL_EXPIRED")
                    self._checkpoint_id, self._finished = checkpoint_id, True
        except BaseException as exc:
            self._finished = False
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RoutingResultDenied("ROUTING_CHECKPOINT_CAPTURE_UNCONFIRMED") from None

    async def read(self, session: AsyncSession, *, input: RoutingRuntimeInput,
        prepared: PreparedRoutingSupportResult,
    ) -> RoutingProductCheckpoint | None:
        if not self._finished or self._input is not input or self._request is None or self._checkpoint_id is None:
            raise RoutingResultDenied("ROUTING_CHECKPOINT_EXECUTION_NOT_FINISHED")
        root, original = input.plan, self._request
        transaction = session.get_transaction()
        if transaction is None or not transaction.is_active or session.get_bind().dialect.name != "postgresql":
            raise RoutingResultDenied("ROUTING_CHECKPOINT_TRANSACTION_REQUIRED")
        try:
            async with asyncio.timeout(0.5):
                logical = await input.read(session, plan=root)
                if _scope(prepared) != original.prepared_scope_digest:
                    raise RoutingResultDenied("ROUTING_CHECKPOINT_PREPARED_CHANGED")
                request = original.model_copy(update={"purpose": "read"})
                permit = await self._permit(session, request, expires_at=min(root.expires_at, prepared.binding.expires_at))
                checkpoint_id, packet = await self._stored(session, request)
                if (checkpoint_id != self._checkpoint_id or packet.get("logical_input") != logical.model_dump(mode="json")
                    or packet.get("canonical_answer") != prepared.answer.model_dump(mode="json")
                    or packet.get("support_result") != prepared.support_result.model_dump(mode="json")):
                    raise RoutingResultDenied("ROUTING_CHECKPOINT_RESULT_CHANGED")
                owned = await owned_run(session, _identity(root))
                checked = datetime.now(UTC)
                if (session.get_transaction() is not transaction or not transaction.is_active
                    or checked >= permit.expires_at or input.plan != root or owned.run.input_sequence is None):
                    raise RoutingResultDenied("ROUTING_CHECKPOINT_READ_EXPIRED")
                return RoutingProductCheckpoint(root_plan_digest=original.root_plan_digest, run_id=root.run_id,
                    attempt_id=root.attempt_id, generation=root.generation, checkpoint_thread_id=original.checkpoint_thread_id,
                    checkpoint_id=checkpoint_id, input_sequence=owned.run.input_sequence, model_input_digest=root.model_input_digest,
                    context_snapshot_digest=root.context_snapshot_digest, canonical_answer_digest=prepared.binding.canonical_answer_digest,
                    support_result_digest=prepared.binding.support_result_digest, history_source_digest=prepared.binding.history_source_digest,
                    checked_at=checked, expires_at=min(checked + timedelta(seconds=1), permit.expires_at))
        except Exception:
            raise RoutingResultDenied("ROUTING_CHECKPOINT_READ_UNCONFIRMED") from None
