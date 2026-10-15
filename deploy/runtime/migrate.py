"""独立迁移进程：持 PostgreSQL 会话级 advisory lock，禁止多个执行者并发升级。"""
import asyncio
import sys
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine
from supportops_core.config import get_settings
from supportops_core.agent_runtime import checkpoint_saver

MIGRATION_LOCK_KEY = 761_040_001


async def upgrade() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("postgresql+"):
        raise RuntimeError("迁移模板仅允许已明确配置的 PostgreSQL")
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            async with asyncio.timeout(15):
                locked = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
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
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
                await connection.commit()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(upgrade())
    except Exception as exc:
        # 原始数据库异常可能带连接/SQL 信息，仅输出类型与固定错误码。
        print(f"MIGRATION_FAILED ({type(exc).__name__})：请由维护者检查目标与迁移状态。", file=sys.stderr)
        raise SystemExit(1) from None
