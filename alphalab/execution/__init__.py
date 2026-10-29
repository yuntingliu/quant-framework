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
from alphalab.execution.risk_guard import (
    AccountRiskMetrics,
    AccountSnapshot,
    PositionSnapshot,
    ProjectedAccount,
    RiskFinding,
    RiskVerdict,
    account_risk_metrics,
    evaluate,
    pause_breaches,
    resume_decision,
)
from alphalab.execution.risk_policy import (
    PauseRules,
    RiskLimits,
    RiskPolicy,
    RiskPolicyError,
    default_policy,
    policy_from_payload,
)
from alphalab.execution.signal_executor import ExecutionResult, SignalExecutor

__all__ = [
    "AccountRiskMetrics",
    "AccountSnapshot",
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
    "PauseRules",
    "Position",
    "PositionSnapshot",
    "ProjectedAccount",
    "RiskFinding",
    "RiskLimits",
    "RiskPolicy",
    "RiskPolicyError",
    "RiskVerdict",
    "SignalExecutor",
    "Trade",
    "account_risk_metrics",
    "default_policy",
    "evaluate",
    "pause_breaches",
    "policy_from_payload",
    "resume_decision",
]
