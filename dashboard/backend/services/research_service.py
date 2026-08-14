"""Deterministic research pipeline; no LLM planner and no broker execution."""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor

from alphalab import ResultStore, StrategyConfig, TimingStrategyConfig
from alphalab.dataio import DataLoadError
from dashboard.backend.services.backtest_analytics_service import analyze_robustness
from dashboard.backend.services.framework_service import (
    _profile_range,
    generate_signal,
    get_strategy_template,
    preview_paper_rebalance,
    run_strategy_backtest,
)


class ResearchCancelled(RuntimeError):
    pass


class ResearchRunManager:
    """Single-process research queue backed by the application SQLite store."""

    def __init__(self):
        store = ResultStore()
        try:
            store.mark_research_interrupted()
        finally:
            store.close()
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="research-run",
        )
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, request: dict) -> dict:
        store = ResultStore()
        try:
            active = [
                item
                for item in store.list_research_runs(limit=100)
                if item["status"] in {"queued", "running"}
            ]
            if active:
                raise DataLoadError(
                    f"A research run is already active: {active[0]['id']}"
                )
            run_id = store.create_research_run(request)
            result = store.get_research_run(run_id)
        finally:
            store.close()
        with self._lock:
            self._futures[run_id] = self._executor.submit(self._run, run_id)
        assert result is not None
        return result

    def run_now(self, request: dict) -> dict:
        store = ResultStore()
        try:
            run_id = store.create_research_run(request)
        finally:
            store.close()
        return self._run(run_id)

    def get(self, run_id: str) -> dict | None:
        store = ResultStore()
        try:
            return store.get_research_run(run_id)
        finally:
            store.close()

    def list(self, limit: int = 50) -> list[dict]:
        store = ResultStore()
        try:
            return store.list_research_runs(limit)
        finally:
            store.close()

    def cancel(self, run_id: str) -> dict | None:
        store = ResultStore()
        try:
            changed = store.request_research_cancel(run_id)
            if changed:
                with self._lock:
                    future = self._futures.get(run_id)
                    if future and future.cancel():
                        store.update_research_run(run_id, status="cancelled")
            return store.get_research_run(run_id)
        finally:
            store.close()

    def retry(self, run_id: str) -> dict:
        previous = self.get(run_id)
        if previous is None:
            raise KeyError(run_id)
        if previous["status"] not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("Only failed, cancelled, or interrupted runs can be retried")
        return self.submit(previous["request"])

    def _run(self, run_id: str) -> dict:
        store = ResultStore()
        current_step = ""
        try:
            run = store.get_research_run(run_id)
            if run is None:
                raise KeyError(run_id)
            request = run["request"]
            store.update_research_run(run_id, status="running")

            current_step = "data_status"
            self._start_step(store, run_id, current_step)
            start, end = _profile_range(request["profile"])
            requested_start = request["start_date"]
            requested_end = request["end_date"]
            if requested_start < start or requested_end > end:
                raise ValueError(
                    f"Requested range must stay within {start} to {end}"
                )
            self._finish_step(
                store,
                run_id,
                current_step,
                {"profile": request["profile"], "start": start, "end": end},
            )
            self._check_cancel(store, run_id)

            current_step = "strategy_validate"
            self._start_step(store, run_id, current_step)
            strategy = get_strategy_template(request["strategy_id"])
            if strategy is None:
                raise KeyError(request["strategy_id"])
            is_timing = strategy["strategy_type"] == "market_timing"
            if is_timing:
                config = TimingStrategyConfig.from_yaml_string(strategy["yaml"])
                hard_error_messages = {
                    "Barebone timing research currently requires the MKT series",
                    "No timing signals defined",
                    "Timing signal weights must sum to a positive value",
                }
                research_inputs = {"signals": config.signal_names}
            else:
                config = StrategyConfig.from_yaml_string(strategy["yaml"])
                hard_error_messages = {
                    "No factors defined",
                    "Factor weights must sum to a positive value",
                }
                research_inputs = {"factors": config.factor_names}
            hard_errors = [
                item for item in config.validate() if item in hard_error_messages
            ]
            if hard_errors:
                raise ValueError("; ".join(hard_errors))
            self._finish_step(
                store,
                run_id,
                current_step,
                {
                    "strategy_id": request["strategy_id"],
                    "strategy_type": strategy["strategy_type"],
                    **research_inputs,
                    "warnings": config.validate(),
                },
            )
            self._check_cancel(store, run_id)

            current_step = "backtest"
            self._start_step(store, run_id, current_step)
            backtest = run_strategy_backtest(
                request["strategy_id"],
                requested_start,
                requested_end,
                request["profile"],
            )
            self._finish_step(
                store,
                run_id,
                current_step,
                {"id": backtest["id"], "metrics": backtest["metrics"]},
            )
            self._check_cancel(store, run_id)

            current_step = "robustness"
            self._start_step(store, run_id, current_step)
            robustness = analyze_robustness(backtest["id"])
            self._finish_step(
                store,
                run_id,
                current_step,
                {
                    "status": robustness["status"],
                    "candidate_rules": robustness["candidate_rules"],
                },
            )
            self._check_cancel(store, run_id)

            if is_timing:
                current_step = "signal"
                self._start_step(store, run_id, current_step)
                execution = backtest.get("execution", {})
                self._finish_step(
                    store,
                    run_id,
                    current_step,
                    {
                        "signal_date": execution.get("latest_signal_date"),
                        "market_exposure": execution.get("latest_exposure"),
                    },
                )
                store.update_research_step(
                    run_id,
                    "risk_preview",
                    "skipped",
                    {"reason": "market timing produces aggregate exposure, not stock orders"},
                )
                result = {
                    "backtest_id": backtest["id"],
                    "robustness_status": robustness["status"],
                    "signal_id": None,
                    "preview_id": None,
                    "paper_execution": "not_applicable_for_market_timing",
                }
                store.update_research_run(run_id, status="succeeded", result=result)
                return store.get_research_run(run_id) or result

            current_step = "signal"
            self._start_step(store, run_id, current_step)
            signal = generate_signal(
                request["strategy_id"],
                requested_end,
                True,
                request["profile"],
            )
            self._finish_step(
                store,
                run_id,
                current_step,
                {
                    "id": signal["id"],
                    "signal_date": signal["signal_date"],
                    "target_count": len(signal["targets"]),
                },
            )
            self._check_cancel(store, run_id)

            current_step = "risk_preview"
            self._start_step(store, run_id, current_step)
            preview = preview_paper_rebalance(
                signal_id=signal["id"],
                profile=request["profile"],
                account_id=request.get("account_id", "paper"),
            )
            self._finish_step(
                store,
                run_id,
                current_step,
                {
                    "preview_id": preview["preview_id"],
                    "risk_status": preview["risk_status"],
                    "orders": len(preview["orders"]),
                },
            )
            result = {
                "backtest_id": backtest["id"],
                "robustness_status": robustness["status"],
                "signal_id": signal["id"],
                "preview_id": preview["preview_id"],
                "paper_execution": "awaiting_user_confirmation",
            }
            store.update_research_run(run_id, status="succeeded", result=result)
        except ResearchCancelled:
            if current_step:
                store.update_research_step(run_id, current_step, "cancelled")
            store.update_research_run(run_id, status="cancelled")
        except Exception as exc:
            if current_step:
                store.update_research_step(
                    run_id,
                    current_step,
                    "failed",
                    {"error": str(exc)[:1000]},
                )
            store.update_research_run(
                run_id,
                status="failed",
                error=f"{type(exc).__name__}: {str(exc)}",
            )
        finally:
            result = store.get_research_run(run_id)
            store.close()
        assert result is not None
        return result

    @staticmethod
    def _start_step(store: ResultStore, run_id: str, name: str) -> None:
        store.update_research_step(run_id, name, "running")

    @staticmethod
    def _finish_step(
        store: ResultStore,
        run_id: str,
        name: str,
        detail: dict,
    ) -> None:
        store.update_research_step(run_id, name, "succeeded", detail)

    @staticmethod
    def _check_cancel(store: ResultStore, run_id: str) -> None:
        if store.research_cancel_requested(run_id):
            raise ResearchCancelled()


_MANAGER = ResearchRunManager()


def get_research_manager() -> ResearchRunManager:
    return _MANAGER


__all__ = ["ResearchRunManager", "get_research_manager"]
