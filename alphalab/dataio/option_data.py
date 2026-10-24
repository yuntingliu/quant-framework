"""Bounded local reads of the shared option snapshot dataset."""

import re

import pandas as pd
import pyarrow.parquet as pq

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.errors import MissingDataError
from alphalab.dataio.option_snapshots import validate_option_partitions
from alphalab.dataio.symbols import to_rq_symbol


def load_option_snapshot(underlying, day, *, root=None):
    underlying = to_rq_symbol(underlying)
    if not re.fullmatch(r"[0-9]{6}\.(XSHG|XSHE)", underlying):
        raise ValueError("Invalid ETF underlying")
    date = pd.Timestamp(day)
    if pd.isna(date):
        raise ValueError("Invalid option snapshot date")
    base = DataCatalog(root).path("rq.option_snapshots")
    path = base / f"date={date:%Y-%m-%d}" / f"underlying={underlying}" / "part.parquet"
    if not path.is_file():
        raise MissingDataError("No validated option snapshot for the requested ETF and date")
    frame = pq.ParquetFile(path).read().to_pandas()
    validate_option_partitions(frame)
    if (
        not frame.underlying.eq(underlying).all()
        or not pd.to_datetime(frame.date).eq(date.normalize()).all()
    ):
        raise ValueError("Option snapshot does not match the requested ETF/date")
    return frame
