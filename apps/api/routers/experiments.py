"""Create and inspect experiment records without scheduling backtests."""

from fastapi import APIRouter, HTTPException, Query

from alphalab.strategy.experiments import ExperimentSpec
from alphalab.strategy.repository import StrategyRepository
from alphalab.strategy.sdk_runtime import SdkRuntimeError
from alphalab.strategy.source import StrategySourceError

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


class ExperimentRequest(ExperimentSpec):
    confirm_python_execution: bool = False


@router.post("", status_code=201)
def create_experiment(request: ExperimentRequest) -> dict:
    if not request.confirm_python_execution:
        raise HTTPException(status_code=409, detail="candidate probes execute trusted local Python and require confirmation")
    repository = StrategyRepository().select_strategy(request.strategy_id)
    try:
        return repository.create_experiment(request.model_dump(exclude={"confirm_python_execution"}))
    except KeyError:
        raise HTTPException(status_code=404, detail="strategy or validation package not found") from None
    except (ValueError, StrategySourceError, SdkRuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        repository.close()


@router.get("")
def list_experiments(project_id: str, limit: int = Query(default=50, ge=1, le=100)) -> list[dict]:
    repository = StrategyRepository()
    try:
        return repository.list_experiments(project_id, limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        repository.close()


@router.get("/{experiment_id}")
def get_experiment(experiment_id: str) -> dict:
    repository = StrategyRepository()
    try:
        result = repository.get_experiment(experiment_id)
        if result is None:
            raise HTTPException(status_code=404, detail="experiment not found")
        return result
    finally:
        repository.close()
