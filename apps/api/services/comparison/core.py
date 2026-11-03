from __future__ import annotations

import json
import os
import queue
import random
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .redaction import redact_obj

_POLL_INTERVAL_S = 0.05
_DONE = object()


class Config(str, Enum):
    A = "A"  # OpenCode + SDK/project docs, no financial tools
    B = "B"  # OpenCode + same financial skills and equivalent financial tools (MUST load tool JSONs)
    C = "C"  # Conexus single agent
    D = "D"  # later


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass
class Limits:
    time_s: Optional[int] = None
    tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    steps: Optional[int] = None


@dataclass
class Task:
    task_id: str
    category: str
    difficulty: str = "medium"
    user_request: str = ""
    starting_files: List[str] = field(default_factory=list)
    permitted_operations: List[str] = field(default_factory=list)
    budget: Dict[str, Any] = field(default_factory=dict)
    required_outputs: List[str] = field(default_factory=list)
    pass_conditions: List[str] = field(default_factory=list)
    distinguish_refusal_clarification_failure: bool = True
    fixtures: Dict[str, Any] = field(default_factory=dict)


class TaskLoader:
    def __init__(self, tasks: Optional[List[Task]] = None):
        self._tasks = tasks or self._default_stubs()

    def _default_stubs(self) -> List[Task]:
        cats = ["data_diagnosis_repair", "factor_implementation", "strategy_backtesting",
                "risk_execution_explanation", "result_auditing", "failure_recovery"]
        tasks = []
        for c in cats:
            tasks.append(Task(task_id=f"stub_{c}", category=c, user_request=f"Stub task for {c}"))
        return tasks

    def load_subset(self, subset: Optional[List[str]] = None) -> List[Task]:
        if not subset:
            return list(self._tasks)
        ids = set(subset)
        return [t for t in self._tasks if t.task_id in ids]


@dataclass
class TrialRecord:
    trial_id: str
    config: Config
    task_id: str
    task_category: str
    adapter_type: str
    timestamps: Dict[str, Any] = field(default_factory=dict)
    reproducibility: Dict[str, Any] = field(default_factory=dict)
    initial_state: Dict[str, Any] = field(default_factory=dict)
    conditions: Dict[str, Any] = field(default_factory=dict)
    execution: Dict[str, Any] = field(default_factory=lambda: {"status": Status.PENDING.value, "events": [], "tool_calls": [], "financial_entry_points": [], "permissions": [], "accessible_assets": [], "confirmations": []})
    results: Dict[str, Any] = field(default_factory=lambda: {"outputs": {}, "artifacts": [], "errors": [], "retries": 0})
    resources: Dict[str, Any] = field(default_factory=dict)
    budget: Dict[str, Any] = field(default_factory=lambda: {"enabled": False, "opt_in": False, "total_budget_usd": None, "spent_usd": 0.0, "within_budget": True})
    reset: Dict[str, Any] = field(default_factory=lambda: {"performed": False, "verified": False, "method": "temp_runtime_dir_fresh_project_from_sdk-v1-default"})
    is_complete: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "trial_id": self.trial_id,
            "run_id": None,
            "config": self.config.value,
            "task_id": self.task_id,
            "task_category": self.task_category,
            "adapter_type": self.adapter_type,
            "timestamps": self.timestamps,
            "reproducibility": self.reproducibility,
            "initial_state": self.initial_state,
            "conditions": self.conditions,
            "execution": self.execution,
            "results": self.results,
            "resources": self.resources,
            "budget": self.budget,
            "reset": self.reset,
            "is_complete": self.is_complete,
        }
        return d


