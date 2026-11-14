from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

from .config import Settings, Source
from .errors import QueryError, fail


class Metadata:
    """Only metadata through the existing adapter; no history RPC or sending API."""
    def __init__(self, context, settings: Settings):
        self.context, self.settings = context, settings
        self.names = OrderedDict()

    async def _api(self, instance, method, **kwargs):
        if method not in {"get_login_info", "get_group_list", "get_stranger_info"}:
            fail("invalid_metadata_operation", "不允许的元信息请求。")
        adapter = self.context.get_platform_inst(instance)
        if adapter is None or adapter.meta().name != "aiocqhttp":
            fail("platform_unavailable", "来源平台当前不可用。")
        try:
            return await adapter.bot.call_action(method, **kwargs)
        except Exception as exc:
            raise QueryError("membership_unknown", "暂时无法确认机器人访问状态，请稍后重试。") from exc

    async def groups(self, instance):
        login = await self._api(instance, "get_login_info")
        if not isinstance(login, dict) or str(login.get("user_id")) != self.settings.instances.get(instance):
            fail("platform_identity_changed", "机器人账号与配置绑定不符，已拒绝该实例历史查询。")
        groups = await self._api(instance, "get_group_list")
        if not isinstance(groups, list) or len(groups) > 2000:
            fail("membership_unknown", "群列表不完整或格式异常，无法确认访问资格。")
        result = {}
        for item in groups:
            if not isinstance(item, dict):
                fail("membership_unknown", "群列表格式异常。")
            peer = str(item.get("group_id", ""))
            if not peer.isascii() or not peer.isdigit():
                fail("membership_unknown", "群列表缺少有效群号。")
            result[peer] = str(item.get("group_name", peer))[:128]
        return result

    async def check(self, sources):
        groups = {}
        checked = set()
        for source in sources:
            if source.platform in checked:
                continue
            if any(s.platform == source.platform and s.kind == "GroupMessage" for s in sources):
                groups[source.platform] = await self.groups(source.platform)
            else:
                login = await self._api(source.platform, "get_login_info")
                if not isinstance(login, dict) or str(login.get("user_id")) != self.settings.instances.get(source.platform):
                    fail("platform_identity_changed", "来源机器人身份无法确认。")
            checked.add(source.platform)
        for source in sources:
            if source.kind == "GroupMessage" and source.peer not in groups[source.platform]:
                fail("not_in_group", "机器人当前不在目标群内，不再开放该群的历史查询。")
        return groups

    async def aliases(self, keys):
        if not keys:
            return {}
        try:
            records = await self.context.get_db().get_umo_aliases(umos=list(keys)[:200])
            return {r.umo: tuple(str(v)[:128] for v in (r.user_alias, r.auto_name) if v)
                    for r in records if r.umo in keys}
        except Exception:
            return {}  # Names are convenience metadata, not authorization evidence.

    async def private_name(self, source, known_name="", known_basis="session_alias"):
        """Call only after source authorization and current bot identity checks.

        Query at most one profile, never the entire friend list. This cache is
        display-only and cannot replace access checks. No data is persisted.
        """
        if known_name and known_basis not in {"current_qq_nickname", "id_only"} and known_name not in {source.peer, f"私聊 {source.peer}"}:
            return str(known_name)[:128], known_basis
        key = (source.platform, self.settings.instances[source.platform], source.peer)
        cached = self.names.get(key)
        if cached and cached[0] > time.monotonic():
            self.names.move_to_end(key)
            return cached[1], cached[2]
        name = ""
        try:
            async with asyncio.timeout(0.35):
                data = await self._api(source.platform, "get_stranger_info",
                                       user_id=int(source.peer), no_cache=False)
            if isinstance(data, dict) and str(data.get("user_id")) == source.peer:
                value = data.get("nickname")
                if isinstance(value, str):
                    name = " ".join(value.split())[:128]
                    if name == source.peer:
                        name = ""
        except (QueryError, TimeoutError):
            pass  # Missing display metadata does not invalidate readable history.
        basis = "current_qq_nickname" if name else "id_only"
        self.names[key] = (time.monotonic() + (300 if name else 30), name, basis)
        self.names.move_to_end(key)
        while len(self.names) > 256:
            self.names.popitem(last=False)
        return name, basis
