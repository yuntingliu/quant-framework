"""Shared input contract for the report tools."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


class LabModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Leg(LabModel):
    contract: str
    kind: Literal["P", "C"]
    strike: float = Field(gt=0)
    quantity: int = Field(ge=-1000, le=1000)
    multiplier: int = Field(gt=0)
    entry_price: float = Field(ge=0)
    iv: float = Field(gt=0)


class ReportRequest(LabModel):
    underlying: str
    as_of: date
    expiry: date
    spot: float = Field(gt=0)
    rate: float
    dividend_yield: float
    legs: list[Leg] = Field(min_length=1, max_length=8)
    terminal_spots: list[Annotated[float, Field(ge=0)]] = Field(min_length=1, max_length=200)
    spot_shocks: list[Annotated[float, Field(gt=-1, le=2)]] = Field(min_length=1, max_length=20)
    iv_shifts: list[Annotated[float, Field(ge=-1, le=1)]] = Field(min_length=1, max_length=10)
    scenario_budget_cny: float = Field(gt=0)

    @property
    def maturity(self) -> float:
        return (self.expiry - self.as_of).days / 365

    @model_validator(mode="after")
    def check_case(self):
        if self.expiry <= self.as_of:
            raise PydanticCustomError("invalid_expiry", "expiry must follow as_of for a mark-to-model stress")
        if any(leg.quantity == 0 for leg in self.legs):
            raise PydanticCustomError("zero_quantity", "each leg needs a nonzero signed contract quantity")
        if len({leg.contract for leg in self.legs}) != len(self.legs):
            raise PydanticCustomError("repeated_contract", "combine quantities for repeated contract IDs")
        if len(self.spot_shocks) * len(self.iv_shifts) > 100:
            raise PydanticCustomError("too_many_scenarios", "use at most 100 stress scenarios")
        if any(leg.iv + min(self.iv_shifts) <= 0 for leg in self.legs):
            raise PydanticCustomError("nonpositive_shocked_iv", "each shocked leg IV must be positive")
        return self


class MonteCarloRequest(LabModel):
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    maturity: float = Field(gt=0, le=5, description="ACT/365F years to expiry")
    rate: float = Field(ge=-1, le=1, description="Continuous annual risk-free rate as a decimal")
    dividend_yield: float = Field(ge=-1, le=1, description="Continuous annual yield as a decimal")
    volatility: float = Field(gt=0, le=3, description="Annual diffusion volatility as a decimal")
    kind: Literal["P", "C"]
    model: Literal["gbm", "merton"]
    paths: int = Field(default=100_000, ge=2, le=1_000_000, strict=True)
    seed: int = Field(default=42, ge=0, le=2**32 - 1, strict=True)
    jump_intensity: float = Field(default=0, ge=0, le=10, description="Expected jumps per year")
    jump_mean: float = Field(default=-0.10, ge=-1, le=1, description="Mean log jump")
    jump_volatility: float = Field(default=0.10, ge=0, le=1, description="Standard deviation of log jump")

    @model_validator(mode="after")
    def check_gbm(self):
        if self.model == "gbm" and self.jump_intensity != 0:
            raise ValueError("GBM requires zero jump intensity")
        return self
