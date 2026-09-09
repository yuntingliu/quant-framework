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
SOURCE_FILES = {"AGENTS.md", "README.md", "THIRD_PARTY.md", "pyproject.toml", ".env.example", ".gitignore", ".gitattributes", ".node-version"}
SAMPLE_FILES = {
    "data/README.md",
    "data/manifest.json",
    "data/market/bars.parquet",
    "data/fundamentals/fundamentals.parquet",
    "data/factors/factor_returns.parquet",
}


def runtime_bundle_files(root: Path) -> set[str]:
    runtime = root / "runtime/conexus"
    upstream = json.loads((runtime / "UPSTREAM.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "integrations/conexus/runtime.lock.json").read_text(encoding="utf-8"))
    owners = {"packages/runtime-protocol", "packages/runtime-core", "packages/node-host-runtime", "apps/local-host"}
    if upstream != lock or lock.get("layoutVersion") != 2 or set(lock["packages"]) != owners:
        raise ValueError("Runtime does not match the reviewed Conexus core boundary and revision.")
    files = set()
    for path in runtime.rglob("*"):
        if not path.is_file() or "node_modules" in path.parts:
            continue
        name = path.relative_to(runtime).as_posix()
        allowed = name in {"package.json", "package-lock.json", "UPSTREAM.json"} or any(
            name == f"{owner}/package.json" or (
                name.startswith(f"{owner}/{'src' if owner == 'apps/local-host' else 'dist'}/")
                and path.suffix in {".js", ".mjs"}
            ) for owner in owners
        )
        if not allowed or path.is_symlink() or not path.resolve().is_relative_to(runtime.resolve()):
            raise ValueError(f"Runtime contains a file outside the core export: {name}")
        files.add(path.relative_to(root).as_posix())
    return files


def main() -> int:
    frontend = ROOT / "dashboard/frontend/dist"
    if not (frontend / "index.html").is_file():
        raise SystemExit("Run npm --prefix dashboard/frontend run build first.")
    runtime = ROOT / "runtime/conexus"
    if not (runtime / "UPSTREAM.json").is_file():
        raise SystemExit("Build the pinned Conexus runtime before creating the deployment package.")
    candidates = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    ).decode("utf-8").split("\0")
    files = {
        name for name in candidates if name and (
            name.split("/")[0] in SOURCE_DIRS or name in SOURCE_FILES or name in SAMPLE_FILES
        )
    }
    files.update(path.relative_to(ROOT).as_posix() for path in frontend.rglob("*") if path.is_file())
    files.update(runtime_bundle_files(ROOT))
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
        "conexus": json.loads((runtime / "UPSTREAM.json").read_text(encoding="utf-8")),
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
