"""Keep the Conexus framework documentation and its examples honest.

The documentation describes a pinned snapshot, so it goes stale silently when
the snapshot moves. These tests make that loud in the ordinary Python job, with
no Node build required.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check_conexus_docs.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_conexus_docs", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_documentation_check_passes():
    result = subprocess.run(
        [sys.executable, str(CHECKER)], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_documentation_check_detects_a_broken_link(tmp_path, monkeypatch):
    """A checker that cannot fail is decoration. Prove it fails."""
    module = load_checker()
    doc = tmp_path / "broken.md"
    doc.write_text("See [the core](../does/not/exist.ts).\n", encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "DOCS", ["broken.md"])
    failures: list[str] = []
    module.check_links(failures)
    assert failures and "broken link" in failures[0]


def test_documentation_check_detects_a_renamed_core_symbol(monkeypatch):
    module = load_checker()
    target = "vendor/conexus/packages/runtime-core/src/agent/agent-loop.ts"
    monkeypatch.setitem(module.ATTRIBUTED_SYMBOLS, target, ["definitelyNotAnExportedSymbol"])
    failures: list[str] = []
    module.check_attributed_symbols(failures)
    assert any("documented symbol not found" in failure for failure in failures)


def test_two_agent_example_stays_reserved():
    """Issue #6 reserves the directory without introducing a second Agent loop."""
    reserved = ROOT / "examples/conexus/two-agent"
    assert {path.name for path in reserved.iterdir()} == {"README.md", "INTERFACE.md"}


@pytest.mark.parametrize(
    "document",
    [
        "docs/10_CONEXUS_FRAMEWORK.md",
        "docs/11_CONEXUS_EXECUTION_LIFECYCLE.md",
        "docs/12_CONEXUS_EXTENSION_GUIDE.md",
    ],
)
def test_framework_documents_are_indexed(document):
    index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    assert Path(document).name in index, f"{document} is not linked from docs/README.md"


def test_examples_do_not_import_the_verified_vendor_snapshot():
    """vendor/conexus carries no installed dependencies and must stay read-only.

    Prose mentioning the vendor tree is fine and in fact expected; only a real
    path reference in executable code is a problem.
    """
    offenders = []
    for path in (ROOT / "examples/conexus").rglob("*.mjs"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("//", 1)[0]
            if "vendor/conexus" in code:
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, f"examples must import build/conexus, not vendor/conexus: {offenders}"


def test_lifecycle_suite_covers_every_required_situation():
    """The six situations issue #6 requires reproducible checks for."""
    suite = (ROOT / "examples/conexus/minimal-agent/test/lifecycle.test.mjs").read_text(encoding="utf-8")
    financial = (ROOT / "examples/conexus/financial-integration/test/research.test.mjs").read_text(encoding="utf-8")
    required = {
        "successful execution": "successful execution invokes a Tool node",
        "tool failure": "a Tool body failure reaches the Agent",
        "user input": "user input suspends the run",
        "cancellation": "cancellation ends a waiting run",
        "browser reconnection": "resumes the event stream with Last-Event-ID",
        "host restart": "Host restart cancels interrupted runs",
        "serial execution": "serializes top-level runs",
    }
    missing = [name for name, probe in required.items() if probe not in suite]
    assert not missing, f"lifecycle suite lost coverage of: {missing}"
    assert "asynchronous backtest is submitted" in financial
    assert "without the confirmation flag is refused" in financial
