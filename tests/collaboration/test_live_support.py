"""Offline checks for the live driver's evidence and measurement boundaries."""
from __future__ import annotations

import json

import pytest

from alphalab.collaboration import EvidenceStore
from scripts.collaboration_live_support import ATTACK, prepare_live, usage


@pytest.mark.parametrize("scenario", ["clean", "disputed", "missing-evidence", "injection"])
def test_live_input_excludes_scripted_answers_but_keeps_supplied_claims(tmp_path, scenario):
    payload = prepare_live(tmp_path, scenario)
    assert payload == json.loads((tmp_path / "agent-input.json").read_text(encoding="utf-8"))
    assert set(payload) == {"task_id", "task", "candidate", "manifest", "delegation", "evidence_ids", "candidate_context"}
    encoded = json.dumps(payload)
    for private in ("expected_status", "oracle", "rationale", "scripted-review", "submission-1"):
        assert private not in encoded
    context = EvidenceStore(tmp_path / "evidence").candidate_context(payload["task_id"], payload["candidate"])
    artifact_ids = {row["artifact_id"] for row in context["manifest"]["artifacts"]}
    assert set(payload["evidence_ids"]) <= artifact_ids
    if scenario == "disputed":
        assert payload["candidate_context"]["reviewer_allegation"] == "The reported 8% excludes fees."
    if scenario == "injection":
        assert "adversarial_note" in payload["evidence_ids"]
        assert ATTACK not in encoded  # It must be read through the frozen evidence tool.


def test_live_usage_preserves_actual_receipts_without_counting_cache_twice():
    result = usage([{"usage": {"prompt_tokens": 10, "completion_tokens": 3, "prompt_cache_hit_tokens": 7}}])
    assert result["status"] == "actual"
    assert result["input_tokens"] == 10 and result["output_tokens"] == 3


@pytest.mark.parametrize("receipt", [{}, {"prompt_tokens": 10}, {"prompt_tokens": True, "completion_tokens": 1},
                                     {"prompt_tokens": -1, "completion_tokens": 1}])
def test_missing_or_invalid_live_usage_is_unknown_not_zero(receipt):
    result = usage([{"usage": {"prompt_tokens": 10, "completion_tokens": 3}}, {"usage": receipt}])
    assert result["status"] == "unavailable"
    assert result["input_tokens"] is None and result["output_tokens"] is None
