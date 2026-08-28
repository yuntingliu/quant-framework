from __future__ import annotations

import pandas as pd
import pytest

from alphalab import create_rq_engine_from_env
from alphalab.dataio import DataCache, DataLoadError, RQDataClient, RQDataConfig, RQDataProvider


class FakeRQData:
    def __init__(self) -> None:
        self.init_args = None
        self.init_kwargs = None
        self.price_kwargs = None
        self.fundamental_calls = []

    def init(self, user, password, host, **kwargs) -> None:
        self.init_args = (user, password, host)
        self.init_kwargs = kwargs

    def get_price(self, order_book_ids, **kwargs):
        self.price_kwargs = {"order_book_ids": order_book_ids, **kwargs}
        index = pd.MultiIndex.from_tuples(
            [
                ("000001.XSHE", pd.Timestamp("2025-01-02")),
                ("600000.XSHG", pd.Timestamp("2025-01-02")),
            ],
            names=["order_book_id", "date"],
        )
        return pd.DataFrame(
            {
                "open": [10.0, 20.0],
                "high": [10.5, 20.5],
                "low": [9.5, 19.5],
                "close": [10.2, 20.2],
                "volume": [1000.0, 2000.0],
                "total_turnover": [10200.0, 40400.0],
                "prev_close": [10.0, 20.0],
                "num_trades": [12, 24],
            },
            index=index,
        )

    def all_instruments(self, **kwargs):
        assert kwargs == {"type": "CS", "market": "cn"}
        return pd.DataFrame(
            {
                "order_book_id": ["600000.XSHG", "000001.XSHE", "900901.XSHG"],
                "exchange": ["XSHG", "XSHE", "XSHG"],
                "round_lot": [100, 100, 100],
            }
        )

    def get_pit_financials_ex(self, **kwargs):
        self.fundamental_calls.append(kwargs)
        symbols = kwargs["order_book_ids"]
        index = pd.MultiIndex.from_tuples(
            [(symbol, "2024q4") for symbol in symbols],
            names=["order_book_id", "quarter"],
        )
        return pd.DataFrame(
            {
                "info_date": [pd.Timestamp("2025-03-20")] * len(symbols),
                "revenue": [100.0 + i for i in range(len(symbols))],
            },
            index=index,
        )


def _provider() -> tuple[RQDataProvider, FakeRQData]:
    module = FakeRQData()
    config = RQDataConfig(user="demo", password="secret", host="rq.example:16011")
    return RQDataProvider(RQDataClient(config, module=module)), module


def test_rq_config_requires_only_three_environment_values() -> None:
    config = RQDataConfig.from_env(
        {"RQ_USER": "demo", "RQ_PASSWORD": "secret", "RQ_HOST": "rq.example:16011"}
    )
    assert config.user == "demo"
    assert config.host == "rq.example:16011"
    assert "secret" not in repr(config)
    assert "demo" not in repr(config)
    assert "rq.example" not in repr(config)

    with pytest.raises(DataLoadError, match="RQ_PASSWORD"):
        RQDataConfig.from_env({"RQ_USER": "demo", "RQ_HOST": "rq.example:16011"})


def test_rq_market_provider_is_lazy_and_normalizes_bars() -> None:
    provider, module = _provider()
    assert provider.client.connected is False

    bars = provider.get_bars(
        ["000001.SZ", "600000.SH"],
        "2025-01-01",
        "2025-01-03",
    )

    assert module.init_args == ("demo", "secret", "rq.example:16011")
    assert module.init_kwargs == {"lazy": False}
    assert module.price_kwargs["order_book_ids"] == ["000001.XSHE", "600000.XSHG"]
    assert module.price_kwargs["fields"][-1] == "total_turnover"
    assert bars["symbol"].tolist() == ["000001.SZ", "600000.SH"]
    assert bars["volume"].tolist() == [10.0, 20.0]
    assert bars["amount"].tolist() == [10200.0, 40400.0]
    assert provider.get_symbols() == ["000001.SZ", "600000.SH"]
    instruments = provider.get_instruments("2025-01-02")
    assert instruments["symbol"].tolist() == ["600000.SH", "000001.SZ"]
    assert instruments["snapshot_date"].notna().all()
    assert instruments["exchange"].tolist() == ["XSHG", "XSHE"]
    assert instruments["round_lot"].tolist() == [100, 100]


def test_rq_market_discovery_preserves_all_daily_fields() -> None:
    provider, module = _provider()

    bars = provider.get_daily_bars_all_fields(
        ["000001.SZ", "600000.SH"],
        "2025-01-01",
        "2025-01-03",
    )

    assert module.price_kwargs["fields"] is None
    assert bars["prev_close"].tolist() == [10.0, 20.0]
    assert bars["num_trades"].tolist() == [12, 24]
    assert bars["amount"].tolist() == [10200.0, 40400.0]


def test_rq_provider_supports_etf_and_lof_instrument_scopes() -> None:
    class FundRQData(FakeRQData):
        def __init__(self) -> None:
            super().__init__()
            self.instrument_calls: list[dict] = []

        def all_instruments(self, **kwargs):
            self.instrument_calls.append(kwargs)
            symbol = "510300.XSHG" if kwargs["type"] == "ETF" else "160105.XSHE"
            return pd.DataFrame({"order_book_id": [symbol], "exchange": ["XSHG"]})

    module = FundRQData()
    client = RQDataClient(
        RQDataConfig(user="demo", password="secret", host="rq.example:16011"),
        module=module,
    )
    provider = RQDataProvider(client, instrument_types=("ETF", "LOF"))

    instruments = provider.get_instruments()

    assert module.instrument_calls == [
        {"type": "ETF", "market": "cn"},
        {"type": "LOF", "market": "cn"},
    ]
    assert instruments[["symbol", "asset_type"]].to_dict("records") == [
        {"symbol": "510300.SH", "asset_type": "ETF"},
        {"symbol": "160105.SZ", "asset_type": "LOF"},
    ]


def test_rq_fundamentals_are_point_in_time_and_batched() -> None:
    provider, module = _provider()
    provider.fundamental_batch_size = 1

    frame = provider.get_fundamentals(
        ["000001.SZ", "600000.SH"],
        ["revenue"],
        "2024q4",
        "2024q4",
        asof_date="2025-03-31",
    )

    assert len(module.fundamental_calls) == 2
    assert all(call["date"] == "2025-03-31" for call in module.fundamental_calls)
    assert frame["symbol"].tolist() == ["000001.SZ", "600000.SH"]
    assert frame["available_date"].max() <= pd.Timestamp("2025-03-31")


def test_rq_engine_factory_registers_market_and_fundamental(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RQ_USER", "demo")
    monkeypatch.setenv("RQ_PASSWORD", "secret")
    monkeypatch.setenv("RQ_HOST", "rq.example:16011")

    engine = create_rq_engine_from_env(cache=DataCache(tmp_path))

    assert engine.providers() == {
        "market": ["rq"],
        "instrument": ["rq"],
        "fundamental": ["rq"],
        "factor": [],
        "research": ["rq"],
    }
