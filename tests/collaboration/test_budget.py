from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from alphalab.collaboration import CollaborationError, ControlStore
from scripts.prepare_collaboration_host import prepare

ROOT = {"role": "researcher", "task_id": "task-1", "node_id": "researcher-1"}
MODEL = {"provider_id": "local-script", "model_id": "synthetic", "profile_id": "fixture-v1",
         "configuration_id": "synthetic-v1", "bounds_id": "synthetic-input-v1",
         "supports_output_cap": True, "pricing": {"id": "fixture-prices-v1", "currency": "USD",
                                                  "input_micros_per_million": 1_000_000,
                                                  "output_micros_per_million": 2_000_000}}


def setup(tmp_path, budget=None, model=None):
    fixture = prepare(tmp_path)
    store = ControlStore(tmp_path / "control", tmp_path / "evidence")
    policy = {"scopes": ["model.invoke", "review.submit", "evidence.read", "review.delegate"], "operations": []}
    store.register("task-1", fixture["candidate"], policy, budget=budget, model=model or MODEL)
    store.request("task-1", "begin")
    return store, fixture, policy


def reserve(store, call="call-1", identity=None, **extra):
    return store.request("task-1", "reserve", call_id=call, identity=identity or ROOT,
                         kind="model", name="completion", input_tokens_bound=10, **extra)


def settle(store, call="call-1", identity=None, usage=None, **extra):
    return store.request("task-1", "settle", call_id=call, identity=identity or ROOT,
                         usage=usage, outcome=extra.pop("outcome", "succeeded"), **extra)


def error(code, callback):
    with pytest.raises(CollaborationError) as failure:
        callback()
    assert failure.value.code == code


def test_shared_60_plus_40_and_conservative_output_cap(tmp_path):
    store, fixture, _ = setup(tmp_path, {"max_total_tokens": 100, "max_output_tokens": 50})
    first = reserve(store)
    assert first["reserved_tokens"] == 60
    settle(store, usage={"input_tokens": 10, "output_tokens": 50, "total_tokens": 60})
    child = store.request("task-1", "start", delegation=fixture["delegation"])
    assert child["budget_id"] == "budget-1"
    identity = {"role": "reviewer", "task_id": "task-1", "node_id": "reviewer-1",
                "child_task_id": fixture["delegation"]["child_task_id"], "attempt_id": fixture["delegation"]["attempt_id"]}
    second = reserve(store, "call-2", identity)
    assert second["max_output_tokens"] == 30
    settle(store, "call-2", identity, {"input_tokens": 10, "output_tokens": 30, "total_tokens": 40})
    ledger = store.request("task-1", "accounting")
    assert ledger["totals"]["total_tokens"] == 100
    assert ledger["roles"]["researcher"]["total_tokens"] == 60
    assert ledger["roles"]["reviewer"]["total_tokens"] == 40
    error("BUDGET_EXHAUSTED", lambda: reserve(store, "call-3"))
    assert store.request("task-1", "view")["children"][0]["status"] == "cancelled"


def test_concurrent_reservations_cannot_overspend(tmp_path):
    store, _, _ = setup(tmp_path, {"max_total_tokens": 20, "max_output_tokens": 10})

    def attempt(number):
        try:
            return reserve(store, f"call-{number}")["reserved_tokens"]
        except CollaborationError as failure:
            return failure.code

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert results.count(20) == 1
    assert results.count("BUDGET_EXHAUSTED") == 1
    assert store.request("task-1", "accounting")["totals"]["charged_tokens"] == 20


def test_restart_retains_uncertain_work_and_duplicate_settlements(tmp_path):
    store, fixture, policy = setup(tmp_path, {"max_total_tokens": 100, "max_output_tokens": 10})
    assert reserve(store)["dispatch"] is True
    assert reserve(store)["dispatch"] is False
    store.request("task-1", "dispatch", call_id="call-1", identity=ROOT)
    reopened = ControlStore(store.root, store.evidence.root)
    reopened.register("task-1", fixture["candidate"], policy,
                      budget={"max_total_tokens": 100, "max_output_tokens": 10}, model=MODEL)
    reopened.request("task-1", "recover")
    accounting = reopened.request("task-1", "accounting")["totals"]
    assert accounting["pending"] == 1 and accounting["charged_tokens"] == 20
    assert accounting["total_tokens"] is None and accounting["cost_micros"] is None
    error("INVALID_STATE", lambda: reserve(reopened, "new"))
    usage = {"input_tokens": 10, "output_tokens": 7, "total_tokens": 17}
    receipt = settle(reopened, usage=usage)
    assert settle(reopened, usage=usage) == receipt
    error("DUPLICATE_CONFLICT", lambda: settle(reopened, usage={"total_tokens": 10}))
    total = reopened.request("task-1", "accounting")["totals"]
    assert total["model_calls"] == 1 and total["total_tokens"] == 17 and total["cost_micros"] == 24
    assert reopened.request("task-1", "events")[-1]["late"] is True
    assert reopened.request("task-1", "view")["status"] == "blocked"


