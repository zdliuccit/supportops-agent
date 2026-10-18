"""独立迁移进程：持 PostgreSQL 会话级 advisory lock，禁止多个执行者并发升级。"""
import asyncio
import sys
from collections.abc import Sequence

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from supportops_core.agent_runtime import checkpoint_saver
from supportops_core.config import get_settings
from supportops_core.schema_structure import (
    MIGRATION_LOCK_KEY,
    SchemaStructureError,
    require_public_schema_context,
)


async def upgrade() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("postgresql+"):
        raise RuntimeError("迁移模板仅允许已明确配置的 PostgreSQL")
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            async with asyncio.timeout(15):
                await connection.run_sync(require_public_schema_context)
                locked = await connection.scalar(
                    text("SELECT pg_catalog.pg_try_advisory_lock(:key)"),
                    {"key": MIGRATION_LOCK_KEY},
                )
            await connection.commit()
            if not locked:
                raise RuntimeError("已有迁移执行者持锁，未开始迁移")
            try:
                # 会话锁跨提交保留；Alembic 必须使用这条连接，不能另连绕过锁。
                def apply(sync_connection: Connection) -> None:
                    config = Config("alembic.ini")
                    scripts = ScriptDirectory.from_config(config)
                    current = MigrationContext.configure(sync_connection).get_current_heads()
                    safe = {"20261010_0017", *(row.revision for row in scripts.iterate_revisions("heads", "20261010_0017"))}
                    if len(current) != 1 or current[0] not in safe:
                        # 历史 0005 会清空旧数据，本入口不自动处理空库/未知基线/历史破坏性升级。
                        raise RuntimeError("需要已批准并达到 0017 的业务库基线")
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")
                await connection.run_sync(apply)
                schema = settings.langgraph_checkpoint_schema  # Settings 限制为安全标识符。
                await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
                await connection.commit()
                # 独立 checkpoint Schema/官方 saver 迁移也由此执行者初始化，保留同一 advisory lock。
                async with checkpoint_saver(settings):
                    pass
            finally:
                if connection.in_transaction():
                    await connection.rollback()
                await connection.execute(
                    text("SELECT pg_catalog.pg_advisory_unlock(:key)"),
                    {"key": MIGRATION_LOCK_KEY},
                )
                await connection.commit()
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments in (["-h"], ["--help"]):
        print(
            "用法：python deploy/runtime/migrate.py\n"
            "仅由获授权维护者在已批准目标执行显式业务迁移与checkpoint初始化。"
        )
        return 0
    if arguments:
        # 拼错参数不能意外进入DDL；不回显可能含秘密的参数原文。
        print("MIGRATION_ARGUMENTS_INVALID：未开始迁移。", file=sys.stderr)
        return 2
    try:
        asyncio.run(upgrade())
    except KeyboardInterrupt:
        # 中断不证明没有提交；保留原事实，由维护者核对，不能自动downgrade或重放。
        print("MIGRATION_INTERRUPTED：需核对当前迁移与checkpoint状态。", file=sys.stderr)
        return 130
    except Exception as exc:
        # 原始数据库异常可能带连接/SQL 信息，仅输出类型与固定错误码。
        code = exc.code if isinstance(exc, SchemaStructureError) else "MIGRATION_FAILED"
        print(
            f"{code} ({type(exc).__name__})：请由维护者检查目标与迁移状态。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
