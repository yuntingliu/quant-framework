"""Non-wrapping registration decorators for Strategy SDK v1."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from alphalab.sdk.v1.model import Event


@dataclass(frozen=True)
class Registration:
    kind: str
    id: str
    label: str | None = None
    event: Event | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _decorate(
    kind: str,
    function: Callable[..., Any] | None,
    *,
    id: str | None,
    label: str | None,
    event: Event | str | None = None,
    **metadata: Any,
):
    def apply(target: Callable[..., Any]) -> Callable[..., Any]:
        public_id = str(id or target.__name__).strip()
        if not public_id or public_id.startswith("_"):
            raise ValueError(f"{kind} id must be a public identifier")
        if hasattr(target, "__alphalab_registration__"):
            raise ValueError(f"{target.__name__} already has an AlphaLab registration")
        target.__alphalab_registration__ = Registration(  # type: ignore[attr-defined]
            kind=kind,
            id=public_id,
            label=label,
            event=Event(event) if event is not None else None,
            metadata=dict(metadata),
        )
        return target

    return apply(function) if function is not None else apply


def universe(function=None, *, id: str | None = None, label: str | None = None):
    return _decorate("universe", function, id=id, label=label)


def factor(
    function=None,
    *,
    id: str | None = None,
    label: str | None = None,
    **legacy_metadata: Any,
):
    # Immutable SDK v1 revisions may still contain inputs=. Current authoring
    # derives data access from context.* calls and does not expose that argument.
    unknown = set(legacy_metadata) - {"inputs"}
    if unknown:
        raise TypeError(f"unsupported @factor arguments: {', '.join(sorted(unknown))}")
    return _decorate("factor", function, id=id, label=label)


def schedule(function=None, *, id: str | None = None, label: str | None = None):
    return _decorate("schedule", function, id=id, label=label)


def signal(
    function=None,
    *,
    id: str | None = None,
    label: str | None = None,
    schedule: Any = None,
):
    return _decorate("signal", function, id=id, label=label, schedule=schedule)


def portfolio(function=None, *, id: str | None = None, label: str | None = None):
    return _decorate("portfolio", function, id=id, label=label)


def on_event(
    event: Event | str,
    *,
    id: str | None = None,
    label: str | None = None,
):
    return _decorate("event", None, id=id, label=label, event=event)


def execution(function=None, *, id: str | None = None, label: str | None = None):
    return _decorate("execution", function, id=id, label=label)


__all__ = [
    "Registration",
    "execution",
    "factor",
    "on_event",
    "portfolio",
    "schedule",
    "signal",
    "universe",
]
