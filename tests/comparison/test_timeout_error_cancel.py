import threading

import pytest

from apps.api.services.comparison import ComparisonRunner, Config, Limits


def run_one(adapter, **kwargs):
    runner = ComparisonRunner()
    records = runner.run(
        [Config.A],
        task_subset=["stub_data_diagnosis_repair"],
        repetitions=1,
        use_doubles=True,
        adapters={Config.A: adapter},
        **kwargs,
    )
    assert len(records) == 1
    return records[0]


def test_time_limit_closes_the_record(hanging_adapter):
    rec = run_one(hanging_adapter, limits=Limits(time_s=0.2))

    assert rec.execution["status"] == "timeout"
    assert rec.is_complete is True
    assert rec.timestamps["duration_s"] >= 0.15
    assert rec.results["errors"] == [{"code": "TIMEOUT", "summary": "Trial exceeded its 0.2s time limit"}]
    assert hanging_adapter.stop_calls >= 1


def test_cancel_event_closes_the_record(hanging_adapter):
    cancel = threading.Event()
    timer = threading.Timer(0.1, cancel.set)
    timer.start()
    try:
        rec = run_one(hanging_adapter, cancel_event=cancel)
    finally:
        timer.cancel()

    assert rec.execution["status"] == "cancelled"
    assert rec.is_complete is True
    assert rec.results["errors"][0]["code"] == "CANCELLED"
    assert hanging_adapter.stop_calls >= 1


def test_runner_cancel_stops_the_rest_of_the_plan(hanging_adapter, make_adapter):
    runner = ComparisonRunner()
    timer = threading.Timer(0.1, runner.cancel)
    timer.start()
    try:
        records = runner.run(
            [Config.A, Config.B],
            task_subset=["stub_data_diagnosis_repair"],
            repetitions=1,
            interleave=False,
            use_doubles=True,
            adapters={Config.A: hanging_adapter, Config.B: make_adapter()},
        )
    finally:
        timer.cancel()

    assert len(records) == 1
    assert records[0].config is Config.A
    assert records[0].execution["status"] == "cancelled"
    assert records[0].is_complete is True


def test_step_limit_stops_after_the_limit(make_adapter):
    adapter = make_adapter(events=[{"type": "step"} for _ in range(5)])

    rec = run_one(adapter, limits=Limits(steps=2))

    assert rec.execution["status"] == "failed"
    assert rec.results["errors"][0]["code"] == "LIMIT_EXCEEDED"
    assert rec.resources["steps"] == 3
    assert len(rec.execution["events"]) == 3
    assert rec.is_complete is True


def test_token_limit_stops_after_the_limit(make_adapter):
    events = [{"type": "usage", "usage": {"total_tokens": 10}} for _ in range(4)]
    adapter = make_adapter(events=events)

    rec = run_one(adapter, limits=Limits(tokens=15))

    assert rec.execution["status"] == "failed"
    assert rec.results["errors"][0]["code"] == "LIMIT_EXCEEDED"
    assert rec.resources["tokens"] == 20
    assert len(rec.execution["events"]) == 2


def test_cost_limit_stops_after_the_limit(make_adapter):
    events = [{"type": "usage", "cost_usd": 0.4} for _ in range(4)]
    adapter = make_adapter(events=events)

    rec = run_one(adapter, limits=Limits(cost_usd=0.5))

    assert rec.execution["status"] == "failed"
    assert rec.results["errors"][0]["code"] == "LIMIT_EXCEEDED"
    assert rec.resources["cost_usd"] == pytest.approx(0.8)
    assert len(rec.execution["events"]) == 2


def test_adapter_error_is_recorded_and_the_record_closes(make_adapter):
    adapter = make_adapter(events=[{"type": "start"}], error=ValueError("adapter exploded"))

    rec = run_one(adapter)

    assert rec.execution["status"] == "failed"
    assert rec.results["errors"] == [{"code": "ERROR", "summary": "adapter exploded"}]
    assert len(rec.execution["events"]) == 1
    assert rec.is_complete is True


def test_adapter_timeout_error_maps_to_timeout_status(make_adapter):
    adapter = make_adapter(error=TimeoutError("adapter timed out"))

    rec = run_one(adapter)

    assert rec.execution["status"] == "timeout"
    assert rec.results["errors"][0]["code"] == "TIMEOUT"
    assert rec.is_complete is True


def test_unlimited_trial_completes_and_stops_the_adapter(make_adapter):
    adapter = make_adapter(events=[{"type": "start"}, {"type": "done"}])

    rec = run_one(adapter, limits=Limits())

    assert rec.execution["status"] == "completed"
    assert rec.results["errors"] == []
    assert len(rec.execution["events"]) == 2
    assert rec.is_complete is True
    assert adapter.stop_calls >= 1
