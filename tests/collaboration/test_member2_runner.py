"""Member 2 runner preflight and frozen-fixture checks (no model/network calls)."""
from __future__ import annotations

from pathlib import Path

import pytest

from alphalab.collaboration import EvidenceStore, validate_contract
from scripts.prepare_collaboration_acceptance import prepare

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'scripts/run_member2_review.mjs'


@pytest.mark.parametrize('scenario,expected', [
    ('clean', 'pass'),
    ('seeded-formula', 'findings'),
    ('seeded-pit', 'findings'),
    ('missing-evidence', 'incomplete'),
])
def test_runner_fixtures_are_valid_and_frozen(tmp_path, scenario, expected):
    fixture = prepare(tmp_path, scenario)
    assert fixture['versions'][0]['review']['status'] == expected
    version = fixture['versions'][0]
    context = EvidenceStore(tmp_path / 'evidence').candidate_context(
        fixture['task_id'], version['candidate'])
    validate_contract('research_package', context['research_package'], context)
    validate_contract('delegation_request', version['delegation'], context)
    validate_contract('review_result', version['review'], {**context, 'delegation': version['delegation']})
    assert fixture['oracle_in_agent_workspace'] is False
    assert not (tmp_path / 'oracle.json').exists()


def test_runner_uses_host_not_direct_submission():
    code = RUNNER.read_text(encoding='utf-8')
    assert 'createCollaborationHost' in code
    assert "node_id: 'review-control'" in code
    assert "node_id: 'review-evidence'" in code
    assert "node_id: 'review-submit'" in code
    assert "args.includes('--live')" in code
    assert 'publication_performed: false' in code

def test_missing_evidence_cannot_be_approved(tmp_path):
    fixture = prepare(tmp_path, "missing-evidence")
    version = fixture["versions"][0]

    review = version["review"]

    assert review["status"] == "incomplete"

    missing_categories = {
        item["category"]
        for item in review["coverage"]
        if item["status"] == "missing"
    }

    assert missing_categories

    context = EvidenceStore(
        tmp_path / "evidence"
    ).candidate_context(
        fixture["task_id"],
        version["candidate"]
    )

    validate_contract(
        "review_result",
        review,
        {
            **context,
            "delegation": version["delegation"],
        },
    )