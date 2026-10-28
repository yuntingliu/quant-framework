"""Gate a Conexus core upgrade, and keep the pinned snapshot honest in between.

``vendor/conexus`` is a checksum-verified export, not a checkout. Two failure
modes follow from that, and this script is the gate for both.

**Between upgrades**, an application change can edit the snapshot. Nothing in
the fast quality job notices today: ``build_conexus_runtime.py`` re-verifies
every digest, but it only runs in the job that builds Node. Run with no
arguments, this check is the cheap version of that verification, so a modified
vendor file fails a pull request instead of a release.

**At upgrade time**, a candidate export has to be reviewed before it is
imported. ``sync_conexus_core.py`` already refuses a dirty or out-of-boundary
export, but it does not say what *changed*, and the changes that matter most
are the ones in files AlphaLab's adapters are written against. With
``--export-root`` this prints the file-level diff grouped by package and names
the application adapters each changed interface file bears on. A change to one
of those files blocks the upgrade until it is acknowledged with
``--accept-interface-change``, which is a reviewer's decision and not a default.

``--verify`` runs the whole documented chain — rebuild from the pinned export,
upstream tests, documentation, adapter contracts, the example suites — and
stops at the first failure, so a verification that fails blocks the upgrade
rather than being stepped over.

Usage::

    python scripts/check_conexus_upgrade.py
    python scripts/check_conexus_upgrade.py --export-root /path/to/conexus-export
    python scripts/check_conexus_upgrade.py --verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "vendor/conexus"
LOCK = ROOT / "integrations/conexus/runtime.lock.json"
CONTRACTS = ROOT / "integrations/conexus/contracts/adapter-contracts.v1.json"

# Core files AlphaLab's adapters are written against, and what bears on what.
# A candidate export touching one of these is the case the issue calls out:
# a change that belongs in an application adapter rather than in the snapshot.
ADAPTER_INTERFACES: dict[str, tuple[str, tuple[str, ...]]] = {
    "apps/local-host/src/model-provider.mjs": (
        "integrations/conexus/model-providers.mjs",
        ("model.completion.request", "model.completion.response", "error.envelope"),
    ),
    "apps/local-host/src/local-runtime.mjs": (
        "integrations/conexus/web-search.mjs",
        ("capability.response", "runtime.adapter.profile"),
    ),
    "apps/local-host/src/index.mjs": (
        "integrations/conexus/local-host.mjs",
        ("authorization.failure",),
    ),
    "apps/local-host/src/state.mjs": (
        "integrations/conexus/local-host.mjs",
        ("state.snapshot",),
    ),
    "packages/runtime-core/src/contracts.ts": (
        "integrations/conexus/contracts/adapter-contracts.v1.json",
        ("model.completion.request", "runtime.adapter.profile"),
    ),
    "packages/runtime-core/src/hosted/openai-completion.ts": (
        "integrations/conexus/contracts/adapter-contracts.v1.json",
        ("model.completion.response",),
    ),
    "packages/runtime-core/src/hosted/hosted-runtime.ts": (
        "integrations/conexus/contracts/adapter-contracts.v1.json",
        ("error.envelope", "runtime.adapter.profile"),
    ),
    "packages/runtime-core/src/capabilities/node-capability-contracts.ts": (
        "integrations/conexus/alphalab-research-agent/",
        ("financial.tool.request", "financial.tool.response"),
    ),
}

# The documented upgrade chain, in order. Each entry blocks the next.
VERIFICATION: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rebuild from the pinned export", ("python", "scripts/build_conexus_runtime.py")),
    ("upstream core tests", ("python", "scripts/check_conexus_runtime.py")),
    ("framework documentation", ("python", "scripts/check_conexus_docs.py")),
    ("adapter contracts", ("python", "-m", "pytest", "tests/contracts/test_conexus_adapter_contracts.py", "-q")),
    ("adapter conformance", ("node", "--test", "tests/conexus-adapter-contracts.test.mjs")),
    ("lifecycle examples", ("node", "--test", "examples/conexus/minimal-agent/test/lifecycle.test.mjs")),
    ("financial examples", ("node", "--test", "examples/conexus/financial-integration/test/research.test.mjs")),
    ("adapter substitution", ("node", "--test",
                              "examples/conexus/adapter-contracts/test/financial.test.mjs",
                              "examples/conexus/adapter-contracts/test/substitution.test.mjs")),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_problems() -> list[str]:
    """Verify the pinned snapshot against its own export manifest and the lock."""
    problems: list[str] = []
    export_file = SNAPSHOT / "EXPORT.json"
    if not export_file.is_file():
        return [f"the pinned core export manifest is missing: {export_file.relative_to(ROOT)}"]
    export = json.loads(export_file.read_text(encoding="utf-8"))
    lock = json.loads(LOCK.read_text(encoding="utf-8"))

    if export["sourceRevision"] != lock["revision"]:
        problems.append(
            f"runtime.lock.json pins {lock['revision'][:7]} but the snapshot exports "
            f"{export['sourceRevision'][:7]}; re-import a reviewed core export."
        )
    if lock["layoutVersion"] != 2:
        problems.append(f"unexpected snapshot layout version: {lock['layoutVersion']}")

    expected = export["files"]
    for name, expected_digest in expected.items():
        path = (SNAPSHOT / name).resolve()
        if not path.is_relative_to(SNAPSHOT.resolve()) or not path.is_file():
            problems.append(f"the pinned core is missing an exported file: {name}")
        elif digest(path) != expected_digest:
            problems.append(
                f"a pinned core file was modified in place: {name}. "
                "Core changes must arrive as a reviewed export, never as an edit."
            )

    tracked = {
        str(path.relative_to(SNAPSHOT)).replace("\\", "/")
        for path in SNAPSHOT.rglob("*")
        if path.is_file()
    } - {"EXPORT.json"}
    for name in sorted(tracked - set(expected)):
        problems.append(f"an unexported file was added to the pinned core: {name}")

    contracts = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    if contracts["verifiedCoreRevision"] != lock["revision"]:
        problems.append(
            f"the adapter contracts were verified against "
            f"{contracts['verifiedCoreRevision'][:7]} but the lock pins {lock['revision'][:7]}; "
            "re-run this check with --verify and update verifiedCoreRevision."
        )
    return problems


def review_export(export_root: Path) -> tuple[list[str], list[str]]:
    """Return (diff report lines, interface changes) for a candidate export."""
    candidate = json.loads((export_root / "EXPORT.json").read_text(encoding="utf-8"))
    current = json.loads((SNAPSHOT / "EXPORT.json").read_text(encoding="utf-8"))
    before, after = current["files"], candidate["files"]

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(name for name in set(after) & set(before) if after[name] != before[name])

    report = [
        f"candidate {candidate['sourceRevision'][:7]} against pinned {current['sourceRevision'][:7]}",
        f"  {len(added)} added, {len(removed)} removed, {len(changed)} changed",
    ]
    for label, names in (("added", added), ("removed", removed), ("changed", changed)):
        for name in names:
            report.append(f"  {label:<8} {name}")

    interface = [name for name in sorted({*added, *removed, *changed}) if name in ADAPTER_INTERFACES]
    return report, interface


def run_verification() -> int:
    python = sys.executable
    node = shutil.which("node")
    if not node:
        print("Node.js is required to verify a core upgrade.", file=sys.stderr)
        return 1
    for label, command in VERIFICATION:
        resolved = [python if part == "python" else node if part == "node" else part for part in command]
        print(f"==> {label}: {' '.join(command)}", flush=True)
        completed = subprocess.run(resolved, cwd=ROOT, check=False)
        if completed.returncode != 0:
            print(
                f"\nVerification failed at: {label}. The upgrade is blocked.\n"
                "Fix the failure, or revert to the previously pinned export.",
                file=sys.stderr,
            )
            return completed.returncode
    print("\nCore upgrade verification passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-root", type=Path, help="Review a candidate core export before importing it.")
    parser.add_argument("--accept-interface-change", action="store_true",
                        help="Acknowledge that the candidate changes a file AlphaLab's adapters depend on.")
    parser.add_argument("--verify", action="store_true", help="Run the full documented verification chain.")
    arguments = parser.parse_args()

    problems = snapshot_problems()
    if problems:
        sys.stdout.flush()
        print("\n".join(problems), file=sys.stderr)
        return 1

    if arguments.export_root:
        export_root = arguments.export_root.resolve()
        report, interface = review_export(export_root)
        print("\n".join(report), flush=True)
        if interface:
            print("\nChanged core interfaces AlphaLab adapts:")
            for name in interface:
                owner, contracts = ADAPTER_INTERFACES[name]
                print(f"  {name}\n      review: {owner}\n      contracts: {', '.join(contracts)}", flush=True)
            if not arguments.accept_interface_change:
                print(
                    "\nThis candidate changes core interfaces the application adapts. Review the "
                    "files above, update the adapter contracts if the shape moved, then re-run with "
                    "--accept-interface-change.",
                    file=sys.stderr,
                )
                return 1
            print("\nInterface change acknowledged.")
        else:
            print("\nNo adapted core interface changed in this candidate.")

    if arguments.verify:
        return run_verification()

    print(f"Pinned Conexus core verified: {json.loads(LOCK.read_text(encoding='utf-8'))['revision'][:7]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
