"""Package an explicit runtime allowlist; never include Git, databases or logs."""
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile


root = Path(__file__).resolve().parents[1]
version = re.search(r'^__version__ = "([^"]+)"', (root / "history_query/__init__.py").read_text(encoding="utf-8"), re.M).group(1)
destination = root / "dist" / f"astrbot_plugin_history_query-{version}.zip"
destination.parent.mkdir(exist_ok=True)
files = [root / name for name in ("main.py", "metadata.yaml", "_conf_schema.json", "README.md")]
files.extend(sorted((root / "history_query").glob("*.py")))
files.extend(root / "views/settings" / name for name in ("index.html", "style.css", "app.js", "model.js"))
files.extend(root / ".astrbot-plugin/i18n" / name for name in ("zh-CN.json", "en-US.json"))
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, Path("astrbot_plugin_history_query") / path.relative_to(root))
print(destination)
