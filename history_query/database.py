"""Fixed read-only SQL operations. Imported only by the isolated worker/tests."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from .config import Source
from .errors import QueryError, fail


REQUIRED = {
    "platform_message_history": {"id", "platform_id", "user_id", "content", "sender_id", "sender_name", "created_at"},
    "conversations": {"inner_conversation_id", "conversation_id", "platform_id", "user_id", "content", "updated_at"},
}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def plain(content, maximum: int) -> str:
    """Only visible text; never return system/tool internals, URLs or local paths."""
    if isinstance(content, str):
        return content[:maximum]
    result = []
    if isinstance(content, list):
        for part in content[:128]:
            if not isinstance(part, dict):
                continue
            kind = str(part.get("type", "")).lower()
            if kind in {"text", "plain"}:
                result.append(str(part.get("text", "")))
            elif kind == "reply":
                result.append("[引用] " + str(part.get("text", "")))
            elif kind == "at":
                result.append("@" + str(part.get("name") or part.get("user_id") or "成员"))
            elif kind:
                result.append(f"[{kind[:32]}]")
            if sum(map(len, result)) >= maximum:
                break
    return " ".join(result)[:maximum]


def group_where(source: Source):
    prefix = f"{source.platform}:GroupMessage:"
    suffix = "_" + source.peer
    args = {"platform": source.platform, "canonical": source.key, "prefix": prefix,
            "prefix_len": len(prefix), "suffix": suffix, "suffix_len": len(suffix)}
    sql = """platform_id=:platform AND (
      user_id=:canonical OR (
        substr(user_id,1,:prefix_len)=:prefix
        AND substr(user_id,-:suffix_len)=:suffix
        AND length(user_id)>:prefix_len+:suffix_len
        AND substr(user_id,:prefix_len+1,length(user_id)-:prefix_len-:suffix_len)
             NOT GLOB '*[^0-9]*'))"""
    return sql, args


class Database:
    def __init__(self, path: str, blob_bytes: int = 1048576, message_chars: int = 1600):
        self.path = Path(path).resolve(strict=True)
        if not self.path.is_file():
            fail("database_unavailable", "宿主数据库不可用。")
        self.blob_bytes, self.message_chars = blob_bytes, message_chars

    def open(self, seconds):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=min(0.1, seconds))
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA cache_size=-1024")
            db.execute("SELECT key FROM json_each('[]')").fetchall()
            for table, required in REQUIRED.items():
                columns = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                if not required <= columns:
                    fail("schema_unsupported", "宿主历史表结构不兼容，已拒绝读取。")
            version = db.execute("PRAGMA schema_version").fetchone()[0]
            stat = self.path.stat()
            generation = [stat.st_dev, stat.st_ino, version]
            allowed_functions = {"substr", "length", "max", "count", "json_valid", "json_array_length", "coalesce", "like", "glob"}
            def authorize(op, a, b, database, trigger):
                if op in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
                    return sqlite3.SQLITE_OK
                if op == sqlite3.SQLITE_READ and a in {*REQUIRED, "json_each", "sqlite_master"}:
                    return sqlite3.SQLITE_OK
                if op == sqlite3.SQLITE_FUNCTION and (b or a or "").lower() in allowed_functions:
                    return sqlite3.SQLITE_OK
                if op == sqlite3.SQLITE_TRANSACTION and a in {"BEGIN", "ROLLBACK", "COMMIT"}:
                    return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY
            db.set_authorizer(authorize)
            end = time.monotonic() + seconds
            db.set_progress_handler(lambda: int(time.monotonic() >= end), 1000)
            db.execute("BEGIN")
            return db, generation, end
        except BaseException:
            db.close()
            raise

    def run(self, request):
        seconds = min(15.0, max(0.001, float(request.get("seconds", 5))))
        db, generation, deadline = self.open(seconds)
        try:
            op = request["op"]
            if op == "catalog_private":
                p = request["platform"]
                prefix = f"{p}:FriendMessage:"
                records = db.execute("""SELECT DISTINCT user_id FROM conversations
                    WHERE platform_id=? AND substr(user_id,1,?)=? AND user_id>?
                    ORDER BY user_id LIMIT ?""", (p, len(prefix), prefix, request.get("after", ""), 201)).fetchall()
                sources = []
                for r in records[:200]:
                    try:
                        s = Source.parse(r[0])
                        if s.kind == "FriendMessage" and s.platform == p:
                            sources.append(s.key)
                    except ValueError:
                        pass
                return {"sources": sources, "more": len(records) > 200, "generation": generation}
            source = Source.parse(request["source"])
            expected = request.get("generation")
            if expected is not None and expected != generation:
                fail("stale", "数据库结构或文件已变化，请重新查询。")
            if op == "start":
                reply = self.start(db, source, request)
            elif op == "page":
                reply = self.group_page(db, source, request, deadline) if source.kind == "GroupMessage" else self.ai_page(db, source, request, deadline)
            elif op == "context":
                reply = self.context(db, source, request, deadline)
            else:
                fail("invalid_request", "不支持的读取操作。")
            reply["generation"] = generation
            return reply
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise QueryError("timeout", "读取时间已耗尽。") from exc
            if "locked" in str(exc) or "busy" in str(exc):
                raise QueryError("database_busy", "数据库暂时无法读取，请稍后重试。") from exc
            raise QueryError("read_failed", "历史读取接口或数据格式不兼容，已停止。") from exc
        except (sqlite3.DatabaseError, ValueError, TypeError, KeyError, OverflowError) as exc:
            raise QueryError("invalid_history", "历史数据或读取参数不兼容。") from exc
        finally:
            db.rollback()
            db.close()

    def start(self, db, source, request):
        if source.kind == "GroupMessage":
            where, args = group_where(source)
            args.update(end=request["end"], since=request["since"], sample=max(0, min(300, request.get("sample", 0))))
            upper = db.execute(f"SELECT max(id) FROM platform_message_history WHERE {where} AND created_at<:end", args).fetchone()[0] or 0
            count = db.execute(f"SELECT count(*) FROM (SELECT id FROM platform_message_history WHERE {where} AND id<=:upper AND created_at>=:since AND created_at<:end LIMIT :sample)", {**args, "upper": upper}).fetchone()[0] if args["sample"] else 0
            return {"state": {"upper": upper, "before": None, "start": request.get("start", ""), "end": request["end"], "done": not upper},
                    "sampled": count, "frequency_known": count < args["sample"] or count >= 300, "high": count >= 300, "rows": [], "scanned": count}
        upper = db.execute("SELECT max(inner_conversation_id) FROM conversations WHERE platform_id=? AND user_id=?", (source.platform, source.key)).fetchone()[0] or 0
        return {"state": {"upper": upper, "before_conv": None, "cid": None, "offset": 0, "digest": None, "done": not upper}, "rows": [], "scanned": 0}

    def matches(self, row, request):
        terms = request.get("keywords", [])
        sender = request.get("sender", "").casefold()
        return all(t.casefold() in row["text"].casefold() for t in terms) and (
            not sender or sender == row["sender_id"].casefold() or sender in row["sender_name"].casefold())

    def group_record(self, row, source):
        if row["content"] is None:
            return None
        try:
            datetime.fromisoformat(row["created_at"])
            payload = json.loads(row["content"])
            content = payload.get("message", [])
        except (ValueError, TypeError, AttributeError, RecursionError):
            return None
        value = plain(content, self.message_chars + 1)
        if not value:
            return None
        fingerprint = digest([row["id"], row["created_at"], row["sender_id"], row["content"]])
        return {"text": value[:self.message_chars],
                "text_truncated": len(value) > self.message_chars or (isinstance(content, list) and len(content) > 128),
                "sender_id": str(row["sender_id"] or ""),
                "sender_name": str(row["sender_name"] or ""), "role": str(payload.get("type", "user")),
                "time": row["created_at"], "time_basis": "stored_utc", "source_umo": row["user_id"],
                "reference": {"id": row["id"], "hash": fingerprint}}

    def group_select(self, where):
        return f"""SELECT id,substr(user_id,1,512) AS user_id,
          substr(sender_id,1,128) AS sender_id,substr(sender_name,1,128) AS sender_name,
          created_at,CASE WHEN length(CAST(content AS BLOB))<=:blob THEN content END AS content
          FROM platform_message_history WHERE {where}"""

    def group_page(self, db, source, request, deadline):
        state = dict(request["state"])
        if state["done"]:
            return {"state": state, "rows": [], "scanned": 0, "warnings": []}
        where, args = group_where(source)
        args.update(upper=state["upper"], before=state["before"], start=state["start"], end=state["end"],
                    blob=self.blob_bytes, limit=min(50, request["limit"]))
        where += " AND id<=:upper AND (:before IS NULL OR id<:before) AND created_at<:end AND (:start='' OR created_at>=:start)"
        cursor = db.execute(self.group_select(where) + " ORDER BY id DESC LIMIT :limit", args)
        result, skipped, scanned = [], 0, 0
        checked = []
        for row in cursor:
            if time.monotonic() >= deadline:
                fail("timeout", "读取时间已耗尽。")
            scanned += 1
            state["before"] = row["id"]
            if row["created_at"]:
                checked.append(str(row["created_at"]))
            item = self.group_record(row, source)
            if item is None:
                skipped += 1
            elif self.matches(item, request):
                result.append(item)
            if len(result) >= request.get("return_limit", 50):
                break
        if scanned < args["limit"] and len(result) < request.get("return_limit", 50):
            state["done"] = True
        return {"state": state, "rows": result, "scanned": scanned,
                "checked_range": [min(checked), max(checked)] if checked else None,
                "warnings": [f"{skipped} 条记录过大、无文字或格式不兼容，已跳过。"] if skipped else []}

    def ai_document(self, db, source, cid):
        row = db.execute("""SELECT inner_conversation_id,conversation_id,updated_at,
          length(CAST(content AS BLOB)) AS size,
          CASE WHEN length(CAST(content AS BLOB))<=? THEN content END AS content
          FROM conversations WHERE platform_id=? AND user_id=? AND conversation_id=?""",
                         (self.blob_bytes, source.platform, source.key, cid)).fetchone()
        return row

    def ai_record(self, raw, index, cid, source, doc_hash, bot_id="bot"):
        try:
            item = json.loads(raw) if isinstance(raw, str) else None
        except (ValueError, RecursionError):
            return None
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            return None
        original = item.get("content")
        content = plain(original, self.message_chars + 1)
        if not content:
            return None
        return {"text": content[:self.message_chars],
                "text_truncated": len(content) > self.message_chars or (isinstance(original, list) and len(original) > 128),
                "sender_id": source.peer if item["role"] == "user" else bot_id,
                "sender_name": "" if item["role"] == "user" else "机器人",
                "role": item["role"], "time": None, "time_basis": "unknown", "source_umo": source.key,
                "conversation_id": cid,
                "reference": {"cid": cid, "index": index, "hash": doc_hash}}

    def ai_page(self, db, source, request, deadline):
        state = dict(request["state"])
        rows, warnings, scanned = [], [], 0
        limit = min(50, request["limit"])
        while not state["done"] and scanned < limit and len(rows) < request.get("return_limit", 50):
            if time.monotonic() >= deadline:
                fail("timeout", "读取时间已耗尽。")
            if not state["cid"]:
                row = db.execute("""SELECT inner_conversation_id,conversation_id FROM conversations
                  WHERE platform_id=? AND user_id=? AND inner_conversation_id<=?
                  AND (? IS NULL OR inner_conversation_id<?) ORDER BY inner_conversation_id DESC LIMIT 1""",
                                 (source.platform, source.key, state["upper"], state["before_conv"], state["before_conv"])).fetchone()
                if row is None:
                    state["done"] = True
                    break
                state.update(cid=row["conversation_id"], before_conv=row["inner_conversation_id"], offset=0, digest=None)
            doc = self.ai_document(db, source, state["cid"])
            if doc is None or doc["content"] is None:
                warnings.append("一个旧对话已删除、为空或超过大小限制，未读取其正文。")
                state.update(cid=None, offset=0, digest=None)
                scanned += 1  # Charge skipped document inspection against bounded work.
                continue
            raw = doc["content"]
            doc_hash = digest(raw)
            if state["digest"] is not None and state["digest"] != doc_hash:
                fail("stale", "旧对话内容已变化，请重新搜索，避免上下文错位。")
            state["digest"] = doc_hash
            if not db.execute("SELECT json_valid(?)", (raw,)).fetchone()[0]:
                warnings.append("一个旧对话格式不兼容，已跳过。")
                state.update(cid=None, offset=0, digest=None)
                scanned += 1
                continue
            count = db.execute("SELECT json_array_length(?)", (raw,)).fetchone()[0]
            if type(count) is not int:
                count = 0
            if count == 0:
                state.update(cid=None, offset=0, digest=None)
                scanned += 1
                continue
            cursor = db.execute("SELECT key,value FROM json_each(?) WHERE key>=? AND key<? ORDER BY key",
                                (raw, state["offset"], state["offset"] + limit - scanned))
            for entry in cursor:
                scanned += 1
                state["offset"] = entry["key"] + 1
                item = self.ai_record(entry["value"], entry["key"], state["cid"], source, doc_hash, request.get("bot_id", "bot"))
                if item and self.matches(item, request):
                    rows.append(item)
                if len(rows) >= request.get("return_limit", 50):
                    break
            if state["offset"] >= count:
                state.update(cid=None, offset=0, digest=None)
                if count == 0:
                    scanned += 1
        return {"state": state, "rows": rows, "scanned": scanned, "warnings": warnings}

    def context(self, db, source, request, deadline):
        ref = request["reference"]
        radius = min(5, max(0, int(request.get("radius", 3))))
        rows = []
        if source.kind == "GroupMessage":
            where, args = group_where(source)
            state = request["state"]
            where += " AND id<=:upper AND created_at<:end AND (:start='' OR created_at>=:start)"
            args.update(upper=state["upper"], start=state["start"], end=state["end"], blob=self.blob_bytes,
                        anchor=ref["id"], radius=radius)
            anchor = db.execute(self.group_select(where + " AND id=:anchor"), args).fetchone()
            anchor_item = self.group_record(anchor, source) if anchor else None
            if anchor_item is None or anchor_item["reference"]["hash"] != ref["hash"]:
                fail("stale", "该命中记录已变化或清理，请重新搜索。")
            before = db.execute(self.group_select(where + " AND id<:anchor") + " ORDER BY id DESC LIMIT :radius", args).fetchall()
            after = db.execute(self.group_select(where + " AND id>:anchor") + " ORDER BY id ASC LIMIT :radius", args).fetchall()
            raw_rows = list(reversed(before)) + [anchor] + after
            for row in raw_rows:
                item = self.group_record(row, source)
                if item:
                    rows.append(item)
            scanned = len(raw_rows)
        else:
            doc = self.ai_document(db, source, ref["cid"])
            if doc is None or doc["content"] is None or digest(doc["content"]) != ref["hash"]:
                fail("stale", "旧对话已变化或清理，请重新搜索。")
            start = max(0, ref["index"] - radius)
            entries = db.execute("SELECT key,value FROM json_each(?) WHERE key>=? AND key<=? ORDER BY key",
                                 (doc["content"], start, ref["index"] + radius)).fetchall()
            for row in entries:
                item = self.ai_record(row["value"], row["key"], ref["cid"], source, ref["hash"], request.get("bot_id", "bot"))
                if item:
                    rows.append(item)
            scanned = len(entries)
        return {"rows": rows, "scanned": scanned, "warnings": []}
