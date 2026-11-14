from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

from tests.host_stub import Context, Executor, FunctionTool, PermissionWrapper, ToolSet, host
from tests.support import Event, config, database, workspace


class EntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = workspace(); self.root = Path(self.temp.name); self.path = self.root / "host.sqlite3"; database(self.path)
        self.manager = host(self.root); self.module, self.astrbot, self.registry = self.manager.__enter__()
        self.context = Context(self.path); self.raw = config(); self.plugin = self.module.HistoryQueryPlugin(self.context, self.raw)
        self.event = Event(); self.rc = NS(context=NS(event=self.event))
        self.runner = NS(run_context=self.rc, tool_executor=Executor)
        self.registry[self.event.unified_msg_origin] = self.runner
    async def asyncTearDown(self):
        await self.plugin.terminate(); self.manager.__exit__(None, None, None); self.temp.cleanup()
    async def begin(self):
        await self.plugin.begin(self.event, self.rc)
        if self.plugin.active:
            self.plugin.active[id(self.event)][1].started = datetime(2026, 11, 14, 12, tzinfo=timezone.utc).timestamp()
    async def call(self, index, **args):
        tool = PermissionWrapper(self.plugin.tools[index])
        return [r async for r in self.runner.tool_executor.execute(tool, self.rc, **args)]

    async def test_full_entry_find_search_context_and_restore(self):
        await self.begin()
        found = json.loads((await self.call(0, name="英语群"))[0])
        source = found["candidates"][0]["source_ref"]
        result = json.loads((await self.call(1, source_refs=[source], keywords=["作业"]))[0])
        self.assertTrue(result["records"], result)
        result = json.loads((await self.call(2, record_ref=result["records"][0]["record_ref"], radius=1))[0])
        self.assertEqual(result["status"], "context")
        await self.plugin.done(self.event, self.rc, None)
        self.assertIs(self.runner.tool_executor, Executor)

    async def test_original_tools_are_unmodified_and_denied_only_after_read(self):
        class Ordinary(FunctionTool):
            async def call(self, context, **kwargs): return "normal"
        tool = Ordinary("unrelated", "normal", {})
        await self.begin()
        before = [x async for x in self.runner.tool_executor.execute(tool, self.rc)]
        self.assertEqual(before, ["normal"])
        found = json.loads((await self.call(0, name="英语群"))[0])
        await self.call(1, source_refs=[found["candidates"][0]["source_ref"]])
        with self.assertRaises(Exception):
            [x async for x in self.runner.tool_executor.execute(tool, self.rc)]
        self.assertTrue(tool.active)

    async def test_host_permission_wrapper_is_not_bypassed(self):
        await self.begin()
        tool = PermissionWrapper(self.plugin.tools[0], denied=True)
        result = [x async for x in self.runner.tool_executor.execute(tool, self.rc, name="英语群")]
        self.assertEqual(result, ["host permission denied"])
        self.assertFalse(self.context.calls)

    async def test_unsupported_version_hides_only_own_tools(self):
        self.astrbot.__version__ = "4.30.0"
        unrelated = FunctionTool("unrelated", "normal", {})
        request = NS(func_tool=ToolSet([PermissionWrapper(self.plugin.tools[0]), unrelated]))
        await self.plugin.filter_tools(self.event, request)
        await self.begin()
        self.assertEqual(request.func_tool.tools, [unrelated])
        self.assertIs(self.runner.tool_executor, Executor)
        self.assertFalse(self.plugin.active)
        self.assertEqual(json.loads(await self.plugin.invoke("find", self.event, {"name": "英语群"}))["status"], "unavailable")

    async def test_missing_registry_and_changed_executor_fail_closed(self):
        self.registry.clear(); await self.begin()
        self.assertFalse(self.plugin.active)
        self.registry[self.event.unified_msg_origin] = self.runner
        previous = object(); self.runner.tool_executor = previous
        await self.begin(); self.assertFalse(self.plugin.active); self.assertIs(self.runner.tool_executor, previous)

    async def test_host_executor_instance_is_supported_and_restored(self):
        original = Executor()
        self.runner.tool_executor = original
        await self.begin()
        self.assertTrue(self.plugin.active)
        found = json.loads((await self.call(0, name="英语群"))[0])
        self.assertEqual(found["status"], "resolved")
        await self.plugin.done(self.event, self.rc, None)
        self.assertIs(self.runner.tool_executor, original)

    async def test_unload_preserves_closed_guard_for_reading_turn(self):
        await self.begin()
        turn = self.plugin.active[id(self.event)][1]
        await turn.bridge.begin_read()
        guarded = self.runner.tool_executor
        await self.plugin.terminate()
        self.assertIs(self.runner.tool_executor, guarded)
        self.assertTrue(turn.closed)

    async def test_disabled_config_never_spawns_worker(self):
        self.raw["enabled"] = False
        await self.begin()
        self.assertIsNone(self.plugin.service)
        self.assertFalse(self.context.calls)

    async def test_missing_fields_in_settings_fail_without_touching_others(self):
        self.raw["instances"] = [{"platform_id": "qq-a"}]
        await self.begin()
        self.assertIs(self.runner.tool_executor, Executor)
        self.assertFalse(self.plugin.active)
