"""Package an explicit runtime allowlist; never include Git, databases or logs."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


root = Path(__file__).resolve().parents[1]
destination = root / "dist" / "astrbot_plugin_history_query-0.1.0.zip"
destination.parent.mkdir(exist_ok=True)
files = [root / name for name in ("main.py", "metadata.yaml", "_conf_schema.json", "README.md")]
files.extend(sorted((root / "history_query").glob("*.py")))
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, Path("astrbot_plugin_history_query") / path.relative_to(root))
print(destination)
