import threading
import time
from pathlib import Path

import pytest

from apps.api.services.comparison import Adapter, Limits, Task

DEFAULT_TASK_ID = "stub_data_diagnosis_repair"


class RecordingAdapter(Adapter):
    """Deterministic adapter standing in for a live OpenCode/Conexus process."""

    def __init__(self, events=None, delay_s=0.0, error=None):
        self.events = list(events or [])
        self.delay_s = float(delay_s)
        self.error = error
        self.stopped = threading.Event()
        self.stop_calls = 0
        self.setup_calls = []

    def setup(self, task, limits, initial_state, use_doubles):
        self.setup_calls.append({"task_id": task.task_id, "limits": limits, "use_doubles": use_doubles})

    def run(self, task, limits, budget_guard):
        for event in self.events:
            if self.stopped.is_set():
                return
            if self.delay_s:
                time.sleep(self.delay_s)
            yield event
        if self.error is not None:
            raise self.error

    def stop(self):
        self.stop_calls += 1
        self.stopped.set()

    def get_capabilities(self):
        return {"has_financial_tools": False}

    def get_financial_entry_points(self):
        return []


class HangingAdapter(RecordingAdapter):
    """Adapter that blocks until the runner calls stop(), to exercise time limits and cancel."""

    def run(self, task, limits, budget_guard):
        while not self.stopped.wait(0.01):
            pass
        yield from ()


@pytest.fixture
def task_id():
    return DEFAULT_TASK_ID


@pytest.fixture
def make_adapter():
    def factory(events=None, delay_s=0.0, error=None):
        return RecordingAdapter(events=events, delay_s=delay_s, error=error)

    return factory


@pytest.fixture
def hanging_adapter():
    return HangingAdapter()


@pytest.fixture
def tool_files():
    base = Path(__file__).resolve().parents[2] / "integrations" / "conexus" / "alphalab-research-agent" / "tools"
    return {
        "Project-Files.tool.json": base / "Project-Files.tool.json",
        "Project-Run.tool.json": base / "Project-Run.tool.json",
    }


@pytest.fixture
def sample_task():
    return Task(task_id="sample_task", category="strategy_backtesting", user_request="Run a backtest")


@pytest.fixture
def dummy_limits():
    return Limits()
