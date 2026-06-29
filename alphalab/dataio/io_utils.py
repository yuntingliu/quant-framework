"""I/O helpers for local parquet-backed development."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def atomic_write_parquet(df: pd.DataFrame, path: str | Path) -> None:
    """Write a parquet file via a temporary sibling path."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp)
    tmp.replace(path)

