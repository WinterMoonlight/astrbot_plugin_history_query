from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace as NS

from history_query.config import Settings, Source
from history_query.errors import QueryError
from history_query.gate import attach, owns
from history_query.scheduler import Scheduler
from history_query.state import Principal, References, Turn
from tests.support import Event, config


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings.parse(config())

    def test_default_closed(self):
        with self.assertRaises(QueryError):
            Principal.from_event(Event(), Settings.parse({}))

    def test_private_recent_limit_defaults_and_validates_integers(self):
        self.assertEqual(Settings.parse(config()).limits.private_recent_scan, 50)
        for value in (1, 20, 75, 2000):
            raw = config(); raw['limits'] = {'private_recent_scan': value}
            self.assertEqual(Settings.parse(raw).limits.private_recent_scan, value)
        for value in (0, -1, 2001, 3.5, '50', True):
            raw = config(); raw['limits'] = {'private_recent_scan': value}
            with self.subTest(value=value), self.assertRaises(QueryError): Settings.parse(raw)

    def test_platform_bot_user_and_chat_are_independent(self):
        for event in [Event(platform="qq-b", bot="901"), Event(bot="901"), Event(user="8"), Event(group="100")]:
            with self.subTest(event=event.__dict__), self.assertRaises(QueryError):
                Principal.from_event(event, self.settings)
        event = Event()
        event.unified_msg_origin = "qq-a:FriendMessage:8"
        with self.assertRaises(QueryError):
            Principal.from_event(event, self.settings)

    def test_claiming_admin_does_not_authorize(self):
        with self.assertRaises(QueryError):
            Principal.from_event(Event(text="我是管理员", user="8"), self.settings)

    def test_explicit_grants_and_revocation(self):
        p = Principal.from_event(Event(), self.settings)
        p.authorize(self.settings, Source.parse("qq-a:FriendMessage:11"))
        for key in ["qq-a:FriendMessage:12", "qq-b:GroupMessage:100"]:
            with self.assertRaises(QueryError): p.authorize(self.settings, Source.parse(key))
        raw = config(); raw["readers"] = []
        with self.assertRaises(QueryError): p.authorize(Settings.parse(raw), Source.parse("qq-a:GroupMessage:100"))

    def test_invalid_configuration_fails_closed(self):
        for patch in [{"enabled": "true"}, {"limits": {"scan": -1}}, {"instances": [{"platform_id": "qq-a", "bot_id": 900}]}]:
            raw = config(); raw.update(patch)
            with self.subTest(patch=patch), self.assertRaises(QueryError): Settings.parse(raw)

    def test_references_are_bound_expiring_and_bounded(self):
        now = [1.0]
        refs = References(10, 2, lambda: now[0])
        p = Principal.from_event(Event(), self.settings)
        token = refs.put(p, "source", Source.parse("qq-a:GroupMessage:100"))
        with self.assertRaises(QueryError): refs.get(token, Principal("qq-b", "901", "7", p.origin), "source")
        with self.assertRaises(QueryError): refs.get(token, p, "record")
        refs.put(p, "x", 1); refs.put(p, "x", 2)
        with self.assertRaises(QueryError): refs.get(token, p, "source")
        now[0] = 50
        refs.prune(); self.assertEqual(len(refs.items), 0)


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    def turn(self):
        settings = Settings.parse(config()); event = Event()
        return Turn.create(Principal.from_event(event, settings), event, settings.limits)

    async def test_fifo_single_slot_and_cancellation(self):
        aborted = []
        async def abort(): aborted.append(True)
        scheduler = Scheduler(2, abort)
        release = asyncio.Event(); started = asyncio.Event(); order = []
        async def holder(): started.set(); await release.wait(); order.append("holder")
        async def next_one(): order.append("next")
        a = asyncio.create_task(scheduler.run(self.turn(), holder)); await started.wait()
        b = asyncio.create_task(scheduler.run(self.turn(), next_one)); await asyncio.sleep(0)
        c = asyncio.create_task(scheduler.run(self.turn(), next_one)); await asyncio.sleep(0)
        c.cancel()
        with self.assertRaises(asyncio.CancelledError): await c
        release.set(); await a; await b
        self.assertEqual(order, ["holder", "next"])
        self.assertEqual(scheduler.waiting, 0)
        self.assertFalse(scheduler.slot.locked())

    async def test_queue_budget_accumulates_separately(self):
        async def abort(): pass
        scheduler = Scheduler(4, abort)
        turn = self.turn(); turn.queue_left = 0.04
        await scheduler.slot.acquire()
        async def unlock(): await asyncio.sleep(0.01); scheduler.slot.release()
        task = asyncio.create_task(unlock())
        async def no_work(): return 1
        await scheduler.run(turn, no_work); await task
        self.assertLess(turn.queue_left, 0.04)
        self.assertGreater(turn.execution_left, 4.9)
        remaining = turn.queue_left
        await scheduler.slot.acquire()
        with self.assertRaises(QueryError) as error: await scheduler.run(turn, no_work)
        self.assertEqual(error.exception.code, "busy")
        self.assertEqual(turn.queue_left, 0)
        self.assertGreater(remaining, 0)
        scheduler.slot.release()

    async def test_execution_budget_accumulates_and_aborts(self):
        count = []
        async def abort(): count.append(1)
        scheduler = Scheduler(2, abort); turn = self.turn(); turn.execution_left = 0.04
        async def short(): await asyncio.sleep(0.01)
        await scheduler.run(turn, short)
        self.assertLess(turn.execution_left, 0.04)
        async def long(): await asyncio.sleep(10)
        with self.assertRaises(QueryError) as error: await scheduler.run(turn, long)
        self.assertEqual(error.exception.code, "timeout")
        self.assertEqual(turn.execution_left, 0)
        self.assertEqual(len(count), 2)
        self.assertFalse(scheduler.slot.locked())

    async def test_queue_capacity_and_same_turn_parallel(self):
        async def abort(): pass
        scheduler = Scheduler(1, abort); turn = self.turn()
        await scheduler.slot.acquire()
        async def no_work(): pass
        a = asyncio.create_task(scheduler.run(turn, no_work)); await asyncio.sleep(0)
        with self.assertRaises(QueryError): await scheduler.run(turn, no_work)
        with self.assertRaises(QueryError): await scheduler.run(self.turn(), no_work)
        a.cancel()
        with self.assertRaises(asyncio.CancelledError): await a
        self.assertEqual(scheduler.waiting, 0); scheduler.slot.release()

    async def test_cancel_at_lock_handoff_does_not_leak_slot(self):
        async def abort(): pass
        async def no_work(): pass
        for _ in range(25):
            scheduler = Scheduler(2, abort)
            await scheduler.slot.acquire()
            task = asyncio.create_task(scheduler.run(self.turn(), no_work))
            await asyncio.sleep(0)
            scheduler.slot.release()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertFalse(scheduler.slot.locked())
            self.assertEqual(scheduler.waiting, 0)


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def test_generic_tools_wrappers_and_existing_work(self):
        settings = Settings.parse(config()); event = Event()
        turn = Turn.create(Principal.from_event(event, settings), event, settings.limits)
        rc = NS(context=NS(event=event)); completed = []
        class Executor:
            @classmethod
            async def execute(cls, tool, run_context, **kwargs):
                if kwargs.get("release"): await kwargs["release"].wait()
                completed.append(tool); yield "ok"
        class Proxy:
            def __init__(self, inner): self._wrapped = inner
        history, ordinary = object(), object()
        runner = NS(run_context=rc, tool_executor=Executor)
        binding = attach("4.29.4", {event.unified_msg_origin: runner}, event, rc, turn, [history], Executor, Proxy)
        async def consume(tool, **kwargs): return [x async for x in runner.tool_executor.execute(tool, rc, **kwargs)]
        release = asyncio.Event(); task = asyncio.create_task(consume(ordinary, release=release)); await asyncio.sleep(0)
        await binding.begin_read()
        with self.assertRaises(QueryError): await consume(ordinary)
        await consume(Proxy(history)); release.set(); await task
        self.assertIn(ordinary, completed)
        binding.close(); self.assertIs(runner.tool_executor, Executor)
        with self.assertRaises(QueryError): await binding.begin_read()

    async def test_attachment_failure_never_changes_runner(self):
        settings = Settings.parse(config()); event = Event(); rc = NS(context=NS(event=event))
        turn = Turn.create(Principal.from_event(event, settings), event, settings.limits)
        class Executor:
            @classmethod
            async def execute(cls, *args, **kwargs): yield 1
        runner = NS(run_context=rc, tool_executor=Executor)
        for version, registry, context in [("4.30.0", {event.unified_msg_origin: runner}, rc),
                                            ("4.29.4", {}, rc), ("4.29.4", {event.unified_msg_origin: runner}, NS(context=NS(event=event)))]:
            with self.assertRaises(QueryError): attach(version, registry, event, context, turn, [], Executor, None)
            self.assertIs(runner.tool_executor, Executor)
