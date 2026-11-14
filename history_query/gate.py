from __future__ import annotations

import asyncio
import inspect
from typing import Any

from .errors import fail
from .state import Turn


SUPPORTED_VERSIONS = frozenset({"4.29.4"})


def owns(tool, owned, permission_wrapper=None):
    for _ in range(3):
        if any(tool is item for item in owned):
            return True
        if permission_wrapper is None or not isinstance(tool, permission_wrapper):
            return False
        tool = getattr(tool, "_wrapped", None)
    return False


class GatedExecutor:
    def __init__(self, delegate, turn: Turn, owned, permission_wrapper):
        self.delegate, self.turn = delegate, turn
        self.owned, self.permission_wrapper = tuple(owned), permission_wrapper

    async def execute(self, tool, run_context, **kwargs):
        async with self.turn.admission:
            if getattr(getattr(run_context, "context", None), "event", None) is not self.turn.event:
                fail("wrong_turn", "工具调用不属于当前轮次。")
            if self.turn.closed:
                fail("closed", "当前轮次已结束，不能启动新的工具调用。")
            if self.turn.locked and not owns(tool, self.owned, self.permission_wrapper):
                fail("history_read_only", "本轮已开始读取历史，只能继续历史查询；其他操作请在下一条消息中明确提出。")
        # Already admitted work may finish even if another call now latches.
        async for result in self.delegate.execute(tool=tool, run_context=run_context, **kwargs):
            yield result


class Binding:
    def __init__(self, runner, gate):
        self.runner, self.gate = runner, gate

    def valid(self):
        return self.runner.tool_executor is self.gate and not self.gate.turn.closed

    async def begin_read(self):
        async with self.gate.turn.admission:
            if not self.valid():
                fail("bridge_unavailable", "当前轮次的历史查询隔离未就绪，已拒绝读取。")
            self.gate.turn.locked = True

    def close(self, restore=True):
        self.gate.turn.closed = True
        if restore and self.runner.tool_executor is self.gate:
            self.runner.tool_executor = self.gate.delegate


def attach(version, registry, event, context, turn, owned, expected_executor, permission_wrapper):
    """Version-bound, request-local mutation; never patches a host class/global tool."""
    if version not in SUPPORTED_VERSIONS or not isinstance(registry, dict):
        fail("unsupported_version", "当前 AstrBot 版本或内部接入不受支持，仅停用历史查询。")
    runner = registry.get(event.unified_msg_origin)
    if runner is None or getattr(runner, "run_context", None) is not context:
        fail("bridge_unavailable", "无法核对当前运行中的 Agent，已拒绝历史查询。")
    if context.context.event is not event or turn.event is not event:
        fail("wrong_turn", "Agent 事件身份不匹配。")
    delegate = getattr(runner, "tool_executor", None)
    if delegate is not expected_executor and type(delegate) is not expected_executor:
        fail("bridge_unavailable", "工具执行器结构发生变化，仅停用历史查询。")
    if not inspect.isasyncgenfunction(delegate.execute):
        fail("bridge_unavailable", "工具执行协议不兼容。")
    gate = GatedExecutor(delegate, turn, owned, permission_wrapper)
    binding = Binding(runner, gate)
    runner.tool_executor = gate
    turn.bridge = binding
    return binding
