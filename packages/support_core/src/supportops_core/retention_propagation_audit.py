"""独立PG维护身份的传播核对审计；只追加最小证明，不访问目标副本。"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field
from sqlalchemy import case, cast, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.types import Text

from supportops_core.models import RetentionPropagationEvent
from supportops_core.retention_batch_contracts import Digest, RetentionBatchContract, batch_digest
from supportops_core.retention_propagation import PropagationAuthority
from supportops_core.retention_propagation_contracts import PropagationAudit, PropagationRequest

MAX_EVENTS = 18
MAX_EVENT_BYTES = 8192
HISTORY_GUARD_BODY = "BEGIN RAISE EXCEPTION 'RETENTION_PROPAGATION_HISTORY_IMMUTABLE'; END;"


class PropagationAuditScope(RetentionBatchContract):
    expected_database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$", repr=False)
    expected_maintenance_role: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$", repr=False)
    tenant_id: UUID
    subject_user_id: UUID


class PropagationAuditRecord(RetentionBatchContract):
    schema_version: Literal["retention_propagation_record_v1"] = "retention_propagation_record_v1"
    tenant_id: UUID
    subject_user_id: UUID
    revision: int = Field(ge=1, le=MAX_EVENTS)
    previous_digest: Digest | None
    audit: PropagationAudit
    recorded_at: AwareDatetime


class PropagationHistoryReadAuthority(Protocol):
    async def assert_history_read_allowed(self, request: PropagationRequest) -> None:
        """核对当前独立维护者的历史读取范围；原核对请求到期不产生或撤回读取许可。"""
        ...


class PropagationAuditUnavailable(RuntimeError):
    code = "RETENTION_PROPAGATION_AUDIT_UNCONFIRMED"


class PostgresPropagationAuditSink:
    """显式独立引擎/主体/固定请求；不创建角色、迁移、缓存许可或自动重放。"""

    def __init__(self, engine: AsyncEngine, scope: PropagationAuditScope, request: PropagationRequest, *,
        authority: PropagationAuthority,
    ) -> None:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(scope, PropagationAuditScope) or not isinstance(request, PropagationRequest)
            or scope.tenant_id != request.fact.tenant_id or scope.subject_user_id != request.fact.subject_user_id
            or authority is None or authority.authority_id != request.authority_id):
            raise PropagationAuditUnavailable()
        self._engine, self._scope, self._request, self._authority = engine, scope, request, authority
        self._digest = batch_digest(request)
        raw = f"supportops_propagation_audit_v1:{request.request_id}".encode("ascii")
        self._lock_key = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)

    async def _current(self) -> None:
        if (self._authority.authority_id != self._request.authority_id
            or not self._request.created_at <= datetime.now(UTC) < self._request.expires_at):
            raise ValueError()
        await self._authority.assert_current(self._request)
        if not self._request.created_at <= datetime.now(UTC) < self._request.expires_at:
            raise ValueError()

    async def _database(self, connection: AsyncConnection, *, write: bool) -> None:
        await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE" if write
            else "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await connection.execute(text("SELECT set_config('statement_timeout','100ms',true), "
            "set_config('lock_timeout','50ms',true)"))
        row = (await connection.execute(text("""
            SELECT current_database() AS db,current_user AS role,session_user AS original_role,
                current_schemas(true) AS schemas,current_setting('session_replication_role') AS replication,
                current_setting('transaction_read_only') AS readonly,
                r.rolsuper,r.rolbypassrls,r.rolcreaterole,r.rolcreatedb,
                c.relkind,c.relrowsecurity,c.relforcerowsecurity,c.relowner=r.oid AS owns_table,
                has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
                has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
                (has_table_privilege(current_user,c.oid,'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(current_user,c.oid,'UPDATE,REFERENCES')) AS other_write,
                has_schema_privilege(current_user,n.oid,'CREATE') AS can_ddl,
                EXISTS(SELECT 1 FROM pg_catalog.pg_class oc JOIN pg_catalog.pg_namespace ons ON ons.oid=oc.relnamespace
                    WHERE ons.nspname='public' AND oc.relkind IN ('r','p','v','m','f')
                    AND oc.relname<>'retention_propagation_events'
                    AND (has_table_privilege(current_user,oc.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                        OR has_any_column_privilege(current_user,oc.oid,'INSERT,UPDATE,REFERENCES'))) AS writes_other_tables
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_roles r ON r.rolname=current_user
            WHERE n.nspname='public' AND c.relname='retention_propagation_events'
        """))).mappings().one()
        if (row["db"] != self._scope.expected_database or row["role"] != self._scope.expected_maintenance_role
            or row["original_role"] != self._scope.expected_maintenance_role
            or list(row["schemas"]) != ["pg_catalog", "public"] or row["replication"] != "origin"
            or row["readonly"] != ("off" if write else "on") or row["relkind"] != "r"
            or not row["can_select"] or not row["can_insert"]
            or any(row[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "owns_table",
                "relrowsecurity", "relforcerowsecurity", "other_write", "can_ddl", "writes_other_tables"))):
            raise ValueError()
        await self._guards(connection)

    async def _guards(self, connection: AsyncConnection) -> None:
        rows = (await connection.execute(text("""
            SELECT t.tgname,t.tgtype,t.tgenabled,t.tgisinternal,t.tgdeferrable,t.tginitdeferred,
                t.tgconstraint,t.tgnargs,t.tgargs,t.tgattr::text AS columns,t.tgqual IS NULL AS unconditional,
                p.oid,CASE WHEN octet_length(p.prosrc)<=512 THEN p.prosrc ELSE NULL END AS body,
                p.prosecdef,p.proconfig,p.pronargs,p.prorettype='pg_catalog.trigger'::regtype AS result,
                p.proowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user) AS owns_function,l.lanname
            FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
            JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
            JOIN pg_catalog.pg_language l ON l.oid=p.prolang
            JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND pn.nspname='public' AND c.relname='retention_propagation_events'
                AND p.proname='supportops_guard_propagation_history'
                AND t.tgname IN ('retention_propagation_history_guard','retention_propagation_truncate_guard') LIMIT 3
        """))).mappings().all()
        expected = {"retention_propagation_history_guard": 27, "retention_propagation_truncate_guard": 34}
        if len(rows) != 2 or {r["tgname"] for r in rows} != set(expected) or len({r["oid"] for r in rows}) != 1:
            raise ValueError()
        for row in rows:
            if (row["body"] != HISTORY_GUARD_BODY or row["prosecdef"] or row["pronargs"] != 0
                or list(row["proconfig"] or []) != ["search_path=pg_catalog"] or not row["result"]
                or row["owns_function"] or row["lanname"] != "plpgsql" or row["tgtype"] != expected[row["tgname"]]
                or row["tgenabled"] != "A" or row["tgisinternal"] or row["tgdeferrable"] or row["tginitdeferred"]
                or row["tgconstraint"] != 0 or row["tgnargs"] != 0 or bytes(row["tgargs"]) != b""
                or row["columns"] != "" or not row["unconditional"]):
                raise ValueError()

    def _next(self, history: list[PropagationAuditRecord], event: PropagationAudit, now: datetime) -> None:
        request = self._request
        if (event.request_id != request.request_id or event.request_digest != self._digest
            or event.fact_digest != request.fact.record_digest or event.inventory_digest != request.inventory_digest
            or not request.created_at <= event.observed_at <= now < request.expires_at
            or history and (history[-1].audit.event == "inspection_finished"
                or now < history[-1].recorded_at or event.observed_at < history[-1].audit.observed_at)
            or event.event_id in {record.audit.event_id for record in history}):
            raise ValueError()
        position = len(history)
        if position == 0:
            if event.event != "inspection_started":
                raise ValueError()
        elif position <= len(request.targets):
            target = request.targets[position - 1]
            result = event.result
            if (event.event != "target_observed" or result is None or result.target_id != target.target_id
                or result.kind != target.kind or not history[-1].recorded_at <= result.observed_at <= event.observed_at
                or result.reason == "confirmed" and result.state != request.fact.action
                or result.reason == "action_mismatch" and result.state == request.fact.action):
                raise ValueError()
        elif position != len(request.targets) + 1 or event.event != "inspection_finished":
            raise ValueError()

    async def _history(self, connection: AsyncConnection) -> list[PropagationAuditRecord]:
        table = RetentionPropagationEvent.__table__
        columns = [column if column.name != "payload" else case(
            (func.octet_length(cast(column, Text())) <= MAX_EVENT_BYTES, column), else_=None).label("payload")
            for column in table.c]
        rows = (await connection.execute(select(*columns).where(table.c.request_id == self._request.request_id)
            .order_by(table.c.revision).limit(MAX_EVENTS + 1))).mappings().all()
        if len(rows) > MAX_EVENTS:
            raise ValueError()
        history: list[PropagationAuditRecord] = []
        previous: str | None = None
        for row in rows:
            if row["payload"] is None:
                raise ValueError()
            record = PropagationAuditRecord.model_validate_json(json.dumps(row["payload"], allow_nan=False))
            if (record.tenant_id != self._scope.tenant_id or record.subject_user_id != self._scope.subject_user_id
                or record.revision != len(history) + 1 or record.previous_digest != previous
                or row["id"] != record.audit.event_id or row["tenant_id"] != record.tenant_id
                or row["subject_user_id"] != record.subject_user_id or row["revision"] != record.revision
                or row["request_digest"] != self._digest or row["event_type"] != record.audit.event
                or row["audit_digest"] != batch_digest(record.audit) or row["previous_digest"] != previous
                or row["record_digest"] != batch_digest(record) or row["recorded_at"] != record.recorded_at
                or record.recorded_at > datetime.now(UTC)):
                raise ValueError()
            self._next(history, record.audit, record.recorded_at)
            history.append(record)
            previous = row["record_digest"]
        return history

    async def record(self, event: PropagationAudit) -> None:
        """与协调器0.5秒审计预算配合；提交未知保留原历史，不自动重放。"""
        try:
            deadline = asyncio.get_running_loop().time() + 0.45
            async with asyncio.timeout(0.45):
                if not isinstance(event, PropagationAudit):
                    raise ValueError()
                await self._current()
                async with self._engine.connect() as connection, connection.begin():
                    await self._database(connection, write=True)
                    if await connection.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"),
                        {"key": self._lock_key}) is not True:
                        raise ValueError()
                    history = await self._history(connection)
                    now = datetime.now(UTC)
                    self._next(history, event, now)
                    record = PropagationAuditRecord(tenant_id=self._scope.tenant_id,
                        subject_user_id=self._scope.subject_user_id, revision=len(history) + 1,
                        previous_digest=batch_digest(history[-1]) if history else None, audit=event, recorded_at=now)
                    if len(record.model_dump_json().encode("utf-8")) > MAX_EVENT_BYTES:
                        raise ValueError()
                    await self._current()
                    await connection.execute(insert(RetentionPropagationEvent.__table__).values(
                        id=event.event_id, tenant_id=record.tenant_id, subject_user_id=record.subject_user_id,
                        request_id=event.request_id, request_digest=self._digest, revision=record.revision,
                        event_type=event.event, audit_digest=batch_digest(event), previous_digest=record.previous_digest,
                        record_digest=batch_digest(record), payload=record.model_dump(mode="json"), recorded_at=now))
                    await self._current()
                await self._current()
            if asyncio.get_running_loop().time() > deadline:
                raise ValueError()
        except Exception:
            raise PropagationAuditUnavailable() from None

    async def read_history(self, *, authority: PropagationHistoryReadAuthority,
    ) -> tuple[PropagationAuditRecord, ...]:
        """以新的独立读取许可复核原记录；允许读取到期请求，不重新运行目标观察。"""
        if authority is None:
            raise PropagationAuditUnavailable()
        try:
            deadline = asyncio.get_running_loop().time() + 2
            async with asyncio.timeout(2):
                await authority.assert_history_read_allowed(self._request)
                async with self._engine.connect() as connection, connection.begin():
                    await self._database(connection, write=False)
                    history = await self._history(connection)
                await authority.assert_history_read_allowed(self._request)
            if asyncio.get_running_loop().time() > deadline:
                raise ValueError()
            return tuple(history)
        except Exception:
            raise PropagationAuditUnavailable() from None
