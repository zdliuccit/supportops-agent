"""恢复知识隔离的独立PG追加审计；唯一领取原请求意图，未知提交不重放。"""

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

from supportops_core.models import RestoreKnowledgeEvent
from supportops_core.restore_knowledge_contracts import RestoreKnowledgeAudit, RestoreKnowledgeRequest
from supportops_core.restore_knowledge_reapplication import RestoreKnowledgeAuthority, _authorize
from supportops_core.retention_batch_contracts import Digest, RetentionBatchContract, batch_digest
from supportops_core.schema_structure import require_public_schema_context

MAX_EVENTS = 8
MAX_EVENT_BYTES = 8192
HISTORY_GUARD_BODY = "BEGIN RAISE EXCEPTION 'RESTORE_KNOWLEDGE_HISTORY_IMMUTABLE'; END;"
_OBSERVATIONS = frozenset({"quarantine_verified", "target_not_observed", "quarantine_not_confirmed"})


class RestoreKnowledgeAuditScope(RetentionBatchContract):
    expected_database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$", repr=False)
    expected_maintenance_role: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$", repr=False)
    tenant_id: UUID
    subject_user_id: UUID


class RestoreKnowledgeAuditRecord(RetentionBatchContract):
    schema_version: Literal["restore_knowledge_record_v1"] = "restore_knowledge_record_v1"
    tenant_id: UUID
    subject_user_id: UUID
    revision: int = Field(ge=1, le=MAX_EVENTS)
    previous_digest: Digest | None
    audit: RestoreKnowledgeAudit
    recorded_at: AwareDatetime


class RestoreKnowledgeHistoryAuthority(Protocol):
    async def assert_history_read_allowed(self, request: RestoreKnowledgeRequest) -> None:
        """核对当前独立历史读取许可；原重施请求、apply/verify许可不能自动授予读取。"""
        ...


class RestoreKnowledgeAuditUnavailable(RuntimeError):
    code = "RESTORE_KNOWLEDGE_AUDIT_UNCONFIRMED"


