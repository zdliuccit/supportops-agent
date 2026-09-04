"""异步数据库引擎、会话工厂与健康检查辅助函数。"""

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from supportops_core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """按配置创建异步 SQLAlchemy 引擎，并为网络数据库启用连接预检。"""

    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if settings.database_url.startswith("sqlite"):
        kwargs.pop("pool_pre_ping")
    return create_async_engine(settings.database_url, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建提交后仍可读取实体属性的异步会话工厂。"""

    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """为 FastAPI 依赖提供自动关闭的请求级数据库会话。"""

    async with session_factory() as session:
        yield session


async def check_database(engine: AsyncEngine) -> None:
    """执行最小查询，验证数据库连接及查询能力。"""

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
