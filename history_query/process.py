from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from .errors import QueryError, fail


class Worker:
    """One bounded, lazily started stdio worker. Scheduler serializes commands."""
    def __init__(self, path, limits):
        self.path, self.limits = Path(path).resolve(strict=True), limits
        self.process = None
        self.inflight = False
        self.idle_task = None
        self.closed = False
        self.starts = 0

    async def _start(self):
        if self.closed:
            fail("closed", "查询工作进程已关闭。")
        if self.process is not None and self.process.returncode is None:
            return
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        pending = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, "-I", "-u", str(Path(__file__).with_name("worker_entry.py")),
            str(self.path), str(self.limits.blob_bytes), str(self.limits.message_chars),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=512 * 1024, **kwargs))
        try:
            self.process = await asyncio.shield(pending)
        except asyncio.CancelledError:
            process = await pending
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        self.starts += 1

    async def command(self, request):
        if self.inflight:
            fail("busy", "查询工作进程正在使用中。")
        if self.idle_task:
            self.idle_task.cancel()
            self.idle_task = None
        self.inflight = True
        completed = False
        try:
            await self._start()
            process = self.process
            payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
            if len(payload) > 65535:
                fail("invalid_request", "查询参数过长。")
            process.stdin.write(payload + b"\n")
            await process.stdin.drain()
            line = await process.stdout.readline()
            if not line or len(line) > 384 * 1024:
                fail("worker_error", "查询工作进程异常退出或结果过大。")
            reply = json.loads(line)
            completed = True
            if not reply.get("ok"):
                raise QueryError(str(reply.get("code", "worker_error")), str(reply.get("message", "读取失败。")))
            return reply["data"]
        except (OSError, ValueError, KeyError) as exc:
            raise QueryError("worker_error", "查询工作进程通信失败。") from exc
        finally:
            if completed:
                self.inflight = False
                self.idle_task = asyncio.create_task(self._idle())
            # Cancelled/failed commands remain marked in-flight until abort().

    async def _idle(self):
        try:
            await asyncio.sleep(self.limits.idle_seconds)
            if not self.inflight:
                await self._stop()
        except asyncio.CancelledError:
            pass

    async def _stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()

    async def abort(self):
        if self.inflight:
            await self._stop()
            self.inflight = False

    async def close(self):
        self.closed = True
        if self.idle_task:
            task, self.idle_task = self.idle_task, None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._stop()
        self.inflight = False
