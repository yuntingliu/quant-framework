"""Versioned risk policies: validation, immutability, and store behaviour."""

from __future__ import annotations

from dataclasses import replace

import pytest

from alphalab import ResultStore
from alphalab.execution.risk_policy import (
    DEFAULT_POLICY_ID,
    PauseRules,
    RiskLimits,
    RiskPolicy,
    RiskPolicyError,
    default_policy,
    policy_from_payload,
)


def test_default_policy_is_a_versioned_baseline():
    policy = default_policy()
    assert policy.policy_id == DEFAULT_POLICY_ID
    assert policy.version == 1
    assert policy.scope == "account"
    assert policy.limits.max_order_value == 100_000.0
    assert policy.limits.max_position_weight == 1.0, "defaults must not forbid portfolios that worked before"
    assert policy.pause.requires_manual_resume is True
    assert policy.label == "default v1"
    assert "Baseline" in policy.reason


def test_policy_round_trips_through_its_record():
    policy = default_policy()
    restored = RiskPolicy.from_record(policy.to_record())
    assert restored.limits == policy.limits
    assert restored.pause == policy.pause
    assert restored.reason == policy.reason
    assert restored.version == policy.version


def test_revise_appends_a_version_and_leaves_the_old_one_intact():
    first = default_policy()
    second = first.revise(
        reason="Tighten concentration after the review",
        author="tom",
        limits={"max_position_weight": 0.05},
        pause={"drawdown_limit": 0.20},
    )
    assert second.version == 2
    assert second.limits.max_position_weight == 0.05
    assert second.pause.drawdown_limit == 0.20
    assert first.limits.max_position_weight == 1.0
    assert first.pause.drawdown_limit == 0.15
    assert second.reason == "Tighten concentration after the review"


def test_revise_requires_a_reason_and_a_real_change():
    with pytest.raises(RiskPolicyError, match="reason"):
        default_policy().revise(reason="  ")
    with pytest.raises(RiskPolicyError, match="changes nothing"):
        default_policy().revise(reason="no-op")


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"max_order_value": -1}, "max_order_value"),
        ({"max_order_value": float("inf")}, "max_order_value"),
        ({"max_order_weight": 1.5}, "max_order_weight"),
        ({"max_position_weight": -0.1}, "max_position_weight"),
        ({"min_cash_weight": 0.6, "max_gross_exposure": 0.6}, "leave room"),
        ({"volume_lookback_days": 0}, "volume_lookback_days"),
        ({"volume_lookback_days": True}, "volume_lookback_days"),
        ({"max_participation": 0.0}, "max_participation"),
    ],
)
def test_limits_reject_unusable_values(payload, message):
    with pytest.raises(RiskPolicyError, match=message):
        RiskLimits(**payload)


def test_limits_from_dict_rejects_unknown_fields():
    with pytest.raises(RiskPolicyError, match="does not accept"):
        RiskLimits.from_dict({"unknown_limit": 1.0})


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"daily_loss_limit": 1.4}, "daily_loss_limit"),
        ({"resume_drawdown": 0.5, "drawdown_limit": 0.2}, "resume_drawdown"),
        ({"resume_after_days": -1}, "resume_after_days"),
        ({"requires_manual_resume": "yes"}, "requires_manual_resume"),
        (
            {"daily_loss_limit": None, "drawdown_limit": None, "resume_drawdown": 0.1},
            "no pause condition",
        ),
    ],
)
def test_pause_rules_reject_inconsistent_values(payload, message):
    with pytest.raises(RiskPolicyError, match=message):
        PauseRules(**payload)


def test_policy_requires_a_reason_version_and_scope_owner():
    with pytest.raises(RiskPolicyError, match="reason"):
        RiskPolicy(reason="")
    with pytest.raises(RiskPolicyError, match="version"):
        RiskPolicy(version=0)
    with pytest.raises(RiskPolicyError, match="scope_id"):
        RiskPolicy(scope="strategy", scope_id="")
    with pytest.raises(RiskPolicyError, match="scope"):
        RiskPolicy(scope="portfolio")


def test_policy_from_payload_rejects_unknown_fields_instead_of_ignoring_them():
    with pytest.raises(RiskPolicyError, match="policy payload does not accept"):
        policy_from_payload({"reason": "x", "max_order_value": 1.0})
    with pytest.raises(RiskPolicyError, match="limits does not accept"):
        policy_from_payload({"reason": "x", "limits": {"max_order_valu": 1.0}})


def test_policy_from_payload_builds_the_next_version_from_the_base():
    base = default_policy()
    proposal = policy_from_payload(
        {"reason": "Lower the order cap", "limits": {"max_order_value": 25_000.0}},
        base=base,
    )
    assert proposal.version == 2
    assert proposal.limits.max_order_value == 25_000.0
    assert proposal.limits.max_position_weight == base.limits.max_position_weight
    assert base.limits.max_order_value == 100_000.0


def test_store_keeps_every_version_and_refuses_to_rewrite_one(tmp_path):
    store = ResultStore(tmp_path / "risk.db")
    try:
        first = default_policy()
        store.save_risk_policy(first.to_record())
        second = first.revise(reason="Tighter orders", limits={"max_order_value": 50_000.0})
        store.save_risk_policy(second.to_record())

        versions = store.list_risk_policy_versions("default")
        assert [item["version"] for item in versions] == [1, 2]
        assert versions[0]["reason"] == first.reason
        assert versions[1]["limits"]["max_order_value"] == 50_000.0

        # Re-saving identical content is idempotent ...
        assert store.save_risk_policy(first.to_record())["version"] == 1
        # ... but the same version with different content is refused.
        with pytest.raises(ValueError, match="already exists with different content"):
            store.save_risk_policy({**first.to_record(), "limits": {"max_order_value": 1.0}})
        assert store.get_risk_policy("default", 1)["limits"]["max_order_value"] == 100_000.0
    finally:
        store.close()


def test_store_activation_keeps_a_strategy_override_above_the_account_policy(tmp_path):
    store = ResultStore(tmp_path / "risk.db")
    try:
        policy = default_policy()
        store.save_risk_policy(policy.to_record())
        store.activate_risk_policy("paper", "default", 1, reason="baseline")
        active = store.active_risk_policy("paper")
        assert active is not None and active["version"] == 1

        tighter = policy.revise(
            reason="Strategy override", limits={"max_order_value": 10_000.0}, scope="strategy", scope_id="s1"
        )
        store.save_risk_policy(tighter.to_record())
        store.activate_risk_policy(
            "paper", tighter.policy_id, tighter.version, scope="strategy", scope_id="s1", reason="override"
        )

        assert store.active_risk_policy("paper", "s1")["limits"]["max_order_value"] == 10_000.0
        assert store.active_risk_policy("paper", "another")["limits"]["max_order_value"] == 100_000.0
        assert store.active_risk_policy("paper")["activation"]["scope"] == "account"
    finally:
        store.close()


def test_revised_policy_validation_still_applies():
    with pytest.raises(RiskPolicyError, match="max_gross_exposure"):
        replace(default_policy().limits, max_gross_exposure=2.0)
