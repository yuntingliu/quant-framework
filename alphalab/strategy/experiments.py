"""Validated configuration for recorded, not yet scheduled, experiments."""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExperimentWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.start >= self.end:
            raise ValueError("window start must precede end")
        return self


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    strategy_id: str = Field(default="main", pattern=r"^[a-z][a-z0-9_]{0,63}$")
    base_revision: int = Field(ge=1)
    validation_revision: int = Field(ge=1)
    candidates: list[dict[str, dict[str, Any]]] = Field(min_length=1, max_length=32)
    training: ExperimentWindow | None = None
    validation: ExperimentWindow
    test: ExperimentWindow
    selection_metric: Literal["sharpe", "total_return"] = "sharpe"
    selection_direction: Literal["maximize", "minimize"] = "maximize"
    tie_break: Literal["candidate_order"] = "candidate_order"
    max_trials: int = Field(ge=2, le=1000)
    max_concurrency: Literal[1] = 1
    data_manifest_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_protocol(self):
        if self.training and self.training.end >= self.validation.start:
            raise ValueError("training must finish before validation starts")
        if self.validation.end >= self.test.start:
            raise ValueError("validation must finish before test starts")
        if self.max_trials < len(self.candidates) + 1:
            raise ValueError("budget must cover every validation trial and one test trial")
        if any(candidate == previous for index, candidate in enumerate(self.candidates)
               for previous in self.candidates[:index]):
            raise ValueError("duplicate candidates are not allowed")
        return self