class Adapter(ABC):
    @abstractmethod
    def setup(self, task: Task, limits: Limits, initial_state: Dict[str, Any], use_doubles: bool) -> None:
        pass

    @abstractmethod
    def run(self, task: Task, limits: Limits, budget_guard: "BudgetGuard") -> Iterator[Dict[str, Any]]:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def get_capabilities(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_financial_entry_points(self) -> List[Dict[str, Any]]:
        pass


class BudgetGuard:
    def __init__(self, total_budget_usd: Optional[float] = None, opt_in: bool = False, use_doubles: bool = True):
        self.total_budget_usd = total_budget_usd
        self.opt_in = opt_in
        self.use_doubles = use_doubles
        self.spent_usd = 0.0

    def check_real_run(self) -> None:
        if self.use_doubles:
            return
        if not self.opt_in:
            raise RuntimeError("Real runs require explicit opt-in")
        if self.total_budget_usd is None:
            raise RuntimeError("Real runs require a stated budget")

    def add_cost(self, cost_usd: float) -> None:
        self.spent_usd += float(cost_usd)
        if self.total_budget_usd is not None and self.spent_usd > self.total_budget_usd:
            raise RuntimeError("Budget exceeded")


class ResetManager:
    def __init__(self):
        self._tmpdirs = []

    def create_isolated_env(self) -> Dict[str, str]:
        td = tempfile.mkdtemp(prefix="alphalab_cmp_")
        self._tmpdirs.append(td)
        env = {
            "ALPHALAB_RUNTIME_DIR": os.path.join(td, "runtime"),
            "ALPHALAB_CACHE_DIR": os.path.join(td, "cache"),
            "CONEXUS_LOCAL_ROOT": os.path.join(td, "conexus_local"),
            "ALPHALAB_MODEL_PROVIDERS_FILE": os.path.join(td, "secrets", "model-providers.json"),
        }
        for k in ["ALPHALAB_RUNTIME_DIR", "ALPHALAB_CACHE_DIR", "CONEXUS_LOCAL_ROOT"]:
            os.makedirs(env[k], exist_ok=True)
        os.makedirs(os.path.dirname(env["ALPHALAB_MODEL_PROVIDERS_FILE"]), exist_ok=True)
        # write empty/minimal providers file
        with open(env["ALPHALAB_MODEL_PROVIDERS_FILE"], "w", encoding="utf-8") as f:
            json.dump({"profiles": [], "active_id": None}, f)
        return env

    def cleanup(self):
        import shutil
        for td in self._tmpdirs:
            try:
                shutil.rmtree(td, ignore_errors=True)
            except Exception:
                pass
        self._tmpdirs = []


class TrialRecorder:
    def __init__(self):
        self.records: List[TrialRecord] = []
        self._counter = 0

    def start_trial(self, config: Config, task: Task, adapter_type: str, initial_state: Dict[str, Any], conditions: Dict[str, Any], reproducibility: Dict[str, Any]) -> TrialRecord:
        self._counter += 1
        rec = TrialRecord(
            trial_id=f"trial_{int(time.time()*1000)}_{self._counter:04d}",
            config=config,
            task_id=task.task_id,
            task_category=task.category,
            adapter_type=adapter_type,
            timestamps={"start": time.time()},
            reproducibility=reproducibility,
            initial_state=initial_state,
            conditions=conditions,
        )
        self.records.append(rec)
        return rec

    def record_event(self, rec: TrialRecord, event: Dict[str, Any]):
        rec.execution["events"].append(event)

    def finish_trial(self, rec: TrialRecord, status: Status, results: Optional[Dict[str, Any]] = None, resources: Optional[Dict[str, Any]] = None):
        rec.execution["status"] = status.value
        rec.timestamps["end"] = time.time()
        rec.timestamps["duration_s"] = rec.timestamps["end"] - rec.timestamps["start"]
        if results:
            rec.results = results
        if resources:
            rec.resources = resources
        rec.is_complete = True


class _TrialCancelled(Exception):
    pass


class _TrialTimeout(Exception):
    pass


class _TrialLimitExceeded(Exception):
    pass


class _AdapterError:
    __slots__ = ("error",)

    def __init__(self, error: BaseException):
        self.error = error


class _EventStream:
    """Consume an adapter's event generator in a worker thread.

    The runner enforces cancellation and limits from the consuming thread, so a slow or hanging
    adapter cannot block the caller. ``close()`` signals stop and joins with a bounded wait.
    """

    def __init__(self, adapter: Adapter, task: Task, limits: Limits, budget_guard: BudgetGuard):
        self._adapter = adapter
        self._task = task
        self._limits = limits
        self._budget_guard = budget_guard
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._pump, name="comparison-adapter", daemon=True)

    def start(self) -> "_EventStream":
        self._thread.start()
        return self

    def _pump(self) -> None:
        try:
            for event in self._adapter.run(self._task, self._limits, self._budget_guard):
                if self._stop.is_set():
                    break
                self._queue.put(event)
        except BaseException as exc:  # forwarded to the runner, never swallowed
            self._queue.put(_AdapterError(exc))
        finally:
            self._queue.put(_DONE)

    def get(self, timeout: Optional[float]) -> Any:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            raise _TrialTimeout() from None

    def close(self) -> None:
        self._stop.set()
        try:
            self._adapter.stop()
        except Exception:
            pass
        self._thread.join(timeout=1.0)


