from __future__ import annotations

import pandas as pd
import pytest

from alphalab.data_sdk.v1 import DataCache, PythonDataSource, data_source


class CustomProvider:
    def get_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        freq: str = "1d",
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "date": [pd.Timestamp("2025-01-02")],
                "symbol": [symbols[0]],
                "close": [10.0],
            }
        )

    def get_symbols(self, universe: str = "all") -> list[str]:
        return ["510300.SH"]

    def get_instruments(self, asof_date: str | None = None) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "snapshot_date": [pd.Timestamp(asof_date or "2025-01-02")],
                "symbol": ["510300.SH"],
                "asset_type": ["ETF"],
            }
        )


def test_custom_python_source_uses_the_normal_data_engine(tmp_path) -> None:
    provider = CustomProvider()
    source = data_source("custom.vendor", market=provider, instruments=provider)
    engine = source.create_engine(cache=DataCache(tmp_path))

    assert isinstance(source, PythonDataSource)
    assert source.capabilities == ("market", "instruments")
    assert engine.providers()["market"] == ["custom.vendor"]
    assert engine.get_symbols() == ["510300.SH"]
    bars = engine.get_bars(
        ["510300.SH"],
        "2025-01-01",
        "2025-01-03",
        fields=["close"],
        use_cache=False,
    )
    assert bars.iloc[0].to_dict()["close"] == 10.0


def test_custom_python_source_rejects_unversioned_or_incomplete_inputs() -> None:
    provider = CustomProvider()
    with pytest.raises(ValueError, match="data source id"):
        data_source("Custom Provider", market=provider, instruments=provider)
    with pytest.raises(TypeError, match="market"):
        data_source("custom.bad", market=object(), instruments=provider)
