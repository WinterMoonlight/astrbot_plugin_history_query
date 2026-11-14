from __future__ import annotations

import asyncio
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .config import Settings, Source
from .errors import QueryError, fail


@dataclass(frozen=True)
class Principal:
    platform: str
    bot: str
    user: str
    origin: str

    @property
    def binding(self):
        return self.platform, self.bot, self.user, self.origin

    @classmethod
    def from_event(cls, event, settings: Settings):
        try:
            p = cls(str(event.get_platform_id()), str(event.get_self_id()),
                    str(event.get_sender_id()), str(event.unified_msg_origin))
            kind = event.get_message_type()
            kind = getattr(kind, "value", kind)
            if not settings.enabled:
                fail("disabled", "历史查询尚未启用。")
            if event.get_platform_name() != "aiocqhttp" or kind != "FriendMessage" or event.get_group_id():
                fail("denied", "历史查询只允许获授权用户在 OneBot 私聊中使用。")
            if settings.instances.get(p.platform) != p.bot or (p.platform, p.user) not in settings.readers:
                fail("denied", "当前账号没有历史查询权限。")
            if p.origin != f"{p.platform}:FriendMessage:{p.user}":
                fail("denied", "当前会话身份无法可靠核对。")
            return p
        except QueryError:
            raise
        except Exception as exc:
            raise QueryError("denied", "无法核对可信的调用者身份。") from exc

    def authorize(self, settings: Settings, source: Source):
        rule = settings.readers.get((self.platform, self.user))
        if not settings.enabled or settings.instances.get(self.platform) != self.bot or not rule or not rule.permits(source):
            fail("denied", "当前账号没有该会话的历史查询权限。")


@dataclass
class Turn:
    principal: Principal
    event: Any
    queue_left: float
    execution_left: float
    rows_left: int
    chars_left: int
    locked: bool = False
    closed: bool = False
    working: bool = False
    ambiguous: bool = False
    busy: asyncio.Lock = field(default_factory=asyncio.Lock)
    admission: asyncio.Lock = field(default_factory=asyncio.Lock)
    scope: tuple | None = None
    bridge: Any = None
    started: float = field(default_factory=time.time)
    checked_sources: set[Source] = field(default_factory=set)

    @classmethod
    def create(cls, principal, event, limits):
        return cls(principal, event, limits.queue_seconds, limits.execution_seconds, limits.scan, limits.return_chars)

    def consume(self, count: int):
        if type(count) is not int or count < 0 or count > self.rows_left:
            fail("worker_protocol", "查询计数异常，已停止读取。")
        self.rows_left -= count


class References:
    """Bounded metadata-only references. No message bodies or raw user prompts."""
    def __init__(self, ttl: float, maximum: int, clock=time.monotonic):
        self.ttl, self.maximum, self.clock = ttl, maximum, clock
        self.items: OrderedDict[str, tuple[float, tuple, str, Any]] = OrderedDict()

    def prune(self):
        now = self.clock()
        for token, entry in list(self.items.items()):
            if entry[0] <= now:
                self.items.pop(token, None)

    def put(self, principal: Principal, kind: str, value: Any) -> str:
        self.prune()
        while len(self.items) >= self.maximum:
            self.items.popitem(last=False)
        token = secrets.token_urlsafe(24)
        self.items[token] = (self.clock() + self.ttl, principal.binding, kind, value)
        return token

    def get(self, token: str, principal: Principal, kind: str):
        self.prune()
        entry = self.items.get(token)
        if not entry or entry[1] != principal.binding or entry[2] != kind:
            fail("invalid_reference", "引用无效、已过期或不属于当前查询者，请重新定位会话。")
        return entry[3]

    def clear(self):
        self.items.clear()
