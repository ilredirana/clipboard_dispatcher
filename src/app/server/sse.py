"""内置 SSE (Server-Sent Events) 推送服务。"""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

_MAX_QUEUE_SIZE = 100


class EventBus:
    """进程内 SSE 事件总线。

    基于 asyncio.Queue 的发布/订阅：每个订阅者一个独立队列（容量 100）。
    30 秒心跳 ping 保持连接。广播采用 fire-and-forget，队列满则丢弃事件。
    """

    def __init__(self):
        self._queues: list[asyncio.Queue] = []

    @property
    def subscriber_count(self) -> int:
        """当前订阅者数量。"""
        return len(self._queues)

    async def subscribe(self):
        """异步生成器：订阅 SSE 事件流。"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._queues.append(queue)
        logger.info("SSE subscriber connected (%d total)", len(self._queues))
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            self._queues.remove(queue)
            logger.info("SSE subscriber disconnected (%d remaining)", len(self._queues))

    async def broadcast(self, payload: dict[str, str]) -> None:
        """向所有订阅者广播剪贴板更新通知。"""
        for queue in self._queues[:]:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("SSE queue full, dropping event for subscriber")
        if self._queues:
            logger.info("Broadcast clipboard update to %d subscribers", len(self._queues))


event_bus = EventBus()
