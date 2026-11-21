from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("member3_confirmation", ROOT / "scripts/verify_member3_confirmation.py")
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)
CORPUS = ROOT / "tests/fixtures/collaboration/confirmation"


def test_confirmation_corpus_hashes_and_independent_facts_pass():
    result = module.verify(CORPUS)
    assert result["status"] == "passed"
    assert result["case_count"] == 8
    assert result["oracle_sent_to_agent"] is False


def test_confirmation_fixture_tampering_is_detected(tmp_path):
    import shutil
    clone = tmp_path / "confirmation"
    shutil.copytree(CORPUS, clone)
    target = clone / "inputs/CONF-CLEAN-01.json"
    target.write_text(target.read_text(encoding="utf-8").replace("0.075", "0.076"), encoding="utf-8")
    with pytest.raises(ValueError, match="Input hash mismatch"):
        module.verify(clone)


def test_confirmation_corpus_blocks_timestamp_without_timezone():
    payload = {
        "evidence": {"timing": {"available_at": "2025-01-01T00:00:00", "decision_at": "2025-01-02T00:00:00Z"}},
        "candidate": {}, "required_evidence_ids": ["timing"],
    }
    with pytest.raises(ValueError, match="explicit timezone"):
        module.facts(payload)
