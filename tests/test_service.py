from __future__ import annotations

import asyncio
import json
import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from history_query.config import Settings
from history_query.errors import QueryError
from history_query.process import Worker
from history_query.scheduler import Scheduler
from history_query.service import Service
from history_query.state import Principal, Turn
from tests.support import Binding, Context, Event, add_group, config, database, workspace


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = workspace(); self.root = Path(self.temp.name); self.path = self.root / "host.sqlite3"
        database(self.path); self.raw = config(); self.context = Context(self.path)
        self.service = Service(self.context, lambda: Settings.parse(self.raw), self.path, self.root / "audit")
        self.event, self.turn = self.make_turn()
    async def asyncTearDown(self):
        await self.service.close(); self.temp.cleanup()
    def make_turn(self, **kwargs):
        event = Event(**kwargs); settings = Settings.parse(self.raw)
        turn = Turn.create(Principal.from_event(event, settings), event, settings.limits)
        turn.started = datetime(2026, 11, 14, 12, tzinfo=timezone.utc).timestamp()
        turn.bridge = Binding(turn)
        return event, turn
    async def call(self, action, args, event=None, turn=None):
        event = event or self.event; turn = turn or self.turn
        return json.loads(await self.service.invoke(action, event, turn, args))
    async def source(self, name="英语群", **kwargs):
        response = await self.call("find", {"name": name}, **kwargs)
        self.assertEqual(response["status"], "resolved", response)
        return response["candidates"][0]["source_ref"]
    def write(self, fn):
        db = sqlite3.connect(self.path)
        try: fn(db); db.commit()
        finally: db.close()

    async def test_lookup_does_not_start_worker_and_search_latches(self):
        source = await self.source()
        self.assertEqual(self.service.worker.starts, 0)
        self.assertFalse(self.turn.locked)
        result = await self.call("search", {"source_refs": [source], "keywords": ["作业"]})
        self.assertTrue(result["records"], result)
        self.assertTrue(self.turn.locked)
        self.assertIn("+08:00", result["records"][0]["time"])
        self.assertEqual(self.service.worker.starts, 1)
        more = await self.call("context", {"record_ref": result["records"][0]["record_ref"], "radius": 1})
        self.assertEqual(more["status"], "context", more)
        self.assertLessEqual(len(more["records"]), 3)
        self.assertEqual(self.service.worker.starts, 1)

    async def test_unauthorized_call_never_queries_metadata_or_worker(self):
        response = await self.call("find", {"name": "英语群"}, event=Event(user="8"))
        self.assertEqual(response["status"], "denied")
        self.assertFalse(self.context.calls)
        self.assertEqual(self.service.worker.starts, 0)

    async def test_ambiguous_name_cannot_be_resolved_in_same_turn(self):
        self.context.groups["qq-a"]["200"] = "英语考试"
        response = await self.call("find", {"name": "英语"})
        self.assertEqual(response["status"], "ambiguous")
        self.assertTrue(all("source_ref" not in r for r in response["candidates"]))
        response = await self.call("find", {"name": "100"})
        self.assertEqual(response["status"], "ask_user")

    async def test_unknown_scope_asks_instead_of_auto_scan(self):
        self.assertEqual((await self.call("find", {"name": ""}))["status"], "ask_user")
        self.assertEqual((await self.call("search", {"source_refs": []}))["status"], "ask_user")
        self.assertFalse(self.context.calls)

    async def test_membership_unknown_and_left_are_not_empty_history(self):
        source = await self.source()
        self.context.offline = True
        response = await self.call("search", {"source_refs": [source]})
        self.assertEqual(response["issue"]["code"], "membership_unknown")
        self.assertFalse(response["records"])
        self.assertEqual(self.service.worker.starts, 0)
        self.context.offline = False; self.context.groups["qq-a"].pop("100")
        response = await self.call("search", {"source_refs": [source]})
        self.assertEqual(response["issue"]["code"], "not_in_group")

    async def test_mid_read_departure_discards_result(self):
        source = await self.source(); command = self.service.worker.command
        async def mutate(request):
            result = await command(request)
            if request["op"] == "page": self.context.groups["qq-a"].pop("100", None)
            return result
        self.service.worker.command = mutate
        response = await self.call("search", {"source_refs": [source]})
        self.assertFalse(response["records"])
        self.assertEqual(response["issue"]["code"], "not_in_group")

    async def test_private_date_requires_user_confirmation(self):
        source = await self.source("小王")
        response = await self.call("search", {"source_refs": [source], "start_time": "2026-11-13", "end_time": "2026-11-14"})
        self.assertEqual(response["status"], "time_unknown")
        response = await self.call("search", {"source_refs": [source], "all_dates": True})
        self.assertEqual(response["status"], "ask_user")
        event, turn = self.make_turn(text="可以")
        response = await self.call("search", {"source_refs": [source], "all_dates": True}, event, turn)
        self.assertTrue(response["records"], response)
        self.assertTrue(all(r["time"] is None for r in response["records"]))

    async def test_same_turn_range_expansion_rejected(self):
        source = await self.source()
        await self.call("search", {"source_refs": [source], "start_time": "2026-11-14"})
        response = await self.call("search", {"source_refs": [source], "start_time": "2026-11-01"})
        self.assertEqual(response["status"], "ask_user")

    async def test_empty_history_is_distinct(self):
        source = await self.source("学院群")
        result = await self.call("search", {"source_refs": [source]})
        self.assertEqual(result["status"], "not_found", result)
        self.assertFalse(result["records"])

    async def test_scanning_fairness_and_budget_includes_misses(self):
        self.write(lambda db: [add_group(db, group, "not matching") for group in (100, 200) for _ in range(600)])
        a, b = await self.source(), await self.source("学院群")
        response = await self.call("search", {"source_refs": [a, b], "keywords": ["absent"], "start_time": "2026-11-14"})
        self.assertEqual(response["status"], "partial", response)
        self.assertEqual([r["scanned"] for r in response["coverage"]], [250, 250])
        self.assertEqual(self.turn.rows_left, 0)
        self.assertIsNotNone(response["continuation"])

    async def test_continuation_and_refs_are_owner_bound(self):
        source = await self.source()
        response = await self.call("search", {"source_refs": [source], "limit": 2})
        token = response["continuation"]
        self.assertTrue(token)
        event, turn = self.make_turn(text="继续")
        more = await self.call("search", {"continuation": token, "limit": 2}, event, turn)
        self.assertTrue(more["records"])
        old = {x["text"] for x in response["records"]}
        self.assertTrue(old.isdisjoint(x["text"] for x in more["records"]))
        self.raw["readers"][0]["groups"] = []
        denied = await self.call("context", {"record_ref": more["records"][0]["record_ref"]}, event, turn)
        self.assertEqual(denied["status"], "denied")

    async def test_output_limit_keeps_record_handles_and_no_body_audit(self):
        self.write(lambda db: [add_group(db, 100, "隐私内容" * 500) for _ in range(20)])
        source = await self.source()
        before = self.turn.chars_left
        raw = await self.service.invoke("search", self.event, self.turn, {"source_refs": [source], "limit": 50})
        self.assertLessEqual(len(raw), before)
        response = json.loads(raw)
        self.assertTrue(response.get("records"), response)
        self.assertTrue(all(r.get("record_ref") for r in response["records"]))
        audit = (self.root / "audit" / "query-audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("隐私内容", audit)
        self.assertNotIn("keywords", audit)

    async def test_revoke_during_audit_does_not_publish_records(self):
        source = await self.source()
        original = self.service.audit.write
        async def revoke(*args, **kwargs):
            await original(*args, **kwargs)
            self.raw["readers"] = []
        self.service.audit.write = revoke
        response = await self.call("search", {"source_refs": [source]})
        self.assertEqual(response["status"], "denied")
        self.assertNotIn("records", response)

    async def test_known_name_beyond_metadata_page_is_prioritized(self):
        self.raw["readers"][0]["all_groups_on"] = ["qq-a"]
        self.context.groups["qq-a"] = {str(i): f"group-{i}" for i in range(1000, 1500)}
        self.context.groups["qq-a"]["9999"] = "最后的英语班"
        result = await self.call("find", {"name": "最后的英语班", "kind": "group"})
        self.assertEqual(result["status"], "resolved", result)
        self.assertEqual(result["candidates"][0]["name"], "最后的英语班")


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = workspace(); self.path = Path(self.temp.name) / "host.sqlite3"; database(self.path)
        self.worker = Worker(self.path, replace(Settings.parse(config()).limits, idle_seconds=0.03))
    async def asyncTearDown(self):
        await self.worker.close(); self.temp.cleanup()
    def request(self): return {"op": "catalog_private", "platform": "qq-a", "seconds": 1}

    async def test_reuse_idle_exit_and_restart(self):
        await self.worker.command(self.request()); pid = self.worker.process.pid
        await self.worker.command(self.request()); self.assertEqual(self.worker.process.pid, pid)
        await asyncio.sleep(0.08); self.assertIsNone(self.worker.process)
        await self.worker.command(self.request()); self.assertEqual(self.worker.starts, 2)

    async def test_cancel_startup_reaps_process(self):
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.worker.command(self.request()), 0.0001)
        await self.worker.abort()
        self.assertIsNone(self.worker.process)
        self.assertFalse(self.worker.inflight)

    async def test_locked_database_timeout_kills_active_process(self):
        await self.worker.command(self.request())
        settings = Settings.parse(config()); event = Event()
        turn = Turn.create(Principal.from_event(event, settings), event, settings.limits)
        turn.execution_left = 0.02
        scheduler = Scheduler(1, self.worker.abort)
        writer = sqlite3.connect(self.path)
        writer.execute("BEGIN EXCLUSIVE")
        try:
            with self.assertRaises(QueryError) as error:
                await scheduler.run(turn, lambda: self.worker.command(self.request()))
            self.assertEqual(error.exception.code, "timeout")
            self.assertIsNone(self.worker.process)
        finally: writer.rollback(); writer.close()
