"""The unified risk evaluation: thresholds, edge cases, pause and fingerprints."""

from __future__ import annotations

from dataclasses import replace

import pytest

from alphalab.execution.risk_guard import (
    AccountSnapshot,
    PositionSnapshot,
    account_risk_metrics,
    evaluate,
    pause_breaches,
    preview_fingerprint,
    resume_decision,
)
from alphalab.execution.risk_policy import PauseRules, RiskLimits, default_policy


def policy(**limits) -> object:
    return replace(
        default_policy(),
        version=2,
        reason="test policy",
        limits=replace(RiskLimits(), **limits),
    )


def with_pause(**rules) -> object:
    return replace(
        default_policy(),
        version=2,
        reason="test pause rules",
        pause=replace(PauseRules(), **rules),
    )


def snapshot(
    *,
    cash: float = 1_000_000.0,
    prices: dict | None = None,
    positions: tuple = (),
    volumes: dict | None = None,
    nav: tuple = (),
    status: str = "active",
    pause_reason: str | None = None,
) -> AccountSnapshot:
    prices = {"AAA": 100.0, "BBB": 10.0} if prices is None else prices
    market = sum(item.market_value or 0.0 for item in positions)
    return AccountSnapshot(
        account_id="paper",
        cash=cash,
        initial_cash=1_000_000.0,
        equity=cash + market,
        positions=positions,
        prices=prices,
        volumes={} if volumes is None else volumes,
        nav=nav,
        price_date="2026-07-10",
        status=status,
        pause_reason=pause_reason,
        policy_id="default",
        policy_version=2,
    )


def held(symbol: str = "AAA", quantity: float = 1000.0, price: float = 100.0) -> PositionSnapshot:
    return PositionSnapshot(symbol, quantity, price, price, quantity * price)


def buy(symbol: str = "BBB", quantity: float = 1000.0, price: float = 10.0) -> dict:
    return {"symbol": symbol, "action": "buy", "quantity": quantity, "price": price}


def sell(symbol: str = "AAA", quantity: float = 1000.0, price: float = 100.0) -> dict:
    return {"symbol": symbol, "action": "sell", "quantity": quantity, "price": price}


def names(verdict) -> dict:
    return {item["name"]: item for item in verdict.checks}


def test_a_reasonable_order_passes_and_projects_the_account():
    verdict = evaluate(policy(), snapshot(), [buy(quantity=1000.0)])
    assert verdict.allowed, [item.message for item in verdict.blocking]
    assert verdict.status == "ready"
    # 10,000 notional plus 3 commission leaves the cash and equity consistent.
    assert verdict.projected.cash == pytest.approx(1_000_000.0 - 10_000.0 - 3.0)
    assert verdict.projected.equity == pytest.approx(999_997.0)
    assert verdict.projected.gross_exposure == pytest.approx(10_000.0 / 999_997.0)
    assert verdict.projected.turnover == pytest.approx(0.01)
    assert names(verdict)["max_order_value"]["passed"] is True


def test_the_order_limit_is_inclusive_at_the_boundary_and_blocks_above_it():
    at_limit = evaluate(policy(), snapshot(), [buy(quantity=1000.0, price=100.0)])
    assert at_limit.allowed

    above = evaluate(policy(), snapshot(), [buy(quantity=1100.0, price=100.0)])
    assert not above.allowed
    rules = [item.rule for item in above.blocking]
    assert "max_order_value" in rules
    assert "110,000.00" in next(item.message for item in above.blocking if item.rule == "max_order_value")


def test_an_order_beyond_the_cash_blocks_without_touching_anything():
    expensive = policy(max_order_value=10_000_000.0)
    verdict = evaluate(expensive, snapshot(cash=1_000_000.0), [buy(quantity=12_000.0, price=100.0)])
    assert not verdict.allowed
    rules = {item.rule for item in verdict.blocking}
    assert {"cash", "max_gross_exposure"} <= rules
    message = next(item.message for item in verdict.blocking if item.rule == "cash")
    assert "more cash than the account has" in message


@pytest.mark.parametrize("quantity", [float("inf"), float("nan"), -100.0, 0.0])
def test_non_finite_or_non_positive_quantities_are_refused(quantity):
    verdict = evaluate(policy(), snapshot(), [{"symbol": "BBB", "action": "buy", "quantity": quantity, "price": 10.0}])
    assert not verdict.allowed
    assert any(item.rule == "order_values" for item in verdict.blocking)


