"""恢复与首次知识隔离共用的最小列写身份核对；不授予权限或执行目标更新。"""

from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from supportops_core.reference_history_schema import verify_reference_history_guards
from supportops_core.retention_knowledge_propagation import KnowledgePropagationDatabase, _READ_COLUMNS
from supportops_core.schema_structure import MIGRATION_LOCK_KEY, require_public_schema_context


async def require_knowledge_quarantine_write_role(connection: AsyncConnection, database: KnowledgePropagationDatabase, *,
    statement_timeout: Literal["100ms", "1000ms"] = "1000ms",
    lock_timeout: Literal["50ms", "250ms"] = "250ms",
) -> None:
    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE"))
    await connection.run_sync(require_public_schema_context)
    await connection.execute(text("SELECT set_config('statement_timeout',:statement_timeout,true), "
        "set_config('lock_timeout',:lock_timeout,true)"),
        {"statement_timeout": statement_timeout, "lock_timeout": lock_timeout})
    if await connection.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock_shared(:key)"),
        {"key": MIGRATION_LOCK_KEY}) is not True:
        raise ValueError()
    rows = (await connection.execute(text("""
        SELECT current_database() AS db,current_user AS role,session_user AS original_role,
            current_schemas(true) AS schemas,current_setting('session_replication_role') AS replication,
            current_setting('transaction_read_only') AS readonly,
            r.rolsuper,r.rolbypassrls,r.rolcreaterole,r.rolcreatedb,
            c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,c.relowner=r.oid AS owns_table,
            has_schema_privilege(current_user,n.oid,'CREATE') AS can_ddl,
            has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS broad_write,
            has_any_column_privilege(current_user,c.oid,'INSERT,REFERENCES') AS other_write,
            a.attname,has_column_privilege(current_user,c.oid,a.attnum,'SELECT') AS can_read,
            has_column_privilege(current_user,c.oid,a.attnum,'UPDATE') AS can_update
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_roles r ON r.rolname=current_user
        JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        WHERE n.nspname='public' AND c.relname IN ('knowledge_snapshots','knowledge_index_generations')
        ORDER BY c.relname,a.attnum LIMIT 65
    """))).mappings().all()
    tables = {"knowledge_snapshots": "deleted_at", "knowledge_index_generations": "status"}
    if len(rows) > 64 or {row["relname"] for row in rows} != set(tables):
        raise ValueError()
    actual: dict[str, set[str]] = {name: set() for name in tables}
    for row in rows:
        actual[row["relname"]].add(row["attname"])
        if (row["db"] != database.expected_database
            or row["role"] != database.expected_maintenance_role
            or row["original_role"] != database.expected_maintenance_role
            or list(row["schemas"]) != ["pg_catalog", "public"] or row["replication"] != "origin"
            or row["readonly"] != "off" or row["relkind"] != "r"
            or row["can_read"] is not (row["attname"] in _READ_COLUMNS[row["relname"]])
            or row["can_update"] is not (row["attname"] == tables[row["relname"]])
            or any(row[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "owns_table",
                "relrowsecurity", "relforcerowsecurity", "can_ddl", "broad_write", "other_write"))):
            raise ValueError()
    if any(not (_READ_COLUMNS[name] | {tables[name]}) <= columns for name, columns in actual.items()):
        raise ValueError()
    excess = await connection.scalar(text("""
        SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
            AND c.relname NOT IN ('knowledge_snapshots','knowledge_index_generations')
            AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
            OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND p.proname IN ('supportops_guard_reference_history',
                    'supportops_guard_knowledge_reference_chain','supportops_reject_reference_history_truncate')
                AND p.proowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user))
    """))
    if excess is not False:
        raise ValueError()
    await connection.run_sync(lambda sync: verify_reference_history_guards(sync, require_read_only=False))
