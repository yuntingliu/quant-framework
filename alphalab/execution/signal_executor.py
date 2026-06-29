"""Signal-to-paper-execution helper."""
from __future__ import annotations

from dataclasses import dataclass

from alphalab.execution.paper_trader import PaperTrader, Trade


@dataclass
class ExecutionResult:
    trades: list[Trade]
    status: str = "ok"


class SignalExecutor:
    """Execute target weights against a PaperTrader."""

    def __init__(self, trader: PaperTrader | None = None):
        self.trader = trader or PaperTrader()

    def execute(self, targets: dict[str, float], prices: dict[str, float]) -> ExecutionResult:
        return ExecutionResult(trades=self.trader.rebalance(targets, prices))

