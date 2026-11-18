"""Member 2 prompt and fixture sanity checks; not a live model-quality test."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPT = ROOT / "integrations/conexus/collaboration/reviewer-prompt.mjs"
HOST = ROOT / "integrations/conexus/collaboration/host.mjs"
DEV = ROOT / "tests/fixtures/collaboration/dev/inputs"


def test_reviewer_prompt_covers_protocol_categories():
    prompt = PROMPT.read_text(encoding="utf-8")
    for category in ("formulas", "pit", "returns", "execution", "risk", "report_consistency", "support"):
        assert f"- {category}:" in prompt
    for requirement in ("review-evidence", "review-submit", "claim_id", "metric_id", "read-only", "incomplete"):
        assert requirement in prompt


def test_host_loads_reviewer_prompt_only_for_reviewer():
    host = HOST.read_text(encoding="utf-8")
    assert "import { FINANCIAL_REVIEWER_PROMPT }" in host
    assert "content: reviewer ? `${FINANCIAL_REVIEWER_PROMPT}" in host
    assert "REVIEW_SCHEMA_GUIDE" in host
    assert "createReviewSubmissionHandler" in host


def test_clean_fixture_evidence_matches_claim_and_is_point_in_time():
    case = json.loads((DEV / "DEV-CLEAN-01.json").read_text(encoding="utf-8"))
    assert Decimal(case["candidate"]["reported_net_return"]) == Decimal(case["evidence"]["result"]["net_total_return"])
    from datetime import datetime
    timing = case["evidence"]["timing"]
    assert datetime.fromisoformat(timing["available_at"]) <= datetime.fromisoformat(timing["decision_at"])


def test_formula_fixture_is_incorrect_under_geometric_compounding():
    case = json.loads((DEV / "DEV-FORMULA-01.json").read_text(encoding="utf-8"))
    total = Decimal("1")
    for row in case["evidence"]["returns"]["rows"]:
        total *= 1 + Decimal(row["return"])
    total -= 1
    assert total == Decimal("-0.0100")
    assert Decimal(case["candidate"]["reported_net_return"]) != total

def test_reviewer_observation_and_evidence_efficiency_instructions():
    prompt = PROMPT.read_text(encoding="utf-8")

    required = (
        'node_id',
        'detail="values"',
        'review-evidence',
        'review-submit',
        'review-control',
        'Do not reread',
        'missing',
        'ReviewResult',
    )

    for instruction in required:
        assert instruction.lower() in prompt.lower()

    assert 'Do not create or edit workspace nodes' in prompt
