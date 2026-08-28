"""SQLite order logging helper."""
from __future__ import annotations

from alphalab.store import ResultStore


class OrderLogger:
    """Persist order-like dictionaries to the ResultStore schema."""

    def __init__(self, store: ResultStore | None = None):
        self.store = store or ResultStore()

    def log(self, order: dict) -> str:
        self.store._conn.execute(
            """INSERT INTO orders
               (id, signal_id, symbol, action, quantity, price, status, broker)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order["id"],
                order.get("signal_id"),
                str(order["symbol"]).upper(),
                order["action"],
                float(order["quantity"]),
                order.get("price"),
                order.get("status", "pending"),
                order.get("broker", "paper"),
            ),
        )
        self.store._conn.commit()
        return str(order["id"])

