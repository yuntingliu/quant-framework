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
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).resolve().parents[1],
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            .decode("utf-8")
            .strip()
        )
    except Exception:
        return None


class ResultStore:
    """Thread-safe SQLite store for strategies, backtests, signals and notes."""

    def __init__(self, db_path: str | Path | None = None):
        self.path = Path(db_path) if db_path else _DEFAULT_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._migrate()

    def _migrate(self) -> None:
        backtest_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(backtests)").fetchall()
        }
        if "provenance_json" not in backtest_columns:
            self._conn.execute("ALTER TABLE backtests ADD COLUMN provenance_json TEXT")
        if "execution_json" not in backtest_columns:
            self._conn.execute("ALTER TABLE backtests ADD COLUMN execution_json TEXT")
        if "event_json" not in backtest_columns:
            self._conn.execute("ALTER TABLE backtests ADD COLUMN event_json TEXT")
        if "attribution_json" not in backtest_columns:
            self._conn.execute("ALTER TABLE backtests ADD COLUMN attribution_json TEXT")
        for name, sql_type in (
            ("pipeline_project_id", "TEXT"),
            ("strategy_source", "TEXT"),
            ("component_manifest_json", "TEXT"),
            ("settings_json", "TEXT"),
            ("strategy_project_id", "TEXT"),
            ("strategy_revision", "INTEGER"),
            ("strategy_source_sha256", "TEXT"),
            ("strategy_manifest_json", "TEXT"),
            ("validation_source", "TEXT"),
            ("validation_revision", "INTEGER"),
            ("validation_source_sha256", "TEXT"),
            ("validation_output_json", "TEXT"),
        ):
            if name not in backtest_columns:
                self._conn.execute(f"ALTER TABLE backtests ADD COLUMN {name} {sql_type}")
        backtest_job_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(backtest_jobs)").fetchall()
        }
        for name in ("error_code", "error_summary", "error_details_json", "log_reference"):
            if name not in backtest_job_columns:
                self._conn.execute(f"ALTER TABLE backtest_jobs ADD COLUMN {name} TEXT")
        order_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(orders)").fetchall()
        }
        if "account_id" not in order_columns:
            self._conn.execute("ALTER TABLE orders ADD COLUMN account_id TEXT")
        signal_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(signals)").fetchall()
        }
        if "profile" not in signal_columns:
            self._conn.execute(
                "ALTER TABLE signals ADD COLUMN profile TEXT NOT NULL DEFAULT 'demo'"
            )
        self._migrate_legacy_paper_ledger()
        self._conn.commit()

    def _migrate_legacy_paper_ledger(self) -> None:
        """Materialize the bundled legacy fills into the new paper ledger once."""
        ledger_count = self._conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
        position_count = self._conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]
        if ledger_count or position_count:
            return
        rows = self._conn.execute(
            """SELECT * FROM orders
               WHERE status = 'filled'
                 AND COALESCE(broker, 'paper') = 'paper'
               ORDER BY submitted_at, id"""
        ).fetchall()
        if not rows:
            return

        self._conn.execute(
            """INSERT OR IGNORE INTO paper_accounts
               (id, name, initial_cash, cash)
               VALUES ('paper', 'Paper Portfolio', 1000000, 1000000)"""
        )
        account = self._conn.execute(
            "SELECT initial_cash FROM paper_accounts WHERE id = 'paper'"
        ).fetchone()
        cash = float(account["initial_cash"])
        positions: dict[str, dict[str, float]] = {}
        fills: list[dict] = []
        for row in rows:
            symbol = str(row["symbol"]).upper()
            action = str(row["action"]).lower()
            quantity = float(row["fill_quantity"] or row["quantity"] or 0)
            price = float(row["fill_price"] or row["price"] or 0)
            commission = float(
                row["commission"] if row["commission"] is not None else quantity * price * 0.0003
            )
            if action not in {"buy", "sell"} or quantity <= 0 or price <= 0:
                return
            position = positions.setdefault(
                symbol,
                {"quantity": 0.0, "avg_cost": 0.0},
            )
            current_quantity = position["quantity"]
            current_cost = position["avg_cost"]
            realized = 0.0
            if action == "buy":
                required = quantity * price + commission
                if required > cash + 1e-9:
                    return
                new_quantity = current_quantity + quantity
                position["avg_cost"] = (current_quantity * current_cost + required) / new_quantity
                position["quantity"] = new_quantity
                cash -= required
            else:
                if quantity > current_quantity + 1e-9:
                    return
                realized = (price - current_cost) * quantity - commission
                position["quantity"] = current_quantity - quantity
                cash += quantity * price - commission
            position["price"] = price
            fills.append(
                {
                    "id": f"legacy-{row['id']}",
                    "order_id": row["id"],
                    "symbol": symbol,
                    "action": action,
                    "quantity": quantity,
                    "price": price,
                    "commission": commission,
                    "realized": realized,
                    "filled_at": row["filled_at"] or row["submitted_at"],
                }
            )

        self._conn.execute(
            """UPDATE orders
               SET account_id = COALESCE(account_id, 'paper'),
                   fill_quantity = COALESCE(fill_quantity, quantity),
                   filled_at = COALESCE(filled_at, submitted_at)
               WHERE status = 'filled'
                 AND COALESCE(broker, 'paper') = 'paper'"""
        )
        for fill in fills:
            self._conn.execute(
                """INSERT INTO paper_fills
                   (id, account_id, order_id, symbol, action, quantity,
                    price, commission, realized_pnl, filled_at)
                   VALUES (?, 'paper', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fill["id"],
                    fill["order_id"],
                    fill["symbol"],
                    fill["action"],
                    fill["quantity"],
                    fill["price"],
                    fill["commission"],
                    fill["realized"],
                    fill["filled_at"],
                ),
            )
        for symbol, position in positions.items():
            if position["quantity"] <= 1e-9:
                continue
            self._conn.execute(
                """INSERT INTO paper_positions
                   (account_id, symbol, quantity, avg_cost, market_price,
                    market_value, unrealized_pnl, price_date)
                   VALUES ('paper', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol,
                    position["quantity"],
                    position["avg_cost"],
                    position["price"],
                    position["quantity"] * position["price"],
                    position["quantity"] * (position["price"] - position["avg_cost"]),
                    str(fills[-1]["filled_at"])[:10],
                ),
            )
        market_value = sum(
            position["quantity"] * position["price"]
            for position in positions.values()
            if position["quantity"] > 1e-9
        )
        nav_date = str(fills[-1]["filled_at"])[:10]
        self._conn.execute(
            """UPDATE paper_accounts
               SET cash = ?, updated_at = datetime('now')
               WHERE id = 'paper'""",
            (cash,),
        )
        self._conn.execute(
            """INSERT OR REPLACE INTO paper_nav
               (account_id, date, cash, market_value, equity)
               VALUES ('paper', ?, ?, ?, ?)""",
            (nav_date, cash, market_value, cash + market_value),
        )

    def close(self) -> None:
        self._conn.close()

    def _write(self, fn) -> None:
        with self._lock:
            try:
                fn()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def ensure_backtest_subject(
        self,
        subject_id: str,
        storage_ref: str,
        description: str = "",
    ) -> None:
        """Maintain the legacy foreign-key row required by upgraded databases."""

        def work() -> None:
            self._conn.execute(
                """INSERT INTO strategies (id, yaml_path, description)
                   VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     yaml_path = excluded.yaml_path,
                     description = excluded.description,
                     updated_at = datetime('now')""",
                (subject_id, storage_ref, description),
            )

        self._write(work)

    def save_backtest(
        self,
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
        provenance: dict | None = None,
        executions: list[dict] | tuple[dict, ...] | None = None,
        events: list[dict] | tuple[dict, ...] | None = None,
        persist_zero_weights: bool = False,
        strategy_source: str | None = None,
        settings: dict | None = None,
        attribution: dict | None = None,
        strategy_project_id: str | None = None,
        strategy_revision: int | None = None,
        strategy_source_sha256: str | None = None,
        strategy_manifest: dict | list | None = None,
        validation_source: str | None = None,
        validation_revision: int | None = None,
        validation_source_sha256: str | None = None,
        validation_output: dict | None = None,
        backtest_id: str | None = None,
    ) -> str:
        backtest_id = str(backtest_id).strip() if backtest_id is not None else _uuid()
        if not backtest_id:
            raise ValueError("backtest_id must not be empty")
        if start_date is None and not returns.empty:
            start_date = str(returns.index.min())[:10]
        if end_date is None and not returns.empty:
            end_date = str(returns.index.max())[:10]

        def work() -> None:
            # A background job uses its stable job id here. Replaying an
            # interrupted job replaces the same result atomically instead of
            # creating a second, indistinguishable backtest.
            self._conn.execute("DELETE FROM backtests WHERE id=?", (backtest_id,))
            self._conn.execute(
                """INSERT INTO backtests
                   (id, strategy_id, config_yaml, code_version, start_date, end_date,
                    total_return, annual_return, annual_vol, sharpe, max_drawdown,
                     n_periods, tags, notes, provenance_json, execution_json, event_json,
                    pipeline_project_id, strategy_source, component_manifest_json,
                    settings_json, attribution_json, strategy_project_id,
                    strategy_revision, strategy_source_sha256, strategy_manifest_json,
                    validation_source, validation_revision, validation_source_sha256,
                    validation_output_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    backtest_id,
                    strategy_id,
                    "",
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
                    json.dumps(provenance, sort_keys=True) if provenance else None,
                    json.dumps(list(executions), sort_keys=True) if executions else None,
                    json.dumps(list(events), sort_keys=True) if events else None,
                    None,
                    strategy_source,
                    None,
                    json.dumps(settings, sort_keys=True) if settings else None,
                    json.dumps(attribution, sort_keys=True) if attribution else None,
                    strategy_project_id,
                    strategy_revision,
                    strategy_source_sha256,
                    json.dumps(strategy_manifest, sort_keys=True) if strategy_manifest else None,
                    validation_source,
                    validation_revision,
                    validation_source_sha256,
                    json.dumps(validation_output, sort_keys=True) if validation_output else None,
                ),
            )
            rows = []
            for dt, value in returns.items():
                bench = (
                    float(benchmark.loc[dt])
                    if benchmark is not None and dt in benchmark.index
                    else None
                )
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
                        if pd.notna(weight) and (
                            persist_zero_weights or abs(float(weight)) > 1e-12
                        ):
                            weight_rows.append(
                                (backtest_id, str(dt)[:10], str(symbol), float(weight))
                            )
            if weight_rows:
                self._conn.executemany(
                    "INSERT INTO backtest_weights (backtest_id, date, symbol, weight) VALUES (?, ?, ?, ?)",
                    weight_rows,
                )

        self._write(work)
        return backtest_id

    def create_backtest_job(self, request: dict) -> str:
        job_id = _uuid()

        def work() -> None:
            self._conn.execute(
                """INSERT INTO backtest_jobs
                   (id, status, request_json, message)
                   VALUES (?, 'queued', ?, 'Waiting to run')""",
                (job_id, json.dumps(request, sort_keys=True)),
            )

        self._write(work)
        return job_id

    def update_backtest_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        message: str | None = None,
        result: dict | None = None,
        result_id: str | None = None,
        error: str | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
        error_details: dict | None = None,
        log_reference: str | None = None,
        request: dict | None = None,
    ) -> None:
        values: dict[str, object] = {}
        if status is not None:
            values["status"] = status
            if status == "running":
                values["started_at"] = datetime.now().isoformat(timespec="seconds")
                values["finished_at"] = None
                values["result_json"] = None
                values["result_id"] = None
                values["error"] = None
                values["error_code"] = None
                values["error_summary"] = None
                values["error_details_json"] = None
                values["log_reference"] = None
            if status in {"succeeded", "failed", "interrupted"}:
                values["finished_at"] = datetime.now().isoformat(timespec="seconds")
            if status == "succeeded":
                values["error"] = None
                values["error_code"] = None
                values["error_summary"] = None
                values["error_details_json"] = None
                values["log_reference"] = None
            if status in {"failed", "interrupted"}:
                values["result_json"] = None
                values["result_id"] = None
        if message is not None:
            values["message"] = message
        if result is not None:
            values["result_json"] = json.dumps(result, default=str)
        if result_id is not None:
            values["result_id"] = result_id
        if error is not None:
            values["error"] = str(error)[:2000]
        if error_code is not None:
            values["error_code"] = str(error_code)[:100]
        if error_summary is not None:
            values["error_summary"] = str(error_summary)[:500]
        if error_details is not None:
            values["error_details_json"] = json.dumps(error_details, default=str)
        if log_reference is not None:
            values["log_reference"] = str(log_reference)[:200]
        if request is not None:
            values["request_json"] = json.dumps(request, sort_keys=True)
        if not values:
            return

        def work() -> None:
            assignments = ", ".join(f"{name}=?" for name in values)
            self._conn.execute(
                f"UPDATE backtest_jobs SET {assignments} WHERE id=?",
                (*values.values(), job_id),
            )

        self._write(work)

    def get_backtest_job(self, job_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM backtest_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        return self._backtest_job_dict(row) if row is not None else None

    def list_backtest_jobs(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM backtest_jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._backtest_job_dict(row) for row in rows]

    def mark_backtest_jobs_interrupted(self) -> int:
        changed = 0

        def work() -> None:
            nonlocal changed
            cursor = self._conn.execute(
                """UPDATE backtest_jobs
                   SET status='interrupted', finished_at=datetime('now'),
                       message='Interrupted', error='Service restarted'
                   WHERE status IN ('queued', 'running')"""
            )
            changed = int(cursor.rowcount)

        self._write(work)
        return changed

    def list_pending_backtest_jobs(self) -> list[dict]:
        """Return unfinished jobs without changing state owned by another worker."""

        return [
            self._backtest_job_dict(row)
            for row in self._conn.execute(
                """SELECT * FROM backtest_jobs
                   WHERE status IN ('queued', 'running') ORDER BY created_at, id"""
            ).fetchall()
        ]

    @staticmethod
    def _backtest_job_dict(row: sqlite3.Row) -> dict:
        value = dict(row)
        value["request"] = json.loads(value.pop("request_json"))
        raw_result = value.pop("result_json")
        value["result"] = json.loads(raw_result) if raw_result else None
        raw_error_details = value.pop("error_details_json")
        value["error_details"] = json.loads(raw_error_details) if raw_error_details else {}
        return value

    def list_backtests(self, strategy_id: str | None = None, limit: int = 20) -> pd.DataFrame:
        columns = """id, strategy_id, code_version, start_date, end_date, run_at,
                     total_return, annual_return, annual_vol, sharpe, max_drawdown,
                     n_periods, tags, notes"""
        if strategy_id:
            return pd.read_sql(
                f"SELECT {columns} FROM backtests WHERE strategy_id = ? ORDER BY run_at DESC LIMIT ?",
                self._conn,
                params=(strategy_id, limit),
            )
        return pd.read_sql(
            f"SELECT {columns} FROM backtests ORDER BY run_at DESC LIMIT ?",
            self._conn,
            params=(limit,),
        )

    def get_backtest_record(self, backtest_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM backtests WHERE id = ?",
            (backtest_id,),
        ).fetchone()
        return dict(row) if row is not None else None

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
        profile: str = "demo",
    ) -> str:
        signal_id = _uuid()

        def work() -> None:
            self._conn.execute(
                """INSERT INTO signals
                   (id, strategy_id, profile, signal_date, status)
                   VALUES (?, ?, ?, ?, ?)""",
                (signal_id, strategy_id, profile, signal_date, status),
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

    def save_research_artifact(
        self,
        artifact_id: str,
        request_id: str,
        profile: str,
        title: str,
        payload: dict,
        provenance: dict,
    ) -> str:
        def work() -> None:
            self._conn.execute(
                """INSERT INTO research_artifacts
                   (id, request_id, profile, title, payload_json, provenance_json)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     request_id=excluded.request_id,
                     profile=excluded.profile,
                     title=excluded.title,
                     payload_json=excluded.payload_json,
                     provenance_json=excluded.provenance_json,
                     updated_at=datetime('now')""",
                (
                    artifact_id,
                    request_id,
                    profile,
                    title,
                    json.dumps(payload, sort_keys=True),
                    json.dumps(provenance, sort_keys=True),
                ),
            )

        self._write(work)
        return artifact_id

    def list_research_artifacts(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """SELECT id, request_id, profile, title, payload_json,
                      provenance_json, created_at, updated_at
               FROM research_artifacts
               ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_research_artifact(self, artifact_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM research_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_latest_signal(
        self,
        strategy_id: str,
        profile: str = "demo",
    ) -> dict | None:
        row = self._conn.execute(
            """SELECT * FROM signals
               WHERE strategy_id = ? AND profile = ?
               ORDER BY signal_date DESC, generated_at DESC
               LIMIT 1""",
            (strategy_id, profile),
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

    def get_signal(self, signal_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        targets = self._conn.execute(
            "SELECT symbol, target_weight FROM signal_targets WHERE signal_id = ?",
            (signal_id,),
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
        account_id: str = "paper",
    ) -> str:
        result = self.apply_paper_orders(
            [
                {
                    "symbol": symbol,
                    "action": action,
                    "quantity": quantity,
                    "price": price,
                }
            ],
            account_id=account_id,
            signal_id=signal_id,
        )
        return result["order_ids"][0]

    def list_orders(
        self,
        limit: int = 100,
        *,
        account_id: str | None = None,
    ) -> pd.DataFrame:
        if account_id is not None:
            return pd.read_sql(
                """SELECT * FROM orders
                   WHERE account_id = ?
                   ORDER BY submitted_at DESC LIMIT ?""",
                self._conn,
                params=(account_id, limit),
            )
        return pd.read_sql(
            "SELECT * FROM orders ORDER BY submitted_at DESC LIMIT ?",
            self._conn,
            params=(limit,),
        )

    def ensure_paper_account(
        self,
        account_id: str = "paper",
        *,
        initial_cash: float = 1_000_000.0,
        name: str = "AlphaLab Paper",
    ) -> dict:
        def work() -> None:
            self._conn.execute(
                """INSERT OR IGNORE INTO paper_accounts
                   (id, name, initial_cash, cash)
                   VALUES (?, ?, ?, ?)""",
                (account_id, name, float(initial_cash), float(initial_cash)),
            )

        self._write(work)
        row = self._conn.execute(
            "SELECT * FROM paper_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        assert row is not None
        return dict(row)

    def list_paper_positions(self, account_id: str = "paper") -> pd.DataFrame:
        self.ensure_paper_account(account_id)
        return pd.read_sql(
            """SELECT * FROM paper_positions
               WHERE account_id = ?
               ORDER BY market_value DESC, symbol""",
            self._conn,
            params=(account_id,),
        )

    def list_paper_fills(
        self,
        account_id: str = "paper",
        limit: int = 100,
    ) -> pd.DataFrame:
        self.ensure_paper_account(account_id)
        return pd.read_sql(
            """SELECT * FROM paper_fills
               WHERE account_id = ?
               ORDER BY filled_at DESC LIMIT ?""",
            self._conn,
            params=(account_id, limit),
        )

    def paper_nav(self, account_id: str = "paper", limit: int = 500) -> pd.DataFrame:
        self.ensure_paper_account(account_id)
        return pd.read_sql(
            """SELECT * FROM (
                 SELECT * FROM paper_nav
                 WHERE account_id = ?
                 ORDER BY date DESC LIMIT ?
               ) ORDER BY date""",
            self._conn,
            params=(account_id, limit),
        )

    def mark_paper_positions(
        self,
        prices: dict[str, float],
        price_date: str,
        *,
        account_id: str = "paper",
    ) -> dict:
        account = self.ensure_paper_account(account_id)

        def work() -> None:
            for symbol, price in prices.items():
                self._conn.execute(
                    """UPDATE paper_positions
                       SET market_price = ?,
                           market_value = quantity * ?,
                           unrealized_pnl = quantity * (? - avg_cost),
                           price_date = ?,
                           updated_at = datetime('now')
                       WHERE account_id = ? AND symbol = ?""",
                    (
                        float(price),
                        float(price),
                        float(price),
                        price_date,
                        account_id,
                        symbol,
                    ),
                )
            market_value = float(
                self._conn.execute(
                    """SELECT COALESCE(SUM(market_value), 0)
                       FROM paper_positions WHERE account_id = ?""",
                    (account_id,),
                ).fetchone()[0]
            )
            equity = float(account["cash"]) + market_value
            self._conn.execute(
                """INSERT INTO paper_nav (account_id, date, cash, market_value, equity)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(account_id, date) DO UPDATE SET
                     cash=excluded.cash,
                     market_value=excluded.market_value,
                     equity=excluded.equity""",
                (account_id, price_date, float(account["cash"]), market_value, equity),
            )

        self._write(work)
        return self.paper_account(account_id)

    def paper_account(self, account_id: str = "paper") -> dict:
        account = self.ensure_paper_account(account_id)
        market_value = float(
            self._conn.execute(
                """SELECT COALESCE(SUM(market_value), 0)
                   FROM paper_positions WHERE account_id = ?""",
                (account_id,),
            ).fetchone()[0]
        )
        realized = float(
            self._conn.execute(
                """SELECT COALESCE(SUM(realized_pnl), 0)
                   FROM paper_fills WHERE account_id = ?""",
                (account_id,),
            ).fetchone()[0]
        )
        equity = float(account["cash"]) + market_value
        return {
            **account,
            "market_value": market_value,
            "equity": equity,
            "total_return": equity / float(account["initial_cash"]) - 1.0,
            "realized_pnl": realized,
            "positions_count": int(
                self._conn.execute(
                    """SELECT COUNT(*) FROM paper_positions
                       WHERE account_id = ? AND quantity > 0""",
                    (account_id,),
                ).fetchone()[0]
            ),
        }

    def apply_paper_orders(
        self,
        orders: list[dict],
        *,
        account_id: str = "paper",
        signal_id: str | None = None,
        commission_rate: float = 0.0003,
        nav_date: str | None = None,
    ) -> dict:
        self.ensure_paper_account(account_id)
        order_ids: list[str] = []
        fill_ids: list[str] = []
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                account = self._conn.execute(
                    "SELECT * FROM paper_accounts WHERE id = ?",
                    (account_id,),
                ).fetchone()
                assert account is not None
                cash = float(account["cash"])
                for item in sorted(
                    orders,
                    key=lambda value: 0 if value["action"] == "sell" else 1,
                ):
                    symbol = str(item["symbol"]).upper()
                    action = str(item["action"]).lower()
                    quantity = float(item["quantity"])
                    price = float(item["price"])
                    if action not in {"buy", "sell"}:
                        raise ValueError("paper action must be buy or sell")
                    if quantity <= 0 or price <= 0:
                        raise ValueError("paper quantity and price must be positive")
                    position = self._conn.execute(
                        """SELECT * FROM paper_positions
                           WHERE account_id = ? AND symbol = ?""",
                        (account_id, symbol),
                    ).fetchone()
                    current_quantity = float(position["quantity"]) if position else 0.0
                    average_cost = float(position["avg_cost"]) if position else 0.0
                    commission = quantity * price * commission_rate
                    realized = 0.0
                    if action == "sell":
                        if quantity > current_quantity + 1e-9:
                            raise ValueError(f"Insufficient paper position for {symbol}")
                        cash += quantity * price - commission
                        new_quantity = current_quantity - quantity
                        realized = (price - average_cost) * quantity - commission
                        if new_quantity <= 1e-9:
                            self._conn.execute(
                                """DELETE FROM paper_positions
                                   WHERE account_id = ? AND symbol = ?""",
                                (account_id, symbol),
                            )
                        else:
                            self._conn.execute(
                                """UPDATE paper_positions
                                   SET quantity = ?, market_price = ?,
                                       market_value = ? * ?,
                                       unrealized_pnl = ? * (? - avg_cost),
                                       updated_at = datetime('now')
                                   WHERE account_id = ? AND symbol = ?""",
                                (
                                    new_quantity,
                                    price,
                                    new_quantity,
                                    price,
                                    new_quantity,
                                    price,
                                    account_id,
                                    symbol,
                                ),
                            )
                    else:
                        required_cash = quantity * price + commission
                        if required_cash > cash + 1e-9:
                            raise ValueError("Insufficient paper cash")
                        cash -= required_cash
                        new_quantity = current_quantity + quantity
                        new_average = (
                            current_quantity * average_cost + required_cash
                        ) / new_quantity
                        self._conn.execute(
                            """INSERT INTO paper_positions
                               (account_id, symbol, quantity, avg_cost, market_price,
                                market_value, unrealized_pnl, price_date, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                               ON CONFLICT(account_id, symbol) DO UPDATE SET
                                 quantity=excluded.quantity,
                                 avg_cost=excluded.avg_cost,
                                 market_price=excluded.market_price,
                                 market_value=excluded.market_value,
                                 unrealized_pnl=excluded.unrealized_pnl,
                                 price_date=excluded.price_date,
                                 updated_at=datetime('now')""",
                            (
                                account_id,
                                symbol,
                                new_quantity,
                                new_average,
                                price,
                                new_quantity * price,
                                new_quantity * (price - new_average),
                                nav_date,
                            ),
                        )
                    order_id = _uuid()
                    fill_id = _uuid()
                    self._conn.execute(
                        """INSERT INTO orders
                           (id, account_id, signal_id, symbol, action, quantity,
                            price, fill_price, fill_quantity, commission, status,
                            broker, filled_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'filled',
                                   'paper', datetime('now'))""",
                        (
                            order_id,
                            account_id,
                            signal_id,
                            symbol,
                            action,
                            quantity,
                            price,
                            price,
                            quantity,
                            commission,
                        ),
                    )
                    self._conn.execute(
                        """INSERT INTO paper_fills
                           (id, account_id, order_id, symbol, action, quantity,
                            price, commission, realized_pnl)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            fill_id,
                            account_id,
                            order_id,
                            symbol,
                            action,
                            quantity,
                            price,
                            commission,
                            realized,
                        ),
                    )
                    order_ids.append(order_id)
                    fill_ids.append(fill_id)
                self._conn.execute(
                    """UPDATE paper_accounts
                       SET cash = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (cash, account_id),
                )
                market_value = float(
                    self._conn.execute(
                        """SELECT COALESCE(SUM(market_value), 0)
                           FROM paper_positions WHERE account_id = ?""",
                        (account_id,),
                    ).fetchone()[0]
                )
                if nav_date:
                    self._conn.execute(
                        """INSERT INTO paper_nav
                           (account_id, date, cash, market_value, equity)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(account_id, date) DO UPDATE SET
                             cash=excluded.cash,
                             market_value=excluded.market_value,
                             equity=excluded.equity""",
                        (account_id, nav_date, cash, market_value, cash + market_value),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return {
            "account_id": account_id,
            "order_ids": order_ids,
            "fill_ids": fill_ids,
            "account": self.paper_account(account_id),
        }

    def create_research_run(self, request: dict) -> str:
        run_id = _uuid()
        steps = (
            "data_status",
            "strategy_validate",
            "backtest",
            "robustness",
            "signal",
            "risk_preview",
        )

        def work() -> None:
            self._conn.execute(
                """INSERT INTO research_runs
                   (id, strategy_id, profile, start_date, end_date, status,
                    request_json)
                   VALUES (?, ?, ?, ?, ?, 'queued', ?)""",
                (
                    run_id,
                    request["strategy_id"],
                    request["profile"],
                    request["start_date"],
                    request["end_date"],
                    json.dumps(request, sort_keys=True),
                ),
            )
            self._conn.executemany(
                """INSERT INTO research_run_steps
                   (run_id, name, position, status)
                   VALUES (?, ?, ?, 'pending')""",
                [(run_id, name, index) for index, name in enumerate(steps)],
            )

        self._write(work)
        return run_id

    def update_research_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        values: dict[str, object] = {}
        if status is not None:
            values["status"] = status
            if status == "running":
                values["started_at"] = datetime.now().isoformat(timespec="seconds")
            if status in {"succeeded", "failed", "cancelled", "interrupted"}:
                values["finished_at"] = datetime.now().isoformat(timespec="seconds")
        if result is not None:
            values["result_json"] = json.dumps(result, default=str)
        if error is not None:
            values["error"] = str(error)[:2000]
        if not values:
            return

        def work() -> None:
            assignments = ", ".join(f"{name}=?" for name in values)
            self._conn.execute(
                f"UPDATE research_runs SET {assignments} WHERE id=?",
                (*values.values(), run_id),
            )

        self._write(work)

    def update_research_step(
        self,
        run_id: str,
        name: str,
        status: str,
        detail: dict | None = None,
    ) -> None:
        def work() -> None:
            started = datetime.now().isoformat(timespec="seconds") if status == "running" else None
            finished = (
                datetime.now().isoformat(timespec="seconds")
                if status in {"succeeded", "failed", "cancelled", "skipped"}
                else None
            )
            self._conn.execute(
                """UPDATE research_run_steps
                   SET status=?,
                       detail_json=COALESCE(?, detail_json),
                       started_at=COALESCE(?, started_at),
                       finished_at=COALESCE(?, finished_at)
                   WHERE run_id=? AND name=?""",
                (
                    status,
                    json.dumps(detail, default=str) if detail is not None else None,
                    started,
                    finished,
                    run_id,
                    name,
                ),
            )

        self._write(work)

    def get_research_run(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM research_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        steps = self._conn.execute(
            """SELECT * FROM research_run_steps
               WHERE run_id=? ORDER BY position""",
            (run_id,),
        ).fetchall()
        return self._research_run_dict(row, steps)

    def list_research_runs(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM research_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            self._research_run_dict(
                row,
                self._conn.execute(
                    """SELECT * FROM research_run_steps
                       WHERE run_id=? ORDER BY position""",
                    (row["id"],),
                ).fetchall(),
            )
            for row in rows
        ]

    def request_research_cancel(self, run_id: str) -> bool:
        changed = False

        def work() -> None:
            nonlocal changed
            cursor = self._conn.execute(
                """UPDATE research_runs SET cancel_requested=1
                   WHERE id=? AND status IN ('queued', 'running')""",
                (run_id,),
            )
            changed = cursor.rowcount > 0

        self._write(work)
        return changed

    def research_cancel_requested(self, run_id: str) -> bool:
        row = self._conn.execute(
            "SELECT cancel_requested FROM research_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        return bool(row and row[0])

    def mark_research_interrupted(self) -> int:
        changed = 0

        def work() -> None:
            nonlocal changed
            cursor = self._conn.execute(
                """UPDATE research_runs
                   SET status='interrupted', finished_at=datetime('now'),
                       error='Service restarted'
                   WHERE status IN ('queued', 'running')"""
            )
            changed = int(cursor.rowcount)

        self._write(work)
        return changed

    @staticmethod
    def _research_run_dict(
        row: sqlite3.Row,
        step_rows: list[sqlite3.Row],
    ) -> dict:
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        raw_result = result.pop("result_json")
        result["result"] = json.loads(raw_result) if raw_result else None
        result["cancel_requested"] = bool(result["cancel_requested"])
        result["steps"] = []
        for step_row in step_rows:
            step = dict(step_row)
            raw_detail = step.pop("detail_json")
            step["detail"] = json.loads(raw_detail) if raw_detail else None
            result["steps"].append(step)
        return result

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
        for table in (
            "strategies",
            "backtests",
            "backtest_jobs",
            "signals",
            "orders",
            "paper_accounts",
            "paper_positions",
            "paper_fills",
            "research_runs",
            "research_artifacts",
            "journal",
        ):
            counts[table] = int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        counts["checked_at"] = datetime.now().isoformat(timespec="seconds")
        return counts
