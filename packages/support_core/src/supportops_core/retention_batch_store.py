"""独立PG维护身份的追加批次账本；不构造应用依赖、不调用目标执行器。"""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import case, cast, func, insert, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.types import Text
from sqlalchemy.sql.elements import ColumnElement

from supportops_core.models import DeletionTombstone, RetentionCleanupEvent
from supportops_core.retention_batch_contracts import (
    Identifier, RetentionBatchContract, RetentionBatchLease, RetentionBatchPlan,
    RetentionBatchReceipt, RetentionBatchRecoveryPermit, RetentionBatchStatus, RetentionBatchVerification,
    RetentionFactReference, batch_digest, require_batch_receipt, require_batch_verification,
    require_live_recovery_permit,
)
from supportops_core.retention_batch_events import (
    MAX_EVENT_BYTES, MAX_EVENTS, RetentionBatchEvent, RetentionBatchInspection, RetentionJobStatusPage,
    require_event_transition, require_live_event,
)
from supportops_core.retention_batch_runtime import Phase, RetentionBatchAuthority
from supportops_core.retention_tombstone_snapshot import _canonical, _fact

HISTORY_GUARD_BODY = "BEGIN RAISE EXCEPTION 'RETENTION_CLEANUP_HISTORY_IMMUTABLE'; END;"
MAX_JOB_HISTORY_BYTES = 4 * 1024 * 1024


class RetentionBatchStoreScope(RetentionBatchContract):
    expected_database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$", repr=False)
    expected_maintenance_role: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$", repr=False)
    tenant_id: UUID
    subject_user_id: UUID
    ledger_id: UUID
    maintenance_identity: Identifier


class RetentionBatchStoreUnavailable(RuntimeError):
    code = "RETENTION_BATCH_STORE_UNCONFIRMED"


