"""Paper risk at the service layer: unified rules, audit, pause, policy versions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from alphalab import ResultStore, create_default_engine
from apps.api.services import result_service


@pytest.fixture
def paper(tmp_path, monkeypatch):
    database = tmp_path / "paper.db"
    monkeypatch.setattr(result_service, "ResultStore", lambda: ResultStore(database))
    # Project concentration is a strategy-source concern, not a risk-policy concern.
    monkeypatch.setattr(result_service, "_project_max_weight", lambda project_id: 1.0)

    engine = create_default_engine()
    symbols = engine.get_symbols()[:3]
    latest = engine.get_latest_date()
    bars = engine.get_bars(symbols, latest, latest, fields=["close"])
    prices = {row["symbol"]: float(row["close"]) for row in bars.to_dict("records")}

    store = ResultStore(database)
    store.ensure_backtest_subject("sdk-v1-default", "projects/sdk-v1-default")
    signal_id = store.save_signal(
        "sdk-v1-default",
        latest,
        {symbol: 0.05 for symbol in symbols},
        status="pending",
        profile="demo",
    )
    store.close()
    return SimpleNamespace(
        database=database,
        symbols=symbols,
        latest=latest,
        prices=prices,
        signal_id=signal_id,
    )


def account_state(paper) -> dict:
    summary = result_service.paper_account_summary("paper", "demo")
    return {
        "cash": summary["account"]["cash"],
        "equity": summary["account"]["equity"],
        "positions": {row["symbol"]: row["quantity"] for row in summary["positions"]},
    }


def test_preview_records_a_snapshot_and_an_evaluation(paper):
    preview = result_service.preview_paper_rebalance(
        signal_id=paper.signal_id, profile="demo", account_id="paper"
    )
    assert preview["allowed"] is True
    assert preview["risk_status"] == "ready"
    assert len(preview["orders"]) == len(paper.symbols)
    assert preview["policy"]["label"] == "default v1"
    assert preview["price_date"] == paper.latest
    assert preview["snapshot_id"] and preview["evaluation_id"]
    assert preview["disclaimer"].startswith("Paper simulation only")

    store = ResultStore(paper.database)
    try:
        snapshot = store.get_risk_snapshot(preview["snapshot_id"])
        evaluation = store.get_risk_evaluation(preview["evaluation_id"])
        assert snapshot["price_date"] == paper.latest
        assert snapshot["cash"] == pytest.approx(1_000_000.0)
        assert snapshot["prices"], "the snapshot freezes the prices the check used"
        assert snapshot["positions"] == [], "a fresh account holds nothing yet"
        assert snapshot["sha256"]
        assert evaluation["door"] == "rebalance_preview"
        assert evaluation["verdict"] == "ready"
        assert evaluation["policy_version"] == 1
        assert evaluation["preview_id"] == preview["preview_id"]
    finally:
        store.close()


def test_execute_refuses_an_unknown_preview_and_leaves_the_account_alone(paper):
    before = account_state(paper)
    with pytest.raises(result_service.StalePreviewError) as excinfo:
        result_service.execute_paper_rebalance(
            signal_id=paper.signal_id,
            profile="demo",
            account_id="paper",
            confirm=True,
            preview_id="not-the-preview",
        )
    assert "out of date" in str(excinfo.value)
    assert excinfo.value.detail["changes"]
    assert account_state(paper) == before


def test_execute_requires_a_preview_id_and_confirmation(paper):
    with pytest.raises(ValueError, match="preview_id"):
        result_service.execute_paper_rebalance(
            signal_id=paper.signal_id, profile="demo", account_id="paper", confirm=True
        )
    with pytest.raises(PermissionError):
        result_service.execute_paper_rebalance(
            signal_id=paper.signal_id, profile="demo", account_id="paper", confirm=False
        )


def test_execute_fills_exactly_what_was_previewed_and_links_the_audit(paper):
    preview = result_service.preview_paper_rebalance(
        signal_id=paper.signal_id, profile="demo", account_id="paper"
    )
    result = result_service.execute_paper_rebalance(
        signal_id=paper.signal_id,
        profile="demo",
        account_id="paper",
        confirm=True,
        preview_id=preview["preview_id"],
    )
    assert result["status"] == "filled"
    assert result["orders_created"] == len(preview["orders"])
    assert result["account"]["positions_count"] == len(paper.symbols)

    store = ResultStore(paper.database)
    try:
        orders = store.list_orders(limit=20, account_id="paper")
        assert set(orders["risk_evaluation_id"]) == {result["evaluation_id"]}
        execution = store.get_risk_evaluation(result["evaluation_id"])
        assert execution["door"] == "rebalance_execute"
        assert execution["preview_id"] == preview["preview_id"]
        approved = store.get_risk_evaluation(preview["evaluation_id"])
        assert approved["preview_id"] == preview["preview_id"]
    finally:
        store.close()


def test_a_used_preview_cannot_be_replayed(paper):
    preview = result_service.preview_paper_rebalance(
        signal_id=paper.signal_id, profile="demo", account_id="paper"
    )
    result_service.execute_paper_rebalance(
        signal_id=paper.signal_id,
        profile="demo",
        account_id="paper",
        confirm=True,
        preview_id=preview["preview_id"],
    )
    before = account_state(paper)
    with pytest.raises(result_service.StalePreviewError) as excinfo:
        result_service.execute_paper_rebalance(
            signal_id=paper.signal_id,
            profile="demo",
            account_id="paper",
            confirm=True,
            preview_id=preview["preview_id"],
        )
    assert any("cash changed" in change or "orders" in change for change in excinfo.value.detail["changes"])
    assert account_state(paper) == before


def test_the_manual_door_enforces_the_same_hard_rules(paper):
    before = account_state(paper)
    with pytest.raises(result_service.RiskBlockedError) as excinfo:
        result_service.create_paper_order(
            paper.symbols[0], "buy", 200_000, None, None, "demo", "paper"
        )
    detail = excinfo.value.detail
    assert detail["blocking"]
    assert detail["policy"] == "default v1"
    assert detail["preview_id"]
    # The refusal did not move a single share or yuan.
    assert account_state(paper) == before

    audit = result_service.list_paper_risk_evaluations("paper", limit=5)
    blocked = [item for item in audit if item["door"] == "manual_order"]
    assert blocked and blocked[0]["verdict"] == "blocked"


def test_a_permitted_manual_order_records_its_policy_and_projection(paper):
    result = result_service.create_paper_order(
        paper.symbols[1], "buy", 100, None, None, "demo", "paper"
    )
    assert result["id"]
    assert result["risk"]["allowed"] is True
    assert result["risk"]["policy"]["label"] == "default v1"
    assert result["risk"]["evaluation_id"]
    assert result["risk"]["projected"]["applied"] is True

    store = ResultStore(paper.database)
    try:
        evaluation = store.get_risk_evaluation(result["risk"]["evaluation_id"])
        assert evaluation["door"] == "manual_order"
        assert evaluation["verdict"] == "ready"
        order = store.list_orders(limit=1, account_id="paper").iloc[0]
        assert order["risk_evaluation_id"] == result["risk"]["evaluation_id"]
    finally:
        store.close()


def test_a_paused_account_refuses_orders_until_an_explicit_resume(paper):
    store = ResultStore(paper.database)
    try:
        store.pause_paper_account("paper", "drawdown 20.00% reached the 15.00% limit")
    finally:
        store.close()

    with pytest.raises(result_service.RiskBlockedError) as excinfo:
        result_service.create_paper_order(
            paper.symbols[0], "buy", 100, None, None, "demo", "paper"
        )
    rules = [item["rule"] for item in excinfo.value.detail["blocking"]]
    assert "account_paused" in rules
    assert any("explicit request" in item["message"] for item in excinfo.value.detail["blocking"])

    with pytest.raises(ValueError, match="reason"):
        result_service.resume_paper_risk("paper", "   ")

    resumed = result_service.resume_paper_risk("paper", "Reviewed the drawdown with the supervisor")
    assert resumed["account"]["status"] == "active"
    result = result_service.create_paper_order(
        paper.symbols[0], "buy", 100, None, None, "demo", "paper"
    )
    assert result["risk"]["allowed"] is True


def test_publishing_a_policy_appends_a_version_and_changes_the_verdict(paper):
    allowed = result_service.preview_paper_rebalance(
        signal_id=paper.signal_id, profile="demo", account_id="paper"
    )
    assert allowed["allowed"] is True

    published = result_service.publish_paper_risk_policy(
        {
            "reason": "Order cap tightened after the incident review",
            "author": "risk-desk",
            "limits": {"max_order_value": 1_000.0},
        },
        account_id="paper",
    )
    assert published["active"]["version"] == 2
    assert published["active"]["label"] == "default v2"
    assert [item["version"] for item in published["versions"]] == [1, 2]
    assert published["versions"][0]["reason"].startswith("Baseline")
    assert published["versions"][1]["reason"] == "Order cap tightened after the incident review"

    blocked = result_service.preview_paper_rebalance(
        signal_id=paper.signal_id, profile="demo", account_id="paper"
    )
    assert blocked["allowed"] is False
    assert any(item["rule"] == "max_order_value" for item in blocked["blocking"])
    assert blocked["policy"]["version"] == 2


def test_a_policy_without_a_reason_or_with_an_unknown_field_is_refused(paper):
    with pytest.raises(ValueError, match="reason"):
        result_service.publish_paper_risk_policy({}, account_id="paper")
    with pytest.raises(ValueError, match="does not accept"):
        result_service.publish_paper_risk_policy(
            {"reason": "typo", "limits": {"max_order_valu": 1.0}}, account_id="paper"
        )


def test_a_strategy_scoped_policy_overrides_the_account_without_replacing_it(paper):
    result_service.publish_paper_risk_policy(
        {
            "reason": "This strategy trades smaller",
            "limits": {"max_order_value": 5_000.0},
            "scope": "strategy",
            "scope_id": "sdk-v1-default",
        },
        account_id="paper",
    )
    scoped = result_service.paper_risk_policy("paper", strategy_id="sdk-v1-default")
    account = result_service.paper_risk_policy("paper")
    assert scoped["active"]["version"] == 2
    assert account["active"]["version"] == 1
    assert account["versions"], "the account policy history is still readable"


def test_holdings_risk_replays_from_frozen_inputs(paper):
    preview = result_service.preview_paper_rebalance(
        signal_id=paper.signal_id, profile="demo", account_id="paper"
    )
    result_service.execute_paper_rebalance(
        signal_id=paper.signal_id,
        profile="demo",
        account_id="paper",
        confirm=True,
        preview_id=preview["preview_id"],
    )

    analysis = result_service.paper_risk_analysis(account_id="paper", profile="demo")
    assert analysis["cached"] is False
    assert analysis["portfolio"]["evaluated"] is True
    assert analysis["portfolio"]["annualized_volatility"] > 0
    assert analysis["contributions"]
    total = sum(item["contribution_to_volatility"] for item in analysis["contributions"])
    assert total == pytest.approx(analysis["portfolio"]["annualized_volatility"], rel=1e-9)
    assert analysis["scenarios"]["items"]
    assert analysis["unevaluated"]["symbols"] == []
    assert analysis["disclaimer"]
    assert analysis["input_hash"]

    replay = result_service.paper_risk_analysis(account_id="paper", profile="demo")
    assert replay["cached"] is True
    assert replay["input_hash"] == analysis["input_hash"]
    assert replay["contributions"] == analysis["contributions"]


def test_the_audit_lists_every_door(paper):
    result_service.preview_paper_rebalance(
        signal_id=paper.signal_id, profile="demo", account_id="paper"
    )
    result_service.create_paper_order(
        paper.symbols[1], "buy", 100, None, None, "demo", "paper"
    )
    audit = result_service.list_paper_risk_evaluations("paper", limit=10)
    doors = [item["door"] for item in audit]
    assert "rebalance_preview" in doors
    assert "manual_order" in doors
    assert all(item["snapshot_id"] for item in audit if item["door"] == "manual_order")