class PostgresRestoreKnowledgeAuditSink:
    """仅接受显式独立审计引擎/主体/原请求；没有默认注册、维护目标写入或迁移。"""

    def __init__(self, engine: AsyncEngine, scope: RestoreKnowledgeAuditScope,
        request: RestoreKnowledgeRequest, *, authority: RestoreKnowledgeAuthority,
    ) -> None:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(scope, RestoreKnowledgeAuditScope) or not isinstance(request, RestoreKnowledgeRequest)
            or authority is None or authority.authority_id != request.propagation.authority_id
            or scope.tenant_id != request.propagation.fact.tenant_id
            or scope.subject_user_id != request.propagation.fact.subject_user_id
            or scope.expected_maintenance_role in {request.write_database.expected_maintenance_role,
                request.verification_database.expected_maintenance_role}):
            raise RestoreKnowledgeAuditUnavailable()
        self._engine, self._scope, self._request, self._authority = engine, scope, request, authority
        self._digest = batch_digest(request)
        raw = f"supportops_restore_knowledge_audit_v1:{request.request_id}".encode("ascii")
        self._lock_key = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)

    async def _database(self, connection: AsyncConnection, *, write: bool) -> None:
        await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE" if write
            else "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await connection.run_sync(require_public_schema_context)
        await connection.execute(text("SELECT pg_catalog.set_config('statement_timeout','100ms',true), "
            "pg_catalog.set_config('lock_timeout','50ms',true)"))
        row = (await connection.execute(text("""
            SELECT current_database() AS db,current_user AS role,session_user AS original_role,
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
                    AND oc.relname<>'restore_knowledge_events'
                    AND (has_table_privilege(current_user,oc.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                        OR has_any_column_privilege(current_user,oc.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS other_access
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_roles r ON r.rolname=current_user
            WHERE n.nspname='public' AND c.relname='restore_knowledge_events'
        """))).mappings().one()
        if (row["db"] != self._scope.expected_database or row["role"] != self._scope.expected_maintenance_role
            or row["original_role"] != self._scope.expected_maintenance_role
            or row["readonly"] != ("off" if write else "on") or row["relkind"] != "r"
            or not row["can_select"] or not row["can_insert"]
            or any(row[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "owns_table",
                "relrowsecurity", "relforcerowsecurity", "other_write", "can_ddl", "other_access"))):
            raise ValueError()
        await self._guards(connection)

    async def _guards(self, connection: AsyncConnection) -> None:
        rows = (await connection.execute(text("""
            SELECT t.tgname,t.tgtype,t.tgenabled,t.tgisinternal,t.tgdeferrable,t.tginitdeferred,
                t.tgconstraint,t.tgparentid,t.tgconstrrelid,t.tgconstrindid,t.tgnargs,t.tgargs,
                t.tgattr::text AS columns,t.tgqual IS NULL AS unconditional,
                t.tgoldtable IS NULL AND t.tgnewtable IS NULL AS no_transition_tables,
                p.oid,CASE WHEN octet_length(p.prosrc)<=512 THEN p.prosrc ELSE NULL END AS body,
                p.prosecdef,p.proconfig,p.pronargs,p.prorettype='pg_catalog.trigger'::regtype AS result,
                p.proowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user) AS owns_function,l.lanname
            FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
            JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
            JOIN pg_catalog.pg_language l ON l.oid=p.prolang
            JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND pn.nspname='public' AND c.relname='restore_knowledge_events'
                AND p.proname='supportops_guard_restore_knowledge_history'
                AND t.tgname IN ('restore_knowledge_history_guard','restore_knowledge_truncate_guard') LIMIT 3
        """))).mappings().all()
        expected = {"restore_knowledge_history_guard": 27, "restore_knowledge_truncate_guard": 34}
        if len(rows) != 2 or {row["tgname"] for row in rows} != set(expected) or len({row["oid"] for row in rows}) != 1:
            raise ValueError()
        for row in rows:
            if (row["body"] != HISTORY_GUARD_BODY or row["prosecdef"] or row["pronargs"] != 0
                or list(row["proconfig"] or []) != ["search_path=pg_catalog"] or not row["result"]
                or row["owns_function"] or row["lanname"] != "plpgsql" or row["tgtype"] != expected[row["tgname"]]
                or row["tgenabled"] != "A" or row["tgisinternal"] or row["tgdeferrable"] or row["tginitdeferred"]
                or any(row[name] != 0 for name in ("tgconstraint", "tgparentid", "tgconstrrelid", "tgconstrindid", "tgnargs"))
                or bytes(row["tgargs"]) != b"" or row["columns"] != "" or not row["unconditional"]
                or not row["no_transition_tables"]):
                raise ValueError()

    def _next(self, history: list[RestoreKnowledgeAuditRecord], event: RestoreKnowledgeAudit, now: datetime) -> None:
        request = self._request
        if (len(history) >= MAX_EVENTS or event.restore_id != request.restore_id or event.request_id != request.request_id
            or event.request_digest != self._digest or event.fact_digest != request.propagation.fact.record_digest
            or event.resource_key_digest != request.propagation.fact.resource_key_digest
            or not request.created_at <= event.observed_at <= now < request.expires_at
            or history and (event.observed_at < history[-1].audit.observed_at or now < history[-1].recorded_at)
            or event.event_id in {record.audit.event_id for record in history}):
            raise ValueError()
        if event.event == "quarantine_requested":
            if history:
                raise ValueError()
        elif event.event == "commit_observed":
            if len(history) != 1 or history[0].audit.event != "quarantine_requested":
                raise ValueError()
        elif event.event not in _OBSERVATIONS:
            raise ValueError()
        # 原请求可直接记录只读复核，但已有任何历史后永不允许领取写意图。

    async def _history(self, connection: AsyncConnection) -> list[RestoreKnowledgeAuditRecord]:
        table = RestoreKnowledgeEvent.__table__
        columns = [column if column.name != "payload" else case(
            (func.octet_length(cast(column, Text())) <= MAX_EVENT_BYTES, column), else_=None).label("payload")
            for column in table.c]
        rows = (await connection.execute(select(*columns).where(table.c.request_id == self._request.request_id)
            .order_by(table.c.revision).limit(MAX_EVENTS + 1))).mappings().all()
        if len(rows) > MAX_EVENTS:
            raise ValueError()
        history: list[RestoreKnowledgeAuditRecord] = []
        previous: str | None = None
        for row in rows:
            if row["payload"] is None:
                raise ValueError()
            record = RestoreKnowledgeAuditRecord.model_validate_json(json.dumps(row["payload"], allow_nan=False))
            if (record.tenant_id != self._scope.tenant_id or record.subject_user_id != self._scope.subject_user_id
                or record.revision != len(history) + 1 or record.previous_digest != previous
                or row["id"] != record.audit.event_id or row["restore_id"] != self._request.restore_id
                or row["tenant_id"] != record.tenant_id or row["subject_user_id"] != record.subject_user_id
                or row["revision"] != record.revision or row["request_digest"] != self._digest
                or row["event_type"] != record.audit.event or row["audit_digest"] != batch_digest(record.audit)
                or row["previous_digest"] != previous or row["record_digest"] != batch_digest(record)
                or row["recorded_at"] != record.recorded_at or record.recorded_at > datetime.now(UTC)):
                raise ValueError()
            self._next(history, record.audit, record.recorded_at)
            history.append(record)
            previous = row["record_digest"]
        return history

    async def record(self, event: RestoreKnowledgeAudit) -> None:
        try:
            deadline = asyncio.get_running_loop().time() + 0.45
            async with asyncio.timeout(0.45):
                if not isinstance(event, RestoreKnowledgeAudit):
                    raise ValueError()
                purpose: Literal["apply", "verify"] = "verify" if event.event in _OBSERVATIONS else "apply"
                await _authorize(self._request, self._authority, purpose)
                async with self._engine.connect() as connection, connection.begin():
                    await self._database(connection, write=True)
                    if await connection.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"),
                        {"key": self._lock_key}) is not True:
                        raise ValueError()
                    history = await self._history(connection)
                    now = datetime.now(UTC)
                    self._next(history, event, now)
                    record = RestoreKnowledgeAuditRecord(tenant_id=self._scope.tenant_id,
                        subject_user_id=self._scope.subject_user_id, revision=len(history) + 1,
                        previous_digest=batch_digest(history[-1]) if history else None, audit=event, recorded_at=now)
                    if len(record.model_dump_json().encode("utf-8")) > MAX_EVENT_BYTES:
                        raise ValueError()
                    await _authorize(self._request, self._authority, purpose)
                    await connection.execute(insert(RestoreKnowledgeEvent.__table__).values(
                        id=event.event_id, tenant_id=record.tenant_id, subject_user_id=record.subject_user_id,
                        restore_id=event.restore_id, request_id=event.request_id, request_digest=self._digest,
                        revision=record.revision, event_type=event.event, audit_digest=batch_digest(event),
                        previous_digest=record.previous_digest, record_digest=batch_digest(record),
                        payload=record.model_dump(mode="json"), recorded_at=now))
                    await _authorize(self._request, self._authority, purpose)
                await _authorize(self._request, self._authority, purpose)
            if asyncio.get_running_loop().time() > deadline:
                raise ValueError()
        except Exception:
            raise RestoreKnowledgeAuditUnavailable() from None

    async def read_history(self, *, authority: RestoreKnowledgeHistoryAuthority,
    ) -> tuple[RestoreKnowledgeAuditRecord, ...]:
        """独立当前许可下读取原链；原请求可到期，不读恢复标记、不查询或重放业务目标。"""
        if authority is None:
            raise RestoreKnowledgeAuditUnavailable()
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
            raise RestoreKnowledgeAuditUnavailable() from None
