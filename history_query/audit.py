from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path


class Audit:
    def __init__(self, directory: Path, enabled: bool):
        self.directory, self.enabled = directory, enabled
        self.lock = asyncio.Lock()

    async def write(self, principal, action, status, count=0, sources=()):
        if not self.enabled:
            return
        record = {"time": datetime.now(timezone.utc).isoformat(), "platform": principal.platform,
                  "user": principal.user, "action": action, "status": status, "returned": count,
                  "sources": list(sources)[:5]}
        try:
            async with self.lock:
                await asyncio.to_thread(self._append, record)
        except OSError:
            pass  # Audit failure must not log query/body data as a fallback.

    def _append(self, record):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "query-audit.jsonl"
        if path.exists() and path.stat().st_size >= 1024 * 1024:
            path.replace(self.directory / "query-audit.previous.jsonl")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