@pytest.mark.parametrize("usage,reason", [(None, "missing_usage"),
    ({"total_tokens": -1}, "malformed_usage"), ({"total_tokens": True}, "malformed_usage"),
    ({"input_tokens": 2, "output_tokens": 2, "total_tokens": 3}, "malformed_usage"),
    ({"input_tokens": 2, "cached_input_tokens": 3}, "malformed_usage")])
def test_missing_and_malformed_usage_never_free_reservations(tmp_path, usage, reason):
    store, _, _ = setup(tmp_path, {"max_total_tokens": 20, "max_output_tokens": 10})
    reserve(store)
    row = settle(store, usage=usage, outcome="failed")
    assert row["settlement"]["usage"]["reason"] == reason
    total = store.request("task-1", "accounting")["totals"]
    assert total["total_tokens"] is None and total["cost_micros"] is None
    assert total["held_tokens"] == 20 and total["held_cost_micros"] == 30
    error("BUDGET_EXHAUSTED", lambda: reserve(store, "new"))


@pytest.mark.parametrize("model,budget,code", [
    ({**MODEL, "bounds_id": None}, {"max_total_tokens": 100}, "BUDGET_UNVERIFIABLE"),
    ({**MODEL, "supports_output_cap": False}, {"max_total_tokens": 100}, "BUDGET_UNVERIFIABLE"),
    ({**MODEL, "pricing": None}, {"max_cost_micros": 100, "currency": "USD"}, "BUDGET_UNVERIFIABLE"),
    (MODEL, {"max_cost_micros": 100, "currency": "EUR"}, "CURRENCY_MISMATCH"),
])
def test_unverifiable_caps_stop_before_dispatch(tmp_path, model, budget, code):
    store, _, _ = setup(tmp_path, budget, model)
    error(code, lambda: reserve(store))
    assert store.request("task-1", "requests") == []
    assert store.request("task-1", "view")["reason"] == code


def test_pricing_subsets_retries_and_tool_costs(tmp_path):
    store, _, _ = setup(tmp_path, {"max_output_tokens": 10, "max_cost_micros": 100, "currency": "USD"})
    for number, purpose in enumerate(("normal", "retry", "schema_repair")):
        reserve(store, str(number), purpose=purpose)
        settle(store, str(number), usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                                         "cached_input_tokens": 7, "reasoning_output_tokens": 3}, outcome="failed")
    store.request("task-1", "reserve", call_id="search", identity=ROOT, kind="tool", name="search", cost_bound_micros=35)
    settle(store, "search", usage={"cost_micros": 33, "currency": "USD"})
    ledger = store.request("task-1", "accounting")["totals"]
    assert ledger["total_tokens"] == 45 and ledger["cost_micros"] == 93
    assert ledger["model_calls"] == 3 and ledger["tool_calls"] == 1
    error("BUDGET_EXHAUSTED", lambda: reserve(store, "fourth"))


def test_unknown_price_and_currency_and_bound_violation(tmp_path):
    store, _, _ = setup(tmp_path, {"max_output_tokens": 10}, {**MODEL, "pricing": None})
    reserve(store)
    row = settle(store, usage={"input_tokens": 10, "output_tokens": 5})
    assert row["settlement"]["usage"]["reason"] == "unknown_price"
    assert store.request("task-1", "accounting")["totals"]["cost_micros"] is None
    reserve(store, "currency")
    row = settle(store, "currency", usage={"cost_micros": 20, "currency": "EUR"})
    assert row["settlement"]["usage"]["cost_status"] == "currency_mismatch"
    reserve(store, "exceeded")
    settle(store, "exceeded", usage={"total_tokens": 100})
    assert store.request("task-1", "view")["reason"] == "BOUND_EXCEEDED"


