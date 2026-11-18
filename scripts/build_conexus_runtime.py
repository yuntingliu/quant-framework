"""Build the checksum-verified Conexus core plus the reviewed Member 2 extension."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _inside(path: Path, parent: Path) -> bool:
    resolved_parent = parent.resolve()
    resolved_path = path.resolve()
    return resolved_path != resolved_parent and resolved_path.is_relative_to(resolved_parent)


def main() -> int:
    source = ROOT / "vendor/conexus"
    export_path = source / "EXPORT.json"
    export_bytes = export_path.read_bytes()
    patch_manifest_path = ROOT / "integrations/conexus/patches/manifest.json"
    patch_manifest = json.loads(patch_manifest_path.read_text(encoding="utf-8"))
    if hashlib.sha256(export_bytes).hexdigest() != patch_manifest.get("upstream_export_sha256"):
        raise SystemExit("The upstream Conexus export manifest hash does not match the Member 2 patch set.")
    metadata = json.loads(export_bytes.decode("utf-8"))
    lock = json.loads((ROOT / "integrations/conexus/runtime.lock.json").read_text(encoding="utf-8"))

    if metadata.get("schema") != "conexus.core-export.v1" or metadata.get("sourceDirty") is not False:
        raise SystemExit("The Conexus source is not a clean verified export.")
    if metadata["sourceRevision"] != lock["revision"] or lock["layoutVersion"] != 2:
        raise SystemExit("Core source and runtime revision differ. Re-import a reviewed core export.")
    if patch_manifest.get("schema") != "alphalab.conexus.patch-set.v1":
        raise SystemExit("Unrecognized Member 2 patch manifest.")
    if patch_manifest.get("upstream_revision") != metadata["sourceRevision"]:
        raise SystemExit("Member 2 patch targets a different upstream revision.")

    npm = shutil.which("npm")
    node = shutil.which("node")
    git = shutil.which("git")
    if not npm or not node or not git:
        raise SystemExit("Node.js 22.18+, npm and Git are required.")

    build_root = (ROOT / "build").resolve()
    member_root = build_root / "member-2"
    workspace = member_root / "conexus-source"
    target = member_root / "conexus-runtime"
    if not _inside(member_root, build_root) or not _inside(workspace, member_root) or not _inside(target, member_root):
        raise SystemExit("Conexus output escaped build/member-2.")
    member_root.mkdir(parents=True, exist_ok=True)

    # Verify and copy the original export. The vendored tree remains read-only.
    if workspace.exists():
        shutil.rmtree(workspace)
    for name, expected in metadata["files"].items():
        path = (source / name).resolve()
        if not _inside(path, source) or not path.is_file():
            raise SystemExit(f"Invalid upstream export path: {name}")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise SystemExit(f"Core source differs from its export: {name}")
        destination = workspace / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    shutil.copy2(source / "EXPORT.json", workspace / "EXPORT.json")

    # Apply only hash-pinned local patches to the disposable source workspace.
    patches = patch_manifest.get("patches")
    if not isinstance(patches, list) or not patches:
        raise SystemExit("The Member 2 patch manifest contains no patches.")
    for item in patches:
        relative = item.get("path")
        expected = item.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise SystemExit("Invalid patch manifest entry.")
        patch = (ROOT / relative).resolve()
        if not _inside(patch, ROOT) or patch.is_symlink() or not patch.is_file():
            raise SystemExit("Invalid or missing Member 2 patch.")
        if hashlib.sha256(patch.read_bytes()).hexdigest() != expected:
            raise SystemExit(f"Member 2 patch integrity mismatch: {relative}")

        # The workspace lives inside the parent Git repository.
        # Explicitly prefix patch targets with the generated workspace path.
        workspace_prefix = workspace.relative_to(ROOT).as_posix() + "/"

        subprocess.run(
            [
                git, "apply",
                "--check",
                "--verbose",
                "-p3",
                f"--directory={workspace_prefix}",
                str(patch),
            ],
            cwd=ROOT,
            check=True,
        )

        subprocess.run(
            [
                git, "apply",
                "--verbose",
                "-p3",
                f"--directory={workspace_prefix}",
                str(patch),
            ],
            cwd=ROOT,
            check=True,
        )

        # A successful git apply exit code is insufficient: Git can skip every
        # patch when invoked from a nested repository directory.
        host_source = workspace / "apps/local-host/src/index.mjs"
        host_tests = workspace / "apps/local-host/test/local-host.test.mjs"

        if (
            "/api/alphalab/publications" not in host_source.read_text(encoding="utf-8")
            or "trusted publication atomically commits" not in host_tests.read_text(encoding="utf-8")
        ):
            raise SystemExit(
                "Member 2 publication patch was not applied to the generated Conexus source."
            )

    subprocess.run([node, "scripts/check-open-core-boundaries.mjs"], cwd=workspace, check=True)
    subprocess.run([npm, "ci", "--no-fund"], cwd=workspace, check=True)
    subprocess.run([npm, "run", "build"], cwd=workspace, check=True)

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy2(workspace / name, target / name)
    for owner in lock["packages"]:
        destination = target / owner
        destination.mkdir(parents=True)
        shutil.copy2(workspace / owner / "package.json", destination / "package.json")
        output = "src" if owner == "apps/local-host" else "dist"
        shutil.copytree(
            workspace / owner / output,
            destination / output,
            ignore=shutil.ignore_patterns("*.test.*", "*.map", "*.d.ts"),
        )
    (target / "UPSTREAM.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    subprocess.run([npm, "ci", "--omit=dev", "--no-fund"], cwd=target, check=True)
    print(f"Conexus core {lock['revision'][:7]} plus Member 2 extension prepared at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