class PostgresRetentionBatchStore:
    """仅接受显式独立引擎、单主体范围和当前authority；不缓存许可或隐式重试。"""

    def __init__(self, engine: AsyncEngine, scope: RetentionBatchStoreScope, *,
        authority: RetentionBatchAuthority,
    ) -> None:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(scope, RetentionBatchStoreScope) or authority is None):
            raise RetentionBatchStoreUnavailable()
        self._engine, self._scope, self._authority = engine, scope, authority
        raw = f"supportops_retention_subject_v1:{scope.tenant_id}:{scope.subject_user_id}".encode("ascii")
        self._lock_key = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)

    def _bound(self, plan: RetentionBatchPlan) -> None:
        if (not isinstance(plan, RetentionBatchPlan) or plan.tenant_id != self._scope.tenant_id
            or plan.subject_user_id != self._scope.subject_user_id or plan.ledger_id != self._scope.ledger_id
            or plan.maintenance_identity != self._scope.maintenance_identity):
            raise ValueError()

    async def _permit(self, plan: RetentionBatchPlan, *,
        purpose: Literal["execute", "verify", "read", "reconcile"],
    ) -> None:
        self._bound(plan)
        async with asyncio.timeout(0.5):
            await self._authority.assert_current(plan, purpose=purpose)

    async def _database(self, connection: AsyncConnection, *, write: bool,
        isolation: Literal["READ COMMITTED", "REPEATABLE READ"] = "READ COMMITTED",
    ) -> None:
        await connection.execute(text(f"SET TRANSACTION ISOLATION LEVEL {isolation}, "
            + ("READ WRITE" if write else "READ ONLY")))
        await connection.execute(text("SELECT set_config('statement_timeout','500ms',true), "
            "set_config('lock_timeout','250ms',true)"))
        rows = (await connection.execute(text("""
            SELECT current_database() AS db,current_user AS role,session_user AS original_role,
                current_schemas(true) AS schemas,current_setting('session_replication_role') AS replication,
                current_setting('transaction_read_only') AS readonly,r.rolsuper,r.rolbypassrls,r.rolcreaterole,r.rolcreatedb,
                c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,c.relowner=r.oid AS owns_table,
                has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
                has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
                (has_table_privilege(current_user,c.oid,'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(current_user,c.oid,'UPDATE,REFERENCES')) AS other_write,
                has_schema_privilege(current_user,n.oid,'CREATE') AS can_ddl
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_roles r ON r.rolname=current_user
            WHERE n.nspname='public' AND c.relname IN ('retention_cleanup_events','deletion_tombstones')
        """))).mappings().all()
        if len(rows) != 2 or {r["relname"] for r in rows} != {"retention_cleanup_events", "deletion_tombstones"}:
            raise ValueError()
        for row in rows:
            if (row["db"] != self._scope.expected_database or row["role"] != self._scope.expected_maintenance_role
                or row["original_role"] != self._scope.expected_maintenance_role
                or list(row["schemas"]) != ["pg_catalog", "public"] or row["replication"] != "origin"
                or row["readonly"] != ("off" if write else "on") or row["relkind"] != "r"
                or not row["can_select"] or not row["can_insert"]
                or any(row[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb",
                    "relrowsecurity", "relforcerowsecurity", "owns_table", "other_write", "can_ddl"))):
                raise ValueError()
        other_write = await connection.scalar(text("""
            SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
                AND c.relname NOT IN ('retention_cleanup_events','deletion_tombstones')
                AND (has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')))
        """))
        if other_write is not False:
            raise ValueError()
        await self._guards(connection)

    async def _guards(self, connection: AsyncConnection) -> None:
        rows = (await connection.execute(text("""
            SELECT c.relname,t.tgname,t.tgtype,t.tgenabled,t.tgisinternal,t.tgdeferrable,t.tginitdeferred,
                t.tgconstraint,t.tgnargs,t.tgargs,t.tgattr::text AS columns,t.tgqual IS NULL AS unconditional,
                p.oid,CASE WHEN octet_length(p.prosrc)<=512 THEN p.prosrc ELSE NULL END AS prosrc,
                p.prosecdef,p.proconfig,p.pronargs,p.prorettype='pg_catalog.trigger'::regtype AS result,
                p.proowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user) AS owns_function,l.lanname
            FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
            JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
            JOIN pg_catalog.pg_language l ON l.oid=p.prolang
            JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND pn.nspname='public' AND p.proname='supportops_guard_cleanup_history'
                AND c.relname IN ('retention_cleanup_events','deletion_tombstones')
                AND t.tgname IN ('retention_cleanup_history_guard','retention_cleanup_truncate_guard',
                    'cleanup_tombstone_history_guard','cleanup_tombstone_truncate_guard') LIMIT 5
        """))).mappings().all()
        expected = {("retention_cleanup_events", "retention_cleanup_history_guard"): 27,
            ("retention_cleanup_events", "retention_cleanup_truncate_guard"): 34,
            ("deletion_tombstones", "cleanup_tombstone_history_guard"): 27,
            ("deletion_tombstones", "cleanup_tombstone_truncate_guard"): 34}
        if (len(rows) != 4 or {(r["relname"], r["tgname"]) for r in rows} != set(expected)
            or len({r["oid"] for r in rows}) != 1):
            raise ValueError()
        for row in rows:
            if (row["prosrc"] != HISTORY_GUARD_BODY or row["prosecdef"] or row["pronargs"] != 0
                or list(row["proconfig"] or []) != ["search_path=pg_catalog"] or not row["result"]
                or row["owns_function"] or row["lanname"] != "plpgsql"
                or row["tgtype"] != expected[(row["relname"], row["tgname"])] or row["tgenabled"] != "A"
                or row["tgisinternal"] or row["tgdeferrable"] or row["tginitdeferred"] or row["tgconstraint"] != 0
                or row["tgnargs"] != 0 or bytes(row["tgargs"]) != b"" or row["columns"] != "" or not row["unconditional"]):
                raise ValueError()

    @asynccontextmanager
    async def _transaction(self, *, write: bool,
        isolation: Literal["READ COMMITTED", "REPEATABLE READ"] = "READ COMMITTED",
    ) -> AsyncIterator[AsyncConnection]:
        try:
            async with asyncio.timeout(2):
                async with self._engine.connect() as connection, connection.begin():
                    await self._database(connection, write=write, isolation=isolation)
                    if write and await connection.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"),
                        {"key": self._lock_key}) is not True:
                        raise ValueError()
                    yield connection
        except Exception:
            raise RetentionBatchStoreUnavailable() from None

    def _filter(self) -> tuple[ColumnElement[bool], ...]:
        table = RetentionCleanupEvent.__table__
        return (table.c.tenant_id == self._scope.tenant_id, table.c.subject_user_id == self._scope.subject_user_id)

    async def _history(self, connection: AsyncConnection, batch_id: UUID, *,
        byte_budget: list[int] | None = None,
    ) -> list[RetentionBatchEvent]:
        if not isinstance(batch_id, UUID):
            raise ValueError()
        table = RetentionCleanupEvent.__table__
        columns = [column if column.name != "payload" else case(
            (func.octet_length(cast(column, Text())) <= MAX_EVENT_BYTES, column), else_=None).label("payload")
            for column in table.c]
        rows = (await connection.execute(select(*columns).where(*self._filter(), table.c.batch_id == batch_id)
            .order_by(table.c.revision).limit(MAX_EVENTS + 1))).mappings().all()
        if len(rows) > MAX_EVENTS:
            raise ValueError()
        events: list[RetentionBatchEvent] = []
        for row in rows:
            raw = json.dumps(row["payload"], ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(raw) > MAX_EVENT_BYTES:
                raise ValueError()
            if byte_budget is not None:
                byte_budget[0] -= len(raw)
                if byte_budget[0] < 0:
                    raise ValueError()
            event = RetentionBatchEvent.model_validate_json(raw)
            self._bound(event.plan)
            if (row["id"] != event.id or row["job_id"] != event.plan.job_id or row["batch_id"] != event.plan.batch_id
                or row["batch_sequence"] != event.plan.sequence or row["revision"] != event.revision
                or row["state"] != event.status.state or row["plan_digest"] != batch_digest(event.plan)
                or row["event_digest"] != batch_digest(event) or row["previous_digest"] != event.previous_digest
                or row["recorded_at"] != event.status.recorded_at or event.status.recorded_at > datetime.now(UTC)):
                raise ValueError()
            require_event_transition(events[-1] if events else None, event)
            events.append(event)
        return events

    async def _current(self, connection: AsyncConnection, lease: RetentionBatchLease, *, live: bool = True) -> RetentionBatchEvent:
        if not isinstance(lease, RetentionBatchLease):
            raise ValueError()
        events = await self._history(connection, lease.batch_id)
        if not events or events[-1].lease != lease:
            raise ValueError()
        event = events[-1]
        if live:
            require_live_event(event, lease)
        return event

    def _event(self, previous: RetentionBatchEvent | None, plan: RetentionBatchPlan, lease: RetentionBatchLease,
        *, kind: Literal["claimed", "dispatched", "verification_started", "recorded", "unconfirmed",
            "reconciliation_started", "reconciled"],
        state: Literal["pending", "claimed", "dispatched", "verifying", "completed", "not_executed", "needs_attention"],
        now: datetime, dispatched_at: datetime | None, attempts: int,
        verification_id: UUID | None = None, started_at: datetime | None = None,
        verification: RetentionBatchVerification | None = None, receipt: RetentionBatchReceipt | None = None,
        recovery_permit: RetentionBatchRecoveryPermit | None = None,
    ) -> RetentionBatchEvent:
        event = RetentionBatchEvent(id=uuid4(), revision=previous.revision + 1 if previous else 1,
            previous_digest=batch_digest(previous) if previous else None, event_type=kind, plan=plan, lease=lease,
            status=RetentionBatchStatus(job_id=plan.job_id, batch_id=plan.batch_id, plan_digest=batch_digest(plan),
                state=state, claim_attempts=lease.claim_attempt, verification_attempts=attempts,
                dispatched_at=dispatched_at, recorded_at=now), verification_id=verification_id,
            verification_started_at=started_at, verification=verification, receipt=receipt,
            recovery_permit=recovery_permit)
        require_event_transition(previous, event)
        return event

    async def _append(self, connection: AsyncConnection, event: RetentionBatchEvent) -> None:
        payload = event.model_dump(mode="json")
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_EVENT_BYTES:
            raise ValueError()
        await connection.execute(insert(RetentionCleanupEvent.__table__).values(id=event.id,
            tenant_id=event.plan.tenant_id, subject_user_id=event.plan.subject_user_id, job_id=event.plan.job_id,
            batch_id=event.plan.batch_id, batch_sequence=event.plan.sequence, revision=event.revision,
            state=event.status.state, plan_digest=batch_digest(event.plan), event_digest=batch_digest(event),
            previous_digest=event.previous_digest, payload=payload, recorded_at=event.status.recorded_at))

    async def _new_batch(self, connection: AsyncConnection, plan: RetentionBatchPlan) -> None:
        table = RetentionCleanupEvent.__table__
        latest = (select(table.c.batch_id, table.c.state).where(*self._filter()).distinct(table.c.batch_id)
            .order_by(table.c.batch_id, table.c.revision.desc()).subquery())
        if await connection.scalar(select(latest.c.batch_id).where(latest.c.state != "completed").limit(1)) is not None:
            raise ValueError()
        prior = (await connection.execute(select(table.c.batch_id, table.c.batch_sequence).where(*self._filter(),
            table.c.job_id == plan.job_id, table.c.revision == 1).order_by(table.c.batch_sequence.desc()).limit(1))).first()
        if plan.sequence != (prior.batch_sequence + 1 if prior else 1):
            raise ValueError()
        if prior:
            previous = (await self._history(connection, prior.batch_id))[-1]
            excluded = {"batch_id", "sequence", "resource_key_digests"}
            if (previous.status.state != "completed"
                or previous.plan.model_dump(exclude=excluded) != plan.model_dump(exclude=excluded)):
                raise ValueError()
        resources = table.c.payload["plan"]["resource_key_digests"]
        duplicate = await connection.scalar(select(table.c.id).where(*self._filter(), table.c.revision == 1,
            table.c.payload["plan"]["category"].as_string() == plan.category,
            table.c.payload["plan"]["operation"].as_string() == plan.operation,
            resources.op("?|")(cast(list(plan.resource_key_digests), ARRAY(Text())))).limit(1))
        if duplicate is not None:
            raise ValueError()
        tombstones = DeletionTombstone.__table__
        if await connection.scalar(select(tombstones.c.id).where(tombstones.c.tenant_id == plan.tenant_id,
            tombstones.c.subject_user_id == plan.subject_user_id, tombstones.c.category == plan.category,
            tombstones.c.action == plan.operation, tombstones.c.resource_key_digest.in_(plan.resource_key_digests))
            .limit(1)) is not None:
            raise ValueError()

    async def claim(self, plan: RetentionBatchPlan, *, owner_id: UUID) -> RetentionBatchLease:
        async with self._transaction(write=True) as connection:
            self._bound(plan)
            if not isinstance(owner_id, UUID):
                raise ValueError()
            await self._permit(plan, purpose="execute")
            events = await self._history(connection, plan.batch_id)
            previous = events[-1] if events else None
            now = datetime.now(UTC)
            if not plan.created_at <= now < plan.expires_at:
                raise ValueError()
            if previous is not None:
                if previous.plan != plan or previous.status.dispatched_at is not None or now < previous.lease.expires_at:
                    raise ValueError()
            else:
                await self._new_batch(connection, plan)
            lease = RetentionBatchLease(batch_id=plan.batch_id, plan_digest=batch_digest(plan), lease_id=uuid4(),
                owner_id=owner_id, generation=previous.lease.generation + 1 if previous else 1,
                claim_attempt=previous.lease.claim_attempt + 1 if previous else 1, claimed_at=now,
                expires_at=min(now + timedelta(seconds=30), plan.expires_at))
            event = self._event(previous, plan, lease, kind="claimed", state="claimed", now=now,
                dispatched_at=None, attempts=0)
            await self._append(connection, event)
            await self._permit(plan, purpose="execute")
            require_live_event(event, lease)
        return lease

    async def assert_current(self, lease: RetentionBatchLease, *, phase: Phase) -> None:
        async with self._transaction(write=False) as connection:
            event = await self._current(connection, lease)
            expected = event.receipt is not None if phase == "recorded" else event.status.state == phase
            if phase not in {"claimed", "dispatched", "verifying", "recorded"} or not expected:
                raise ValueError()
            await self._permit(event.plan, purpose="verify" if phase in {"verifying", "recorded"} else "execute")
            require_live_event(event, lease)

    async def mark_dispatched(self, lease: RetentionBatchLease) -> None:
        async with self._transaction(write=True) as connection:
            previous = await self._current(connection, lease)
            await self._permit(previous.plan, purpose="execute")
            now = datetime.now(UTC)
            event = self._event(previous, previous.plan, lease, kind="dispatched", state="dispatched", now=now,
                dispatched_at=now, attempts=0)
            await self._append(connection, event)
            await self._permit(previous.plan, purpose="execute")
            require_live_event(event, lease)

    async def begin_verification(self, lease: RetentionBatchLease, *, verification_id: UUID, max_attempts: int) -> None:
        async with self._transaction(write=True) as connection:
            previous = await self._current(connection, lease)
            if (not isinstance(verification_id, UUID) or type(max_attempts) is not int
                or max_attempts != previous.plan.max_verification_attempts
                or previous.status.verification_attempts >= max_attempts):
                raise ValueError()
            await self._permit(previous.plan, purpose="verify")
            now = datetime.now(UTC)
            event = self._event(previous, previous.plan, lease, kind="verification_started", state="verifying", now=now,
                dispatched_at=previous.status.dispatched_at, attempts=previous.status.verification_attempts + 1,
                verification_id=verification_id, started_at=now)
            await self._append(connection, event)
            await self._permit(previous.plan, purpose="verify")
            require_live_event(event, lease)

    async def _facts(self, connection: AsyncConnection, plan: RetentionBatchPlan,
        verification: RetentionBatchVerification, *, now: datetime,
    ) -> tuple[RetentionFactReference, ...]:
        verified = [item for item in verification.resources if item.status == "verified"]
        if not verified:
            return ()
        table = DeletionTombstone.__table__
        rows = (await connection.execute(select(table).where(table.c.tenant_id == plan.tenant_id,
            table.c.erasure_request_id == plan.erasure_request_id, table.c.category == plan.category,
            table.c.action == plan.operation, table.c.resource_key_digest.in_([r.resource_key_digest for r in verified]))
            .limit(101))).mappings().all()
        existing = {row["resource_key_digest"]: row for row in rows}
        if len(existing) != len(rows):
            raise ValueError()
        facts = []
        for item in verified:
            evidence_digest = item.evidence_digest
            if evidence_digest is None:
                raise ValueError()
            row: Mapping[str, object] | None = existing.get(item.resource_key_digest)
            if row is None:
                values = dict(id=uuid4(), tenant_id=plan.tenant_id, subject_user_id=plan.subject_user_id,
                    erasure_request_id=plan.erasure_request_id, category=plan.category,
                    resource_key_digest=item.resource_key_digest, scope_digest=plan.scope_digest,
                    action=plan.operation, evidence_digest=evidence_digest,
                    maintenance_identity=plan.maintenance_identity, recorded_at=now)
                body = {"schema_version": "supportops_deletion_fact_v1", **values}
                for key in ("id", "tenant_id", "subject_user_id", "erasure_request_id"):
                    body[key] = str(body[key])
                body["recorded_at"] = now.isoformat()
                values["record_digest"] = hashlib.sha256(_canonical(body)).hexdigest()
                _fact(values, now)
                await connection.execute(insert(table).values(**values))
                row = values
            _fact(dict(row), now)
            if (row["subject_user_id"] != plan.subject_user_id or row["scope_digest"] != plan.scope_digest
                or row["evidence_digest"] != evidence_digest or row["maintenance_identity"] != plan.maintenance_identity):
                raise ValueError()
            record_digest = row["record_digest"]
            if not isinstance(record_digest, str):
                raise ValueError()
            facts.append(RetentionFactReference(resource_key_digest=item.resource_key_digest,
                action=plan.operation, evidence_digest=evidence_digest, record_digest=record_digest))
        return tuple(facts)

    async def finalize(self, plan: RetentionBatchPlan, lease: RetentionBatchLease,
        verification: RetentionBatchVerification,
    ) -> RetentionBatchReceipt:
        async with self._transaction(write=True) as connection:
            previous = await self._current(connection, lease)
            if (previous.plan != plan or previous.status.state != "verifying" or previous.receipt is not None
                or previous.verification_id is None or previous.verification_started_at is None):
                raise ValueError()
            require_batch_verification(plan, lease, verification, verification_id=previous.verification_id,
                started_at=previous.verification_started_at)
            await self._permit(plan, purpose="verify")
            now = datetime.now(UTC)
            statuses = {item.status for item in verification.resources}
            state: Literal["completed", "not_executed", "needs_attention"] = (
                "completed" if statuses == {"verified"} else "not_executed" if statuses == {"not_executed"} else "needs_attention")
            facts = await self._facts(connection, plan, verification, now=now)
            receipt = RetentionBatchReceipt(batch_id=plan.batch_id, plan_digest=batch_digest(plan), lease_id=lease.lease_id,
                generation=lease.generation, verification_id=verification.verification_id,
                verification_digest=batch_digest(verification), ledger_id=plan.ledger_id, state=state,
                facts=facts, recorded_at=now)
            require_batch_receipt(plan, lease, verification, receipt)
            event = self._event(previous, plan, lease, kind="recorded", state=state, now=now,
                dispatched_at=previous.status.dispatched_at, attempts=previous.status.verification_attempts,
                verification_id=previous.verification_id, started_at=previous.verification_started_at,
                verification=verification, receipt=receipt)
            await self._append(connection, event)
            await self._permit(plan, purpose="verify")
            require_live_event(event, lease)
        return receipt

    def _recovery_binding(self, plan: RetentionBatchPlan, lease: RetentionBatchLease,
        permit: RetentionBatchRecoveryPermit,
    ) -> None:
        require_live_recovery_permit(permit)
        if (permit.plan_digest != batch_digest(plan) or permit.lease_id != lease.lease_id
            or permit.generation != lease.generation or permit.verifier_id != plan.verifier_id
            or permit.authority_id != self._authority.authority_id
            or permit.approver_id == str(plan.subject_user_id) or permit.issued_at < lease.expires_at):
            raise ValueError()

    async def begin_reconciliation(self, plan: RetentionBatchPlan, lease: RetentionBatchLease, *,
        permit: RetentionBatchRecoveryPermit, verification_id: UUID,
    ) -> None:
        async with self._transaction(write=True) as connection:
            previous = await self._current(connection, lease, live=False)
            now = datetime.now(UTC)
            self._bound(plan)
            self._recovery_binding(plan, lease, permit)
            if (previous.plan != plan or previous.status.dispatched_at is None or previous.receipt is not None
                or previous.status.state not in {"dispatched", "verifying", "needs_attention"}
                or now < lease.expires_at or not isinstance(verification_id, UUID)
                or previous.status.verification_attempts >= plan.max_verification_attempts):
                raise ValueError()
            permit_used = await connection.scalar(select(RetentionCleanupEvent.id).where(
                *self._filter(), RetentionCleanupEvent.batch_id == plan.batch_id,
                RetentionCleanupEvent.payload["recovery_permit"]["permit_id"].as_string() == str(permit.permit_id),
            ).limit(1))
            if permit_used is not None:
                raise ValueError()
            await self._permit(plan, purpose="reconcile")
            event = self._event(previous, plan, lease, kind="reconciliation_started", state="verifying",
                now=now, dispatched_at=previous.status.dispatched_at,
                attempts=previous.status.verification_attempts + 1, verification_id=verification_id,
                started_at=now, recovery_permit=permit)
            await self._append(connection, event)
            await self._permit(plan, purpose="reconcile")
            self._recovery_binding(plan, lease, permit)

    async def finalize_reconciliation(self, plan: RetentionBatchPlan, lease: RetentionBatchLease, *,
        permit: RetentionBatchRecoveryPermit, verification: RetentionBatchVerification,
    ) -> RetentionBatchReceipt:
        async with self._transaction(write=True) as connection:
            previous = await self._current(connection, lease, live=False)
            self._recovery_binding(plan, lease, permit)
            if (previous.plan != plan or previous.event_type != "reconciliation_started"
                or previous.recovery_permit != permit or previous.receipt is not None
                or previous.verification_id is None or previous.verification_started_at is None
                or datetime.now(UTC) >= permit.expires_at):
                raise ValueError()
            require_batch_verification(plan, lease, verification, verification_id=previous.verification_id,
                started_at=previous.verification_started_at)
            await self._permit(plan, purpose="reconcile")
            now = datetime.now(UTC)
            self._recovery_binding(plan, lease, permit)
            statuses = {item.status for item in verification.resources}
            state: Literal["completed", "not_executed", "needs_attention"] = (
                "completed" if statuses == {"verified"} else "not_executed"
                if statuses == {"not_executed"} else "needs_attention")
            facts = await self._facts(connection, plan, verification, now=now)
            receipt = RetentionBatchReceipt(batch_id=plan.batch_id, plan_digest=batch_digest(plan),
                lease_id=lease.lease_id, generation=lease.generation, verification_id=verification.verification_id,
                verification_digest=batch_digest(verification), ledger_id=plan.ledger_id, state=state,
                facts=facts, recorded_at=now)
            require_batch_receipt(plan, lease, verification, receipt)
            event = self._event(previous, plan, lease, kind="reconciled", state=state, now=now,
                dispatched_at=previous.status.dispatched_at, attempts=previous.status.verification_attempts,
                verification_id=previous.verification_id, started_at=previous.verification_started_at,
                verification=verification, receipt=receipt, recovery_permit=permit)
            await self._append(connection, event)
            await self._permit(plan, purpose="reconcile")
            self._recovery_binding(plan, lease, permit)
        return receipt

    async def record_unconfirmed(self, lease: RetentionBatchLease) -> None:
        async with self._transaction(write=True) as connection:
            previous = await self._current(connection, lease, live=False)
            # 过期许可只记录原派发的最小未知观察，不产生目标操作/续租或新权限。
            event = self._event(previous, previous.plan, lease, kind="unconfirmed",
                state=previous.status.state if previous.receipt is not None else "needs_attention",
                now=datetime.now(UTC), dispatched_at=previous.status.dispatched_at,
                attempts=previous.status.verification_attempts, verification_id=previous.verification_id,
                started_at=previous.verification_started_at, verification=previous.verification, receipt=previous.receipt)
            await self._append(connection, event)

    async def read_status(self, plan: RetentionBatchPlan) -> RetentionBatchInspection | None:
        """当前独立维护权限下读取原单批次元数据；不续租、调用目标或加载正文。"""
        async with self._transaction(write=False) as connection:
            await self._permit(plan, purpose="read")
            events = await self._history(connection, plan.batch_id)
            if events and events[-1].plan != plan:
                raise ValueError()
            await self._permit(plan, purpose="read")
            result = None
            if events:
                result = self._inspection(events[-1])
        return result

    @staticmethod
    def _inspection(event: RetentionBatchEvent) -> RetentionBatchInspection:
        now = datetime.now(UTC)
        expired = now >= event.lease.expires_at
        review = event.status.state == "needs_attention" or (expired and event.status.dispatched_at is not None
            and event.status.state not in {"completed", "not_executed"})
        return RetentionBatchInspection(sequence=event.plan.sequence, status=event.status, observed_at=now,
            lease_expires_at=event.lease.expires_at, lease_expired=expired, reconciliation_required=review)

    async def read_job_status(self, plan: RetentionBatchPlan, *, after_sequence: int = 0,
        page_size: int = 20,
    ) -> RetentionJobStatusPage:
        """当前独立维护权限下分页读取单一作业的最新批次投影，不扫描无界历史。"""
        if (type(after_sequence) is not int or not 0 <= after_sequence <= 1_000_000
            or type(page_size) is not int or not 1 <= page_size <= 20):
            raise RetentionBatchStoreUnavailable()
        async with self._transaction(write=False, isolation="REPEATABLE READ") as connection:
            self._bound(plan)
            await self._permit(plan, purpose="read")
            table = RetentionCleanupEvent.__table__
            rows = (await connection.execute(select(table.c.batch_id, table.c.batch_sequence)
                .where(*self._filter(), table.c.job_id == plan.job_id, table.c.revision == 1,
                    table.c.batch_sequence > after_sequence)
                .order_by(table.c.batch_sequence).limit(page_size + 1))).all()
            has_more = len(rows) > page_size
            selected = rows[:page_size]
            base_plan = plan.model_dump(exclude={"batch_id", "sequence", "resource_key_digests"})
            inspections = []
            byte_budget = [MAX_JOB_HISTORY_BYTES]
            if selected:
                selected_ids = [row.batch_id for row in selected]
                history_sizes = (await connection.execute(select(RetentionCleanupEvent.batch_id,
                    func.count(RetentionCleanupEvent.id).label("event_count"),
                    func.coalesce(func.sum(func.octet_length(cast(RetentionCleanupEvent.payload, Text()))), 0)
                        .label("payload_bytes"))
                    .where(*self._filter(), RetentionCleanupEvent.batch_id.in_(selected_ids))
                    .group_by(RetentionCleanupEvent.batch_id))).all()
                if (len(history_sizes) != len(selected) or any(row.event_count > MAX_EVENTS
                    or row.payload_bytes > MAX_JOB_HISTORY_BYTES for row in history_sizes)
                    or sum(row.payload_bytes for row in history_sizes) > MAX_JOB_HISTORY_BYTES):
                    raise ValueError()
            for row in selected:
                events = await self._history(connection, row.batch_id, byte_budget=byte_budget)
                if not events:
                    raise ValueError()
                event = events[-1]
                if (event.plan.job_id != plan.job_id or event.plan.sequence != row.batch_sequence
                    or event.plan.model_dump(exclude={"batch_id", "sequence", "resource_key_digests"}) != base_plan):
                    raise ValueError()
                inspections.append(self._inspection(event))
            await self._permit(plan, purpose="read")
            next_after = inspections[-1].sequence if has_more and inspections else None
            return RetentionJobStatusPage(job_id=plan.job_id, after_sequence=after_sequence,
                items=tuple(inspections), has_more=has_more, next_after_sequence=next_after)
