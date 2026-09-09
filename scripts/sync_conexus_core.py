"""Import only a verified, clean Conexus core export; never copy the private checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNERS = {"packages/runtime-protocol", "packages/runtime-core", "packages/node-host-runtime", "apps/local-host"}
ROOT_FILES = {"package.json", "package-lock.json", "README.md", "open-core.manifest.json",
              "scripts/check-open-core-boundaries.mjs", "docs/open-core/README.md", "docs/open-core/ARCHITECTURE.md"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-root", type=Path, required=True)
    source = parser.parse_args().export_root.resolve()
    metadata = json.loads((source / "EXPORT.json").read_text(encoding="utf-8"))
    manifest = json.loads((source / "open-core.manifest.json").read_text(encoding="utf-8"))
    if metadata.get("schema") != "conexus.core-export.v1" or metadata.get("sourceDirty") is not False:
        raise SystemExit("Use an export from a clean Conexus core commit.")
    if set(manifest["packages"]) != OWNERS:
        raise SystemExit("The exported package boundary differs from the reviewed core boundary.")
    files = metadata["files"]
    for name, expected in files.items():
        allowed = name in ROOT_FILES or any(
            name in {f"{owner}/package.json", f"{owner}/tsconfig.json"}
            or name.startswith((f"{owner}/src/", f"{owner}/test/")) for owner in OWNERS
        )
        if not allowed:
            raise SystemExit(f"Export file is outside the reviewed source boundary: {name}")
        path = (source / name).resolve()
        if not path.is_relative_to(source) or not path.is_file():
            raise SystemExit(f"Invalid export file: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SystemExit(f"Export file changed: {name}")
    destination = ROOT / "vendor/conexus"
    destination.mkdir(parents=True, exist_ok=True)
    old = destination / "EXPORT.json"
    if old.is_file():
        for name in json.loads(old.read_text(encoding="utf-8"))["files"].keys() - files.keys():
            target = (destination / name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise SystemExit("Old export path escaped the core directory.")
            target.unlink(missing_ok=True)
    for name in [*files, "EXPORT.json"]:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    lock = {
        "repository": "Conexus Core", "revision": metadata["sourceRevision"],
        "packages": sorted(OWNERS), "host": "apps/local-host/src",
        "layoutVersion": 2, "licenseStatus": metadata["licenseStatus"],
    }
    (ROOT / "integrations/conexus/runtime.lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {len(files)} core source files from {metadata['sourceRevision'][:7]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
