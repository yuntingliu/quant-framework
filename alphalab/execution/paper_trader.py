"""Simple paper trader."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass
class Trade:
    timestamp: datetime
    symbol: str
    side: str
    quantity: float
    price: float
    commission: float = 0.0

    @property
    def value(self) -> float:
        return self.quantity * self.price


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_cost: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price


class PaperTrader:
    """Paper account that can rebalance to target weights."""

    def __init__(self, initial_capital: float = 1_000_000.0, commission_rate: float = 0.0003):
        self.initial_capital = float(initial_capital)
        self.cash = float(initial_capital)
        self.commission_rate = float(commission_rate)
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []

    @property
    def total_value(self) -> float:
        return self.cash + sum(position.market_value for position in self.positions.values())

    def update_prices(self, prices: dict[str, float]) -> None:
        for symbol, price in prices.items():
            key = str(symbol).upper()
            if key in self.positions:
                self.positions[key].current_price = float(price)

    def rebalance(self, target_weights: dict[str, float], prices: dict[str, float]) -> list[Trade]:
        self.update_prices(prices)
        total = self.total_value
        trades: list[Trade] = []
        target_quantity: dict[str, float] = {}
        for symbol, weight in target_weights.items():
            key = str(symbol).upper()
            price = float(prices[key])
            target_quantity[key] = (total * float(weight)) / price if price > 0 else 0.0

        for symbol, position in list(self.positions.items()):
            delta = target_quantity.get(symbol, 0.0) - position.quantity
            if delta < -1e-9:
                trades.append(self._trade(symbol, "sell", -delta, float(prices[symbol])))
        for symbol, quantity in target_quantity.items():
            current = self.positions.get(symbol)
            delta = quantity - (current.quantity if current else 0.0)
            if delta > 1e-9:
                trades.append(self._trade(symbol, "buy", delta, float(prices[symbol])))
        return trades

    def _trade(self, symbol: str, side: str, quantity: float, price: float) -> Trade:
        value = quantity * price
        commission = value * self.commission_rate
        if side == "buy":
            self.cash -= value + commission
            old = self.positions.get(symbol)
            old_qty = old.quantity if old else 0.0
            old_cost = old.avg_cost * old_qty if old else 0.0
            new_qty = old_qty + quantity
            self.positions[symbol] = Position(symbol, new_qty, (old_cost + value) / new_qty, price)
        else:
            self.cash += value - commission
            old = self.positions[symbol]
            remaining = old.quantity - quantity
            if remaining <= 1e-9:
                del self.positions[symbol]
            else:
                old.quantity = remaining
                old.current_price = price
        trade = Trade(datetime.now(), symbol, side, quantity, price, commission)
        self.trades.append(trade)
        return trade

    def get_positions_df(self) -> pd.DataFrame:
        return pd.DataFrame([position.__dict__ for position in self.positions.values()])

