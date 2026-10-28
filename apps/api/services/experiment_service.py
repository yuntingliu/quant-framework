"""Serial experiment orchestration using durable canonical backtest jobs."""

import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from threading import Event, Lock

from alphalab.strategy.repository import StrategyRepository
from apps.api.services import backtest_job_service

logger = logging.getLogger(__name__)


class ExperimentManager:
    def __init__(self, db_path=None, jobs=None):
        self._db_path = db_path
        self._jobs = jobs if jobs is not None else backtest_job_service.manager()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alphalab-experiment")
        self._lock = Lock()
        self._futures = {}
        self._stopping = Event()

    def start(self, experiment_id: str) -> dict:
        repository = StrategyRepository(self._db_path)
        try:
            experiment = repository.start_experiment(experiment_id)
        finally:
            repository.close()
        if experiment["execution"]["status"] != "running":
            return experiment
        with self._lock:
            if experiment_id in self._futures:
                return experiment
            future = self._executor.submit(self._drive, experiment_id)
            self._futures[experiment_id] = future
        future.add_done_callback(lambda _: self._forget(experiment_id))
        return experiment

    def _forget(self, experiment_id):
        with self._lock:
            self._futures.pop(experiment_id, None)

    def _drive(self, experiment_id):
        repository = StrategyRepository(self._db_path)
        try:
            while not self._stopping.is_set():
                job_id = repository.advance_experiment(experiment_id)
                if job_id is None:
                    return
                self._jobs.get(job_id)
                self._stopping.wait(0.1)
        except Exception as exc:
            logger.exception("Experiment %s stopped", experiment_id)
            repository.fail_experiment(experiment_id, backtest_job_service._safe_error_summary(exc))
        finally:
            repository.close()

    def shutdown(self):
        self._stopping.set()
        self._executor.shutdown(wait=True)


@lru_cache(maxsize=1)
def manager() -> ExperimentManager:
    return ExperimentManager()
