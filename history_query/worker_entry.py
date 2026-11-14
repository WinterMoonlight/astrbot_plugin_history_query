"""Private stdio worker; has no AstrBot import and never connects to a network."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from history_query.database import Database  # noqa: E402
from history_query.errors import QueryError  # noqa: E402


def main():
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_AS, (192 * 1024 * 1024, 192 * 1024 * 1024))
    db = Database(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
    for line in sys.stdin.buffer:
        if len(line) > 65536:
            break
        try:
            request = json.loads(line)
            result = {"ok": True, "data": db.run(request)}
        except QueryError as exc:
            result = {"ok": False, "code": exc.code, "message": exc.message}
        except BaseException:
            result = {"ok": False, "code": "worker_error", "message": "查询工作进程无法处理该记录。"}
        output = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
        if len(output) > 384 * 1024:
            output = b'{"ok":false,"code":"output_limit","message":"Worker result exceeded limit"}'
        sys.stdout.buffer.write(output + b"\n")
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
