"""Export a reviewable source snapshot; ignored guides/evidence are shared separately.

No Git mutation, provider access, runtime-store copy or publication. Snapshot hashes
identify uncommitted work explicitly. Generated build artifacts never enter the ZIP.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {".git", ".local-plans", ".codex", ".agents", ".aws", "node_modules", "__pycache__",
                   "build", "artifacts", "secrets", "backups", ".venv"}
FORBIDDEN_NAMES = {".env", "model-providers.json", "search-providers.json", "model-profile.pending.json",
                   "alphalab-connection.json", "admin-token", "historical-reports.json", "report-migration.json"}
SHIPPED_DATA = {"data/manifest.json", "data/market/bars.parquet", "data/fundamentals/fundamentals.parquet",
                "data/factors/factor_returns.parquet"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def source_allowed(name):
    path = PurePosixPath(name)
    parts = tuple(part.casefold() for part in path.parts)
    return (bool(parts) and not path.is_absolute() and ":" not in name and "\\" not in name and ".." not in parts
            and not FORBIDDEN_PARTS.intersection(parts) and path.name.casefold() not in FORBIDDEN_NAMES
            and not path.name.endswith((".pyc", ".pyo"))
            and not (path.name.casefold().startswith(".env.") and not path.name.casefold().endswith(".example"))
            and parts[0] != "runtime"
            and not (parts[0] == "data" and name.casefold() not in SHIPPED_DATA | {"data/readme.md"})
            and not (name.casefold().startswith("docs/guides/collaboration-") and path.suffix.casefold() == ".md"))


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], stderr=subprocess.PIPE)


def snapshot(output):
    if Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve() != ROOT.resolve():
        raise ValueError("Run the exporter from an actual source checkout")
    if output.exists():
        raise ValueError("Choose a fresh output directory; existing artifacts are never replaced")
    names = sorted(set(git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().strip("\0").split("\0")))
    selected = [name for name in names if name and source_allowed(name)]
    # These are repository-bundled development fixtures, not the user's acquired
    # market data. Export them only when tracked and unchanged from the base.
    git("ls-files", "--error-unmatch", "--", *sorted(SHIPPED_DATA))
    if git("diff", "--name-only", "HEAD", "--", *sorted(SHIPPED_DATA)).strip():
        raise ValueError("Bundled data differs from the repository baseline; do not export local replacements")
    exports = json.loads((ROOT / "vendor/conexus/EXPORT.json").read_text(encoding="utf-8"))["files"]
    if any("vendor/conexus/" + name not in selected for name in exports):
        raise ValueError("Exporter omitted a required verified core source file")
    # Source files must be regular files under this checkout, never links into a private sibling.
    for name in selected:
        path = ROOT / name
        if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
            raise ValueError("Source contains a missing or redirected file")
    hashes = {}
    for name in selected:
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = (ROOT / name).read_bytes()
        target.write_bytes(raw)
        hashes[name] = digest(raw)
    lockfiles = ["pyproject.toml", "apps/desktop/package-lock.json", "vendor/conexus/package-lock.json",
                 "vendor/conexus/EXPORT.json", "integrations/conexus/runtime.lock.json"]
    manifest = {"format_version": 1, "base_commit": git("rev-parse", "HEAD").decode().strip(),
                "branch": git("branch", "--show-current").decode().strip(),
                "source_state": "Explicit uncommitted source snapshot; not an implementation commit",
                "tracked_diff_sha256": digest(git("diff", "--binary", "HEAD")),
                "source_tree_sha256": digest(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()),
                "source_files": hashes, "lockfiles": {name: hashes[name] for name in lockfiles},
                "excluded": "Ignored files, private guides/plans, credentials, runtime stores and generated artifacts",
                "python_dependencies": "Project has ranges, not a complete committed Python lock. Retain pip freeze for validation.",
                "core_revision": json.loads((ROOT / "integrations/conexus/runtime.lock.json").read_text())["revision"]}
    (output / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"status": "exported", "source_files": len(hashes), "source_tree_sha256": manifest["source_tree_sha256"]}


def verify(output):
    manifest = json.loads((output / "source-manifest.json").read_text(encoding="utf-8"))
    hashes = manifest["source_files"]
    if any(not source_allowed(name) for name in hashes):
        raise ValueError("Manifest contains a forbidden source path")
    for name, expected in hashes.items():
        path = output / "source" / name
        if path.resolve() != path.absolute() or path.is_symlink() or digest(path.read_bytes()) != expected:
            raise ValueError("Snapshot source integrity mismatch")
    actual = digest(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode())
    if actual != manifest["source_tree_sha256"]:
        raise ValueError("Manifest tree integrity mismatch")
    return manifest


def seal(output):
    manifest = verify(output)
    # Deliberate, small allowlist: raw logs, DBs, provider responses and environments never enter the archive.
    extras = ["source-manifest.json", "HANDOVER.md", "verification.json", "validation-requirements.txt"]
    for name in extras:
        path = output / name
        if not path.is_file() or path.resolve() != path.absolute():
            raise ValueError("Handover packet is incomplete or redirected")
    verification = json.loads((output / "verification.json").read_text(encoding="utf-8"))
    if verification.get("status") != "passed" or verification.get("source_tree_sha256") != manifest["source_tree_sha256"]:
        raise ValueError("Passing verification must identify this exact source snapshot")
    archive = output / "member1-handover.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(manifest["source_files"]):
            bundle.write(output / "source" / name, "source/" + name)
        for name in extras:
            bundle.write(output / name, name)
    return {"status": "sealed", "archive_sha256": digest(archive.read_bytes()), "source_files": len(manifest["source_files"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["snapshot", "verify", "seal"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.absolute()
    if output.resolve() != output or not output.is_relative_to((ROOT / "build").resolve()) or output == ROOT / "build":
        parser.error("Output must stay within this checkout's ignored build directory without redirects")
    result = snapshot(output) if args.operation == "snapshot" else seal(output) if args.operation == "seal" else {
        "status": "verified", "source_tree_sha256": verify(output)["source_tree_sha256"]}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
