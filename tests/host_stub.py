"""Documented host-contract doubles. No server or model is started."""
from __future__ import annotations

import importlib.util
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS

from tests.support import Context as MetadataContext, ROOT


class FunctionTool:
    def __init__(self, name, description, parameters, **kwargs):
        self.name, self.description, self.parameters = name, description, parameters
        self.active = True
        self.handler = None


class ToolSet:
    def __init__(self, tools=None): self.tools = list(tools or [])


class PermissionWrapper(FunctionTool):
    def __init__(self, tool, denied=False):
        super().__init__(tool.name, tool.description, tool.parameters)
        self._wrapped, self.denied = tool, denied
    async def call(self, context, **kwargs):
        if self.denied: return "host permission denied"
        return await self._wrapped.call(context, **kwargs)


class Executor:
    @classmethod
    async def execute(cls, tool, run_context, **kwargs):
        yield await tool.call(run_context, **kwargs)


class Manager:
    def __init__(self): self.tools = []
    def get_func(self, name): return next((t for t in self.tools if t.name == name), None)


class SQLiteDatabase:
    def __init__(self, path): self.db_path = str(path)
    async def get_umo_aliases(self, umos): return []


class Context(MetadataContext):
    def __init__(self, path): super().__init__(path); self.manager = Manager(); self.web_apis = []
    def get_db(self): return SQLiteDatabase(self.path)
    def get_llm_tool_manager(self): return self.manager
    def add_llm_tools(self, *tools): self.manager.tools.extend(tools)
    def register_web_api(self, *args): self.web_apis.append(args)


@contextmanager
def host(data_path, real_tool_types=None, permission_wrapper=None):
    saved = {key: value for key, value in sys.modules.items() if key == "astrbot" or key.startswith("astrbot.") or key.startswith("_history_test_plugin")}
    names = ["astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star", "astrbot.api.web", "astrbot.core", "astrbot.core.agent",
             "astrbot.core.agent.tool", "astrbot.core.utils", "astrbot.core.utils.astrbot_path",
             "astrbot.core.provider", "astrbot.core.provider.func_tool_manager", "astrbot.core.pipeline",
             "astrbot.core.pipeline.process_stage", "astrbot.core.pipeline.process_stage.follow_up",
             "astrbot.core.astr_agent_tool_exec"]
    for name in names:
        module = types.ModuleType(name); module.__path__ = []; sys.modules[name] = module
    astro = sys.modules["astrbot"]; astro.__version__ = "4.29.4"
    api = sys.modules["astrbot.api"]; api.AstrBotConfig = dict; api.logger = NS(warning=lambda *a: None)
    web = sys.modules["astrbot.api.web"]
    web.request = NS(username=None, plugin_name=None, query={})
    web.json_response = lambda value, status_code=200: NS(data=value, status_code=status_code)
    web.error_response = lambda message, status_code=400: NS(data={"status":"error","message":message},status_code=status_code)
    def decorator(): return lambda function: function
    event = sys.modules["astrbot.api.event"]
    event.AstrMessageEvent = object; event.filter = NS(on_llm_request=decorator, on_agent_begin=decorator, on_agent_done=decorator)
    class Star:
        def __init__(self, context): self.context = context
    star = sys.modules["astrbot.api.star"]; star.Context = Context; star.Star = Star
    tools = sys.modules["astrbot.core.agent.tool"]
    tools.FunctionTool, tools.ToolSet = real_tool_types or (FunctionTool, ToolSet)
    sys.modules["astrbot.core.utils.astrbot_path"].get_astrbot_data_path = lambda: str(data_path)
    sys.modules["astrbot.core.provider.func_tool_manager"]._PermissionGuardedTool = permission_wrapper or PermissionWrapper
    sys.modules["astrbot.core.astr_agent_tool_exec"].FunctionToolExecutor = Executor
    registry = sys.modules["astrbot.core.pipeline.process_stage.follow_up"]._ACTIVE_AGENT_RUNNERS = {}
    package = types.ModuleType("_history_test_plugin"); package.__path__ = [str(ROOT)]; sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location("_history_test_plugin.main", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module, astro, registry
    finally:
        for key in list(sys.modules):
            if key in names or key.startswith("_history_test_plugin"):
                sys.modules.pop(key, None)
        sys.modules.update(saved)
