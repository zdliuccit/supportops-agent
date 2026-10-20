"""0049/0050历史保护的只读目录核对；不读业务记录或执行触发器函数。"""

from hashlib import sha256

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from supportops_core.schema_structure import (
    SchemaStructureError,
    require_public_schema_context,
    require_public_read_only_schema,
)

_TABLE_GUARDS = {
    "model_credential_revisions": "supportops_guard_reference_history",
    "knowledge_evidence": "supportops_guard_reference_history",
    "knowledge_chunks": "supportops_guard_reference_history",
    "knowledge_versions": "supportops_guard_knowledge_reference_chain",
    "knowledge_snapshots": "supportops_guard_knowledge_reference_chain",
    "knowledge_index_generations": "supportops_guard_knowledge_reference_chain",
}
# 固定0049/0050的AS $$...$$原始UTF-8函数体，含空白；不从待核验数据库生成预期。
_FUNCTION_DIGESTS = {
    "supportops_guard_reference_history":
        "6e3b9eba9821cef45c923f7515c4b485089437ac31bc57d3dcf201cdb4ef7fe3",
    "supportops_reject_reference_history_truncate":
        "756dc021a351cfba24c6fa2c829a6256553477634b6f8fd1b479d62e3122e9ac",
    "supportops_guard_knowledge_reference_chain":
        "2a630fb57051afbbe33a6eff0532efed4041d843be2b58aaf26ec8d59d7029ed",
}
# pg_trigger.h的ROW/BEFORE/DELETE/UPDATE/TRUNCATE位，精确匹配而非仅含某个事件。
_TRIGGERS = {
    "reference_history_guard": 1 | 2 | 8 | 16,
    "reference_history_truncate_guard": 2 | 32,
}
_MAX_FUNCTION_BYTES = 16384


def verify_reference_history_guards(connection: Connection, *, require_read_only: bool = True) -> dict[str, object]:
    """调用者持统一维护共享锁；这里只读当前目录，不承诺核对后DDL不变。"""
    try:
        if require_read_only:
            require_public_read_only_schema(connection)
        else:
            # 独立恢复维护者在已取得共享维护锁的写事务内复用同一目录核对；核对仍只执行SELECT。
            require_public_schema_context(connection)
            if not connection.in_transaction() or connection.scalar(text("SHOW transaction_read_only")) != "off":
                raise SchemaStructureError("SCHEMA_ENFORCEMENT_CONTEXT_UNSAFE")
        functions = connection.execute(text("""
            SELECT p.oid::bigint AS function_oid, p.proname AS function_name,
                (p.prokind = 'f' AND l.lanname = 'plpgsql'
                    AND NOT p.prosecdef AND NOT p.proisstrict AND NOT p.proretset
                    AND NOT p.proleakproof AND p.provolatile = 'v' AND p.proparallel = 'u'
                    AND p.pronargs = 0 AND p.pronargdefaults = 0 AND p.provariadic = 0
                    AND p.prosupport = 0 AND p.proallargtypes IS NULL
                    AND p.proargmodes IS NULL AND p.proargnames IS NULL
                    AND p.proargdefaults IS NULL AND p.protrftypes IS NULL
                    AND p.probin IS NULL AND p.prosqlbody IS NULL
                    AND p.prorettype = 'pg_catalog.trigger'::pg_catalog.regtype
                    AND p.proconfig = ARRAY['search_path=pg_catalog']::text[]) AS shape_valid,
                CASE WHEN pg_catalog.octet_length(p.prosrc) <= :max_bytes
                    THEN p.prosrc ELSE NULL END AS body
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            JOIN pg_catalog.pg_language l ON l.oid = p.prolang
            WHERE n.nspname = 'public' AND p.proname IN :functions
            ORDER BY p.oid LIMIT :function_limit
        """).bindparams(bindparam("functions", expanding=True)), {
            "functions": list(_FUNCTION_DIGESTS), "max_bytes": _MAX_FUNCTION_BYTES,
            "function_limit": len(_FUNCTION_DIGESTS) + 1,
        }).mappings().all()
        if len(functions) != len(_FUNCTION_DIGESTS):
            raise SchemaStructureError("SCHEMA_REFERENCE_HISTORY_GUARD_MISMATCH")
        function_ids: dict[str, int] = {}
        for row in functions:
            name, body = row["function_name"], row["body"]
            if (
                name not in _FUNCTION_DIGESTS or name in function_ids
                or row["shape_valid"] is not True or not isinstance(body, str)
                or sha256(body.encode("utf-8")).hexdigest() != _FUNCTION_DIGESTS[name]
            ):
                raise SchemaStructureError("SCHEMA_REFERENCE_HISTORY_GUARD_MISMATCH")
            function_ids[name] = row["function_oid"]

        triggers = connection.execute(text("""
            SELECT c.relname AS table_name, t.tgname AS trigger_name,
                t.tgfoid::bigint AS function_oid, t.tgtype AS trigger_type,
                (c.relkind = 'r' AND t.tgenabled = 'A' AND NOT t.tgisinternal
                    AND t.tgparentid = 0 AND t.tgconstraint = 0 AND t.tgconstrrelid = 0
                    AND t.tgconstrindid = 0 AND NOT t.tgdeferrable AND NOT t.tginitdeferred
                    AND t.tgnargs = 0 AND pg_catalog.octet_length(t.tgargs) = 0
                    AND t.tgattr::text = '' AND t.tgqual IS NULL
                    AND t.tgoldtable IS NULL AND t.tgnewtable IS NULL) AS shape_valid
            FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname IN :tables AND t.tgname IN :triggers
            ORDER BY c.relname, t.tgname LIMIT :trigger_limit
        """).bindparams(bindparam("tables", expanding=True), bindparam("triggers", expanding=True)), {
            "tables": list(_TABLE_GUARDS), "triggers": list(_TRIGGERS),
            "trigger_limit": len(_TABLE_GUARDS) * len(_TRIGGERS) + 1,
        }).mappings().all()
        expected = {(table, trigger) for table in _TABLE_GUARDS for trigger in _TRIGGERS}
        observed: set[tuple[str, str]] = set()
        for row in triggers:
            key = (row["table_name"], row["trigger_name"])
            if key not in expected or key in observed:
                raise SchemaStructureError("SCHEMA_REFERENCE_HISTORY_GUARD_MISMATCH")
            trigger_type = _TRIGGERS[key[1]]
            function_name = (
                _TABLE_GUARDS[key[0]] if key[1] == "reference_history_guard"
                else "supportops_reject_reference_history_truncate"
            )
            if (
                row["shape_valid"] is not True or row["trigger_type"] != trigger_type
                or row["function_oid"] != function_ids[function_name]
            ):
                raise SchemaStructureError("SCHEMA_REFERENCE_HISTORY_GUARD_MISMATCH")
            observed.add(key)
        if observed != expected:
            raise SchemaStructureError("SCHEMA_REFERENCE_HISTORY_GUARD_MISMATCH")
    except SchemaStructureError:
        raise
    except Exception:
        raise SchemaStructureError("SCHEMA_REFERENCE_HISTORY_GUARD_UNAVAILABLE") from None
    return {
        "schema_version": "reference_history_schema_v2", "scope": "named_0049_0050_guards",
        "table_count": len(_TABLE_GUARDS), "trigger_count": len(expected),
        "function_count": len(function_ids), "function_definitions_matched": True,
        "always_enabled": True, "other_triggers_verified": False,
        "roles_verified": False, "history_contents_verified": False,
        "catalog_snapshot_is_atomic": False, "future_ddl_prevented": False,
    }
