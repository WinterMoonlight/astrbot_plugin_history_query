from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from pathlib import Path

from history_query.config import Source
from history_query.database import Database
from history_query.errors import QueryError
from tests.support import add_group, database, workspace


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace(); self.path = Path(self.temp.name) / "host.sqlite3"
        database(self.path); self.reader = Database(str(self.path))
    def tearDown(self): self.temp.cleanup()
    def write(self, fn):
        db = sqlite3.connect(self.path)
        try: fn(db); db.commit()
        finally: db.close()
    def start(self, source="qq-a:GroupMessage:100", sample=0):
        return self.reader.run({"op": "start", "source": source, "end": "2026-11-15 00:00:00", "since": "2026-11-14 00:00:00", "sample": sample})
    def page(self, start, source="qq-a:GroupMessage:100", **extra):
        return self.reader.run({"op": "page", "source": source, "state": start["state"], "generation": start["generation"], "limit": 10, **extra})

    def test_readonly_file_schema_and_operation_guard(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        db, _, _ = self.reader.open(1)
        try:
            for query in ["DELETE FROM conversations", "CREATE INDEX no ON conversations(user_id)", "ATTACH ':memory:' AS side", "PRAGMA user_version=55", "SELECT load_extension('x')"]:
                with self.subTest(query=query), self.assertRaises(sqlite3.DatabaseError): db.execute(query)
        finally: db.rollback(); db.close()
        self.page(self.start())
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_same_id_other_platform_and_isolated_group_scopes(self):
        self.write(lambda db: [add_group(db, 100, "isolated", session=s) for s in [
            "qq-a:GroupMessage:7_100", "qq-a:GroupMessage:bad_100", "qq-a:GroupMessage:7_2100"]])
        rows = self.page(self.start(), limit=50)["rows"]
        texts = [r["text"] for r in rows]
        self.assertEqual(texts.count("isolated"), 1)
        self.assertNotIn("secret other platform", texts)

    def test_stable_cursor_with_insert_and_deleted_anchor(self):
        started = self.start(); first = self.page(started)
        anchor = first["state"]["before"]
        self.write(lambda db: (add_group(db, 100, "new"), db.execute("DELETE FROM platform_message_history WHERE id=?", (anchor,))))
        second = self.page({**started, "state": first["state"]})
        ids = [r["reference"]["id"] for r in first["rows"] + second["rows"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i <= started["state"]["upper"] for i in ids))

    def test_context_stale_or_wrong_source_rejected(self):
        start = self.start(); row = self.page(start)["rows"][0]
        request = {"op": "context", "source": "qq-a:GroupMessage:100", "state": start["state"], "reference": row["reference"], "radius": 2}
        self.assertLessEqual(self.reader.run(request)["scanned"], 5)
        with self.assertRaises(QueryError): self.reader.run({**request, "source": "qq-b:GroupMessage:100"})
        self.write(lambda db: db.execute("UPDATE platform_message_history SET content='{}' WHERE id=?", (row["reference"]["id"],)))
        with self.assertRaises(QueryError): self.reader.run(request)

    def test_frequency_boundary_and_uncertain_sample(self):
        self.write(lambda db: [add_group(db, 200, "x") for _ in range(299)])
        a = self.start("qq-a:GroupMessage:200", 300)
        self.assertTrue(a["frequency_known"]); self.assertFalse(a["high"])
        self.write(lambda db: add_group(db, 200, "x"))
        b = self.start("qq-a:GroupMessage:200", 300)
        self.assertTrue(b["high"]); self.assertEqual(b["scanned"], 300)
        c = self.start("qq-a:GroupMessage:200", 50)
        self.assertFalse(c["frequency_known"])

    def test_ai_multiple_conversations_and_exclude_internal_messages(self):
        source = "qq-a:FriendMessage:11"; start = self.start(source)
        rows = self.page(start, source, limit=50)["rows"]
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r["time"] is None for r in rows))
        self.assertFalse(any("leak" in r["text"] or "internal" in r["text"] for r in rows))

    def test_ai_digest_rejects_same_length_edit(self):
        source = "qq-a:FriendMessage:11"; start = self.start(source)
        page = self.page(start, source, limit=2)
        self.write(lambda db: db.execute("UPDATE conversations SET content=replace(content,'安排','更改') WHERE conversation_id='c2'"))
        with self.assertRaises(QueryError) as error: self.page({**start, "state": page["state"]}, source)
        self.assertEqual(error.exception.code, "stale")

    def test_oversize_and_invalid_json_do_not_become_instructions(self):
        self.write(lambda db: (add_group(db, 100, "x" * 2000000), add_group(db, 100, "long" * 600), db.execute("UPDATE platform_message_history SET content='not json' WHERE id=1")))
        page = self.page(self.start(), limit=50)
        self.assertTrue(page["warnings"])
        self.assertTrue(all(len(r["text"]) <= 1600 for r in page["rows"]))
        self.assertTrue(next(r for r in page["rows"] if r["text"].startswith("long"))["text_truncated"])

    def test_generation_change_requires_new_cursor(self):
        start = self.start()
        self.write(lambda db: db.execute("CREATE TABLE new_column_test(x)"))
        with self.assertRaises(QueryError) as error: self.page(start)
        self.assertEqual(error.exception.code, "stale")

    def test_literal_keyword_and_empty_source(self):
        page = self.page(self.start(), keywords=["' OR 1=1 --"])
        self.assertEqual(page["rows"], [])
        self.assertEqual(page["scanned"], 10)
        self.assertEqual(self.page(self.start("qq-a:GroupMessage:999"), "qq-a:GroupMessage:999")["rows"], [])
