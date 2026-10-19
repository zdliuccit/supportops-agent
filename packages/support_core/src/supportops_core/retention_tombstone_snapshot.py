"""独立维护者的有界只读删除事实快照；不创建删除事实或修改恢复库。"""

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from supportops_core.models import DeletionTombstone
from supportops_core.retention_contracts import DATA_CATEGORIES

MAX_FACTS = 4096
MAX_BYTES = 4 * 1024 * 1024


class TombstoneSnapshotUnavailable(RuntimeError):
    code = "TOMBSTONE_SNAPSHOT_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class TombstoneSnapshotScope:
    """由独立维护者提供；元数据绑定不证明账本身份或代次已获外部批准。"""

    mode: Literal["ledger", "restored"]
    expected_database: str = field(repr=False)
    expected_maintenance_role: str = field(repr=False)
    ledger_id: UUID | None = None
    ledger_generation: int | None = None
    restore_id: UUID | None = None
    backup_id: str | None = None
    target_alias: str | None = None


def _scope_valid(scope: TombstoneSnapshotScope) -> bool:
    if (scope.mode not in ("ledger", "restored")
        or not isinstance(scope.expected_database, str)
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,62}", scope.expected_database) is None
        or not isinstance(scope.expected_maintenance_role, str)
        or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", scope.expected_maintenance_role) is None):
        return False
    if scope.mode == "ledger":
        return (isinstance(scope.ledger_id, UUID) and type(scope.ledger_generation) is int
            and 1 <= scope.ledger_generation <= 2**63 - 1
            and scope.restore_id is None and scope.backup_id is None and scope.target_alias is None)
    return (scope.ledger_id is None and scope.ledger_generation is None and isinstance(scope.restore_id, UUID)
        and isinstance(scope.backup_id, str) and re.fullmatch(r"supportops-backup\.[A-Za-z0-9]{12}", scope.backup_id) is not None
        and isinstance(scope.target_alias, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", scope.target_alias) is not None)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _fact(row: dict[str, object], captured_at: datetime) -> dict[str, object]:
    """固定版本化行摘要；不补造、修复或重新签发数据库里的record_digest。"""
    payload: dict[str, object] = {"schema_version": "supportops_deletion_fact_v1"}
    for name in ("id", "tenant_id", "subject_user_id", "erasure_request_id"):
        if not isinstance(row[name], UUID):
            raise ValueError()
        payload[name] = str(row[name])
    for name in ("category", "resource_key_digest", "scope_digest", "action", "evidence_digest", "maintenance_identity"):
        payload[name] = row[name]
    if (payload["category"] not in DATA_CATEGORIES or payload["action"] not in {"quarantined", "erased"}
        or not isinstance(payload["maintenance_identity"], str)
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,99}", payload["maintenance_identity"]) is None):
        raise ValueError()
    for name in ("resource_key_digest", "scope_digest", "evidence_digest", "record_digest"):
        if not isinstance(row[name], str) or re.fullmatch(r"[a-f0-9]{64}", row[name]) is None:
            raise ValueError()
    recorded_at = row["recorded_at"]
    if not isinstance(recorded_at, datetime) or recorded_at.tzinfo is None:
        raise ValueError()
    recorded_at = recorded_at.astimezone(UTC)
    if recorded_at > captured_at:
        raise ValueError()
    payload["recorded_at"] = recorded_at.isoformat()
    if hashlib.sha256(_canonical(payload)).hexdigest() != row["record_digest"]:
        raise ValueError()
    payload["record_digest"] = row["record_digest"]
    return payload


async def export_tombstone_snapshot(engine: AsyncEngine, scope: TombstoneSnapshotScope) -> bytes:
    """不接应用会话工厂；独立维护角色在单个只读可重复读事务中完整读取有界事实。"""
    if (not isinstance(scope, TombstoneSnapshotScope) or not isinstance(engine, AsyncEngine)
        or not _scope_valid(scope) or engine.dialect.name != "postgresql"):
        raise TombstoneSnapshotUnavailable() from None
    try:
        async with asyncio.timeout(8):
            async with engine.connect() as connection:
                async with connection.begin():
                    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                    await connection.execute(text("SELECT set_config('statement_timeout', '2500ms', true), "
                        "set_config('lock_timeout', '500ms', true)"))
                    metadata = (await connection.execute(text("""
                        SELECT current_database() AS database_name, current_user AS role_name,
                            session_user AS session_role, current_schemas(true) AS schemas,
                            current_setting('transaction_read_only') AS read_only,
                            current_setting('transaction_isolation') AS isolation,
                            current_setting('session_replication_role') AS replication_role,
                            r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb,
                            c.relrowsecurity, c.relforcerowsecurity, c.relkind,
                            c.relowner = r.oid AS is_owner,
                            has_table_privilege(current_user, c.oid, 'SELECT') AS can_select,
                            (has_table_privilege(current_user, c.oid,
                                'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                                OR has_any_column_privilege(current_user, c.oid,
                                    'INSERT,UPDATE,REFERENCES')) AS can_mutate,
                            transaction_timestamp() AS captured_at
                        FROM pg_catalog.pg_class c
                        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                        JOIN pg_catalog.pg_roles r ON r.rolname = current_user
                        WHERE n.nspname = 'public' AND c.relname = 'deletion_tombstones'
                    """))).mappings().one()
                    if (metadata["database_name"] != scope.expected_database
                        or metadata["role_name"] != scope.expected_maintenance_role
                        or metadata["session_role"] != scope.expected_maintenance_role
                        or list(metadata["schemas"]) != ["pg_catalog", "public"]
                        or metadata["read_only"] != "on" or metadata["isolation"] != "repeatable read"
                        or metadata["replication_role"] != "origin" or metadata["relkind"] != "r"
                        or any(metadata[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb",
                            "relrowsecurity", "relforcerowsecurity", "is_owner", "can_mutate"))
                        or not metadata["can_select"]):
                        raise ValueError()
                    captured_at = metadata["captured_at"]
                    if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
                        raise ValueError()
                    captured_at = captured_at.astimezone(UTC)
                    table = DeletionTombstone.__table__
                    rows = (await connection.execute(select(table).order_by(table.c.recorded_at, table.c.id)
                        .limit(MAX_FACTS + 1))).mappings().all()
                    if len(rows) > MAX_FACTS:
                        raise ValueError()
                    facts = [_fact(dict(row), captured_at) for row in rows]
                    # 事务结束前不把一部分行或未通过身份检查的结果交给调用者。
                    payload: dict[str, object] = {"schema_version": "supportops_deletion_ledger_snapshot_v1"
                        if scope.mode == "ledger" else "supportops_restored_deletion_snapshot_v1",
                        "snapshot_id": str(uuid4()), "captured_at": captured_at.isoformat(),
                        "coverage": "all_recorded_tombstones" if scope.mode == "ledger" else "all_restored_tombstones",
                        "facts": facts}
                    if scope.mode == "ledger":
                        payload.update({"ledger_id": str(scope.ledger_id), "generation": scope.ledger_generation})
                    else:
                        payload.update({"restore_id": str(scope.restore_id), "backup_id": scope.backup_id,
                            "target_alias": scope.target_alias})
                    result = _canonical(payload) + b"\n"
                    if len(result) > MAX_BYTES:
                        raise ValueError()
            return result
    except Exception:
        # 不携带连接串、角色、数据库名、行值或驱动异常；外部取消继续传播。
        raise TombstoneSnapshotUnavailable() from None
