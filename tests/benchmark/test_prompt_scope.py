"""Every constraint needed to solve a task must reach the agent.

Only ``prompt.md`` and the supplied ``workspace/`` are agent-visible. A window
recorded in a reference invocation but never stated to the agent produces the
defect this suite exists to prevent: a correct agent evaluates a different
window, computes correct numbers, and fails the blocking conditions.

That is not hypothetical. The fixture spans 2025-01-02 to 2027-12-01 and today
falls inside it, so "the full history available" is genuinely two windows. Read
as ending today, momentum research returns 20 periods rather than 34 — enough to
flip the task from sufficient to insufficient — and the derived-risk series
returns 22 observations rather than 36.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "benchmark"

# Tasks whose whole point is an unresolved request. Stating a scope would
# destroy the stimulus, and they grade no window-sensitive number.
AMBIGUOUS_BY_DESIGN = {"audit-underspecified-request"}

WINDOW_KEYS = ("start_date", "end_date", "as_of_date", "start", "end", "as_of")


def _tasks() -> list[tuple[Path, dict]]:
    return [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(BENCHMARK.glob("tasks/*/*/task.json"))
    ]


def _reference(task: dict) -> dict:
    return json.loads(
        (ROOT / task["reference"]["directory"] / task["reference"]["expected_file"]).read_text(
            encoding="utf-8"
        )
    )


def _renderings(iso: str) -> set[str]:
    """Forms a prompt may legitimately use for one date."""

    date = dt.date.fromisoformat(iso)
    month = date.strftime("%B")
    day = date.day
    return {
        iso,
        f"{day} {month} {date.year}",
        f"{month} {day}, {date.year}",
        f"{month} {day} {date.year}",
        # "2 January through 7 May 2025" states the year once, at the end.
        f"{day} {month}",
        f"{month} {day}",
    }


def _agent_visible_text(path: Path, task: dict) -> str:
    text = (path.parent / task["request"]["prompt_file"]).read_text(encoding="utf-8")
    workspace = task["environment"].get("workspace")
    if workspace:
        for item in sorted((path.parent / workspace).rglob("*")):
            if item.is_file():
                text += "\n" + item.read_text(encoding="utf-8", errors="ignore")
    return text


def _window_sensitive_conditions(task: dict) -> list[dict]:
    """Blocking numeric or scalar checks on a value a window would change."""

    return [
        condition
        for condition in task["pass_conditions"]
        if condition["weight"] == "blocking"
        and condition["type"] in {"numeric", "scalar_equals", "set_equals", "set_contains"}
    ]


def test_every_recorded_window_is_stated_to_the_agent():
    checked = 0
    for path, task in _tasks():
        if task["task_id"] in AMBIGUOUS_BY_DESIGN:
            continue
        invocation = _reference(task).get("invocation") or {}
        dates = {
            value
            for key, value in invocation.items()
            if key in WINDOW_KEYS and isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        }
        if not dates or not _window_sensitive_conditions(task):
            continue
        visible = _agent_visible_text(path, task)
        for iso in sorted(dates):
            assert any(form in visible for form in _renderings(iso)), (
                f"{task['task_id']}: its reference evaluates {iso}, but no agent-visible input "
                f"states that date. A correct agent would evaluate a different window and fail "
                f"this task's blocking conditions."
            )
            checked += 1
    assert checked >= 6, f"Only {checked} recorded window dates were verified"


def test_the_fixture_span_is_not_self_evident_from_today():
    # The guard above matters only while "full history" is ambiguous. If the
    # fixture ever ends before today, re-read whether these prompts still need
    # explicit dates — but do not silently drop them.
    fixture = json.loads(
        (BENCHMARK / "data" / "research-small" / "fixture.json").read_text(encoding="utf-8")
    )
    end = dt.date.fromisoformat(fixture["calendar"]["end"])
    start = dt.date.fromisoformat(fixture["calendar"]["start"])
    assert start < end
    # No assertion on today: the point is that the prompts state their window
    # outright, so the task set does not depend on when it is run.
    for task_id in ("factor-rank-ic-evidence", "risk-derived-series-claim"):
        path = next(BENCHMARK.glob(f"tasks/*/{task_id}/prompt.md"))
        text = path.read_text(encoding="utf-8")
        assert fixture["calendar"]["start"] in text and fixture["calendar"]["end"] in text, (
            f"{task_id} asks for the project history, so it must name the span outright"
        )


def test_no_prompt_defers_its_scope_to_the_run_date():
    # "today", "now", "current date" and friends make a task's answer depend on
    # when it runs, which no frozen reference can track. A prohibition such as
    # "without extending it to the current date" is the opposite, so the phrase
    # is only a defect when it is not negated.
    banned = ("as of today", "up to today", "through today", "until today",
              "to the current date", "up to now", "latest available data")
    negations = ("without", "not ", "never", "don't", "do not", "rather than",
                 "instead of", "no later than", "excluding")
    for path, task in _tasks():
        visible = _agent_visible_text(path, task).lower()
        for phrase in banned:
            for match in re.finditer(re.escape(phrase), visible):
                lead = visible[max(0, match.start() - 60) : match.start()]
                if any(cue in lead for cue in negations):
                    continue
                assert False, (
                    f"{task['task_id']} defers its scope to the run date via {phrase!r}; "
                    f"state the window instead. Context: ...{lead[-50:]}{phrase}..."
                )
