"""One risk evaluation for every paper-trading entry point.

The manual order door, the rebalance preview and the rebalance execution all call
:func:`evaluate`. The evaluator takes a frozen :class:`AccountSnapshot`, applies
the proposed orders to a copy of it, and reports what is blocking, what is only a
warning, and what is a research-quality observation.

Two properties are deliberate:

* **The verdict binds to the state it judged.** :attr:`RiskVerdict.preview_id`
  hashes the orders, the account snapshot and the policy version. A later
  execution must present the same hash, so an approval that has gone stale --
  because prices, cash, positions or the rules changed -- cannot be replayed.
* **A failure never projects.** The evaluator only *describes* the resulting
  account; it never writes. The store applies orders in one transaction
  afterwards, so a refused check leaves the account exactly as it was.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from alphalab.execution.risk_policy import (
    SEVERITY_BLOCKING,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    RiskPolicy,
)

COMMISSION_RATE = 0.0003
BOARD_LOT = 100.0
_EPSILON = 1e-9
_DISCLAIMER = "Paper simulation only; no broker order is created."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Any, digits: int = 6) -> float:
    number = _finite(value)
    return round(number, digits) if number is not None else 0.0


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    quantity: float
    avg_cost: float
    price: float | None
    market_value: float | None

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_cost": self.avg_cost,
            "price": self.price,
            "market_value": self.market_value,
        }


@dataclass(frozen=True)
class AccountSnapshot:
    """The account exactly as it was when a check ran."""

    account_id: str
    cash: float
    initial_cash: float
    equity: float
    positions: tuple[PositionSnapshot, ...] = ()
    prices: Mapping[str, float] = field(default_factory=dict)
    volumes: Mapping[str, float] = field(default_factory=dict)
    nav: tuple[tuple[str, float], ...] = ()
    price_date: str | None = None
    status: str = "active"
    pause_reason: str | None = None
    paused_at: str | None = None
    policy_id: str = ""
    policy_version: int = 1
    taken_at: str = field(default_factory=_now)

    def position(self, symbol: str) -> PositionSnapshot | None:
        for item in self.positions:
            if item.symbol == symbol:
                return item
        return None

    def quantity(self, symbol: str) -> float:
        item = self.position(symbol)
        return float(item.quantity) if item is not None else 0.0

    def price(self, symbol: str) -> float | None:
        value = _finite(self.prices.get(symbol))
        if value is not None:
            return value
        item = self.position(symbol)
        if item is not None and item.price is not None and math.isfinite(item.price):
            return float(item.price)
        return None

    @property
    def market_value(self) -> float:
        return float(sum(item.market_value or 0.0 for item in self.positions))

    @property
    def gross_exposure(self) -> float:
        if not math.isfinite(self.equity) or self.equity <= 0:
            return 0.0
        return self.market_value / self.equity

    def as_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "taken_at": self.taken_at,
            "price_date": self.price_date,
            "cash": self.cash,
            "initial_cash": self.initial_cash,
            "equity": self.equity,
            "status": self.status,
            "pause_reason": self.pause_reason,
            "paused_at": self.paused_at,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "positions": [item.as_dict() for item in self.positions],
            "prices": dict(self.prices),
            "volumes": dict(self.volumes),
            "nav": [list(item) for item in self.nav],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AccountSnapshot:
        positions = tuple(
            PositionSnapshot(
                symbol=str(item["symbol"]),
                quantity=float(item["quantity"]),
                avg_cost=float(item.get("avg_cost") or 0.0),
                price=_finite(item.get("price")),
                market_value=_finite(item.get("market_value")),
            )
            for item in payload.get("positions", [])
        )
        return cls(
            account_id=str(payload["account_id"]),
            cash=float(payload["cash"]),
            initial_cash=float(payload.get("initial_cash") or payload["cash"]),
            equity=float(payload["equity"]),
            positions=positions,
            prices={str(key): float(value) for key, value in (payload.get("prices") or {}).items()},
            volumes={
                str(key): float(value) for key, value in (payload.get("volumes") or {}).items()
            },
            nav=tuple(
                (str(item[0]), float(item[1])) for item in payload.get("nav") or ()
            ),
            price_date=payload.get("price_date"),
            status=str(payload.get("status") or "active"),
            pause_reason=payload.get("pause_reason"),
            paused_at=payload.get("paused_at"),
            policy_id=str(payload.get("policy_id") or ""),
            policy_version=int(payload.get("policy_version") or 1),
            taken_at=str(payload.get("taken_at") or _now()),
        )

    def fingerprint(self) -> str:
        """A stable hash of the facts a verdict depends on (never the clock)."""
        body = {
            "account_id": self.account_id,
            "price_date": self.price_date,
            "cash": _round(self.cash, 4),
            "equity": _round(self.equity, 4),
            "status": self.status,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "positions": sorted(
                (
                    item.symbol,
                    _round(item.quantity, 4),
                    _round(item.price, 4),
                )
                for item in self.positions
            ),
            "prices": {key: _round(value, 4) for key, value in sorted(self.prices.items())},
        }
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def snapshot_record(self) -> dict:
        """The row shape for ``risk_snapshots`` (hashed on the same facts)."""
        return {
            "account_id": self.account_id,
            "taken_at": self.taken_at,
            "price_date": self.price_date,
            "cash": self.cash,
            "equity": self.equity,
            "gross_exposure": self.gross_exposure,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "status": self.status,
            "positions": [item.as_dict() for item in self.positions],
            "prices": dict(self.prices),
            "sha256": self.fingerprint(),
        }


@dataclass(frozen=True)
class RiskFinding:
    rule: str
    severity: str
    message: str
    observed: float | None = None
    limit: float | None = None
    symbol: str | None = None

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "observed": self.observed,
            "limit": self.limit,
            "symbol": self.symbol,
        }


@dataclass(frozen=True)
class ProjectedAccount:
    """What the account would look like if these orders executed."""

    applied: bool
    cash: float = 0.0
    equity: float = 0.0
    market_value: float = 0.0
    gross_exposure: float = 0.0
    turnover: float = 0.0
    cash_weight: float = 0.0
    positions: tuple[tuple[str, float], ...] = ()

    def quantity(self, symbol: str) -> float:
        for name, quantity in self.positions:
            if name == symbol:
                return quantity
        return 0.0

    def as_dict(self) -> dict:
        return {
            "applied": self.applied,
            "cash": self.cash,
            "equity": self.equity,
            "market_value": self.market_value,
            "gross_exposure": self.gross_exposure,
            "turnover": self.turnover,
            "cash_weight": self.cash_weight,
            "positions": [
                {"symbol": symbol, "quantity": quantity} for symbol, quantity in self.positions
            ],
        }


@dataclass(frozen=True)
class RiskVerdict:
    allowed: bool
    findings: tuple[RiskFinding, ...]
    checks: tuple[dict, ...]
    projected: ProjectedAccount
    preview_id: str
    policy: RiskPolicy
    snapshot: AccountSnapshot
    door: str
    metrics: "AccountRiskMetrics"

    @property
    def blocking(self) -> list[RiskFinding]:
        return [item for item in self.findings if item.severity == SEVERITY_BLOCKING]

    @property
    def warnings(self) -> list[RiskFinding]:
        return [item for item in self.findings if item.severity == SEVERITY_WARNING]

    @property
    def info(self) -> list[RiskFinding]:
        return [item for item in self.findings if item.severity == SEVERITY_INFO]

    @property
    def status(self) -> str:
        return "ready" if self.allowed else "blocked"

    def summary(self) -> str:
        if self.allowed:
            return f"All blocking rules passed under {self.policy.label}"
        first = self.blocking[0]
        return f"Blocked by {len(self.blocking)} rule(s); first: {first.message}"

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "preview_id": self.preview_id,
            "door": self.door,
            "policy": self.policy.to_json(),
            "snapshot": self.snapshot.as_dict(),
            "projected": self.projected.as_dict(),
            "metrics": self.metrics.as_dict(),
            "checks": list(self.checks),
            "findings": [item.as_dict() for item in self.findings],
            "blocking": [item.as_dict() for item in self.blocking],
            "warnings": [item.as_dict() for item in self.warnings],
            "info": [item.as_dict() for item in self.info],
            "summary": self.summary(),
            "disclaimer": _DISCLAIMER,
        }


def _normalize_orders(orders: Iterable[Mapping[str, Any]]) -> list[dict]:
    normalized: list[dict] = []
    for item in orders:
        symbol = str(item.get("symbol") or "").strip().upper()
        action = str(item.get("action") or "").strip().lower()
        normalized.append(
            {
                "symbol": symbol,
                "action": action,
                "quantity": item.get("quantity"),
                "price": item.get("price"),
                "target_weight": item.get("target_weight"),
            }
        )
    return normalized


class _Report:
    """Collects rule outcomes once, then exposes checks and findings."""

    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.findings: list[RiskFinding] = []

    def rule(
        self,
        name: str,
        passed: bool,
        detail: str,
        *,
        severity: str = SEVERITY_BLOCKING,
        message: str | None = None,
        observed: float | None = None,
        limit: float | None = None,
        symbol: str | None = None,
    ) -> None:
        self.checks.append(
            {"name": name, "passed": bool(passed), "detail": detail, "severity": severity}
        )
        if not passed:
            self.findings.append(
                RiskFinding(
                    rule=name,
                    severity=severity,
                    message=message or detail,
                    observed=observed,
                    limit=limit,
                    symbol=symbol,
                )
            )

    def note(
        self,
        name: str,
        severity: str,
        detail: str,
        *,
        observed: float | None = None,
        limit: float | None = None,
        symbol: str | None = None,
        check: bool = False,
    ) -> None:
        self.findings.append(
            RiskFinding(
                rule=name,
                severity=severity,
                message=detail,
                observed=observed,
                limit=limit,
                symbol=symbol,
            )
        )
        if check:
            self.checks.append(
                {"name": name, "passed": True, "detail": detail, "severity": severity}
            )

    def blocking(self) -> bool:
        return any(item.severity == SEVERITY_BLOCKING for item in self.findings)


def _project(
    snapshot: AccountSnapshot, orders: list[dict], prices: dict[str, float]
) -> ProjectedAccount:
    quantities = {item.symbol: float(item.quantity) for item in snapshot.positions}
    cash = float(snapshot.cash)
    notional_total = 0.0
    for item in sorted(orders, key=lambda order: 0 if order["action"] == "sell" else 1):
        symbol = item["symbol"]
        quantity = float(item["quantity"])
        price = float(prices[symbol])
        value = quantity * price
        commission = value * COMMISSION_RATE
        notional_total += value
        if item["action"] == "sell":
            cash += value - commission
            quantities[symbol] = quantities.get(symbol, 0.0) - quantity
        else:
            cash -= value + commission
            quantities[symbol] = quantities.get(symbol, 0.0) + quantity

    market_value = 0.0
    positions: list[tuple[str, float]] = []
    for symbol, quantity in sorted(quantities.items()):
        if abs(quantity) <= _EPSILON:
            continue
        price = prices.get(symbol) or snapshot.price(symbol) or 0.0
        market_value += abs(quantity) * float(price)
        positions.append((symbol, quantity))
    equity = cash + market_value
    return ProjectedAccount(
        applied=True,
        cash=cash,
        equity=equity,
        market_value=market_value,
        gross_exposure=(market_value / equity) if equity > 0 else 0.0,
        turnover=(notional_total / snapshot.equity) if snapshot.equity > 0 else 0.0,
        cash_weight=(cash / equity) if equity > 0 else 0.0,
        positions=tuple(positions),
    )


def _order_notional(item: dict, price: float) -> float:
    return abs(float(item["quantity"])) * float(price)


def evaluate(
    policy: RiskPolicy,
    snapshot: AccountSnapshot,
    orders: Iterable[Mapping[str, Any]],
    *,
    door: str = "unknown",
) -> RiskVerdict:
    """Judge proposed orders against one policy version and one account snapshot."""
    report = _Report()
    normalized = _normalize_orders(orders)
    metrics = account_risk_metrics(snapshot)

    report.rule(
        "policy",
        True,
        f"{policy.label}: {policy.reason}",
        severity=SEVERITY_INFO,
    )
    if not math.isfinite(snapshot.equity) or snapshot.equity < 0:
        report.rule(
            "account_state",
            False,
            f"account equity {snapshot.equity!r} is not a usable number",
            message="Account equity is not a usable number; refusing to trade on it.",
        )
    if snapshot.status == "paused":
        resume = policy.pause.describe()
        report.rule(
            "account_paused",
            False,
            snapshot.pause_reason or "account paused by a loss rule",
            message=(
                f"Account is paused ({snapshot.pause_reason or 'loss limit reached'}). "
                f"Resume condition: {resume}."
            ),
        )
    for reason in pause_breaches(policy, metrics):
        report.rule(
            "pause_trigger",
            False,
            reason,
            message=(
                f"The loss rule pauses this account: {reason}. Trading stays refused until "
                f"the resume condition is met ({policy.pause.describe()})."
            ),
        )

    if not normalized:
        report.rule(
            "orders_present",
            False,
            "no orders proposed",
            message="Nothing to execute: the proposal contains no orders.",
        )

    prices: dict[str, float] = {}
    usable: list[dict] = []
    missing_prices: list[str] = []
    for index, item in enumerate(normalized):
        symbol = item["symbol"] or f"#{index + 1}"
        quantity = _finite(item["quantity"])
        price = _finite(item["price"])
        if not item["symbol"]:
            report.rule("order_values", False, f"order {index + 1} has no symbol")
            continue
        if item["action"] not in {"buy", "sell"}:
            report.rule(
                "order_values",
                False,
                f"{symbol}: action {item['action']!r} is not buy or sell",
                message=f"{symbol}: action must be buy or sell, got {item['action']!r}.",
            )
            continue
        if quantity is None or quantity <= 0:
            report.rule(
                "order_values",
                False,
                f"{symbol}: quantity {item['quantity']!r}",
                message=f"{symbol}: quantity must be a finite number above zero.",
                symbol=symbol,
            )
            continue
        if price is None or price <= 0:
            market_price = snapshot.price(item["symbol"])
            if market_price is None or market_price <= 0:
                missing_prices.append(item["symbol"])
                continue
            price = market_price
        prices[item["symbol"]] = price
        usable.append(
            {
                "symbol": item["symbol"],
                "action": item["action"],
                "quantity": quantity,
                "price": price,
                "target_weight": item.get("target_weight"),
            }
        )

    symbols = [item["symbol"] for item in usable]
    duplicates = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
    report.rule(
        "duplicate_symbols",
        not duplicates,
        "unique" if not duplicates else f"duplicated: {', '.join(duplicates)}",
        message=f"One symbol appears more than once in the proposal: {', '.join(duplicates)}.",
    )

    report.rule(
        "price_coverage",
        not missing_prices,
        "complete" if not missing_prices else f"missing {', '.join(sorted(set(missing_prices)))}",
        message=(
            "No usable market price for "
            + ", ".join(sorted(set(missing_prices)))
            + ", so those orders cannot be valued."
        ),
    )

    board_lot_failures = [
        item["symbol"]
        for item in usable
        if abs(item["quantity"] / BOARD_LOT - round(item["quantity"] / BOARD_LOT)) > 1e-9
    ]
    report.rule(
        "board_lot",
        not board_lot_failures,
        "100-share lots" if not board_lot_failures else f"not in lots: {', '.join(board_lot_failures)}",
        message=(
            "Quantities must be whole 100-share board lots; not in lots: "
            + ", ".join(board_lot_failures)
            + "."
        ),
    )

    oversells = []
    for item in usable:
        held = snapshot.quantity(item["symbol"])
        if item["action"] == "sell" and item["quantity"] > held + _EPSILON:
            oversells.append(f"{item['symbol']} (sell {item['quantity']:g} > held {held:g})")
    report.rule(
        "position_available",
        not oversells,
        "within holdings" if not oversells else "; ".join(oversells),
        message="Cannot sell more than the account holds: " + "; ".join(oversells) + ".",
    )

    if not usable:
        return _finish(report, snapshot, policy, ProjectedAccount(applied=False), door, metrics)

    projected = _project(snapshot, usable, prices)

    max_value = 0.0
    max_weight = 0.0
    value_breaches: list[str] = []
    weight_breaches: list[str] = []
    for item in usable:
        symbol = item["symbol"]
        notional = _order_notional(item, item["price"])
        max_value = max(max_value, notional)
        if notional > policy.limits.max_order_value + _EPSILON:
            value_breaches.append(f"{symbol} at {notional:,.2f}")
        weight = notional / snapshot.equity if snapshot.equity > 0 else 0.0
        max_weight = max(max_weight, weight)
        if weight > policy.limits.max_order_weight + 1e-9:
            weight_breaches.append(f"{symbol} at {weight:.2%}")
    report.rule(
        "max_order_value",
        not value_breaches,
        f"largest {max_value:,.2f} / {policy.limits.max_order_value:,.2f}",
        message=(
            "Order value above the per-order limit "
            f"{policy.limits.max_order_value:,.2f}: " + "; ".join(value_breaches) + "."
        ),
        observed=max_value,
        limit=policy.limits.max_order_value,
    )
    report.rule(
        "max_order_weight",
        not weight_breaches,
        f"largest {max_weight:.2%} / {policy.limits.max_order_weight:.2%}",
        message=(
            "Order above the per-order weight limit "
            f"{policy.limits.max_order_weight:.2%} of equity: "
            + "; ".join(weight_breaches)
            + "."
        ),
        observed=max_weight,
        limit=policy.limits.max_order_weight,
    )

    weight_failures = []
    for symbol in sorted({item["symbol"] for item in usable}):
        price = prices.get(symbol) or snapshot.price(symbol) or 0.0
        value = projected.quantity(symbol) * price
        weight = value / projected.equity if projected.equity > 0 else 0.0
        if weight > policy.limits.max_position_weight + 1e-9:
            weight_failures.append(f"{symbol} {weight:.2%}")
    report.rule(
        "max_position_weight",
        not weight_failures,
        "within concentration limit" if not weight_failures else "; ".join(weight_failures),
        message=(
            "Resulting position above the concentration limit "
            f"{policy.limits.max_position_weight:.2%}: " + "; ".join(weight_failures) + "."
        ),
    )

    over_gross = projected.gross_exposure > policy.limits.max_gross_exposure + 1e-9
    report.rule(
        "max_gross_exposure",
        not over_gross,
        f"{projected.gross_exposure:.2%} / {policy.limits.max_gross_exposure:.2%}",
        message=(
            f"Resulting gross exposure {projected.gross_exposure:.2%} exceeds the limit "
            f"{policy.limits.max_gross_exposure:.2%}."
        ),
        observed=projected.gross_exposure,
        limit=policy.limits.max_gross_exposure,
    )

    under_cash = projected.cash_weight < policy.limits.min_cash_weight - 1e-9
    report.rule(
        "min_cash_weight",
        not under_cash,
        f"{projected.cash_weight:.2%} / {policy.limits.min_cash_weight:.2%}",
        message=(
            f"Resulting cash {projected.cash_weight:.2%} of equity is below the minimum "
            f"{policy.limits.min_cash_weight:.2%}."
        ),
        observed=projected.cash_weight,
        limit=policy.limits.min_cash_weight,
    )

    negative_cash = projected.cash < -1e-6
    report.rule(
        "cash",
        not negative_cash,
        f"projected {projected.cash:,.2f}",
        message=(
            f"The orders need {-projected.cash:,.2f} more cash than the account has "
            f"(projected cash {projected.cash:,.2f})."
        ),
        observed=projected.cash,
    )

    over_turnover = projected.turnover > policy.limits.max_turnover + 1e-9
    report.rule(
        "max_turnover",
        not over_turnover,
        f"{projected.turnover:.2f}x / {policy.limits.max_turnover:.2f}x",
        message=(
            f"Turnover {projected.turnover:.2f}x equity exceeds the limit "
            f"{policy.limits.max_turnover:.2f}x."
        ),
        observed=projected.turnover,
        limit=policy.limits.max_turnover,
    )

    if policy.limits.max_participation is None:
        report.rule("max_participation", True, "disabled")
    else:
        unevaluated: list[str] = []
        breaches: list[str] = []
        for item in usable:
            volume = _finite(snapshot.volumes.get(item["symbol"]))
            if volume is None or volume <= 0:
                unevaluated.append(item["symbol"])
                continue
            share = item["quantity"] / volume
            if share > policy.limits.max_participation + 1e-9:
                breaches.append(f"{item['symbol']} {share:.2%}")
        report.rule(
            "max_participation",
            not breaches,
            "within volume" if not breaches else "; ".join(breaches),
            message=(
                "Order above the liquidity cap "
                f"{policy.limits.max_participation:.2%} of average daily volume: "
                + "; ".join(breaches)
                + "."
            ),
            limit=policy.limits.max_participation,
        )
        if unevaluated:
            report.note(
                "liquidity_unevaluated",
                SEVERITY_WARNING,
                "Liquidity not evaluated for "
                + ", ".join(sorted(set(unevaluated)))
                + ": no volume history is available for that symbol.",
                check=True,
            )

    if policy.limits.max_order_weight < 1.0:
        near = [
            item["symbol"]
            for item in usable
            if snapshot.equity > 0
            and _order_notional(item, item["price"]) / snapshot.equity
            >= 0.9 * policy.limits.max_order_weight
        ]
        if near:
            report.note(
                "near_order_limit",
                SEVERITY_WARNING,
                "Orders close to the per-order weight limit: " + ", ".join(sorted(set(near))),
                check=True,
            )

    holdings = [item for item in projected.positions if abs(item[1]) > _EPSILON]
    if len(holdings) <= 3 and holdings:
        report.note(
            "concentration_note",
            SEVERITY_INFO,
            f"The resulting portfolio holds {len(holdings)} position(s); "
            "concentrated portfolios amplify both gains and losses.",
            check=True,
        )

    if projected.equity > 0:
        report.note(
            "projection",
            SEVERITY_INFO,
            f"Projected equity {projected.equity:,.2f}, cash {projected.cash_weight:.2%}, "
            f"gross {projected.gross_exposure:.2%}, turnover {projected.turnover:.2f}x.",
            check=True,
        )

    return _finish(report, snapshot, policy, projected, door, metrics)


def _finish(
    report: _Report,
    snapshot: AccountSnapshot,
    policy: RiskPolicy,
    projected: ProjectedAccount,
    door: str,
    metrics: AccountRiskMetrics,
) -> RiskVerdict:
    findings = sorted(
        report.findings,
        key=lambda item: (0 if item.severity == SEVERITY_BLOCKING else 1 if item.severity == SEVERITY_WARNING else 2),
    )
    preview_id = preview_fingerprint(snapshot, policy, projected)
    return RiskVerdict(
        allowed=not report.blocking(),
        findings=tuple(findings),
        checks=tuple(report.checks),
        projected=projected,
        preview_id=preview_id,
        policy=policy,
        snapshot=snapshot,
        door=door,
        metrics=metrics,
    )


def preview_fingerprint(
    snapshot: AccountSnapshot, policy: RiskPolicy, projected: ProjectedAccount
) -> str:
    """Hash of everything an execution must still agree with.

    Deliberately excludes the door and the clock: the same proposal judged twice
    from two entry points must produce the same id, while any change in prices,
    cash, positions, orders or the policy version must produce a different one.
    """
    body = {
        "snapshot": snapshot.fingerprint(),
        "policy": [policy.policy_id, policy.version],
        "applied": projected.applied,
        "cash": _round(projected.cash, 4),
        "equity": _round(projected.equity, 4),
        "positions": sorted((symbol, _round(quantity, 4)) for symbol, quantity in projected.positions),
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


# -- loss and pause rules ---------------------------------------------------


@dataclass(frozen=True)
class AccountRiskMetrics:
    equity: float
    high_water: float
    drawdown: float
    daily_return: float | None
    observations: int

    def as_dict(self) -> dict:
        return {
            "equity": self.equity,
            "high_water": self.high_water,
            "drawdown": self.drawdown,
            "daily_return": self.daily_return,
            "observations": self.observations,
        }


def account_risk_metrics(snapshot: AccountSnapshot) -> AccountRiskMetrics:
    """Drawdown and last-day move from the stored NAV series.

    With no NAV history the drawdown is zero: an account that has never been
    valued cannot be described as being in drawdown. That is stated in the
    analysis assumptions rather than silently assumed.
    """
    equity = _finite(snapshot.equity)
    current = float(equity) if equity is not None else 0.0
    series = [(str(date), float(value)) for date, value in snapshot.nav if _finite(value) is not None]
    if not series:
        return AccountRiskMetrics(
            equity=current,
            high_water=current,
            drawdown=0.0,
            daily_return=None,
            observations=0,
        )
    values = [value for _, value in series]
    if current > 0:
        values.append(current)
    high_water = max(values) if values else current
    drawdown = 0.0 if high_water <= 0 else max(0.0, 1.0 - current / high_water)
    daily_return = None
    if len(series) >= 2 and series[-2][1] > 0:
        daily_return = series[-1][1] / series[-2][1] - 1.0
    return AccountRiskMetrics(
        equity=current,
        high_water=high_water,
        drawdown=drawdown,
        daily_return=daily_return,
        observations=len(series),
    )


def pause_breaches(policy: RiskPolicy, metrics: AccountRiskMetrics) -> list[str]:
    """Reasons the policy demands a pause right now (empty when none apply)."""
    reasons: list[str] = []
    daily = policy.pause.daily_loss_limit
    if daily is not None and metrics.daily_return is not None:
        if metrics.daily_return <= -daily - 1e-12:
            reasons.append(
                f"daily loss {abs(metrics.daily_return):.2%} reached the "
                f"{daily:.2%} limit"
            )
    drawdown = policy.pause.drawdown_limit
    if drawdown is not None and metrics.drawdown >= drawdown - 1e-12:
        reasons.append(
            f"drawdown {metrics.drawdown:.2%} reached the {drawdown:.2%} limit"
        )
    return reasons


def resume_decision(
    policy: RiskPolicy,
    metrics: AccountRiskMetrics,
    *,
    paused_at: str | None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Whether an automatic resume is allowed, and a readable reason either way."""
    rules = policy.pause
    if rules.requires_manual_resume:
        return False, "policy requires an explicit resume request"
    checks: list[str] = []
    if rules.resume_drawdown is not None:
        if metrics.drawdown > rules.resume_drawdown + 1e-12:
            return (
                False,
                f"drawdown {metrics.drawdown:.2%} is still above the resume level "
                f"{rules.resume_drawdown:.2%}",
            )
        checks.append(f"drawdown {metrics.drawdown:.2%} is below {rules.resume_drawdown:.2%}")
    if rules.resume_after_days is not None:
        if paused_at is None:
            return False, "pause start is unknown, so the waiting period cannot be verified"
        try:
            started = datetime.fromisoformat(str(paused_at))
        except ValueError:
            return False, f"pause start {paused_at!r} is not a readable date"
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed = ((now or datetime.now(timezone.utc)) - started).total_seconds() / 86400.0
        if elapsed < rules.resume_after_days:
            return (
                False,
                f"only {elapsed:.1f} day(s) since the pause; "
                f"{rules.resume_after_days} required",
            )
        checks.append(f"{elapsed:.1f} day(s) since the pause")
    return True, "; ".join(checks) if checks else "resume conditions met"


__all__ = [
    "AccountRiskMetrics",
    "AccountSnapshot",
    "COMMISSION_RATE",
    "BOARD_LOT",
    "PositionSnapshot",
    "ProjectedAccount",
    "RiskFinding",
    "RiskVerdict",
    "account_risk_metrics",
    "evaluate",
    "pause_breaches",
    "preview_fingerprint",
    "resume_decision",
]
