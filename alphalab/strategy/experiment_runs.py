"""Transactional experiment progression over the existing backtest job table."""

import json
import math
from uuid import uuid4


class ExperimentRuns:
    def start_experiment(self, experiment_id: str) -> dict:
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            experiment = self.get_experiment(experiment_id)
            if experiment is None:
                raise KeyError(experiment_id)
            if experiment["protocol"].get("training") is not None:
                raise ValueError("training/fitting is not implemented; use fixed parameter candidates")
            self._conn.execute(
                "INSERT OR IGNORE INTO experiment_execution(experiment_id, status) VALUES (?, 'running')",
                (experiment_id,),
            )
        return self.get_experiment(experiment_id)

    def advance_experiment(self, experiment_id: str) -> str | None:
        """Reconcile completed jobs and return the sole job to execute or wait for."""
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            experiment = self.get_experiment(experiment_id)
            if experiment is None:
                raise KeyError(experiment_id)
            execution = experiment["execution"]
            if execution is None or execution["status"] != "running":
                return None
            protocol = experiment["protocol"]
            for trial in experiment["trials"]:
                if trial["status"] not in {"queued", "running"}:
                    continue
                job = self._conn.execute("SELECT * FROM backtest_jobs WHERE id = ?", (trial["job_id"],)).fetchone()
                if job is None:
                    self._finish_experiment(experiment_id, "failed", "linked backtest job is missing")
                    return None
                self._conn.execute(
                    "UPDATE experiment_trials SET status = ?, result_id = ?, error = ? WHERE id = ?",
                    (job["status"], job["result_id"], job["error_summary"] or job["error"], trial["id"]),
                )
                if job["status"] in {"queued", "running"}:
                    return job["id"]
            experiment = self.get_experiment(experiment_id)
            tests = [trial for trial in experiment["trials"] if trial["stage"] == "test"]
            if tests and tests[0]["status"] != "planned":
                status = "succeeded" if tests[0]["status"] == "succeeded" else "failed"
                self._finish_experiment(experiment_id, status, tests[0]["error"])
                return None
            pending = next((trial for trial in experiment["trials"] if trial["status"] == "planned"), None)
            attempted = sum(trial["job_id"] is not None for trial in experiment["trials"])
            if attempted >= protocol["max_trials"]:
                self._finish_experiment(experiment_id, "failed", "trial budget exhausted")
                return None
            if pending is None:
                selection = self._select_experiment_candidate(experiment)
                self._conn.execute(
                    "UPDATE experiment_execution SET selection_json = ? WHERE experiment_id = ?",
                    (json.dumps(selection, allow_nan=False), experiment_id),
                )
                if selection["candidate_id"] is None:
                    self._finish_experiment(experiment_id, "failed", "no eligible validation candidate")
                    return None
                pending = {
                    "id": uuid4().hex, "candidate_id": selection["candidate_id"], "stage": "test",
                    "start_date": protocol["test"]["start"], "end_date": protocol["test"]["end"],
                }
                self._conn.execute(
                    """INSERT INTO experiment_trials
                       (id, experiment_id, candidate_id, stage, start_date, end_date)
                       VALUES (?, ?, ?, 'test', ?, ?)""",
                    (pending["id"], experiment_id, pending["candidate_id"], pending["start_date"], pending["end_date"]),
                )
            candidate = next(item for item in experiment["candidates"] if item["candidate_id"] == pending["candidate_id"])
            request = {
                "project_id": protocol["project_id"], "strategy_id": protocol["strategy_id"],
                "strategy_name": protocol["strategy_id"], "revision": candidate["revision"],
                "validation_revision": protocol["validation_revision"], "settings": protocol["settings"],
                "profile": "runtime", "start_date": pending["start_date"], "end_date": pending["end_date"],
                "batch_id": experiment_id,
            }
            job_id = pending["id"]
            self._conn.execute(
                "INSERT INTO backtest_jobs(id, status, request_json, message) VALUES (?, 'queued', ?, 'Experiment trial')",
                (job_id, json.dumps(request, allow_nan=False)),
            )
            self._conn.execute(
                "UPDATE experiment_trials SET job_id = ?, status = 'queued' WHERE id = ?",
                (job_id, pending["id"]),
            )
            return job_id

    def _select_experiment_candidate(self, experiment: dict) -> dict:
        protocol = experiment["protocol"]
        scores, excluded = {}, {}
        for trial in experiment["trials"]:
            if trial["stage"] != "validation":
                continue
            candidate_id = trial["candidate_id"]
            if trial["status"] != "succeeded":
                excluded[candidate_id] = trial["error"] or trial["status"]
                continue
            job = self._conn.execute("SELECT result_json FROM backtest_jobs WHERE id = ?", (trial["job_id"],)).fetchone()
            result = json.loads(job["result_json"] or "{}")
            score = result.get("metrics", {}).get(protocol["selection_metric"])
            if result.get("execution_reliable") is not True or result.get("research_invalid_reasons"):
                excluded[candidate_id] = "execution or research evidence is invalid or unassessed"
            elif isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                excluded[candidate_id] = "selection metric is missing or non-finite"
            else:
                scores[candidate_id] = score
        ordered = [candidate["candidate_id"] for candidate in experiment["candidates"] if candidate["candidate_id"] in scores]
        choose = max if protocol["selection_direction"] == "maximize" else min
        winner = choose(ordered, key=scores.get) if ordered else None
        return {"candidate_id": winner, "metric": protocol["selection_metric"], "scores": scores, "excluded": excluded}

    def fail_experiment(self, experiment_id: str, error: str) -> None:
        with self._lock, self._conn:
            self._finish_experiment(experiment_id, "failed", error)

    def _finish_experiment(self, experiment_id: str, status: str, error: str | None) -> None:
        self._conn.execute(
            "UPDATE experiment_execution SET status = ?, error = ?, finished_at = datetime('now') WHERE experiment_id = ? AND status = 'running'",
            (status, error, experiment_id),
        )
