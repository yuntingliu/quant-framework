"""Pydantic request models for the workstation API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SignalRequest(BaseModel):
    strategy_id: str
    as_of_date: str | None = None
    persist: bool = True
    profile: Literal["runtime"] = "runtime"


class PaperOrderRequest(BaseModel):
    symbol: str
    action: str
    quantity: float = Field(gt=0)
    price: float | None = Field(default=None, gt=0)
    signal_id: str | None = None
    profile: Literal["runtime"] = "runtime"
    account_id: str = "paper"

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

    @field_validator("quantity")
    @classmethod
    def validate_board_lot(cls, value: float) -> float:
        if abs(value / 100 - round(value / 100)) > 1e-9:
            raise ValueError("quantity must use 100-share board lots")
        return value

    @field_validator("account_id")
    @classmethod
    def validate_account(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("account_id is required")
        return normalized


class PaperRebalanceRequest(BaseModel):
    strategy_id: str | None = None
    signal_id: str | None = None
    profile: Literal["runtime"] = "runtime"
    account_id: str = "paper"
    confirm: bool = False

    @field_validator("account_id")
    @classmethod
    def validate_account(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("account_id is required")
        return normalized
