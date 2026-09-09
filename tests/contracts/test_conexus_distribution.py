from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def test_distribution_rejects_private_host_and_unreviewed_runtime_files(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/build_local_bundle.py"
    spec = importlib.util.spec_from_file_location("local_bundle", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lock = {
        "layoutVersion": 2, "revision": "test",
        "packages": ["packages/runtime-protocol", "packages/runtime-core", "packages/node-host-runtime", "apps/local-host"],
    }
    runtime = tmp_path / "build/conexus"
    runtime.mkdir(parents=True)
    pin = tmp_path / "integrations/conexus/runtime.lock.json"
    pin.parent.mkdir(parents=True)
    pin.write_text(json.dumps(lock), encoding="utf-8")
    (runtime / "UPSTREAM.json").write_text(json.dumps(lock), encoding="utf-8")
    local_host = runtime / "apps/local-host/src/index.mjs"
    local_host.parent.mkdir(parents=True)
    local_host.write_text("export {}", encoding="utf-8")
    assert "build/conexus/apps/local-host/src/index.mjs" in module.runtime_bundle_files(tmp_path)
    private = runtime / "apps/web/server-dist/auth/enterprise-credential-store.js"
    private.parent.mkdir(parents=True)
    private.write_text("export {}", encoding="utf-8")
    with pytest.raises(ValueError, match="outside the core export"):
        module.runtime_bundle_files(tmp_path)
    private.unlink()
    (runtime / ".env").write_text("LOCAL_SECRET=example", encoding="utf-8")
    with pytest.raises(ValueError, match="outside the core export"):
        module.runtime_bundle_files(tmp_path)