def test_model_configuration_and_budget_cannot_change_on_restart(tmp_path):
    store, fixture, policy = setup(tmp_path)
    for kwargs in ({"model": {**MODEL, "model_id": "switched"}}, {"model": MODEL, "budget": {"max_model_calls": 999}}):
        error("DUPLICATE_CONFLICT", lambda: store.register("task-1", fixture["candidate"], policy, **kwargs))


def test_explicit_reconciliation_releases_only_unknown_reservations(tmp_path):
    store, _, _ = setup(tmp_path, {"max_total_tokens": 20, "max_output_tokens": 10})
    reserve(store)
    settle(store, outcome="failed")
    store.request("task-1", "recover")
    assert store.request("task-1", "accounting")["totals"]["held_tokens"] == 20
    args = {"call_id": "call-1", "identity": ROOT, "outcome": "failed", "usage": {"input_tokens": 4, "output_tokens": 2}}
    receipt = store.request("task-1", "reconcile", **args)
    assert store.request("task-1", "reconcile", **args) == receipt
    total = store.request("task-1", "accounting")["totals"]
    assert total["held_tokens"] == 0 and total["total_tokens"] == 6 and total["cost_micros"] == 8
    assert store.request("task-1", "view")["status"] == "blocked"
    error("DUPLICATE_CONFLICT", lambda: store.request("task-1", "reconcile", **{**args, "usage": {"input_tokens": 0, "output_tokens": 0}}))


def test_paged_events_over_ui_limit_human_time_and_idempotency(tmp_path):
    store, _, _ = setup(tmp_path)
    for index in range(1005):
        event = {"event_id": str(index), "role": "researcher", "kind": "denied", "error_code": "ACCESS_DENIED"}
        receipt = store.request("task-1", "event", event=event)
    assert store.request("task-1", "event", event=event) == receipt
    error("DUPLICATE_CONFLICT", lambda: store.request("task-1", "event", event={**event, "error_code": "INVALID_STATE"}))
    for identifier, kind, active in (("human-1", "approval", None), ("human-2", "manual_repair", 1200), ("human-3", "rerun", None)):
        store.request("task-1", "human", event_id=identifier, kind=kind, active_time_ms=active, identity=ROOT)
    events, after = [], 0
    while page := store.request("task-1", "events", after=after, limit=200):
        events.extend(page)
        after = page[-1]["sequence"]
    assert len(events) == 1009  # begin + 1005 durable events + three interventions
    assert events[-3]["active_time_ms"] is None and events[-2]["active_time_ms"] == 1200
    assert all(event["root_run_id"] and event["budget_id"] and event["candidate"] for event in events)


def test_call_limits_and_root_anchored_deadline(tmp_path):
    store, fixture, _ = setup(tmp_path, {"max_model_calls": 1, "wall_time_ms": 5000})
    state = store.request("task-1", "view")
    child = store.request("task-1", "start", delegation=fixture["delegation"])
    assert child["budget_id"] == "budget-1"
    assert store.request("task-1", "view")["deadline"] == state["deadline"]
    reserve(store)
    settle(store, outcome="failed")
    error("BUDGET_EXHAUSTED", lambda: reserve(store, "retry", purpose="retry"))


def test_concurrent_store_initialization_is_serialized(tmp_path):
    prepare(tmp_path)
    with ThreadPoolExecutor(2) as executor:
        stores = list(executor.map(lambda _: ControlStore(tmp_path / "control", tmp_path / "evidence"), range(2)))
    assert stores[0].path == stores[1].path


def test_queue_time_does_not_reset_an_expired_absolute_deadline(tmp_path):
    fixture = prepare(tmp_path, lifetime_seconds=-1)
    store = ControlStore(tmp_path / "control", tmp_path / "evidence")
    store.register("task-1", fixture["candidate"], {"scopes": [], "operations": []}, model=MODEL)
    error("INVALID_STATE", lambda: store.request("task-1", "begin"))
    assert store.request("task-1", "view")["status"] == "timed_out"
    ledger = store.request("task-1", "accounting")
    assert ledger["started_at"] is None and ledger["totals"]["model_calls"] == 0
