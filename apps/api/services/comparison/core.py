from __future__ import annotations

import json
import os
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


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
        for i, c in enumerate(cats):
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

    def start_trial(self, config: Config, task: Task, adapter_type: str, initial_state: Dict[str, Any], conditions: Dict[str, Any], reproducibility: Dict[str, Any]) -> TrialRecord:
        rec = TrialRecord(
            trial_id=f"trial_{int(time.time()*1000)}_{id(self)}",
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


class ComparisonRunner:
    def __init__(self, loader: Optional[TaskLoader] = None):
        self.loader = loader or TaskLoader()
        self.recorder = TrialRecorder()
        self.reset_mgr = ResetManager()

    def run(self, configs: List[Config], task_subset: Optional[List[str]] = None, repetitions: int = 1,
            interleave: bool = True, use_doubles: bool = True, budget_opt_in: bool = False,
            total_budget_usd: Optional[float] = None, adapters: Optional[Dict[Config, Adapter]] = None) -> List[TrialRecord]:
        tasks = self.loader.load_subset(task_subset)
        # build plan
        plan = []
        for rep in range(repetitions):
            for cfg in configs:
                for t in tasks:
                    plan.append((rep, cfg, t))
        if interleave:
            import random
            random.shuffle(plan)
        results = []
        for rep, cfg, task in plan:
            env = self.reset_mgr.create_isolated_env()
            initial_state = {"project_id": f"cmp_{task.task_id}_{rep}", "runtime_dir": env["ALPHALAB_RUNTIME_DIR"], "initial_data_hash": "stub", "limits": {}, "data_profile": "runtime",
                              "conexus_local_root": env["CONEXUS_LOCAL_ROOT"], "model_providers_file": env["ALPHALAB_MODEL_PROVIDERS_FILE"]}
            conditions = {"cold_start": True, "warm_cache": False, "cache_labels": ["cold"], "conditions_not_matched": []}
            reproducibility = {"task_subset": task_subset, "repetition_index": rep, "repetition_count": repetitions, "interleaved_order": len(results)}
            adapter = adapters.get(cfg) if adapters else None
            adapter_type = "opencode" if cfg in (Config.A, Config.B) else "conexus"
            rec = self.recorder.start_trial(cfg, task, adapter_type, initial_state, conditions, reproducibility)
            rec.reset = {"performed": True, "verified": True, "method": "temp_runtime_dir_fresh_project_from_sdk-v1-default"}
            budget_guard = BudgetGuard(total_budget_usd=total_budget_usd, opt_in=budget_opt_in, use_doubles=use_doubles)
            try:
                budget_guard.check_real_run()
                if adapter:
                    adapter.setup(task, Limits(), initial_state, use_doubles)
                    for ev in adapter.run(task, Limits(), budget_guard):
                        self.recorder.record_event(rec, ev)
                self.recorder.finish_trial(rec, Status.COMPLETED)
            except RuntimeError as e:
                rec.results["errors"] = [{"code": "BUDGET_OR_OPTIN", "summary": str(e)}]
                self.recorder.finish_trial(rec, Status.FAILED)
            except TimeoutError:
                self.recorder.finish_trial(rec, Status.TIMEOUT)
            except Exception as e:
                rec.results["errors"] = [{"code": "ERROR", "summary": str(e)}]
                self.recorder.finish_trial(rec, Status.FAILED)
            results.append(rec)
        self.reset_mgr.cleanup()
        return results
