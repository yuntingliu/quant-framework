"""Execution facade with protocol-first paper trading."""

from alphalab.execution.broker import (
    BrokerAccount,
    BrokerContract,
    BrokerOrder,
    BrokerOrderIntent,
    BrokerPosition,
    BrokerProtocol,
    BrokerQuote,
)
from alphalab.execution.order import Order, OrderSide, OrderStatus, OrderType
from alphalab.execution.order_logger import OrderLogger
from alphalab.execution.paper_trader import PaperTrader, Position, Trade
from alphalab.execution.risk_guard import RiskGuard, RiskGuardConfig, RiskViolation
from alphalab.execution.signal_executor import ExecutionResult, SignalExecutor

__all__ = [
    "BrokerAccount",
    "BrokerContract",
    "BrokerOrder",
    "BrokerOrderIntent",
    "BrokerPosition",
    "BrokerProtocol",
    "BrokerQuote",
    "ExecutionResult",
    "Order",
    "OrderLogger",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "PaperTrader",
    "Position",
    "RiskGuard",
    "RiskGuardConfig",
    "RiskViolation",
    "SignalExecutor",
    "Trade",
]
