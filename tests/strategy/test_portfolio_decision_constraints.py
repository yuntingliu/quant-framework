"""Constraint evidence survives the Strategy SDK decision boundary."""

import json

import pytest

from alphalab.sdk.v1 import GroupWeightConstraint, PortfolioConstraintSet, PortfolioDecision
from alphalab.strategy.sdk_runtime import (
    SdkRuntimeError,
    _decision_from_payload,
    _decision_payload,
)


def _constraint_set() -> PortfolioConstraintSet:
    return PortfolioConstraintSet(
        constraints=(
            GroupWeightConstraint(id="technology_cap", classification="test:1", upper=0.3),
        )
    )


def test_decision_constraint_evidence_round_trips() -> None:
    constraints = _constraint_set()
    decision = PortfolioDecision(
        target_weights={"aaa": 0.2},
        constraints=constraints,
        constraint_data_version="a" * 64,
        diagnostics={"solver": "SLSQP"},
    )

    payload = _decision_payload(decision)
    assert payload["constraint_set_sha256"] == constraints.constraint_set_sha256
    assert payload["constraint_data_version"] == "a" * 64
    restored = _decision_from_payload(json.loads(json.dumps(payload)))
    assert restored.constraints.to_dict() == constraints.to_dict()
    assert restored.constraint_set_sha256 == constraints.constraint_set_sha256
    assert restored.target_weights == {"AAA": 0.2}


def test_legacy_decision_payload_remains_valid() -> None:
    decision = _decision_from_payload({"target_weights": {"AAA": 0.5}})
    assert decision.constraints is None
    assert decision.constraint_set_sha256 is None
    assert decision.constraint_data_version is None


def test_decision_rejects_mismatched_constraint_hash() -> None:
    payload = _decision_payload(PortfolioDecision(target_weights={}, constraints=_constraint_set()))
    payload["constraint_set_sha256"] = "0" * 64
    with pytest.raises(SdkRuntimeError, match="constraint set hash does not match"):
        _decision_from_payload(payload)


def test_decision_rejects_invalid_data_version_and_oversized_evidence() -> None:
    with pytest.raises(ValueError, match="constraint_data_version"):
        PortfolioDecision(target_weights={}, constraint_data_version="unknown")

    # Diagnostics alone fit; the added rule set must share the same 64 KB evidence budget.
    with pytest.raises(ValueError, match="portfolio constraint evidence exceeds"):
        PortfolioDecision(
            target_weights={},
            diagnostics={"checks": "x" * 63_900},
            constraints=_constraint_set(),
        )
