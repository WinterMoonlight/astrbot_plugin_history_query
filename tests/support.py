from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

from history_query.config import Settings

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".test-artifacts"


def workspace():
    BASE.mkdir(exist_ok=True)
    temp = tempfile.TemporaryDirectory(prefix="synthetic-", dir=BASE)
    assert Path(temp.name).resolve().is_relative_to(BASE.resolve())
    return temp


def config():
    return {"enabled": True, "instances": [
        {"platform_id": "qq-a", "bot_id": "900"}, {"platform_id": "qq-b", "bot_id": "901"}],
        "readers": [{"platform_id": "qq-a", "user_id": "7", "groups": ["qq-a:GroupMessage:100", "qq-a:GroupMessage:200"],
                     "private_sessions": ["qq-a:FriendMessage:11"], "all_groups_on": [], "all_private_on": []}],
        "aliases": [{"source": "qq-a:GroupMessage:100", "names": ["英语群"]},
                    {"source": "qq-a:FriendMessage:11", "names": ["小王"]}],
        "audit_enabled": True}


def database(path, groups=30):
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE platform_message_history(id INTEGER PRIMARY KEY AUTOINCREMENT,platform_id TEXT,user_id TEXT,
        sender_id TEXT,sender_name TEXT,content JSON,created_at DATETIME);
      CREATE INDEX ix_platform_message_history_platform_user_id ON platform_message_history(platform_id,user_id,id);
      CREATE TABLE conversations(inner_conversation_id INTEGER PRIMARY KEY,conversation_id TEXT UNIQUE,platform_id TEXT,
        user_id TEXT,content JSON,updated_at DATETIME);
      """)
    for i in range(groups):
        add_group(db, 100, f"作业 synthetic {i}")
    add_group(db, 100, "secret other platform", platform="qq-b")
    for i in range(3):
        db.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?)", (i + 1, f"c{i}", "qq-a", "qq-a:FriendMessage:11",
          json.dumps([{"role": "system", "content": "must not leak"},
                      {"role": "user", "content": f"安排 synthetic {i}"},
                      {"role": "assistant", "content": "回答"},
                      {"role": "tool", "content": "internal secret"}], ensure_ascii=False), "2026-11-14 01:00:00"))
    db.commit()
    db.close()


def add_group(db, group, body, *, platform="qq-a", session=None, timestamp="2026-11-14 01:00:00", sender="11"):
    db.execute("INSERT INTO platform_message_history(platform_id,user_id,sender_id,sender_name,content,created_at) VALUES (?,?,?,?,?,?)",
               (platform, session or f"{platform}:GroupMessage:{group}", sender, "小王",
                json.dumps({"type": "user", "message": [{"type": "plain", "text": body}]}, ensure_ascii=False), timestamp))


class Event:
    def __init__(self, text="查英语群作业", platform="qq-a", bot="900", user="7", group=""):
        self.text, self.platform, self.bot, self.user, self.group = text, platform, bot, user, group
        self.unified_msg_origin = f"{platform}:GroupMessage:{group}" if group else f"{platform}:FriendMessage:{user}"
    def get_platform_id(self): return self.platform
    def get_platform_name(self): return "aiocqhttp"
    def get_self_id(self): return self.bot
    def get_sender_id(self): return self.user
    def get_group_id(self): return self.group
    def get_message_type(self): return NS(value="GroupMessage" if self.group else "FriendMessage")
    def get_message_str(self): return self.text


class Context:
    def __init__(self, path):
        self.path = path
        self.groups = {"qq-a": {"100": "大学英语", "200": "学院群"}, "qq-b": {"100": "别的平台"}}
        self.offline = False
        self.calls = []
        self.nicknames = {}
        self.profile_requests = []
    def get_platform_inst(self, instance):
        async def action(method, **kwargs):
            self.calls.append((instance, method))
            if self.offline:
                raise ConnectionError("synthetic offline")
            if method == "get_login_info": return {"user_id": "900" if instance == "qq-a" else "901"}
            if method == "get_group_list": return [{"group_id": k, "group_name": v} for k, v in self.groups[instance].items()]
            if method == "get_stranger_info":
                peer = str(kwargs["user_id"])
                self.profile_requests.append((instance, peer))
                return {"user_id": peer, "nickname": self.nicknames.get((instance, peer), "")}
            raise AssertionError("No other RPC allowed")
        return NS(meta=lambda: NS(name="aiocqhttp"), bot=NS(call_action=action))
    def get_db(self):
        async def aliases(umos): return []
        return NS(db_path=str(self.path), get_umo_aliases=aliases)


class Binding:
    def __init__(self, turn): self.turn = turn
    def valid(self): return not self.turn.closed
    async def begin_read(self): self.turn.locked = True
