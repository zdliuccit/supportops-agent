"""显式部署预检使用的只读结构核对；不读取业务行、不执行DDL或修复数据库。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import (
    CheckConstraint,
    Column,
    Enum,
    ForeignKeyConstraint,
    Index,
    MetaData,
    String,
    UniqueConstraint,
    bindparam,
    inspect,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.engine.interfaces import ReflectedForeignKeyConstraint

# 与既有迁移入口保持原key；会话独占迁移锁和事务共享预检锁使用同一命名空间。
MIGRATION_LOCK_KEY = 761_040_001

SchemaStructureCode = Literal[
    "SCHEMA_BACKEND_UNSUPPORTED",
    "SCHEMA_TRANSACTION_NOT_READ_ONLY",
    "SCHEMA_SEARCH_PATH_UNSAFE",
    "SCHEMA_METADATA_LIMIT",
    "SCHEMA_TABLE_MISSING",
    "SCHEMA_COLUMN_MISSING",
    "SCHEMA_TYPE_MISMATCH",
    "SCHEMA_NULLABILITY_MISMATCH",
    "SCHEMA_PRIMARY_KEY_MISMATCH",
    "SCHEMA_UNIQUE_CONSTRAINT_MISMATCH",
    "SCHEMA_FOREIGN_KEY_MISMATCH",
    "SCHEMA_CHECK_CONSTRAINT_MISMATCH",
    "SCHEMA_UNIQUE_INDEX_DECLARATION_UNSUPPORTED",
    "SCHEMA_UNIQUE_INDEX_MISMATCH",
    "SCHEMA_CONSTRAINT_UNVALIDATED",
    "SCHEMA_CONSTRAINT_NOT_ENFORCED",
    "SCHEMA_CONSTRAINT_TIMING_MISMATCH",
    "SCHEMA_KEY_INDEX_INVALID",
    "SCHEMA_FOREIGN_KEY_TRIGGER_DISABLED",
    "SCHEMA_ENFORCEMENT_CONTEXT_UNSAFE",
    "SCHEMA_REFLECTION_UNAVAILABLE",
    "SCHEMA_REFERENCE_HISTORY_GUARD_MISMATCH",
    "SCHEMA_REFERENCE_HISTORY_GUARD_UNAVAILABLE",
]


class SchemaStructureError(ValueError):
    """错误只携带固定码，不回显连接串、元数据原文或反射异常。"""

    def __init__(self, code: SchemaStructureCode) -> None:
        super().__init__(code)
        self.code = code


def require_public_schema_path(connection: Connection) -> None:
    """迁移和预检共用表定位规则，不修改调用者search_path来掩盖配置漂移。"""
    if connection.dialect.name != "postgresql":
        raise SchemaStructureError("SCHEMA_BACKEND_UNSUPPORTED")
    schemas = tuple(connection.scalar(text("SELECT pg_catalog.current_schemas(false)")) or ())
    effective = tuple(connection.scalar(text("SELECT pg_catalog.current_schemas(true)")) or ())
    # 隐式pg_temp不会出现在false结果中，但会优先解析未限定表名；同样必须拒绝。
    if (
        schemas not in {("public",), ("pg_catalog", "public")}
        or effective != ("pg_catalog", "public")
    ):
        raise SchemaStructureError("SCHEMA_SEARCH_PATH_UNSAFE")


def require_public_schema_context(connection: Connection) -> None:
    """迁移及预检都要求正常约束执行上下文，不自动切换复制角色。"""
    require_public_schema_path(connection)
    if connection.scalar(text("SHOW session_replication_role")) != "origin":
        raise SchemaStructureError("SCHEMA_ENFORCEMENT_CONTEXT_UNSAFE")


def require_public_read_only_schema(connection: Connection) -> None:
    """先检查实际会话，不能把public反射通过误用于另一上下文下的应用表。"""
    if connection.dialect.name != "postgresql":
        raise SchemaStructureError("SCHEMA_BACKEND_UNSUPPORTED")
    if (
        not connection.in_transaction()
        or connection.scalar(text("SHOW transaction_read_only")) != "on"
    ):
        raise SchemaStructureError("SCHEMA_TRANSACTION_NOT_READ_ONLY")
    require_public_schema_context(connection)


@dataclass(frozen=True, slots=True)
class _ConstraintState:
    kind: str
    validated: bool
    enforced: bool
    deferrable: bool
    deferred: bool
    index_valid: bool | None
    index_ready: bool | None
    index_immediate: bool | None
    index_unique: bool | None
    index_live: bool | None
    foreign_triggers_enabled: bool | None


@dataclass(frozen=True, slots=True)
class _UniqueIndexState:
    columns: tuple[str | None, ...]
    has_expressions: bool
    partial: bool
    nulls_not_distinct: bool
    unique: bool
    valid: bool
    ready: bool
    live: bool
    immediate: bool


def _unique_index_states(
    connection: Connection, *, table_names: list[str], index_names: list[str],
) -> dict[tuple[str, str], _UniqueIndexState]:
    """仅查询声明名称下的索引元数据，不读取谓词/表达式原文或业务行。"""
    if not index_names:
        return {}
    if len(index_names) > 1000:
        raise SchemaStructureError("SCHEMA_METADATA_LIMIT")
    version = connection.dialect.server_version_info
    if not version or version < (11,):
        raise SchemaStructureError("SCHEMA_BACKEND_UNSUPPORTED")
    nulls_sql = "i.indnullsnotdistinct" if version >= (15,) else "false"
    rows = connection.execute(text(f"""
        SELECT t.relname AS table_name, x.relname AS index_name,
            ARRAY(SELECT a.attname::text
                  FROM pg_catalog.unnest(i.indkey::pg_catalog.int2[]) WITH ORDINALITY AS k(attnum, ord)
                  LEFT JOIN pg_catalog.pg_attribute AS a ON a.attrelid = i.indrelid
                      AND a.attnum = k.attnum AND NOT a.attisdropped
                  WHERE k.ord <= i.indnkeyatts ORDER BY k.ord) AS key_columns,
            (i.indexprs IS NOT NULL) AS has_expressions, (i.indpred IS NOT NULL) AS is_partial,
            {nulls_sql} AS nulls_not_distinct, i.indisunique AS is_unique,
            i.indisvalid AS is_valid, i.indisready AS is_ready, i.indislive AS is_live,
            i.indimmediate AS is_immediate
        FROM pg_catalog.pg_index AS i
        JOIN pg_catalog.pg_class AS t ON t.oid = i.indrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = t.relnamespace
        JOIN pg_catalog.pg_class AS x ON x.oid = i.indexrelid AND x.relnamespace = t.relnamespace
        WHERE n.nspname = 'public' AND t.relname IN :table_names AND x.relname IN :index_names
        ORDER BY t.relname, x.relname LIMIT 1001
    """).bindparams(bindparam("table_names", expanding=True), bindparam("index_names", expanding=True)), {
        "table_names": table_names, "index_names": index_names,
    }).mappings().all()
    if len(rows) > 1000:
        raise SchemaStructureError("SCHEMA_METADATA_LIMIT")
    return {
        (row["table_name"], row["index_name"]): _UniqueIndexState(
            columns=tuple(row["key_columns"]), has_expressions=row["has_expressions"],
            partial=row["is_partial"], nulls_not_distinct=row["nulls_not_distinct"],
            unique=row["is_unique"], valid=row["is_valid"], ready=row["is_ready"],
            live=row["is_live"], immediate=row["is_immediate"],
        ) for row in rows
    }


def _unique_index_columns(index: Index) -> tuple[str, ...]:
    """只支持当前声明的直接列键；未知表达式或自定义键选项不能略过。"""
    options = index.dialect_options["postgresql"]
    if (
        not isinstance(index.name, str) or not index.name or not index.expressions or index.table is None
        or options.get("include") or options.get("ops")
        or options.get("using") not in (None, "", "btree")
    ):
        raise SchemaStructureError("SCHEMA_UNIQUE_INDEX_DECLARATION_UNSUPPORTED")
    columns: list[str] = []
    for value in index.expressions:
        if not isinstance(value, Column) or value.table is not index.table:
            raise SchemaStructureError("SCHEMA_UNIQUE_INDEX_DECLARATION_UNSUPPORTED")
        columns.append(value.name)
    return tuple(columns)


def _constraint_states(
    connection: Connection, names: list[str]
) -> dict[tuple[str, str], _ConstraintState]:
    version = connection.dialect.server_version_info
    if not version:
        raise SchemaStructureError("SCHEMA_BACKEND_UNSUPPORTED")
    # PostgreSQL 18新增显式执行标志；旧版本不引用不存在的列，不从validated推断18已执行。
    enforced_sql = "c.conenforced" if version >= (18,) else "true"
    statement = text(f"""
        SELECT t.relname AS table_name, c.conname AS constraint_name, c.contype::text AS kind,
            c.convalidated AS validated, c.condeferrable AS deferrable, c.condeferred AS deferred,
            {enforced_sql} AS enforced,
            i.indisvalid AS index_valid, i.indisready AS index_ready, i.indimmediate AS index_immediate,
            i.indisunique AS index_unique, i.indislive AS index_live,
            CASE WHEN c.contype = 'f' THEN
                (SELECT pg_catalog.bool_and(g.tgenabled IN ('O', 'A'))
                 FROM pg_catalog.pg_trigger AS g WHERE g.tgconstraint = c.oid AND g.tgisinternal)
                ELSE true END AS foreign_triggers_enabled
        FROM pg_catalog.pg_constraint AS c
        JOIN pg_catalog.pg_class AS t ON t.oid = c.conrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = t.relnamespace
        LEFT JOIN pg_catalog.pg_index AS i ON i.indexrelid = c.conindid
        WHERE n.nspname = 'public' AND t.relname IN :table_names AND c.contype IN ('p', 'u', 'f', 'c')
        ORDER BY t.relname, c.conname LIMIT 20001
    """).bindparams(bindparam("table_names", expanding=True))
    rows = connection.execute(statement, {"table_names": names}).mappings().all()
    if len(rows) > 20000:
        raise SchemaStructureError("SCHEMA_METADATA_LIMIT")
    return {
        (row["table_name"], row["constraint_name"]): _ConstraintState(
            kind=row["kind"],
            validated=row["validated"],
            enforced=row["enforced"],
            deferrable=row["deferrable"],
            deferred=row["deferred"],
            index_valid=row["index_valid"],
            index_ready=row["index_ready"],
            index_immediate=row["index_immediate"],
            index_unique=row["index_unique"],
            index_live=row["index_live"],
            foreign_triggers_enabled=row["foreign_triggers_enabled"],
        )
        for row in rows
    }


def _require_enforced(
    state: _ConstraintState | None,
    *,
    kind: str,
    deferrable: bool,
    initially: str | None,
) -> None:
    if state is None or state.kind != kind or state.validated is not True:
        raise SchemaStructureError("SCHEMA_CONSTRAINT_UNVALIDATED")
    if state.enforced is not True:
        raise SchemaStructureError("SCHEMA_CONSTRAINT_NOT_ENFORCED")
    if (
        state.deferrable != deferrable
        or state.deferred != ((initially or "IMMEDIATE").upper() == "DEFERRED")
    ):
        raise SchemaStructureError("SCHEMA_CONSTRAINT_TIMING_MISMATCH")
    if kind in {"p", "u"} and (
        state.index_valid is not True
        or state.index_ready is not True
        or state.index_unique is not True
        or state.index_live is not True
        or state.index_immediate != (not deferrable)
    ):
        raise SchemaStructureError("SCHEMA_KEY_INDEX_INVALID")
    if kind == "f" and state.foreign_triggers_enabled is not True:
        raise SchemaStructureError("SCHEMA_FOREIGN_KEY_TRIGGER_DISABLED")


def _require_matching_constraint(
    states: dict[tuple[str, str], _ConstraintState],
    *,
    table_name: str,
    names: list[str | None],
    kind: str,
    deferrable: bool,
    initially: str | None,
) -> None:
    # 名称不是语义匹配依据；用于核对反射结果对应的真实约束执行状态。
    first_failure: SchemaStructureError | None = None
    for name in names:
        try:
            _require_enforced(
                states.get((table_name, name)) if name is not None else None,
                kind=kind,
                deferrable=deferrable,
                initially=initially,
            )
            return
        except SchemaStructureError as error:
            if first_failure is None:
                first_failure = error
    raise first_failure or SchemaStructureError("SCHEMA_CONSTRAINT_UNVALIDATED")


ForeignKeySignature = tuple[tuple[str, ...], str, str, tuple[str, ...], str, str, str]


def _foreign_signature(value: ReflectedForeignKeyConstraint) -> ForeignKeySignature:
    options = value.get("options", {})
    return (
        tuple(value["constrained_columns"]),
        value.get("referred_schema") or "public",
        value["referred_table"],
        tuple(value["referred_columns"]),
        (options.get("ondelete") or "NO ACTION").upper(),
        (options.get("onupdate") or "NO ACTION").upper(),
        (options.get("match") or "SIMPLE").upper(),
    )


def _expected_foreign_signature(constraint: ForeignKeyConstraint) -> ForeignKeySignature:
    return (
        tuple(element.parent.name for element in constraint.elements),
        constraint.referred_table.schema or "public",
        constraint.referred_table.name,
        tuple(element.column.name for element in constraint.elements),
        (constraint.ondelete or "NO ACTION").upper(),
        (constraint.onupdate or "NO ACTION").upper(),
        (constraint.match or "SIMPLE").upper(),
    )


def verify_business_schema(
    connection: Connection,
    *,
    metadata: MetaData,
    compare_types: Callable[[Column[object], Column[object]], bool],
) -> dict[str, object]:
    """核对列、键及具名CHECK/唯一索引状态，不验证表达式或谓词语义。"""
    try:
        return _verify_business_schema(connection, metadata=metadata, compare_types=compare_types)
    except SchemaStructureError:
        raise
    except Exception:
        raise SchemaStructureError("SCHEMA_REFLECTION_UNAVAILABLE") from None


def _verify_business_schema(
    connection: Connection,
    *,
    metadata: MetaData,
    compare_types: Callable[[Column[object], Column[object]], bool],
) -> dict[str, object]:
    require_public_read_only_schema(connection)
    tables = sorted(metadata.tables.values(), key=lambda table: table.name)
    if (
        not 0 < len(tables) <= 256
        or any(table.schema not in {None, "public"} for table in tables)
        or sum(len(table.columns) for table in tables) > 10000
    ):
        raise SchemaStructureError("SCHEMA_METADATA_LIMIT")
    names = [table.name for table in tables]
    declared_unique_indexes: list[tuple[str, Index, tuple[str, ...]]] = []
    for table in tables:
        for index in table.indexes:
            if not index.unique:
                continue
            if len(declared_unique_indexes) >= 1000:
                raise SchemaStructureError("SCHEMA_METADATA_LIMIT")
            declared_unique_indexes.append((table.name, index, _unique_index_columns(index)))
    index_names = [str(index.name) for _, index, _ in declared_unique_indexes]
    if len(set(index_names)) != len(index_names):
        # 同public命名空间不能靠重复名称把不同声明算作已核对。
        raise SchemaStructureError("SCHEMA_UNIQUE_INDEX_DECLARATION_UNSUPPORTED")
    inspector = inspect(connection)
    columns = inspector.get_multi_columns(schema="public", filter_names=names)
    primary_keys = inspector.get_multi_pk_constraint(schema="public", filter_names=names)
    unique_constraints = inspector.get_multi_unique_constraints(schema="public", filter_names=names)
    foreign_keys = inspector.get_multi_foreign_keys(
        schema="public", filter_names=names, postgresql_ignore_search_path=True
    )
    states = _constraint_states(connection, names)
    index_states = _unique_index_states(connection, table_names=names, index_names=index_names)
    for table_name, index, expected_columns in declared_unique_indexes:
        index_state = index_states.get((table_name, str(index.name)))
        options = index.dialect_options["postgresql"]
        if (
            index_state is None or index_state.columns != expected_columns or index_state.has_expressions is not False
            or index_state.partial != (options.get("where") is not None)
            or index_state.nulls_not_distinct != bool(options.get("nulls_not_distinct"))
        ):
            raise SchemaStructureError("SCHEMA_UNIQUE_INDEX_MISMATCH")
        if (
            index_state.unique is not True or index_state.valid is not True or index_state.ready is not True
            or index_state.live is not True or index_state.immediate is not True
        ):
            raise SchemaStructureError("SCHEMA_KEY_INDEX_INVALID")
    column_count, unique_count, foreign_count, check_count = 0, 0, 0, 0
    for table in tables:
        key = ("public", table.name)
        if key not in columns:
            raise SchemaStructureError("SCHEMA_TABLE_MISSING")
        actual_columns = {column["name"]: column for column in columns[key]}
        for column in table.columns:
            actual = actual_columns.get(column.name)
            if actual is None:
                raise SchemaStructureError("SCHEMA_COLUMN_MISSING")
            reflected = Column(column.name, actual["type"])
            if compare_types(reflected, column):
                raise SchemaStructureError("SCHEMA_TYPE_MISMATCH")
            expected_type = column.type.dialect_impl(connection.dialect)
            actual_type = actual["type"].dialect_impl(connection.dialect)
            # Alembic允许未指定类型参数；已明确的字符串上限不能因此被放宽。
            if isinstance(expected_type, String) and expected_type.length is not None:
                if not isinstance(actual_type, String) or actual_type.length != expected_type.length:
                    raise SchemaStructureError("SCHEMA_TYPE_MISMATCH")
            if isinstance(expected_type, Enum) and expected_type.native_enum:
                if (
                    not isinstance(actual_type, Enum)
                    or tuple(actual_type.enums) != tuple(expected_type.enums)
                ):
                    raise SchemaStructureError("SCHEMA_TYPE_MISMATCH")
            if actual["nullable"] != column.nullable:
                raise SchemaStructureError("SCHEMA_NULLABILITY_MISMATCH")
            column_count += 1

        primary_key = primary_keys.get(key)
        if (
            primary_key is None
            or tuple(primary_key["constrained_columns"]) != tuple(table.primary_key.columns.keys())
        ):
            raise SchemaStructureError("SCHEMA_PRIMARY_KEY_MISMATCH")
        _require_matching_constraint(
            states,
            table_name=table.name,
            names=[primary_key.get("name")],
            kind="p",
            deferrable=bool(table.primary_key.deferrable),
            initially=table.primary_key.initially,
        )

        # 同时覆盖列内声明的CHECK，不依赖它们是否出现在table.constraints中。
        declared_constraints = set(table.constraints)
        for column in table.columns:
            declared_constraints.update(column.constraints)
        for constraint in declared_constraints:
            if isinstance(constraint, CheckConstraint):
                # 这里只证明具名约束存在且处于要求的状态，名称不证明表达式相同。
                name = constraint.name
                state = states.get((table.name, name)) if isinstance(name, str) and name else None
                if state is None or state.kind != "c":
                    raise SchemaStructureError("SCHEMA_CHECK_CONSTRAINT_MISMATCH")
                _require_enforced(
                    state,
                    kind="c",
                    deferrable=bool(constraint.deferrable),
                    initially=constraint.initially,
                )
                check_count += 1
                continue
            if not isinstance(constraint, UniqueConstraint):
                continue
            expected_nulls = bool(constraint.dialect_options["postgresql"].get("nulls_not_distinct"))
            matches = [
                value for value in unique_constraints.get(key, ())
                if tuple(value["column_names"]) == tuple(constraint.columns.keys())
                and bool(value.get("dialect_options", {}).get("postgresql_nulls_not_distinct"))
                == expected_nulls
            ]
            if not matches:
                raise SchemaStructureError("SCHEMA_UNIQUE_CONSTRAINT_MISMATCH")
            _require_matching_constraint(
                states,
                table_name=table.name,
                names=[match.get("name") for match in matches],
                kind="u",
                deferrable=bool(constraint.deferrable),
                initially=constraint.initially,
            )
            unique_count += 1

        for constraint in table.foreign_key_constraints:
            matches = [
                value for value in foreign_keys.get(key, ())
                if _foreign_signature(value) == _expected_foreign_signature(constraint)
            ]
            if not matches:
                raise SchemaStructureError("SCHEMA_FOREIGN_KEY_MISMATCH")
            _require_matching_constraint(
                states,
                table_name=table.name,
                names=[match.get("name") for match in matches],
                kind="f",
                deferrable=bool(constraint.deferrable),
                initially=constraint.initially,
            )
            foreign_count += 1

    return {
        "schema_version": "business_schema_structure_v3",
        "status": "passed",
        "business_tables": len(tables),
        "columns": column_count,
        "primary_keys": len(tables),
        "unique_constraints": unique_count,
        "foreign_keys": foreign_count,
        "foreign_key_trigger_enablement_checked": True,
        "named_check_constraints": check_count,
        "named_check_constraint_states_verified": True,
        "named_unique_indexes": len(declared_unique_indexes),
        "named_unique_index_states_verified": True,
        "unique_index_definitions_verified": False,
        "check_expressions_verified": False,
        "partial_indexes_verified": False,
        "non_constraint_indexes_verified": False,
        "migration_only_guards_verified": False,
        "extra_objects_verified": False,
        "complete_schema_verified": False,
        "triggers_verified": False,
        "server_defaults_verified": False,
        "privileges_verified": False,
    }