class ComparisonRunner:
    def __init__(self, loader: Optional[TaskLoader] = None):
        self.loader = loader or TaskLoader()
        self.recorder = TrialRecorder()
        self.reset_mgr = ResetManager()
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Ask the current run to stop after the trial in flight."""
        self._cancel_event.set()

    def run(self, configs: List[Config], task_subset: Optional[List[str]] = None, repetitions: int = 1,
            interleave: bool = True, seed: Optional[int] = None,
            use_doubles: bool = True, budget_opt_in: bool = False,
            total_budget_usd: Optional[float] = None, adapters: Optional[Dict[Config, Adapter]] = None,
            limits: Optional[Limits] = None, cancel_event: Optional[threading.Event] = None,
            artifact_dir: Optional[str] = None, cleanup: bool = True) -> List[TrialRecord]:
        trial_limits = limits or Limits()
        tasks = self.loader.load_subset(task_subset)
        # build plan
        plan = [(rep, cfg, t) for rep in range(repetitions) for cfg in configs for t in tasks]
        interleave_seed: Optional[int] = None
        if interleave:
            interleave_seed = random.randrange(0, 2**31) if seed is None else int(seed)
            random.Random(interleave_seed).shuffle(plan)
        cancel = self._cancel_event if cancel_event is None else cancel_event
        if cancel_event is None:
            self._cancel_event.clear()
        results: List[TrialRecord] = []
        try:
            for plan_index, (rep, cfg, task) in enumerate(plan):
                env = self.reset_mgr.create_isolated_env()
                initial_state = {"project_id": f"cmp_{task.task_id}_{rep}", "runtime_dir": env["ALPHALAB_RUNTIME_DIR"], "initial_data_hash": "stub", "limits": {}, "data_profile": "runtime",
                                  "conexus_local_root": env["CONEXUS_LOCAL_ROOT"], "model_providers_file": env["ALPHALAB_MODEL_PROVIDERS_FILE"]}
                conditions = {"cold_start": True, "warm_cache": False, "cache_labels": ["cold"], "conditions_not_matched": [],
                              "limits": {"time_s": trial_limits.time_s, "tokens": trial_limits.tokens, "cost_usd": trial_limits.cost_usd, "steps": trial_limits.steps}}
                reproducibility = {"task_subset": task_subset, "repetition_index": rep, "repetition_count": repetitions,
                                   "interleaved_order": plan_index, "plan_size": len(plan), "interleave_seed": interleave_seed}
                adapter = adapters.get(cfg) if adapters else None
                adapter_type = "opencode" if cfg in (Config.A, Config.B) else "conexus"
                rec = self.recorder.start_trial(cfg, task, adapter_type, initial_state, conditions, reproducibility)
                rec.reset = {"performed": True, "verified": True, "method": "temp_runtime_dir_fresh_project_from_sdk-v1-default"}
                budget_guard = BudgetGuard(total_budget_usd=total_budget_usd, opt_in=budget_opt_in, use_doubles=use_doubles)
                self._run_trial(rec, task, adapter, trial_limits, budget_guard, cancel, use_doubles, env, artifact_dir)
                results.append(rec)
                if cancel.is_set():
                    break
        finally:
            if cleanup:
                self.reset_mgr.cleanup()
        return results

    def _run_trial(self, rec: TrialRecord, task: Task, adapter: Optional[Adapter], limits: Limits,
                   budget_guard: BudgetGuard, cancel: threading.Event, use_doubles: bool,
                   env: Dict[str, str], artifact_dir: Optional[str]) -> None:
        status = Status.COMPLETED
        errors: List[Dict[str, Any]] = []
        stream: Optional[_EventStream] = None
        rec.resources = {"steps": 0, "tokens": 0, "cost_usd": 0.0}
        try:
            budget_guard.check_real_run()
            if adapter is not None:
                adapter.setup(task, limits, rec.initial_state, use_doubles)
                stream = _EventStream(adapter, task, limits, budget_guard).start()
                self._consume_events(rec, stream, limits, cancel)
        except _TrialCancelled:
            status = Status.CANCELLED
            errors.append({"code": "CANCELLED", "summary": "Trial cancelled by caller"})
        except _TrialTimeout:
            status = Status.TIMEOUT
            errors.append({"code": "TIMEOUT", "summary": f"Trial exceeded its {limits.time_s}s time limit"})
        except _TrialLimitExceeded as exc:
            status = Status.FAILED
            errors.append({"code": "LIMIT_EXCEEDED", "summary": str(exc)})
        except TimeoutError as exc:
            status = Status.TIMEOUT
            errors.append({"code": "TIMEOUT", "summary": str(exc) or "Adapter timed out"})
        except RuntimeError as exc:
            status = Status.FAILED
            code = "BUDGET_EXCEEDED" if "Budget exceeded" in str(exc) else "BUDGET_OR_OPTIN"
            errors.append({"code": code, "summary": str(exc)})
        except Exception as exc:
            status = Status.FAILED
            errors.append({"code": "ERROR", "summary": str(exc)})
        finally:
            if stream is not None:
                stream.close()
            elif adapter is not None:
                try:
                    adapter.stop()
                except Exception:
                    pass
            if errors:
                rec.results["errors"] = errors
            if adapter is not None:
                try:
                    rec.execution["financial_entry_points"] = adapter.get_financial_entry_points()
                except Exception:
                    pass
            spent = max(budget_guard.spent_usd, float(rec.resources.get("cost_usd") or 0.0))
            rec.budget = {
                "enabled": not use_doubles,
                "opt_in": budget_guard.opt_in,
                "total_budget_usd": budget_guard.total_budget_usd,
                "spent_usd": spent,
                "within_budget": budget_guard.total_budget_usd is None or spent <= budget_guard.total_budget_usd,
            }
            self.recorder.finish_trial(rec, status)
            self._persist(rec, env, artifact_dir)

    def _consume_events(self, rec: TrialRecord, stream: _EventStream, limits: Limits,
                        cancel: threading.Event) -> None:
        deadline = None if limits.time_s is None else time.monotonic() + float(limits.time_s)
        while True:
            if cancel.is_set():
                raise _TrialCancelled()
            try:
                item = stream.get(_POLL_INTERVAL_S)
            except _TrialTimeout:
                if deadline is not None and time.monotonic() >= deadline:
                    raise _TrialTimeout() from None
                continue
            if deadline is not None and time.monotonic() >= deadline:
                raise _TrialTimeout()
            if item is _DONE:
                return
            if isinstance(item, _AdapterError):
                raise item.error
            self.recorder.record_event(rec, item)
            rec.resources["steps"] += 1
            if limits.steps is not None and rec.resources["steps"] > limits.steps:
                raise _TrialLimitExceeded(f"Steps limit {limits.steps} exceeded")
            usage = item.get("usage") if isinstance(item, dict) else None
            if isinstance(usage, dict) and usage.get("total_tokens") is not None:
                rec.resources["tokens"] += int(usage["total_tokens"])
            if limits.tokens is not None and rec.resources["tokens"] > limits.tokens:
                raise _TrialLimitExceeded(f"Token limit {limits.tokens} exceeded")
            if isinstance(item, dict) and item.get("cost_usd") is not None:
                rec.resources["cost_usd"] += float(item["cost_usd"])
            if limits.cost_usd is not None and rec.resources["cost_usd"] > limits.cost_usd:
                raise _TrialLimitExceeded(f"Cost limit {limits.cost_usd} USD exceeded")

    def _persist(self, rec: TrialRecord, env: Dict[str, str], artifact_dir: Optional[str]) -> None:
        target_dir = Path(artifact_dir) if artifact_dir is not None else Path(env["ALPHALAB_RUNTIME_DIR"]) / "trials"
        path = target_dir / f"{rec.trial_id}.json"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            rec.results.setdefault("artifacts", []).append({"kind": "trial_record", "path": str(path)})
            payload = redact_obj(rec.to_dict())
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except OSError as exc:
            rec.results.setdefault("errors", []).append({"code": "PERSIST_FAILED", "summary": str(exc)})
