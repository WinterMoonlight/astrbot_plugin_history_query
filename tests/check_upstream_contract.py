"""Optional offline check against an exact AstrBot v4.29.4 source checkout.

Requires pydantic, jsonschema and deprecated in the test environment. No host
startup or external requests. No upstream source is bundled into this project.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import sys
import types
import typing
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jsonschema
from deprecated import deprecated
from pydantic import Field, model_validator
from pydantic.dataclasses import dataclass

from tests.host_stub import Context, Executor, host
from tests.support import Event, config, database, workspace


EXPECTED = {
    "astrbot/core/agent/tool.py": "9e31fcc60dbc2881374c3a2619569a8ad68753b9",
    "astrbot/core/provider/func_tool_manager.py": "cda0ff8b39a82a0e5dc7f7951eead14f967d015a",
}


def load_classes(root, relative, names, namespace):
    path = root / relative
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    assert sha == EXPECTED[relative], (relative, sha)
    parsed = ast.parse(raw.decode(), filename=str(path))
    nodes = [node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name in names]
    assert len(nodes) == len(names)
    tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
    ast.fix_missing_locations(tree)
    exec(compile(tree, str(path), "exec"), namespace)


async def check(root):
    module = types.ModuleType("upstream_tools_contract"); sys.modules[module.__name__] = module
    ns = module.__dict__
    ns.update(jsonschema=jsonschema, Field=Field, model_validator=model_validator, dataclass=dataclass,
              deprecated=deprecated, Any=typing.Any, Generic=typing.Generic, TContext=typing.TypeVar("TContext"),
              Callable=Callable, Awaitable=Awaitable, AsyncGenerator=AsyncGenerator, ParametersType=dict,
              ToolExecResult=typing.Any, MessageEventResult=object)
    load_classes(root, "astrbot/core/agent/tool.py", {"ToolSchema", "FunctionTool", "ToolSet"}, ns)
    load_classes(root, "astrbot/core/provider/func_tool_manager.py", {"_PermissionGuardedTool"}, ns)
    wrapper = ns["_PermissionGuardedTool"]
    temp = workspace()
    try:
        path = Path(temp.name) / "host.sqlite3"; database(path)
        with host(Path(temp.name), (ns["FunctionTool"], ns["ToolSet"]), wrapper) as (plugin_module, _, registry):
            context = Context(path); plugin = plugin_module.HistoryQueryPlugin(context, config())
            assert plugin.setup_error is None
            event = Event(); rc = NS(context=NS(event=event)); original_executor = Executor()
            runner = NS(run_context=rc, tool_executor=original_executor)
            registry[event.unified_msg_origin] = runner
            class Manager:
                deny = False
                async def _check_tool_permission(self, name, ctx):
                    return "denied by host" if self.deny else None
            manager = Manager()
            try:
                tools = ns["ToolSet"]([wrapper(t, manager) for t in plugin.tools])
                assert len(tools.get_light_tool_set().tools) == 3
                await plugin.filter_tools(event, NS(func_tool=tools))
                await plugin.begin(event, rc)
                assert plugin.active
                plugin.active[id(event)][1].started = datetime(2026, 11, 14, 12, tzinfo=timezone.utc).timestamp()
                async def invoke(index, **kwargs):
                    response = [r async for r in runner.tool_executor.execute(tools.tools[index], rc, **kwargs)]
                    return response[0]
                manager.deny = True
                assert await invoke(0, name="英语群") == "denied by host"
                assert not context.calls
                manager.deny = False
                found = json.loads(await invoke(0, name="英语群"))
                result = json.loads(await invoke(1, source_refs=[found["candidates"][0]["source_ref"]]))
                assert result["records"]
                assert plugin.active[id(event)][1].locked
                await plugin.done(event, rc, None)
                assert runner.tool_executor is original_executor
            finally:
                await plugin.terminate()
        print(json.dumps({"status": "passed", "upstream": "AstrBot v4.29.4",
                          "verified": ["real FunctionTool/ToolSet construction", "permission wrapper denial", "find and search", "gate restoration"],
                          "limitation": "Host lifecycle/executor and adapter are synthetic; not a full AstrBot process."}, indent=2))
    finally:
        temp.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--source", type=Path, required=True)
    asyncio.run(check(parser.parse_args().source.resolve()))
