"""Built-in provider protocols and local parquet adapters."""

from alphalab.dataio.providers.local import (
    LocalParquetFactorProvider,
    LocalParquetFundamentalProvider,
    LocalParquetInstrumentProvider,
    LocalParquetMarketDataProvider,
    PartitionedParquetFactorProvider,
    PartitionedParquetFundamentalProvider,
    PartitionedParquetInstrumentProvider,
    PartitionedParquetMarketDataProvider,
    PartitionedParquetResearchDataProvider,
)
from alphalab.dataio.providers.protocol import (
    FactorProvider,
    FundamentalProvider,
    InstrumentProvider,
    MarketDataProvider,
    RealtimeProvider,
    ResearchDataProvider,
    Tick,
    to_long,
    to_wide,
)
from alphalab.dataio.providers.rq import RQDataClient, RQDataConfig, RQDataProvider

__all__ = [
    "FactorProvider",
    "FundamentalProvider",
    "InstrumentProvider",
    "LocalParquetFactorProvider",
    "LocalParquetFundamentalProvider",
    "LocalParquetInstrumentProvider",
    "LocalParquetMarketDataProvider",
    "PartitionedParquetFactorProvider",
    "PartitionedParquetFundamentalProvider",
    "PartitionedParquetInstrumentProvider",
    "PartitionedParquetMarketDataProvider",
    "PartitionedParquetResearchDataProvider",
    "MarketDataProvider",
    "RealtimeProvider",
    "ResearchDataProvider",
    "RQDataClient",
    "RQDataConfig",
    "RQDataProvider",
    "Tick",
    "to_long",
    "to_wide",
]
