"""Run the built upstream tests with the event-loop lifetime of a real Host."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required.")
    source = ROOT / "build/conexus-source"
    setup = (ROOT / "scripts/conexus-test-setup.mjs").as_uri()
    for relative in ["packages/runtime-core/dist", "packages/node-host-runtime/dist", "apps/local-host/test"]:
        tests = sorted((source / relative).rglob("*.test.js")) + sorted((source / relative).rglob("*.test.mjs"))
        if not tests:
            raise SystemExit("Build the pinned Conexus runtime first.")
        subprocess.run([node, "--import", setup, "--test", *map(str, tests)], cwd=source, check=True, timeout=180)
    subprocess.run([node, "scripts/check-open-core-boundaries.mjs"], cwd=source, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
