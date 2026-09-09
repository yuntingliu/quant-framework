"""Bundle reviewed source and the built browser UI without local user state."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = {"alphalab", "dashboard", "docs", "examples", "integrations", "scripts", "tests"}
SOURCE_FILES = {"AGENTS.md", "README.md", "pyproject.toml", ".env.example", ".gitignore", ".node-version"}
SAMPLE_FILES = {
    "data/README.md",
    "data/manifest.json",
    "data/market/bars.parquet",
    "data/fundamentals/fundamentals.parquet",
    "data/factors/factor_returns.parquet",
}


def main() -> int:
    frontend = ROOT / "dashboard/frontend/dist"
    if not (frontend / "index.html").is_file():
        raise SystemExit("Run npm --prefix dashboard/frontend run build first.")
    candidates = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    ).decode("utf-8").split("\0")
    files = {
        name for name in candidates if name and (
            name.split("/")[0] in SOURCE_DIRS or name in SOURCE_FILES or name in SAMPLE_FILES
        )
    }
    files.update(path.relative_to(ROOT).as_posix() for path in frontend.rglob("*") if path.is_file())
    hashes = {}
    for name in sorted(files):
        path = ROOT / name
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise SystemExit(f"Bundle input is missing or outside the checkout: {name}")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    build = {
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "working tree",
        "files": hashes,
    }
    output = ROOT / "artifacts/AlphaLab-local-deployment.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(files):
            archive.write(ROOT / name, f"AlphaLab/{name}")
        archive.writestr("AlphaLab/LOCAL_BUILD.json", json.dumps(build, indent=2) + "\n")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    print(f"{output} ({output.stat().st_size:,} bytes; {len(files)} files)")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
