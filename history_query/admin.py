"""Authenticated dashboard configuration operations; never exposed as LLM tools."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import asdict

from .config import Limits, Settings, Source, identifier, platform
from .gate import SUPPORTED_VERSIONS
from .metadata import Metadata


PLUGIN_NAME = "astrbot_plugin_history_query"
LIMIT_FIELDS = ("scan", "private_recent_scan", "queue_seconds", "execution_seconds", "queue_capacity", "page_size",
                "return_chars", "blob_bytes", "idle_seconds")
LIMIT_RANGES = {"scan": (1, 2000), "private_recent_scan": (1, 2000), "queue_seconds": (0.1, 15), "execution_seconds": (0.1, 15),
                "queue_capacity": (1, 16), "page_size": (1, 50), "return_chars": (1000, 24000),
                "blob_bytes": (16384, 4194304), "idle_seconds": (5, 120)}
COLLECTION_FIELDS = {
    "instances": {"platform_id", "bot_id"},
    "readers": {"platform_id", "user_id", "groups", "private_sessions", "all_groups_on", "all_private_on"},
    "aliases": {"source", "names"},
}


class AdminError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def defaults():
    limits = asdict(Limits())
    return {"enabled": False, "instances": [], "readers": [], "aliases": [],
            "limits": {key: limits[key] for key in LIMIT_FIELDS}, "audit_enabled": True}


def revision(config):
    encoded = json.dumps(dict(config), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def normalize(raw):
    """Strict editor input; original schema remains a usable fallback editor."""
    if not isinstance(raw, dict) or set(raw) - set(defaults()):
        raise AdminError("设置必须是对象，且不能包含未知配置项。")
    if len(json.dumps(raw, ensure_ascii=False).encode()) > 262144:
        raise AdminError("配置超过 256 KiB，请减少条目数量。", 413)
    result = defaults()
    result.update(copy.deepcopy(raw))
    if not isinstance(result["limits"], dict) or set(result["limits"]) - set(LIMIT_FIELDS):
        raise AdminError("资源限额包含未知配置项。")
    result["limits"] = {**defaults()["limits"], **result["limits"]}
    for collection, fields in COLLECTION_FIELDS.items():
        values = result[collection]
        maximum = {"instances": 32, "readers": 128, "aliases": 512}[collection]
        if not isinstance(values, list) or len(values) > maximum:
            raise AdminError(f"{collection} 的条目数量不合法，最多 {maximum} 项。")
        clean = []
        for index, value in enumerate(values):
            if not isinstance(value, dict) or set(value) - (fields | {"__template_key"}):
                raise AdminError(f"{collection} 第 {index + 1} 项包含未知字段。")
            if value.get("__template_key", "entry") != "entry":
                raise AdminError("不支持的配置模板。")
            item = {"__template_key": "entry"}
            for field in fields:
                list_field = field in {"groups", "private_sessions", "all_groups_on", "all_private_on", "names"}
                supplied = value.get(field, [] if list_field else "")
                if list_field:
                    if not isinstance(supplied, list) or any(not isinstance(v, str) for v in supplied):
                        raise AdminError(f"{collection} 第 {index + 1} 项的 {field} 必须为文本列表。")
                    item[field] = list(dict.fromkeys(v.strip() for v in supplied))
                else:
                    if not isinstance(supplied, str):
                        raise AdminError(f"{collection} 第 {index + 1} 项的 {field} 必须为文本。")
                    item[field] = supplied.strip()
            clean.append(item)
        result[collection] = clean
    try:
        Settings.parse(result)
    except Exception as exc:
        raise AdminError(str(exc)) from exc
    return result


class ConfigEditor:
    def __init__(self, config, context, version, runtime, persist, apply, closed):
        self.config, self.context, self.version = config, context, version
        self.runtime, self.persist, self.apply, self.closed = runtime, persist, apply, closed
        self.lock = asyncio.Lock()

    def authorize(self, username, plugin_name):
        if not isinstance(username, str) or not username.strip() or plugin_name != PLUGIN_NAME:
            raise AdminError("请从已登录的 AstrBot 插件视图打开管理页。", 403)
        if self.closed():
            raise AdminError("插件正在卸载或重载，请稍后刷新。", 503)

    def available_instances(self):
        result = []
        manager = getattr(self.context, "platform_manager", None)
        for adapter in getattr(manager, "platform_insts", [])[:200]:
            try:
                meta = adapter.meta()
                if meta.name == "aiocqhttp":
                    result.append({"id": str(meta.id)[:128], "type": "aiocqhttp"})
            except Exception:
                continue
        return result

    def snapshot(self):
        raw = dict(self.config)
        # Only this plugin's public configuration fields are exposed.
        config = normalize({key: raw[key] for key in defaults() if key in raw})
        return {"config": config, "revision": revision(raw), "defaults": defaults(),
                "limits": {key: list(bounds) for key, bounds in LIMIT_RANGES.items()},
                "available_instances": self.available_instances(),
                "status": {"astrbot_version": self.version, "version_supported": self.version in SUPPORTED_VERSIONS,
                           **self.runtime()}}

    async def get(self, username, plugin_name):
        self.authorize(username, plugin_name)
        async with self.lock:
            return self.snapshot()

    async def save(self, username, plugin_name, payload):
        self.authorize(username, plugin_name)
        if not isinstance(payload, dict) or set(payload) != {"revision", "config"}:
            raise AdminError("保存请求需要 revision 和 config。")
        if not isinstance(payload["revision"], str) or len(payload["revision"]) != 64:
            raise AdminError("配置版本无效，请刷新后重试。")
        desired = normalize(payload["config"])
        async with self.lock:
            self.authorize(username, plugin_name)
            if payload["revision"] != revision(self.config):
                raise AdminError("设置已在其他页面更新。当前草稿仍保留，请刷新后重新编辑。", 409)
            before = copy.deepcopy(dict(self.config))
            if desired == self.snapshot()["config"]:
                return {**self.snapshot(), "saved": False, "message": "设置没有变化。"}
            try:
                committed = await self.persist(desired)
            except Exception as exc:
                # The native saver updates memory before writing its atomic file.
                # Only undo our own still-current mutation; never undo a newer one.
                merged = {**before, **desired}
                if revision(self.config) == revision(merged):
                    self.config.clear()
                    self.config.update(before)
                raise AdminError("保存失败，请检查配置文件写入权限后重试；草稿仍保留。", 500) from exc
            if committed is False or self.snapshot()["config"] != desired:
                raise AdminError("保存期间发生了另一项配置更新，请刷新后核对。", 409)
            warning = await self.apply()
            return {**self.snapshot(), "saved": True, "message": "设置已保存。后续查询使用新设置，旧查询引用需重新获取。",
                    "warning": warning}

    async def identify(self, username, plugin_name, instance):
        self.authorize(username, plugin_name)
        try:
            instance = platform(instance)
            adapter = self.context.get_platform_inst(instance)
            if adapter is None or adapter.meta().name != "aiocqhttp":
                raise AdminError("该 OneBot 实例当前不可用。")
            async with asyncio.timeout(5):
                data = await adapter.bot.call_action("get_login_info")
            bot = identifier(str(data.get("user_id", "")))
            return {"platform_id": instance, "bot_id": bot}
        except AdminError:
            raise
        except Exception as exc:
            raise AdminError("暂时无法识别机器人，请检查实例连接或手动填写 QQ。", 503) from exc

    async def groups(self, username, plugin_name, instance):
        self.authorize(username, plugin_name)
        try:
            settings = Settings.parse(dict(self.config))
            instance = platform(instance)
            if instance not in settings.instances:
                raise AdminError("请先保存该实例的机器人绑定，再获取群列表。")
            async with asyncio.timeout(5):
                groups = await Metadata(self.context, settings).groups(instance)
            return {"platform_id": instance, "groups": [
                {"id": peer, "name": name, "source": Source(instance, "GroupMessage", peer).key}
                for peer, name in sorted(groups.items(), key=lambda item: (item[1], item[0]))]}
        except AdminError:
            raise
        except Exception as exc:
            raise AdminError("暂时无法获取群列表或机器人绑定不符，可稍后重试。", 503) from exc