def test_non_finite_prices_fall_back_to_the_market_price_and_missing_prices_block():
    nan_price = evaluate(policy(), snapshot(), [{"symbol": "BBB", "action": "buy", "quantity": 1000.0, "price": float("nan")}])
    assert nan_price.allowed, "a missing order price must fall back to the marked price"

    missing = evaluate(policy(), snapshot(), [{"symbol": "ZZZ", "action": "buy", "quantity": 100.0, "price": None}])
    assert not missing.allowed
    assert any(item.rule == "price_coverage" for item in missing.blocking)
    assert "ZZZ" in next(item.message for item in missing.blocking if item.rule == "price_coverage")


def test_selling_more_than_held_blocks_but_selling_the_whole_position_passes():
    too_much = evaluate(policy(), snapshot(positions=(held(),)), [sell(quantity=2000.0)])
    assert not too_much.allowed
    assert any(item.rule == "position_available" for item in too_much.blocking)

    whole = evaluate(policy(), snapshot(positions=(held(),)), [sell(quantity=1000.0)])
    assert whole.allowed
    assert whole.projected.positions == ()
    assert whole.projected.cash == pytest.approx(1_100_000.0 - 30.0)


def test_duplicate_symbols_and_broken_board_lots_are_refused():
    duplicates = evaluate(policy(), snapshot(), [buy(quantity=100.0), buy(quantity=200.0)])
    assert any(item.rule == "duplicate_symbols" for item in duplicates.blocking)

    broken_lot = evaluate(policy(), snapshot(), [buy(quantity=150.0)])
    assert any(item.rule == "board_lot" for item in broken_lot.blocking)


def test_a_tightened_policy_enforces_concentration_exposure_cash_and_turnover():
    concentrated = evaluate(
        policy(max_position_weight=0.05), snapshot(), [buy(quantity=1000.0, price=100.0)]
    )
    assert any(item.rule == "max_position_weight" for item in concentrated.blocking)
    assert "concentration limit 5.00%" in next(
        item.message for item in concentrated.blocking if item.rule == "max_position_weight"
    )

    exposed = evaluate(
        policy(max_order_value=10_000_000.0, max_gross_exposure=0.5),
        snapshot(),
        [buy(quantity=6000.0, price=100.0)],
    )
    assert any(item.rule == "max_gross_exposure" for item in exposed.blocking)

    cash_floor = evaluate(
        policy(max_order_value=10_000_000.0, max_gross_exposure=0.8, min_cash_weight=0.2),
        snapshot(),
        [buy(quantity=8500.0, price=100.0)],
    )
    assert any(item.rule == "min_cash_weight" for item in cash_floor.blocking)

    churn = evaluate(
        policy(max_order_value=10_000_000.0, max_turnover=0.1),
        snapshot(),
        [buy(quantity=2000.0, price=100.0)],
    )
    assert any(item.rule == "max_turnover" for item in churn.blocking)


def test_liquidity_blocks_when_volume_is_known_and_warns_when_it_is_not():
    known = evaluate(
        policy(max_order_value=10_000_000.0, max_participation=0.10),
        snapshot(volumes={"BBB": 10_000.0, "AAA": 1_000_000.0}),
        [buy(quantity=5000.0)],
    )
    assert not known.allowed
    assert any(item.rule == "max_participation" for item in known.blocking)

    unknown = evaluate(
        policy(max_order_value=10_000_000.0, max_participation=0.10),
        snapshot(),
        [buy(quantity=5000.0)],
    )
    assert unknown.allowed, "missing volume history must not invent a breach"
    assert any(item.severity == "warning" and item.rule == "liquidity_unevaluated" for item in unknown.findings)


def test_a_paused_account_is_refused_and_told_how_to_resume():
    paused = evaluate(
        with_pause(requires_manual_resume=True),
        snapshot(status="paused", pause_reason="drawdown 20.00% reached the 15.00% limit"),
        [buy()],
    )
    assert not paused.allowed
    message = next(item.message for item in paused.blocking if item.rule == "account_paused")
    assert "explicit request" in message
    assert "Resume condition" in message


