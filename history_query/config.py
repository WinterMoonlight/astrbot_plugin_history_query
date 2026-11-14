from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .errors import QueryError


def text(value: Any, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("字符串缺失或过长")
    return value.strip()


def identifier(value: Any) -> str:
    value = text(value)
    if not value.isascii() or not value.isdigit():
        raise ValueError("QQ 用户/机器人/群 ID 必须是数字字符串")
    return value


def platform(value: Any) -> str:
    value = text(value)
    if ":" in value or any(ord(c) < 32 for c in value):
        raise ValueError("平台实例 ID 不能含冒号或控制字符")
    return value


def strings(value: Any) -> frozenset[str]:
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError("授权列表必须是最多 500 项的列表")
    return frozenset(text(item, maximum=512) for item in value)


def boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("开关必须是布尔值")
    return value


@dataclass(frozen=True)
class Source:
    platform: str
    kind: str
    peer: str

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.kind}:{self.peer}"

    @classmethod
    def parse(cls, value: str) -> Source:
        parts = value.split(":")
        if len(parts) != 3 or parts[1] not in {"GroupMessage", "FriendMessage"}:
            raise ValueError("来源格式应为 实例ID:GroupMessage:群号 或 实例ID:FriendMessage:用户号")
        return cls(platform(parts[0]), parts[1], identifier(parts[2]))


@dataclass(frozen=True)
class ReaderRule:
    platform: str
    user_id: str
    groups: frozenset[str]
    private_sessions: frozenset[str]
    all_groups_on: frozenset[str]
    all_private_on: frozenset[str]

    def permits(self, source: Source) -> bool:
        if source.kind == "GroupMessage":
            return source.key in self.groups or source.platform in self.all_groups_on
        return source.key in self.private_sessions or source.platform in self.all_private_on


@dataclass(frozen=True)
class Limits:
    scan: int = 500
    private_recent_scan: int = 50
    queue_seconds: float = 5.0
    execution_seconds: float = 5.0
    queue_capacity: int = 4
    page_size: int = 25
    max_return: int = 50
    return_chars: int = 12000
    message_chars: int = 1600
    blob_bytes: int = 1024 * 1024
    idle_seconds: float = 30.0
    reference_ttl: float = 900.0
    max_references: int = 2048
    max_sources: int = 5
    max_catalog: int = 200
    high_frequency: int = 300


@dataclass(frozen=True)
class Settings:
    enabled: bool
    instances: Mapping[str, str]
    readers: Mapping[tuple[str, str], ReaderRule]
    aliases: Mapping[str, tuple[str, ...]]
    limits: Limits
    audit_enabled: bool

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> Settings:
        try:
            instances = {}
            for item in raw.get("instances", []):
                key = platform(item["platform_id"])
                if key in instances:
                    raise ValueError("平台实例重复")
                instances[key] = identifier(item["bot_id"])
            readers = {}
            for item in raw.get("readers", []):
                rule = ReaderRule(
                    platform(item["platform_id"]), identifier(item["user_id"]),
                    strings(item.get("groups", [])), strings(item.get("private_sessions", [])),
                    strings(item.get("all_groups_on", [])), strings(item.get("all_private_on", [])),
                )
                if rule.platform not in instances or (rule.platform, rule.user_id) in readers:
                    raise ValueError("查询用户所属实例未绑定或用户重复")
                for values, kind in [(rule.groups, "GroupMessage"), (rule.private_sessions, "FriendMessage")]:
                    for value in values:
                        source = Source.parse(value)
                        if source.kind != kind or source.platform not in instances:
                            raise ValueError("授权来源类型错误或实例未绑定")
                if not (rule.all_groups_on | rule.all_private_on) <= instances.keys():
                    raise ValueError("全部会话授权引用了未绑定实例")
                readers[rule.platform, rule.user_id] = rule
            aliases = {}
            for item in raw.get("aliases", []):
                source = Source.parse(text(item["source"], maximum=512))
                if source.platform not in instances or source.key in aliases:
                    raise ValueError("别名来源未绑定或重复")
                aliases[source.key] = tuple(sorted(strings(item.get("names", []))))
            changes = {}
            ranges = {
                "scan": (1, 2000), "private_recent_scan": (1, 2000), "queue_seconds": (0.1, 15), "execution_seconds": (0.1, 15),
                "queue_capacity": (1, 16), "page_size": (1, 50), "return_chars": (1000, 24000),
                "blob_bytes": (16384, 4 * 1024 * 1024), "idle_seconds": (5, 120),
            }
            integers = {"scan", "private_recent_scan", "queue_capacity", "page_size", "return_chars", "blob_bytes"}
            for key, value in raw.get("limits", {}).items():
                if key not in ranges:
                    continue  # schema UI may add harmless presentation metadata
                low, high = ranges[key]
                if type(value) not in (int, float) or (key in integers and type(value) is not int) or not low <= value <= high:
                    raise ValueError(f"限额 {key} 不在允许范围")
                changes[key] = value
            return cls(boolean(raw.get("enabled", False)), MappingProxyType(instances),
                       MappingProxyType(readers), MappingProxyType(aliases), Limits(**changes),
                       boolean(raw.get("audit_enabled", True)))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise QueryError("invalid_config", f"历史查询配置无效：{exc}") from exc
