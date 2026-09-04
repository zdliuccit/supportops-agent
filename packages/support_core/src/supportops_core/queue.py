"""基于 Redis 阻塞列表的 Agent 运行任务队列。"""

from uuid import UUID

from redis.asyncio import Redis


class RunQueue:
    """以 UUID 作为消息体的轻量 FIFO 运行队列。"""

    def __init__(self, redis: Redis, name: str) -> None:
        """绑定 Redis 客户端和隔离不同环境的队列名称。"""

        self._redis = redis
        self._name = name

    async def enqueue(self, run_id: UUID) -> None:
        """将待执行运行追加到队尾。"""

        await self._redis.rpush(self._name, str(run_id))

    async def dequeue(self, *, timeout_seconds: int = 5) -> UUID | None:
        """阻塞获取最早任务；超时无任务时返回 ``None``。"""

        item = await self._redis.blpop(self._name, timeout=timeout_seconds)
        if item is None:
            return None
        _, value = item
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return UUID(value)
