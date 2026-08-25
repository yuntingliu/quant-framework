"""Versioned RQData synchronization templates.

Templates define a useful acquisition scope.  They do not own another data
runtime: every template is resolved into the same RQ acquirer, runtime store,
quality checks, and provider contracts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from alphalab.dataio.errors import DataValidationError


@dataclass(frozen=True)
class RQSyncTemplate:
    id: str
    label: str
    description: str
    market: str
    instrument_types: tuple[str, ...]
    datasets: tuple[str, ...]
    scope: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def resolve_datasets(self, requested: Iterable[str] | None) -> tuple[str, ...]:
        values = tuple(
            dict.fromkeys(
                self.datasets
                if requested is None
                else (str(value).strip().lower() for value in requested)
            )
        )
        if not values:
            raise DataValidationError("at least one sync dataset is required")
        unsupported = sorted(set(values) - set(self.datasets))
        if unsupported:
            raise DataValidationError(
                f"template {self.id!r} does not support datasets: {unsupported}"
            )
        return values


RQ_SYNC_TEMPLATES: tuple[RQSyncTemplate, ...] = (
    RQSyncTemplate(
        id="rq.a_share_daily",
        label="A 股日线",
        description="中国 A 股标的信息与前复权/不复权日线行情。",
        market="cn",
        instrument_types=("CS",),
        datasets=("instruments", "bars"),
        scope="a_shares",
    ),
    RQSyncTemplate(
        id="rq.etf_daily",
        label="ETF 日线",
        description="中国场内 ETF 标的信息与前复权/不复权日线行情。",
        market="cn",
        instrument_types=("ETF",),
        datasets=("instruments", "bars"),
        scope="etfs",
    ),
    RQSyncTemplate(
        id="rq.exchange_fund_daily",
        label="场内基金与指数日线",
        description="ETF、LOF 与指数标的信息和日线行情。",
        market="cn",
        instrument_types=("ETF", "LOF", "INDX"),
        datasets=("instruments", "bars"),
        scope="exchange_funds_and_indices",
    ),
    RQSyncTemplate(
        id="rq.a_share_research",
        label="A 股完整研究",
        description="A 股日线、PIT 财务报表、标准基本面因子与归因收益。",
        market="cn",
        instrument_types=("CS",),
        datasets=("instruments", "bars", "fundamentals", "factors"),
        scope="a_share_research",
    ),
)

_BY_ID = {item.id: item for item in RQ_SYNC_TEMPLATES}


def list_rq_sync_templates() -> tuple[RQSyncTemplate, ...]:
    return RQ_SYNC_TEMPLATES


def get_rq_sync_template(template_id: str) -> RQSyncTemplate:
    try:
        return _BY_ID[str(template_id).strip()]
    except KeyError:
        raise DataValidationError(f"unknown RQ sync template: {template_id!r}") from None


__all__ = [
    "RQ_SYNC_TEMPLATES",
    "RQSyncTemplate",
    "get_rq_sync_template",
    "list_rq_sync_templates",
]
