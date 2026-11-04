"""The source exporter must never package private paths or accept changed bytes."""
from __future__ import annotations

import json

import pytest

from scripts.package_collaboration_handover import digest, source_allowed, verify


@pytest.mark.parametrize("name", [".env", ".env.local", "data/runtime/x", ".local-plans/plan.md", "../secret",
                                "C:/secret", "/secret", "nested\\secret", "x/secrets/model.json",
                                "docs/guides/collaboration-foundation.md", "x/model-providers.json", "build/x.py", "", ".", "Build/x.py", "runtime/user.json", ".ENV.local", "data/market/private.parquet"])
def test_private_and_escaping_paths_are_never_source(name):
    assert not source_allowed(name)


@pytest.mark.parametrize("name", ["alphalab/collaboration/control.py", "alphalab.env.example",
                                "tests/fixtures/collaboration/contracts/examples.json", "data/README.md",
                                "vendor/conexus/packages/runtime-core/src/runtime/runtime-job-registry.ts",
                                "data/manifest.json", "data/market/bars.parquet", "data/fundamentals/fundamentals.parquet",
                                "data/factors/factor_returns.parquet"])
def test_required_code_examples_and_templates_remain_exportable(name):
    assert source_allowed(name)


def test_snapshot_verification_detects_modified_bytes(tmp_path):
    target = tmp_path / "source/alphalab/module.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original")
    hashes = {"alphalab/module.py": digest(b"original")}
    manifest = {"source_files": hashes, "source_tree_sha256": digest(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode())}
    (tmp_path / "source-manifest.json").write_text(json.dumps(manifest))
    assert verify(tmp_path) == manifest
    target.write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity mismatch"):
        verify(tmp_path)


def test_exported_measurements_use_verified_snapshot_not_parent_git(tmp_path, monkeypatch):
    from scripts import measure_collaboration_baseline as baseline

    source = tmp_path / "source"
    source.mkdir()
    (source / "example.py").write_bytes(b"example")
    hashes = {"example.py": digest(b"example")}
    manifest = {"source_files": hashes,
                "source_tree_sha256": digest(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()),
                "base_commit": "a" * 40, "core_revision": "verified-core", "tracked_diff_sha256": "b" * 64}
    (tmp_path / "source-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(baseline, "ROOT", source)
    monkeypatch.setattr(baseline, "git", lambda *args: pytest.fail("Must not discover an enclosing Git repository"))
    identity = baseline.source_identity()
    assert identity["dirty"] and identity["working_commit"] == manifest["base_commit"]
    assert identity["dependency_hashes"]["example.py"] == hashes["example.py"]
    assert "../source-manifest.json" in identity["dependency_hashes"]
    (source / "example.py").write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity mismatch"):
        baseline.source_identity()
