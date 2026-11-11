"""Read-only daily data adapter for the PS2 option-contract backtest."""

from pathlib import Path

import pandas as pd
from pyarrow.parquet import ParquetFile

from alphalab.dataio.catalog import DataCatalog


UNDERLYING = "510050.XSHG"
CHAIN_COLUMNS = [
    "date", "order_book_id", "underlying", "option_type", "listed_date",
    "maturity_date", "strike_price", "contract_multiplier", "spot",
    "open", "settlement", "volume",
]


def load_history(start: str, end: str, root: Path | None = None):
    """Return the ETF session series and option frames keyed by session.

    A missing option partition stays absent from the map. The ETF's daily
    series defines the session index independently of option availability.
    """
    catalog = DataCatalog(root)
    months = pd.period_range(start, end, freq="M")
    underlying_base = catalog.path("rq.option_underlyings")
    prices = []
    for month in months:
        path = underlying_base / f"year={month.year}" / f"month={month.month:02d}" / "part.parquet"
        frame = ParquetFile(path).read(
            columns=["symbol", "date", "raw_close"]
        ).to_pandas()
        prices.append(frame.loc[frame.symbol.eq(UNDERLYING), ["date", "raw_close"]])
    spot = pd.concat(prices).set_index("date").raw_close.sort_index().loc[start:end]
    if spot.empty or not spot.index.is_unique or not spot.gt(0).all():
        raise ValueError("Invalid SSE 50 ETF session series")

    chain_base = catalog.path("rq.option_snapshots")
    chains = {}
    for day in spot.index:
        path = chain_base / f"date={day:%Y-%m-%d}" / f"underlying={UNDERLYING}" / "part.parquet"
        if not path.is_file():
            continue
        frame = ParquetFile(path).read(columns=CHAIN_COLUMNS).to_pandas()
        if (
            frame.empty or not pd.to_datetime(frame.date).eq(day).all()
            or not frame.underlying.eq(UNDERLYING).all()
            or not frame.spot.sub(spot.at[day]).abs().lt(1e-8).all()
        ):
            raise ValueError(f"Invalid chain partition for {day.date()}")
        frame["maturity_date"] = pd.to_datetime(frame.maturity_date)
        frame["listed_date"] = pd.to_datetime(frame.listed_date)
        chains[day] = frame.set_index("order_book_id", verify_integrity=True)
    return spot, chains


def load_signal_inputs(start: str, end: str, root: Path | None = None) -> pd.DataFrame:
    """Return ETF closes and dated 10Y China government yields in decimals.

    The yield aligned to an ETF session uses the most recent observation
    available by the previous ETF session, with at most seven calendar days
    between its observation date and that previous session. Missing values
    are preserved. Include an earlier start date for rolling-window warm-up.
    """
    catalog = DataCatalog(root)
    prices, rates = [], []
    for month in pd.period_range(start, end, freq="M"):
        partition = Path(f"year={month.year}") / f"month={month.month:02d}" / "part.parquet"
        underlying = catalog.path("rq.option_underlyings") / partition
        frame = ParquetFile(underlying).read(
            columns=["symbol", "date", "adjusted_close"]
        ).to_pandas()
        prices.append(frame.loc[frame.symbol.eq(UNDERLYING), ["date", "adjusted_close"]])

        rate_path = catalog.path("rq.option_rates") / partition
        if rate_path.is_file():
            frame = ParquetFile(rate_path).read(
                columns=["date", "tenor", "annual_yield"]
            ).to_pandas()
            rates.append(frame.loc[frame.tenor.eq("10Y"), ["date", "annual_yield"]])
    close = pd.concat(prices).set_index("date", verify_integrity=True).adjusted_close.sort_index().loc[start:end]
    if close.empty or not close.gt(0).all():
        raise ValueError("Invalid adjusted ETF closes")
    result = close.to_frame()
    result["lagged_yield_10y"] = float("nan")
    result["yield_observation_date"] = pd.NaT
    if rates:
        raw = pd.concat(rates).set_index("date", verify_integrity=True).annual_yield.sort_index()
        aligned = raw.reindex(close.index, method="ffill", tolerance=pd.Timedelta(days=7))
        dates = raw.index.to_series().reindex(close.index, method="ffill", tolerance=pd.Timedelta(days=7))
        result["lagged_yield_10y"] = aligned.shift(1)
        result["yield_observation_date"] = dates.shift(1)
    return result
