"""Order primitives."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Order:
    symbol: str
    side: str
    quantity: float
    price: float | None = None
    status: str = "pending"
    created_at: datetime = datetime.now()


class OrderSide:
    BUY = "buy"
    SELL = "sell"


class OrderType:
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus:
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

