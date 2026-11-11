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
