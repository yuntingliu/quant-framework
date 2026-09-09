"""Build the included Conexus core snapshot for local deployment."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    source = ROOT / "integrations/conexus/core"
    metadata = json.loads((source / "EXPORT.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "integrations/conexus/runtime.lock.json").read_text(encoding="utf-8"))
    if metadata["sourceRevision"] != lock["revision"] or lock["layoutVersion"] != 2:
        raise SystemExit("Core source and runtime revision differ. Re-import a reviewed core export.")
    for name, digest in metadata["files"].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"Core source differs from its export: {name}")
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        raise SystemExit("Node.js 22.18+ and npm are required.")
    subprocess.run([node, "scripts/check-open-core-boundaries.mjs"], cwd=source, check=True)
    subprocess.run([npm, "ci", "--no-fund"], cwd=source, check=True)
    subprocess.run([npm, "run", "build"], cwd=source, check=True)
    target = (ROOT / "runtime/conexus").resolve()
    # This directory is generated exclusively by this builder. Validate before replacing it,
    # including removal of the old private-host prototype from earlier local experiments.
    if not target.is_relative_to((ROOT / "runtime").resolve()):
        raise SystemExit("Runtime output escaped its generated directory.")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy2(source / name, target / name)
    for owner in lock["packages"]:
        destination = target / owner
        destination.mkdir(parents=True)
        shutil.copy2(source / owner / "package.json", destination / "package.json")
        output = "src" if owner == "apps/local-host" else "dist"
        shutil.copytree(source / owner / output, destination / output,
                        ignore=shutil.ignore_patterns("*.test.*", "*.map", "*.d.ts"))
    (target / "UPSTREAM.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    subprocess.run([npm, "ci", "--omit=dev", "--no-fund"], cwd=target, check=True)
    print(f"Conexus core {lock['revision'][:7]} prepared at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
