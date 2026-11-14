"""Small synthetic benchmark for the isolated worker, not production sizing."""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import platform
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from history_query.config import Limits
from history_query.process import Worker
from tests.support import add_group, database, workspace


def process_memory(pid):
    if os.name == "nt":
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("peak_rss", "rss", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
        if not handle: return {"available": False}
        try:
            counters = Counters(); counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb): return {"available": False}
            return {"rss_bytes": counters.rss, "peak_rss_bytes": counters.peak_rss}
        finally: kernel.CloseHandle(handle)
    values = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                key, size, _ = line.split(); values[key] = int(size) * 1024
        return {"rss_bytes": values.get("VmRSS:"), "peak_rss_bytes": values.get("VmHWM:")}
    except OSError: return {"available": False}


async def run(output):
    temp = workspace(); path = Path(temp.name) / "benchmark.sqlite3"; database(path, groups=0)
    db = sqlite3.connect(path)
    try:
        for i in range(3000): add_group(db, 100, f"作业-{i}: " + "合成中文资料" * 150)
        db.commit()
    finally: db.close()
    worker = Worker(path, Limits())
    try:
        started = time.perf_counter()
        first = await worker.command({"op": "start", "source": "qq-a:GroupMessage:100", "sample": 0,
                                      "since": "2026-11-14 00:00:00", "end": "2026-11-15 00:00:00", "seconds": 5})
        cold = time.perf_counter() - started
        cursor = first["state"]; scanned = 0; started = time.perf_counter()
        calls = 0
        while scanned < 500:
            result = await worker.command({"op": "page", "source": "qq-a:GroupMessage:100", "state": cursor,
                "generation": first["generation"], "limit": 25, "return_limit": 25, "keywords": ["不存在的关键词"], "seconds": 5})
            cursor = result["state"]; scanned += result["scanned"]; calls += 1
        warm = time.perf_counter() - started
        memory = process_memory(worker.process.pid)
        report = {"measured_at_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
                  "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                  "synthetic_group_rows": 3000, "database_bytes": path.stat().st_size,
                  "cold_start_and_metadata_seconds": cold, "scan_500_seconds": warm,
                  "ipc_page_calls": calls, "worker_starts": worker.starts, "worker_memory": memory,
                  "scope": "Isolated Python+SQLite worker only; excludes AstrBot/NapCat/model/network; Windows result does not certify 2-core 2-GB Linux throughput."}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        await worker.close(); temp.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, default=Path("docs/benchmark.json"))
    asyncio.run(run(parser.parse_args().output))
