"""Versioned risk policies for paper accounts.

A policy is an immutable version of the trading rulebook: the numeric limits and
the loss/pause rules that a paper account follows. Changing a limit never edits an
existing version -- it appends the next version with a reason, so an evaluation
recorded months ago can always name the rules it was judged by.

The defaults in :class:`RiskLimits` reproduce the behaviour that existed before
this module: the pre-existing single-order limit (100,000) stays, and the limits
that were not enforced before are set to their non-binding value (1.0) so that
enabling this policy does not silently forbid portfolios that used to work.
Tightening them is a deliberate, recorded act.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

SEVERITY_BLOCKING = "blocking"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

SEVERITIES = (SEVERITY_BLOCKING, SEVERITY_WARNING, SEVERITY_INFO)

SCOPE_ACCOUNT = "account"
SCOPE_STRATEGY = "strategy"

SCOPES = (SCOPE_ACCOUNT, SCOPE_STRATEGY)

DEFAULT_POLICY_ID = "default"

_STATUS_ACTIVE = "active"
_STATUS_PAUSED = "paused"

ACCOUNT_STATUSES = (_STATUS_ACTIVE, _STATUS_PAUSED)


class RiskPolicyError(ValueError):
    """Raised when a policy payload is not a usable rulebook."""


def _reject_unknown(payload: dict[str, Any], allowed: tuple[str, ...], *, what: str) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise RiskPolicyError(f"{what} does not accept: {', '.join(unknown)}")


def _finite(name: str, value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RiskPolicyError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise RiskPolicyError(f"{name} must be finite, got {value!r}")
    return number


def _non_negative(name: str, value: Any) -> float:
    number = _finite(name, value)
    if number < 0:
        raise RiskPolicyError(f"{name} must not be negative, got {number!r}")
    return number


def _fraction(name: str, value: Any) -> float:
    number = _finite(name, value)
    if number < 0 or number > 1:
        raise RiskPolicyError(f"{name} must be between 0 and 1, got {number!r}")
    return number


def _fraction_or_none(name: str, value: Any) -> float | None:
    if value is None:
        return None
    return _fraction(name, value)


@dataclass(frozen=True)
class RiskLimits:
    """The numeric side of the rulebook."""

    #: Largest notional value of a single order.
    max_order_value: float = 100_000.0
    #: Largest single order as a share of account equity. 1.0 = non-binding.
    max_order_weight: float = 1.0
    #: Largest resulting weight of one position, measured after the order.
    max_position_weight: float = 1.0
    #: Largest resulting sum of absolute position weights.
    max_gross_exposure: float = 1.0
    #: Share of equity that must remain in cash after the order(s).
    min_cash_weight: float = 0.0
    #: Largest turnover of one execution pass, as a share of equity.
    max_turnover: float = 2.0
    #: Largest order as a share of the symbol's average daily volume. ``None`` disables it.
    max_participation: float | None = 0.10
    #: How many trading days of volume history the participation cap looks back on.
    volume_lookback_days: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "max_order_value", _non_negative("max_order_value", self.max_order_value)
        )
        object.__setattr__(
            self, "max_order_weight", _fraction("max_order_weight", self.max_order_weight)
        )
        object.__setattr__(
            self,
            "max_position_weight",
            _fraction("max_position_weight", self.max_position_weight),
        )
        object.__setattr__(
            self, "max_gross_exposure", _fraction("max_gross_exposure", self.max_gross_exposure)
        )
        object.__setattr__(
            self, "min_cash_weight", _fraction("min_cash_weight", self.min_cash_weight)
        )
        object.__setattr__(self, "max_turnover", _non_negative("max_turnover", self.max_turnover))
        object.__setattr__(
            self,
            "max_participation",
            _fraction_or_none("max_participation", self.max_participation),
        )
        if self.max_participation is not None and self.max_participation == 0:
            raise RiskPolicyError("max_participation must be greater than 0, or null to disable")
        lookback = self.volume_lookback_days
        if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1:
            raise RiskPolicyError(
                f"volume_lookback_days must be a positive whole number, got {lookback!r}"
            )
        if self.min_cash_weight + self.max_gross_exposure > 1.0 + 1e-9:
            raise RiskPolicyError(
                "min_cash_weight plus max_gross_exposure must leave room for each other "
                f"(got {self.min_cash_weight} + {self.max_gross_exposure})"
            )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RiskLimits:
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise RiskPolicyError("limits must be an object")
        _reject_unknown(payload, tuple(field_names(cls)), what="limits")
        return cls(**payload)


@dataclass(frozen=True)
class PauseRules:
    """When trading stops, and what has to happen before it resumes."""

    #: Stop trading when the last NAV move is at least this loss (0.03 = 3%).
    daily_loss_limit: float | None = 0.03
    #: Stop trading when equity is this far below its high-water mark.
    drawdown_limit: float | None = 0.15
    #: An automatic resume waits until the drawdown is back below this level.
    resume_drawdown: float | None = 0.10
    #: An automatic resume may also wait this many days after the pause.
    resume_after_days: int | None = None
    #: When true, only an explicit resume clears the pause.
    requires_manual_resume: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "daily_loss_limit",
            _fraction_or_none("daily_loss_limit", self.daily_loss_limit),
        )
        object.__setattr__(
            self, "drawdown_limit", _fraction_or_none("drawdown_limit", self.drawdown_limit)
        )
        object.__setattr__(
            self, "resume_drawdown", _fraction_or_none("resume_drawdown", self.resume_drawdown)
        )
        after_days = self.resume_after_days
        if after_days is not None:
            if isinstance(after_days, bool) or not isinstance(after_days, int) or after_days < 0:
                raise RiskPolicyError(
                    f"resume_after_days must be zero or a positive whole number, got {after_days!r}"
                )
        if not isinstance(self.requires_manual_resume, bool):
            raise RiskPolicyError("requires_manual_resume must be true or false")
        if (
            self.resume_drawdown is not None
            and self.drawdown_limit is not None
            and self.resume_drawdown > self.drawdown_limit
        ):
            raise RiskPolicyError(
                "resume_drawdown must not be larger than drawdown_limit "
                f"(got {self.resume_drawdown} > {self.drawdown_limit})"
            )
        if (
            self.drawdown_limit is None
            and self.daily_loss_limit is None
            and (self.resume_after_days is not None or self.resume_drawdown is not None)
        ):
            raise RiskPolicyError(
                "resume conditions set, but no pause condition: set daily_loss_limit "
                "or drawdown_limit, or clear the resume conditions"
            )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PauseRules:
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise RiskPolicyError("pause must be an object")
        _reject_unknown(payload, tuple(field_names(cls)), what="pause")
        return cls(**payload)

    def describe(self) -> str:
        parts = []
        if self.daily_loss_limit is not None:
            parts.append(f"daily loss {self.daily_loss_limit:.2%}")
        if self.drawdown_limit is not None:
            parts.append(f"drawdown {self.drawdown_limit:.2%}")
        if not parts:
            return "no automatic pause configured"
        condition = " or ".join(parts)
        if self.requires_manual_resume:
            return f"pause after {condition}; resume requires an explicit request"
        if self.resume_drawdown is not None:
            return f"pause after {condition}; resume when drawdown is back below {self.resume_drawdown:.2%}"
        if self.resume_after_days is not None:
            return f"pause after {condition}; resume {self.resume_after_days} day(s) later"
        return f"pause after {condition}; resume on the next evaluation"


@dataclass(frozen=True)
class RiskPolicy:
    """One immutable version of an account's or a strategy's rulebook."""

    policy_id: str = DEFAULT_POLICY_ID
    version: int = 1
    scope: str = SCOPE_ACCOUNT
    scope_id: str = ""
    limits: RiskLimits = field(default_factory=RiskLimits)
    pause: PauseRules = field(default_factory=PauseRules)
    reason: str = "Baseline policy"
    author: str = "system"
    created_at: str | None = None

    def __post_init__(self) -> None:
        if not str(self.policy_id or "").strip():
            raise RiskPolicyError("policy_id is required")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise RiskPolicyError(f"version must be a whole number starting at 1, got {self.version!r}")
        if self.scope not in SCOPES:
            raise RiskPolicyError(f"scope must be one of {SCOPES}, got {self.scope!r}")
        if self.scope == SCOPE_STRATEGY and not str(self.scope_id or "").strip():
            raise RiskPolicyError("a strategy-scoped policy needs a scope_id")
        if not str(self.reason or "").strip():
            raise RiskPolicyError("reason is required: a version records why it exists")
        if not isinstance(self.limits, RiskLimits):
            raise RiskPolicyError("limits must be a RiskLimits instance")
        if not isinstance(self.pause, PauseRules):
            raise RiskPolicyError("pause must be a PauseRules instance")

    @property
    def label(self) -> str:
        return f"{self.policy_id} v{self.version}"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at or _now()
        return payload

    def to_record(self) -> dict[str, Any]:
        """The row shape stored in ``risk_policies``."""
        created = self.created_at or _now()
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "limits": self.limits.as_dict(),
            "pause": self.pause.as_dict(),
            "reason": self.reason,
            "author": self.author,
            "created_at": created,
        }

    def to_json(self) -> dict[str, Any]:
        payload = self.to_record()
        payload["label"] = self.label
        payload["resume_condition"] = self.pause.describe()
        return payload

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> RiskPolicy:
        return cls(
            policy_id=str(record["policy_id"]),
            version=int(record["version"]),
            scope=str(record.get("scope") or SCOPE_ACCOUNT),
            scope_id=str(record.get("scope_id") or ""),
            limits=RiskLimits.from_dict(_as_mapping(record.get("limits"))),
            pause=PauseRules.from_dict(_as_mapping(record.get("pause"))),
            reason=str(record.get("reason") or ""),
            author=str(record.get("author") or ""),
            created_at=record.get("created_at"),
        )

    def revise(self, *, reason: str, author: str | None = None, **changes: Any) -> RiskPolicy:
        """Append the next version; the current object is never mutated."""
        if not str(reason or "").strip():
            raise RiskPolicyError("reason is required when a policy changes")
        limits_changes = changes.pop("limits", None)
        pause_changes = changes.pop("pause", None)
        allowed = ("scope", "scope_id", "policy_id")
        unknown = sorted(set(changes) - set(allowed))
        if unknown:
            raise RiskPolicyError(f"revise does not accept: {', '.join(unknown)}")
        limits = replace(self.limits, **limits_changes) if limits_changes else self.limits
        pause = replace(self.pause, **pause_changes) if pause_changes else self.pause
        if limits == self.limits and pause == self.pause:
            raise RiskPolicyError("the new version changes nothing; edit a limit or leave it as it is")
        return replace(
            self,
            version=self.version + 1,
            limits=limits,
            pause=pause,
            reason=str(reason).strip(),
            author=(author if author is not None else self.author) or "unknown",
            created_at=_now(),
            **changes,
        )


def field_names(cls: type) -> list[str]:
    return [item.name for item in cls.__dataclass_fields__.values()]  # type: ignore[attr-defined]


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RiskPolicyError(f"expected an object, got {type(value).__name__}")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_policy() -> RiskPolicy:
    return RiskPolicy(
        policy_id=DEFAULT_POLICY_ID,
        version=1,
        scope=SCOPE_ACCOUNT,
        scope_id="",
        limits=RiskLimits(),
        pause=PauseRules(),
        reason=(
            "Baseline policy: keeps the pre-existing 100,000 single-order limit, leaves the "
            "newer concentration and exposure limits non-binding, and pauses an account after "
            "a 3% daily loss or a 15% drawdown until an explicit resume."
        ),
        author="system",
    )


def policy_from_payload(
    payload: dict[str, Any],
    *,
    base: RiskPolicy | None = None,
) -> RiskPolicy:
    """Build the *next* policy version from an API payload.

    ``base`` is the version the caller is revising. Without a base, the default
    policy is the starting point. Unknown fields are rejected instead of ignored:
    a typo in a limit name must not silently leave that limit untouched.
    """
    if not isinstance(payload, dict):
        raise RiskPolicyError("policy payload must be an object")
    _reject_unknown(
        payload,
        ("reason", "author", "limits", "pause", "scope", "scope_id"),
        what="policy payload",
    )
    starting = base or default_policy()
    limits_payload = _as_mapping(payload.get("limits"))
    pause_payload = _as_mapping(payload.get("pause"))
    _reject_unknown(limits_payload, tuple(field_names(RiskLimits)), what="limits")
    _reject_unknown(pause_payload, tuple(field_names(PauseRules)), what="pause")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise RiskPolicyError("reason is required: explain why the rules change")
    limits = replace(starting.limits, **limits_payload) if limits_payload else starting.limits
    pause = replace(starting.pause, **pause_payload) if pause_payload else starting.pause
    if limits == starting.limits and pause == starting.pause:
        raise RiskPolicyError("the new version changes nothing; set at least one limit")
    return RiskPolicy(
        policy_id=starting.policy_id,
        version=starting.version + 1,
        scope=str(payload.get("scope") or starting.scope),
        scope_id=str(payload.get("scope_id") or starting.scope_id),
        limits=limits,
        pause=pause,
        reason=reason,
        author=str(payload.get("author") or "unknown"),
        created_at=_now(),
    )


__all__ = [
    "ACCOUNT_STATUSES",
    "DEFAULT_POLICY_ID",
    "PauseRules",
    "RiskLimits",
    "RiskPolicy",
    "RiskPolicyError",
    "SCOPES",
    "SCOPE_ACCOUNT",
    "SCOPE_STRATEGY",
    "SEVERITIES",
    "SEVERITY_BLOCKING",
    "SEVERITY_INFO",
    "SEVERITY_WARNING",
    "default_policy",
    "policy_from_payload",
]
