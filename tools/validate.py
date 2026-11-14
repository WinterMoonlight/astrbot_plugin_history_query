"""Run synthetic unit/integration tests and save a concise reproducible result."""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
parser = argparse.ArgumentParser()
parser.add_argument("--upstream-source", type=Path)
args = parser.parse_args()
started = time.perf_counter()
suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py", top_level_dir=str(root))
result = unittest.TextTestRunner(verbosity=2).run(suite)
report = {"time_utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
          "platform": platform.platform(), "tests": result.testsRun, "failures": len(result.failures),
          "errors": len(result.errors), "skipped": len(result.skipped), "seconds": time.perf_counter() - started,
          "passed": result.wasSuccessful(), "scope": "Synthetic records and host-contract doubles; no server/model calls."}
if args.upstream_source:
    process = subprocess.run([sys.executable, "-X", "utf8", str(root / "tests/check_upstream_contract.py"),
                              "--source", str(args.upstream_source.resolve())], capture_output=True, text=True, encoding="utf-8")
    if process.returncode:
        print(process.stderr, file=sys.stderr)
        report["upstream_contract"] = {"status": "failed"}
        report["passed"] = False
    else:
        report["upstream_contract"] = json.loads(process.stdout)
(root / "docs/validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
raise SystemExit(0 if report["passed"] else 1)
