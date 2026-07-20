"""Pydantic request models for the workstation API."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class SignalRequest(BaseModel):
    strategy_id: str
    as_of_date: str | None = None
    persist: bool = True
    profile: str = "demo"


class PaperOrderRequest(BaseModel):
    symbol: str
    action: str
    quantity: float = Field(gt=0)
    price: float | None = Field(default=None, gt=0)
    signal_id: str | None = None

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"buy", "sell"}:
            raise ValueError("action must be buy or sell")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol is required")
        return normalized
