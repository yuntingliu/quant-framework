"""Broker-neutral execution contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class BrokerContract:
    symbol: str
    sec_type: str = "STK"
    exchange: str = ""
    currency: str = ""
    local_symbol: str = ""
    contract_id: Optional[int] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrokerAccount:
    account_id: str = ""
    total_asset: float = 0.0
    cash: float = 0.0
    market_value: float = 0.0
    buying_power: float = 0.0
    currency: str = ""
    broker: str = ""


@dataclass
class BrokerPosition:
    symbol: str
    quantity: float = 0.0
    available: float = 0.0
    avg_cost: float = 0.0
    market_value: float = 0.0
    last_price: float = 0.0
    contract: BrokerContract | None = None


@dataclass
class BrokerOrder:
    order_id: str
    symbol: str
    action: str
    quantity: float
    price: float | None = None
    filled_quantity: float = 0.0
    filled_price: float | None = None
    status: str = "pending"
    broker: str = "paper"
    submitted_at: datetime = field(default_factory=datetime.now)


@dataclass
class BrokerQuote:
    symbol: str
    price: float
    source: str = ""
    bid: float | None = None
    ask: float | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class BrokerOrderIntent:
    symbol: str
    action: str
    quantity: float
    price: float | None = None
    broker: str = "paper"
    order_type: str = "MKT"
    time_in_force: str = "DAY"


class BrokerProtocol(Protocol):
    def connect(self) -> bool:
        ...

    def disconnect(self) -> None:
        ...

    def query_account(self) -> BrokerAccount | None:
        ...

    def query_positions(self) -> list[BrokerPosition]:
        ...

    def query_orders(self) -> list[BrokerOrder]:
        ...

    def get_quote(self, symbol: str) -> BrokerQuote:
        ...