def test_a_loss_breach_is_blocking_even_before_the_pause_is_persisted():
    hard_drawdown = snapshot(
        cash=700_000.0,
        nav=(("2026-01-02", 1_000_000.0), ("2026-07-10", 700_000.0)),
    )
    metrics = account_risk_metrics(hard_drawdown)
    assert metrics.drawdown == pytest.approx(0.3)
    assert metrics.high_water == pytest.approx(1_000_000.0)
    breaches = pause_breaches(with_pause(), metrics)
    assert breaches and "30.00%" in breaches[0]

    verdict = evaluate(with_pause(), hard_drawdown, [buy()])
    assert not verdict.allowed
    assert any(item.rule == "pause_trigger" for item in verdict.blocking)


def test_resume_conditions_are_evaluated_from_the_rules_not_the_clock():
    metrics = account_risk_metrics(
        snapshot(cash=950_000.0, nav=(("2026-01-02", 1_000_000.0), ("2026-07-10", 950_000.0)))
    )
    assert metrics.drawdown == pytest.approx(0.05)
    automatic = with_pause(requires_manual_resume=False, drawdown_limit=0.15, resume_drawdown=0.10)
    allowed, reason = resume_decision(automatic, metrics, paused_at="2026-07-01T00:00:00+00:00")
    assert allowed and "below" in reason

    still_deep = account_risk_metrics(
        snapshot(cash=700_000.0, nav=(("2026-01-02", 1_000_000.0), ("2026-07-10", 700_000.0)))
    )
    allowed, reason = resume_decision(automatic, still_deep, paused_at="2026-07-01T00:00:00+00:00")
    assert not allowed and "still above" in reason

    manual = with_pause(requires_manual_resume=True)
    allowed, reason = resume_decision(manual, metrics, paused_at=None)
    assert not allowed and "explicit" in reason

    waiting = with_pause(
        requires_manual_resume=False,
        drawdown_limit=0.15,
        resume_drawdown=0.10,
        resume_after_days=3,
    )
    allowed, reason = resume_decision(waiting, metrics, paused_at="2026-07-01T00:00:00+00:00")
    assert allowed and "day(s) since the pause" in reason

    allowed, reason = resume_decision(waiting, metrics, paused_at="not-a-date")
    assert not allowed and "not a readable date" in reason


def test_preview_fingerprint_binds_orders_snapshot_and_policy_version():
    base = evaluate(policy(), snapshot(), [buy()])
    again = evaluate(policy(), snapshot(), [buy()])
    assert base.preview_id == again.preview_id, "the same proposal must judge to the same id"

    different_order = evaluate(policy(), snapshot(), [buy(quantity=2000.0)])
    assert different_order.preview_id != base.preview_id

    moved_price = evaluate(policy(), snapshot(prices={"AAA": 100.0, "BBB": 11.0}), [buy()])
    assert moved_price.preview_id != base.preview_id

    newer_policy = replace(policy(), version=3, limits=replace(RiskLimits(), max_order_value=90_000.0))
    assert evaluate(newer_policy, snapshot(), [buy()]).preview_id != base.preview_id


def test_findings_separate_blocking_warnings_and_research_notes():
    verdict = evaluate(
        policy(max_order_value=10_000_000.0, max_position_weight=0.05),
        snapshot(),
        [buy(quantity=1000.0, price=100.0)],
    )
    assert {item.severity for item in verdict.blocking} == {"blocking"}
    assert all(item.severity == "blocking" for item in verdict.findings[: len(verdict.blocking)])
    assert any(item.severity == "info" for item in verdict.info)
    payload = verdict.as_dict()
    assert payload["status"] == "blocked"
    assert payload["disclaimer"].startswith("Paper simulation only")
    assert payload["checks"]


def test_an_empty_proposal_is_not_silently_accepted():
    verdict = evaluate(policy(), snapshot(), [])
    assert not verdict.allowed
    assert any(item.rule == "orders_present" for item in verdict.blocking)


def test_the_disabled_participation_rule_does_not_warn():
    verdict = evaluate(
        policy(max_participation=None),
        snapshot(),
        [buy()],
    )
    assert verdict.allowed
    assert names(verdict)["max_participation"]["detail"] == "disabled"


def test_fingerprint_helper_is_stable_for_equal_states():
    first = snapshot()
    second = snapshot()
    verdict = evaluate(policy(), first, [buy()])
    assert first.fingerprint() == second.fingerprint()
    assert preview_fingerprint(first, policy(), verdict.projected) == verdict.preview_id
