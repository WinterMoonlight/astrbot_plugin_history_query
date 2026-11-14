from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from .errors import QueryError, fail
from .state import Turn


class Scheduler:
    """One active operation; bounded FIFO waiters; two independent turn budgets."""
    def __init__(self, capacity: int, abort: Callable[[], Awaitable[None]], clock=time.monotonic):
        self.capacity, self.abort, self.clock = capacity, abort, clock
        self.slot = asyncio.Lock()
        self.waiting = 0
        self.closed = False

    async def run(self, turn: Turn, operation: Callable[[], Awaitable]):
        if self.closed or turn.closed:
            fail("closed", "本轮查询已经结束。")
        if turn.working:
            fail("busy", "本轮已有历史查询正在执行，请等待其结果。")
        if turn.execution_left <= 0 or turn.rows_left <= 0 or turn.chars_left <= 0:
            fail("budget_exhausted", "本轮读取额度已用完；已有结果可能不完整，请询问用户是否继续。")
        turn.working = True
        acquired = False
        queued = False
        try:
            if self.slot.locked() or self.waiting:
                if self.waiting >= self.capacity or turn.queue_left <= 0:
                    fail("busy", "历史查询繁忙，请稍后重试。")
                self.waiting += 1
                queued = True
                started = self.clock()
                acquire = asyncio.create_task(self.slot.acquire())
                try:
                    await asyncio.wait_for(asyncio.shield(acquire), turn.queue_left)
                    acquired = True
                except TimeoutError as exc:
                    raise QueryError("busy", "本轮累计排队额度已用完，请稍后重试。") from exc
                finally:
                    if not acquired:
                        acquire.cancel()
                        await asyncio.gather(acquire, return_exceptions=True)
                        if not acquire.cancelled() and acquire.exception() is None and acquire.result():
                            self.slot.release()
                    turn.queue_left = max(0.0, turn.queue_left - (self.clock() - started))
                    self.waiting -= 1
                    queued = False
            else:
                await self.slot.acquire()
                acquired = True
            if self.closed or turn.closed:
                fail("closed", "查询已取消。")
            started = self.clock()
            try:
                async with asyncio.timeout(turn.execution_left):
                    return await operation()
            except TimeoutError as exc:
                raise QueryError("timeout", "本轮累计读取与筛选额度已用完；请询问是否继续。") from exc
            finally:
                turn.execution_left = max(0.0, turn.execution_left - (self.clock() - started))
                # Cleanup belongs outside the execution timer. Abort only kills an
                # in-flight worker command; a healthy idle worker can be reused.
                await asyncio.shield(self.abort())
        finally:
            if queued:
                self.waiting -= 1
            if acquired:
                self.slot.release()
            turn.working = False

    async def close(self):
        self.closed = True
        await self.abort()
