"""SQLite result store for the barebone framework."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd

from alphalab.utils.paths import APP_DATA_DIR

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_DEFAULT_DB = APP_DATA_DIR / "alphalab.db"


def _uuid() -> str:
    return uuid4().hex[:12]


def _git_hash() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8").strip()
    except Exception:
        return None


class ResultStore:
    """Thread-safe SQLite store for strategies, backtests, signals and notes."""

    def __init__(self, db_path: str | Path | None = None):
        self.path = Path(db_path) if db_path else _DEFAULT_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))

    def close(self) -> None:
        self._conn.close()

    def _write(self, fn) -> None:
        with self._lock:
            fn()
            self._conn.commit()

    def register_strategy(self, strategy_id: str, yaml_path: str, description: str = "") -> None:
        def work() -> None:
            self._conn.execute(
                """INSERT INTO strategies (id, yaml_path, description)
                   VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     yaml_path = excluded.yaml_path,
                     description = excluded.description,
                     updated_at = datetime('now')""",
                (strategy_id, yaml_path, description),
            )

        self._write(work)

    def list_strategies(self) -> pd.DataFrame:
        return pd.read_sql("SELECT * FROM strategies ORDER BY id", self._conn)

    def save_backtest(
        self,
        config_yaml: str,
        returns: pd.Series,
        metrics: dict,
        *,
        strategy_id: str | None = None,
        benchmark: pd.Series | None = None,
        weights: pd.DataFrame | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        tags: list[str] | None = None,
        notes: str | None = None,
    ) -> str:
        backtest_id = _uuid()
        if start_date is None and not returns.empty:
            start_date = str(returns.index.min())[:10]
        if end_date is None and not returns.empty:
            end_date = str(returns.index.max())[:10]

        def work() -> None:
            self._conn.execute(
                """INSERT INTO backtests
                   (id, strategy_id, config_yaml, code_version, start_date, end_date,
                    total_return, annual_return, annual_vol, sharpe, max_drawdown,
                    n_periods, tags, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    backtest_id,
                    strategy_id,
                    config_yaml,
                    _git_hash(),
                    start_date,
                    end_date,
                    metrics.get("total_return"),
                    metrics.get("annual_return"),
                    metrics.get("annual_vol"),
                    metrics.get("sharpe"),
                    metrics.get("max_drawdown"),
                    metrics.get("n_periods", len(returns)),
                    json.dumps(tags) if tags else None,
                    notes,
                ),
            )
            rows = []
            for dt, value in returns.items():
                bench = float(benchmark.loc[dt]) if benchmark is not None and dt in benchmark.index else None
                rows.append((backtest_id, str(dt)[:10], float(value), bench))
            if rows:
                self._conn.executemany(
                    "INSERT INTO backtest_returns (backtest_id, date, strategy, benchmark) VALUES (?, ?, ?, ?)",
                    rows,
                )
            weight_rows = []
            if weights is not None and not weights.empty:
                for dt, row in weights.iterrows():
                    for symbol, weight in row.items():
                        if pd.notna(weight) and abs(float(weight)) > 1e-12:
                            weight_rows.append((backtest_id, str(dt)[:10], str(symbol), float(weight)))
            if weight_rows:
                self._conn.executemany(
                    "INSERT INTO backtest_weights (backtest_id, date, symbol, weight) VALUES (?, ?, ?, ?)",
                    weight_rows,
                )

        self._write(work)
        return backtest_id

    def list_backtests(self, strategy_id: str | None = None, limit: int = 20) -> pd.DataFrame:
        if strategy_id:
            return pd.read_sql(
                "SELECT * FROM backtests WHERE strategy_id = ? ORDER BY run_at DESC LIMIT ?",
                self._conn,
                params=(strategy_id, limit),
            )
        return pd.read_sql("SELECT * FROM backtests ORDER BY run_at DESC LIMIT ?", self._conn, params=(limit,))

    def load_returns(self, backtest_id: str) -> pd.DataFrame:
        df = pd.read_sql(
            "SELECT date, strategy, benchmark FROM backtest_returns WHERE backtest_id = ? ORDER BY date",
            self._conn,
            params=(backtest_id,),
        )
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        return df

    def load_weights(self, backtest_id: str) -> pd.DataFrame:
        df = pd.read_sql(
            "SELECT date, symbol, weight FROM backtest_weights WHERE backtest_id = ? ORDER BY date, symbol",
            self._conn,
            params=(backtest_id,),
        )
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        return df

    def save_signal(
        self,
        strategy_id: str,
        signal_date: str,
        targets: dict[str, float],
        current_weights: dict[str, float] | None = None,
        status: str = "pending",
    ) -> str:
        signal_id = _uuid()

        def work() -> None:
            self._conn.execute(
                "INSERT INTO signals (id, strategy_id, signal_date, status) VALUES (?, ?, ?, ?)",
                (signal_id, strategy_id, signal_date, status),
            )
            rows = [
                (signal_id, symbol, float(weight), (current_weights or {}).get(symbol))
                for symbol, weight in targets.items()
            ]
            if rows:
                self._conn.executemany(
                    "INSERT INTO signal_targets (signal_id, symbol, target_weight, current_weight) VALUES (?, ?, ?, ?)",
                    rows,
                )

        self._write(work)
        return signal_id

    def get_latest_signal(self, strategy_id: str) -> dict | None:
        row = self._conn.execute(
            """SELECT * FROM signals
               WHERE strategy_id = ?
               ORDER BY signal_date DESC, generated_at DESC
               LIMIT 1""",
            (strategy_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        targets = self._conn.execute(
            "SELECT symbol, target_weight FROM signal_targets WHERE signal_id = ?",
            (result["id"],),
        ).fetchall()
        result["targets"] = {item["symbol"]: float(item["target_weight"]) for item in targets}
        return result

    def save_paper_order(
        self,
        symbol: str,
        action: str,
        quantity: float,
        price: float,
        *,
        signal_id: str | None = None,
    ) -> str:
        order_id = _uuid()

        def work() -> None:
            commission = float(quantity) * float(price) * 0.0003
            self._conn.execute(
                """INSERT INTO orders
                   (id, signal_id, symbol, action, quantity, price, fill_price,
                    fill_quantity, commission, status, broker, filled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'filled', 'paper', datetime('now'))""",
                (
                    order_id,
                    signal_id,
                    str(symbol).upper(),
                    action,
                    float(quantity),
                    float(price),
                    float(price),
                    float(quantity),
                    commission,
                ),
            )

        self._write(work)
        return order_id

    def list_orders(self, limit: int = 100) -> pd.DataFrame:
        return pd.read_sql(
            "SELECT * FROM orders ORDER BY submitted_at DESC LIMIT ?",
            self._conn,
            params=(limit,),
        )

    def save_journal(
        self,
        date: str,
        content: str,
        *,
        title: str | None = None,
        strategy_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        journal_id = _uuid()

        def work() -> None:
            self._conn.execute(
                "INSERT INTO journal (id, date, strategy_id, title, content, tags) VALUES (?, ?, ?, ?, ?, ?)",
                (journal_id, date, strategy_id, title, content, json.dumps(tags) if tags else None),
            )

        self._write(work)
        return journal_id

    def stats(self) -> dict[str, int | str]:
        counts: dict[str, int | str] = {"path": str(self.path)}
        for table in ("strategies", "backtests", "signals", "orders", "journal"):
            counts[table] = int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        counts["checked_at"] = datetime.now().isoformat(timespec="seconds")
        return counts
