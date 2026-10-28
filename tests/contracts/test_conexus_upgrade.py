"""The core-upgrade gate must block, not warn.

Issue #11 asks for two guarantees that only hold if something enforces them:
an application change must not silently modify the vendor snapshot, and a
failed verification must block an upgrade. Both are decisions this gate makes,
so each is exercised here against a synthetic snapshot rather than the real
one, which lets a test produce the failures the real tree must never have.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_conexus_upgrade.py"

REVISION = "a" * 40


def load_gate(tmp_path: Path):
    """Load the gate with its paths rebound to a synthetic repository."""
    spec = importlib.util.spec_from_file_location(f"conexus_upgrade_{tmp_path.name}", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    module.SNAPSHOT = tmp_path / "vendor/conexus"
    module.LOCK = tmp_path / "integrations/conexus/runtime.lock.json"
    module.CONTRACTS = tmp_path / "integrations/conexus/contracts/adapter-contracts.v1.json"
    return module


def write_core_file(path: Path, content: str) -> str:
    """Write a snapshot file as bytes and return its digest.

    Bytes, not text: ``write_text`` translates ``\n`` to ``\r\n`` on Windows,
    so a fixture that hashed the string and wrote the text would record a digest
    the file on disk does not have, and every synthetic file would look edited.
    The real snapshot is immune because .gitattributes pins ``vendor/conexus``
    to LF on every platform; this fixture has to pin itself.
    """
    payload = content.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == hashlib.sha256(payload).hexdigest(), f"{path} did not round-trip its bytes"
    return digest


def write_snapshot(tmp_path: Path, files: dict[str, str], revision: str = REVISION) -> None:
    snapshot = tmp_path / "vendor/conexus"
    digests = {}
    for name, content in files.items():
        digests[name] = write_core_file(snapshot / name, content)
    (snapshot / "EXPORT.json").write_text(
        json.dumps({"schema": "conexus.core-export.v1", "sourceRevision": revision,
                    "sourceDirty": False, "licenseStatus": "pending-owner-selection", "files": digests}),
        encoding="utf-8",
    )
    lock = tmp_path / "integrations/conexus/runtime.lock.json"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"revision": revision, "layoutVersion": 2,
                                "packages": ["apps/local-host"]}), encoding="utf-8")
    contracts = tmp_path / "integrations/conexus/contracts/adapter-contracts.v1.json"
    contracts.parent.mkdir(parents=True, exist_ok=True)
    contracts.write_text(json.dumps({"verifiedCoreRevision": revision}), encoding="utf-8")


BASELINE = {
    "apps/local-host/src/model-provider.mjs": "export function createModelCompletion() {}\n",
    "apps/local-host/src/index.mjs": "export function createLocalHost() {}\n",
    "packages/runtime-core/src/release/release.ts": "export const release = 1\n",
}


def test_the_gate_passes_on_this_repository() -> None:
    module = load_gate(ROOT)
    assert module.snapshot_problems() == []


def test_an_edited_vendor_file_is_reported(tmp_path: Path) -> None:
    write_snapshot(tmp_path, BASELINE)
    module = load_gate(tmp_path)
    assert module.snapshot_problems() == []

    edited = tmp_path / "vendor/conexus/apps/local-host/src/index.mjs"
    write_core_file(edited, "export function createLocalHost() { /* local tweak */ }\n")
    problems = module.snapshot_problems()
    assert any("modified in place" in problem for problem in problems), problems


def test_an_added_vendor_file_is_reported(tmp_path: Path) -> None:
    write_snapshot(tmp_path, BASELINE)
    module = load_gate(tmp_path)
    write_core_file(tmp_path / "vendor/conexus/apps/local-host/src/patch.mjs", "export {}\n")
    problems = module.snapshot_problems()
    assert any("unexported file was added" in problem for problem in problems), problems


def test_a_missing_vendor_file_is_reported(tmp_path: Path) -> None:
    write_snapshot(tmp_path, BASELINE)
    module = load_gate(tmp_path)
    (tmp_path / "vendor/conexus/packages/runtime-core/src/release/release.ts").unlink()
    problems = module.snapshot_problems()
    assert any("missing an exported file" in problem for problem in problems), problems


def test_contracts_verified_against_another_revision_are_reported(tmp_path: Path) -> None:
    write_snapshot(tmp_path, BASELINE)
    module = load_gate(tmp_path)
    module.CONTRACTS.write_text(json.dumps({"verifiedCoreRevision": "b" * 40}), encoding="utf-8")
    problems = module.snapshot_problems()
    assert any("verified against" in problem for problem in problems), problems


def test_a_lock_and_export_disagreement_is_reported(tmp_path: Path) -> None:
    write_snapshot(tmp_path, BASELINE)
    module = load_gate(tmp_path)
    module.LOCK.write_text(json.dumps({"revision": "c" * 40, "layoutVersion": 2,
                                       "packages": ["apps/local-host"]}), encoding="utf-8")
    problems = module.snapshot_problems()
    assert any("re-import a reviewed core export" in problem for problem in problems), problems


def candidate_export(tmp_path: Path, files: dict[str, str], revision: str) -> Path:
    root = tmp_path / f"export-{revision[:7]}"
    write_snapshot(root.parent / "candidate-tree", files, revision)
    # write_snapshot lays out a repository; the export is just its vendor tree.
    staged = root.parent / "candidate-tree/vendor/conexus"
    staged.rename(root)
    return root


def test_a_candidate_touching_no_adapted_interface_is_reported_as_such(tmp_path: Path) -> None:
    write_snapshot(tmp_path, BASELINE)
    module = load_gate(tmp_path)
    updated = {**BASELINE, "packages/runtime-core/src/release/release.ts": "export const release = 2\n"}
    report, interface = module.review_export(candidate_export(tmp_path, updated, "d" * 40))
    assert interface == []
    assert any("1 changed" in line for line in report), report


def test_a_candidate_changing_an_adapted_interface_blocks(tmp_path: Path) -> None:
    write_snapshot(tmp_path, BASELINE)
    module = load_gate(tmp_path)
    updated = {
        **BASELINE,
        "apps/local-host/src/model-provider.mjs": "export function createModelCompletion(options) {}\n",
        "apps/local-host/src/state.mjs": "export function openState() {}\n",
    }
    report, interface = module.review_export(candidate_export(tmp_path, updated, "e" * 40))
    assert interface == ["apps/local-host/src/model-provider.mjs", "apps/local-host/src/state.mjs"]
    assert any("added    apps/local-host/src/state.mjs" in line for line in report), report
    # Each flagged file names the application file to review and the contracts
    # whose shape may have moved, which is the "belongs in an adapter" answer.
    for name in interface:
        owner, contracts = module.ADAPTER_INTERFACES[name]
        assert owner.startswith("integrations/conexus/")
        assert contracts


def test_every_adapted_interface_exists_in_the_pinned_snapshot() -> None:
    """A rename upstream must not quietly empty the interface watch list."""
    module = load_gate(ROOT)
    missing = [name for name in module.ADAPTER_INTERFACES if not (module.SNAPSHOT / name).is_file()]
    assert missing == [], f"the gate watches core files that no longer exist: {missing}"


def test_every_named_application_owner_exists() -> None:
    module = load_gate(ROOT)
    for name, (owner, contracts) in module.ADAPTER_INTERFACES.items():
        assert (ROOT / owner).exists(), f"{name} names a missing application owner: {owner}"
        document = json.loads(module.CONTRACTS.read_text(encoding="utf-8"))
        for contract_id in contracts:
            assert contract_id in document["contracts"], f"{name} names an unknown contract: {contract_id}"


def test_the_verification_chain_targets_exist() -> None:
    """A documented verification step that runs nothing is not a gate."""
    module = load_gate(ROOT)
    assert module.VERIFICATION
    for label, command in module.VERIFICATION:
        assert label
        for part in command:
            if part in {"python", "node", "-m", "--test", "-q", "pytest"}:
                continue
            assert (ROOT / part).exists(), f"{label}: missing verification target {part}"


def test_the_gate_requires_an_export_manifest(tmp_path: Path) -> None:
    """A snapshot with no manifest cannot be verified, so it is not accepted."""
    (tmp_path / "vendor/conexus").mkdir(parents=True)
    module = load_gate(tmp_path)
    assert any("export manifest is missing" in problem for problem in module.snapshot_problems())
