"""Schema discovery for local canonical and runtime parquet datasets."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True)
class DatasetField:
    """One physical dataset column exposed through a profile-aware API."""

    name: str
    data_type: str
    nullable: bool


def discover_parquet_fields(paths: Iterable[str | Path]) -> tuple[DatasetField, ...]:
    """Return the ordered union of columns from every existing parquet path.

    Runtime datasets are partitioned and schemas can evolve between syncs, so
    discovery reads file metadata from all partitions instead of assuming the
    first file is representative. No data rows are loaded.
    """

    discovered: dict[str, DatasetField] = {}
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            continue
        for field in pq.read_schema(path):
            candidate = DatasetField(
                name=field.name,
                data_type=_logical_type(field.type),
                nullable=field.nullable,
            )
            previous = discovered.get(field.name)
            if previous is None:
                discovered[field.name] = candidate
            elif previous.data_type != candidate.data_type:
                discovered[field.name] = DatasetField(
                    name=field.name,
                    data_type="mixed",
                    nullable=previous.nullable or candidate.nullable,
                )
            elif candidate.nullable and not previous.nullable:
                discovered[field.name] = DatasetField(
                    name=field.name,
                    data_type=previous.data_type,
                    nullable=True,
                )
    return tuple(discovered.values())


def _logical_type(data_type: pa.DataType) -> str:
    if pa.types.is_integer(data_type) or pa.types.is_floating(data_type) or pa.types.is_decimal(data_type):
        return "number"
    if pa.types.is_boolean(data_type):
        return "boolean"
    if pa.types.is_timestamp(data_type) or pa.types.is_date(data_type) or pa.types.is_time(data_type):
        return "date"
    if pa.types.is_string(data_type) or pa.types.is_large_string(data_type):
        return "string"
    return "mixed"


__all__ = ["DatasetField", "discover_parquet_fields"]
