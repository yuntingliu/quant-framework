"""Walk the acceptance criteria of issue #20 and print one verdict each.

This is the reviewer's one-command check: it needs no network, no credentials and
no test framework. It runs against a throwaway database under ``build/`` so it
never touches a real paper account, and exits non-zero if any check fails.

Every scenario gets its own paper account. A shared account would make the checks
depend on each other -- a rebalance executed by one scenario leaves the next one
with nothing to propose.

    python scripts/check_paper_risk.py
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STATE = ROOT / "build" / "check-paper-risk"
if STATE.exists():
    shutil.rmtree(STATE, ignore_errors=True)
for name, folder in (
    ("ALPHALAB_APP_DATA_DIR", "app"),
    ("ALPHALAB_CACHE_DIR", "cache"),
    ("ALPHALAB_RUNTIME_DIR", "runtime"),
):
    os.environ[name] = str(STATE / folder)

from alphalab import ResultStore, create_default_engine  # noqa: E402
from apps.api.services import result_service  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(label: str, passed: bool, detail: str) -> None:
    RESULTS.append((label, passed, detail))
    mark = "PASS" if passed else "FAIL"
    print(f"  [{mark}] {label}: {detail}", flush=True)


def section(number: int, text: str) -> None:
    print(f"\n=== {number}. {text} ===", flush=True)


def prepare() -> dict:
    """Fresh databases plus one signal every rebalance scenario can use."""
    engine = create_default_engine()
    symbols = engine.get_symbols()[:3]
    latest = engine.get_latest_date()
    bars = engine.get_bars(symbols, latest, latest, fields=["close"])
    prices = {row["symbol"]: float(row["close"]) for row in bars.to_dict("records")}

    store = ResultStore()
    store.ensure_backtest_subject("sdk-v1-default", "projects/sdk-v1-default", "check")
    signal_id = store.save_signal(
        "sdk-v1-default",
        latest,
        {symbol: 0.05 for symbol in symbols},
        status="pending",
        profile="demo",
    )
    store.close()
    return {"symbols": symbols, "latest": latest, "prices": prices, "signal_id": signal_id}


def preview(context: dict, account: str) -> dict:
    return result_service.preview_paper_rebalance(
        signal_id=context["signal_id"], profile="demo", account_id=account
    )


def execute(context: dict, account: str, preview_id: str) -> dict:
    return result_service.execute_paper_rebalance(
        signal_id=context["signal_id"],
        profile="demo",
        account_id=account,
        confirm=True,
        preview_id=preview_id,
    )


def criterion_direct_call(context: dict) -> None:
    section(1, "A direct API call cannot bypass the hard rules")
    account = "check-direct"
    store = ResultStore()
    before = store.paper_account(account)["cash"]
    store.close()

    refused = False
    detail = "the order was accepted, which is a failure"
    try:
        result_service.create_paper_order(
            context["symbols"][0], "buy", 200_000, None, None, "demo", account
        )
    except result_service.RiskBlockedError as exc:
        refused = True
        rules = ", ".join(item["rule"] for item in exc.detail["blocking"])
        detail = f"refused by {rules}"

    store = ResultStore()
    after = store.paper_account(account)["cash"]
    orders = len(store.list_orders(limit=10, account_id=account))
    store.close()
    record("direct order refused", refused, detail)
    record(
        "account untouched by the refusal",
        before == after and orders == 0,
        f"cash {before:,.2f} -> {after:,.2f}, orders {orders}",
    )


def criterion_stale_preview(context: dict) -> None:
    section(2, "A stale approval is refused instead of executed")
    account = "check-stale"
    judged = preview(context, account)
    record(
        "preview judged the proposal",
        bool(judged["allowed"]),
        f"{len(judged['orders'])} orders, policy {judged['policy']['label']}",
    )
    execute(context, account, judged["preview_id"])

    refused = False
    detail = "the replay was accepted, which is a failure"
    try:
        execute(context, account, judged["preview_id"])
    except result_service.StalePreviewError as exc:
        refused = True
        detail = str(exc)[:110]
    record("replayed approval refused", refused, detail)


def criterion_concurrency(context: dict) -> None:
    section(3, "Two concurrent executions of one approval fill once")
    account = "check-race"
    judged = preview(context, account)
    if not judged["allowed"] or not judged["orders"]:
        record("a proposal exists to race on", False, f"allowed={judged['allowed']}, orders={len(judged['orders'])}")
        return
    record("a proposal exists to race on", True, f"{len(judged['orders'])} orders approved")

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def run() -> None:
        barrier.wait(timeout=30)
        try:
            execute(context, account, judged["preview_id"])
            outcome = "filled"
        except Exception:  # noqa: BLE001 - the loser is expected to raise
            outcome = "refused"
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    record(
        "exactly one execution won",
        outcomes.count("filled") == 1 and outcomes.count("refused") == 1,
        f"{outcomes.count('filled')} filled, {outcomes.count('refused')} refused",
    )

    store = ResultStore()
    orders = store.list_orders(limit=50, account_id=account)
    store.close()
    record(
        "no duplicate fills",
        len(orders) == len(judged["orders"]),
        f"{len(orders)} orders for {len(judged['orders'])} approved",
    )


def criterion_reproducible_risk(context: dict) -> None:
    section(4, "Stress analysis replays from frozen inputs, gaps stay explicit")
    account = "check-stale"  # holds the positions the rebalance just bought
    analysis = result_service.paper_risk_analysis(account_id=account, profile="demo")
    replay = result_service.paper_risk_analysis(account_id=account, profile="demo")
    record(
        "same inputs give the same numbers",
        replay["input_hash"] == analysis["input_hash"]
        and replay["contributions"] == analysis["contributions"],
        f"hash {analysis['input_hash'][:12]}, replay served from store: {replay['cached']}",
    )
    volatility = analysis["portfolio"]["annualized_volatility"]
    total = sum(item["contribution_to_volatility"] for item in analysis["contributions"])
    record(
        "contributions sum to portfolio volatility",
        volatility is not None and abs(total - volatility) < 1e-9,
        f"{total:.6f} vs {volatility:.6f}" if volatility else "not evaluated",
    )
    record(
        "the result carries its assumptions",
        bool(analysis["assumptions"]) and bool(analysis["disclaimer"]),
        f"window {analysis['assumptions']['lookback_days']} days, {analysis['assumptions']['frequency']}",
    )

    empty = result_service.paper_risk_analysis(account_id="check-empty", profile="demo")
    stable = (
        empty["portfolio"]["evaluated"] is False
        and empty["portfolio"]["annualized_volatility"] is None
        and empty["concentration"]["effective_positions"] is None
        and empty["shocks"]["price_shock"]["evaluated"] is False
        and bool(empty["warnings"])
    )
    record("an empty account reports gaps, not zeros", stable, "null numbers plus a reason")


def criterion_versions_and_pause(context: dict) -> None:
    section(5, "Versions are preserved and the pause round-trip works")
    account = "check-stale"
    published = result_service.publish_paper_risk_policy(
        {
            "reason": "Verification run: halve the order cap",
            "author": "check-script",
            "limits": {"max_order_value": 50_000.0},
        },
        account_id=account,
    )
    versions = [item["version"] for item in published["versions"]]
    first_reason = published["versions"][0]["reason"]
    record(
        "a new version is appended, not rewritten",
        versions == [1, 2] and first_reason.startswith("Baseline"),
        f"versions {versions}, v1 reason kept",
    )

    store = ResultStore()
    store.pause_paper_account(account, "drawdown 20.00% reached the 15.00% limit")
    store.close()
    paused_refusal = False
    try:
        result_service.create_paper_order(context["symbols"][0], "buy", 100, None, None, "demo", account)
    except result_service.RiskBlockedError as exc:
        paused_refusal = any(item["rule"] == "account_paused" for item in exc.detail["blocking"])
    record("a paused account refuses orders", paused_refusal, "blocked by account_paused")

    reason_required = False
    try:
        result_service.resume_paper_risk(account, "   ")
    except ValueError:
        reason_required = True
    record("resuming requires a reason", reason_required, "empty reason rejected")

    result_service.resume_paper_risk(account, "Verified by the check script")
    resumed = result_service.create_paper_order(
        context["symbols"][0], "buy", 100, None, None, "demo", account
    )
    record("an explicit resume restores trading", bool(resumed.get("id")), f"order {resumed.get('id')}")


CRITERIA = (
    ("direct call behind every door", criterion_direct_call),
    ("stale approvals", criterion_stale_preview),
    ("concurrent execution", criterion_concurrency),
    ("reproducible risk", criterion_reproducible_risk),
    ("versions and pause", criterion_versions_and_pause),
)


def main() -> int:
    print("AlphaLab paper risk: acceptance checks for issue #20")
    context = prepare()
    for label, criterion in CRITERIA:
        try:
            criterion(context)
        except Exception as exc:  # noqa: BLE001 - one broken check must not hide the rest
            record(f"criterion crashed: {label}", False, f"{type(exc).__name__}: {exc}")
            traceback.print_exc()

    failures = [item for item in RESULTS if not item[1]]
    print(f"\n{len(RESULTS) - len(failures)} of {len(RESULTS)} checks passed")
    if failures:
        for label, _, detail in failures:
            print(f"  FAILED: {label} ({detail})")
        return 1
    print("All acceptance checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
