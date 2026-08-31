from __future__ import annotations

import pandas as pd

from alphalab.dataio.runtime import RuntimeStore
from alphalab.provenance import build_research_provenance


def test_runtime_provenance_fingerprints_only_declared_inputs(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    store.write(
        "rq.bars",
        pd.DataFrame(
            {
                "date": pd.to_datetime(["2025-01-02"]),
                "symbol": ["000001.SZ"],
                "close": [10.0],
            }
        ),
    )

    provenance = build_research_provenance(
        "runtime",
        strategy_python="def strategy():\n    return None\n",
        runtime_root=tmp_path,
        runtime_datasets=["rq.bars"],
    )

    assert provenance["version"] == 2
    assert set(provenance["data"]["datasets"]) == {"rq.bars"}
    assert provenance["data"]["files"]
    assert provenance["data"]["aggregate_sha256"]
