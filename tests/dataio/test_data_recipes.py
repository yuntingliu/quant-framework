from __future__ import annotations

import ast
import sqlite3

import pandas as pd
import pytest

from alphalab.data_sdk.v1 import (
    DataRecipeContext,
    RQDataAPI,
    normalize_rq_bars,
    normalize_rq_instruments,
    normalize_rq_suspension,
)
from alphalab.dataio import DataValidationError
from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.providers import rq as rq_provider
from alphalab.dataio.recipes import (
    DataRecipeError,
    execute_data_recipe,
    inspect_data_recipe_source,
    migrate_legacy_builtin_recipe,
    probe_data_recipe,
    render_builtin_recipe,
    update_recipe_parameters,
)
from alphalab.dataio.runtime import OperationsStore, RuntimeStore
from alphalab.dataio.sync import SyncJobManager


def test_builtin_recipe_is_real_python_and_parameters_are_cst_projected() -> None:
    source = render_builtin_recipe(
        "rq.etf_daily",
        start="2021-08-25",
        end="2026-08-25",
    )
    inspection = inspect_data_recipe_source(source)

    assert inspection.matched_template_id == "rq.etf_daily"
    assert inspection.template_id == "rq.etf_daily"
    assert "rq.all_instruments(" in source
    assert "rq.get_price(" in source
    assert 'fields=["open", "high", "low", "close"]' in source
    assert 'required_columns=("raw_open", "raw_high", "raw_low", "raw_close")' in source
    assert "RQSyncRequest" not in source
    assert "# 1. 直接调用 rq.all_instruments" in source
    assert "# 3. 分批查询日线" in source
    assert "停牌状态按实际研究标的查询" in source
    assert "ST 只适用于普通股票" in source
    assert "template 参数只用于界面识别" in source
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "research_data"
    )
    assert ast.get_docstring(function)
    assert {item.name: item.default for item in inspection.parameters} == {
        "start": "2021-08-25",
        "end": "2026-08-25",
        "symbols": None,
    }

    updated, projected = update_recipe_parameters(
        source,
        {"start": "2022-01-01", "symbols": ("510300.XSHG",)},
    )
    assert 'start: str = "2022-01-01"' not in updated
    assert "start: str = '2022-01-01'" in updated
    assert "symbols: tuple[str, ...] | None = ('510300.XSHG',)" in updated
    assert projected.matched_template_id == "rq.etf_daily"
    preview = probe_data_recipe(updated)
    assert preview["sync_request"] is None
    assert [item["operation"] for item in preview["planned"]] == [
        "rq.all_instruments",
        "rq.get_price",
        "rq.get_price(skip_suspended)",
    ]


def test_builtin_recipe_comment_upgrade_preserves_custom_logic() -> None:
    source = render_builtin_recipe(
        "rq.etf_daily",
        start="2021-08-25",
        end="2026-08-25",
    )
    raw_close_only_source = (
        source.replace(
            "未复权 OHLC 用于真实交易约束",
            "未复权收盘价用于估值口径",
        )
        .replace(
            "单独读取未复权 OHLC，保存为 raw_open/raw_high/raw_low/raw_close",
            "单独读取未复权 close，保存为统一契约中的 raw_close",
        )
        .replace(
            '        required_columns=("raw_open", "raw_high", "raw_low", "raw_close"),\n',
            "",
        )
        .replace("unadjusted_raw", "raw_close")
        .replace('["open", "high", "low", "close"]', '["close"]')
    )
    old_commented_source = source.replace(
        "    '''Preview the sync plan and publish normalized research data in run mode.'''\n",
        "",
        1,
    )

    assert migrate_legacy_builtin_recipe(raw_close_only_source) == source
    assert migrate_legacy_builtin_recipe(old_commented_source) == source

    custom = old_commented_source.replace("CHUNK_DAYS = 366", "CHUNK_DAYS = 180", 1)
    assert migrate_legacy_builtin_recipe(custom) == custom


def test_visible_recipe_instrument_normalizer_keeps_only_a_shares_for_cs() -> None:
    raw = pd.DataFrame(
        {
            "order_book_id": ["600000.XSHG", "900901.XSHG", "510300.XSHG"],
            "listed_date": pd.Timestamp("2020-01-01"),
        }
    )

    stocks = normalize_rq_instruments(raw, snapshot_date="2025-01-02", asset_type="CS")
    funds = normalize_rq_instruments(raw, snapshot_date="2025-01-02", asset_type="ETF")

    assert stocks["symbol"].tolist() == ["600000.SH"]
    assert funds["symbol"].tolist() == ["600000.SH", "900901.SH", "510300.SH"]


def test_instrument_normalizer_stabilizes_board_type_across_stocks_and_etfs(
    tmp_path,
) -> None:
    stocks = normalize_rq_instruments(
        pd.DataFrame(
            {
                "order_book_id": ["600000.XSHG"],
                "listed_date": [pd.Timestamp("2020-01-01")],
                "board_type": ["MainBoard"],
            }
        ),
        snapshot_date="2026-08-24",
        asset_type="CS",
    )
    funds = normalize_rq_instruments(
        pd.DataFrame(
            {
                "order_book_id": ["510300.XSHG"],
                "listed_date": [pd.Timestamp("2020-01-01")],
                "board_type": [1],
            }
        ),
        snapshot_date="2026-08-24",
        asset_type="ETF",
    )

    assert stocks.loc[0, "board_type"] == "MainBoard"
    assert funds.loc[0, "board_type"] == "1"
    assert isinstance(funds.loc[0, "board_type"], str)

    store = RuntimeStore(tmp_path)
    store.write("rq.instruments", stocks)
    store.write("rq.instruments", funds)
    persisted = store.read("rq.instruments").set_index("symbol")

    assert persisted.loc["600000.SH", "board_type"] == "MainBoard"
    assert persisted.loc["510300.SH", "board_type"] == "1"


def test_price_suspension_normalizer_marks_rows_omitted_from_tradable_prices() -> None:
    index = pd.MultiIndex.from_tuples(
        [
            ("510300.XSHG", pd.Timestamp("2025-01-02")),
            ("510300.XSHG", pd.Timestamp("2025-01-03")),
        ],
        names=["order_book_id", "date"],
    )
    filled = pd.DataFrame({"close": [4.0, 4.0]}, index=index)
    tradable = filled.iloc[:1]

    paused = normalize_rq_suspension(filled, tradable)

    assert paused.to_dict("records") == [
        {"date": pd.Timestamp("2025-01-02"), "symbol": "510300.SH", "paused": False},
        {"date": pd.Timestamp("2025-01-03"), "symbol": "510300.SH", "paused": True},
    ]
    assert normalize_rq_suspension(filled, pd.DataFrame())["paused"].tolist() == [
        True,
        True,
    ]


def test_manual_recipe_logic_is_custom_and_static_errors_have_a_phase() -> None:
    source = render_builtin_recipe(
        "rq.a_share_daily",
        start="2021-08-25",
        end="2026-08-25",
    ).replace('adjust_type="pre"', 'adjust_type="post"', 1)

    assert inspect_data_recipe_source(source).matched_template_id is None
    with pytest.raises(DataRecipeError, match="exactly one") as caught:
        inspect_data_recipe_source("value = 1\n")
    assert caught.value.phase == "register"


def test_operations_store_persists_recipe_drafts_and_custom_templates(tmp_path) -> None:
    operations = OperationsStore(tmp_path)
    source = render_builtin_recipe(
        "rq.a_share_daily",
        start="2021-08-25",
        end="2026-08-25",
    )
    draft = operations.save_recipe_draft("sample-project", source)
    assert operations.get_recipe_draft("sample-project") == draft

    with pytest.raises(RuntimeError, match="changed"):
        operations.save_recipe_draft(
            "sample-project",
            source + "\n",
            expected_source_sha256="stale",
        )

    selected = operations.save_recipe_draft(
        "sample-project",
        source,
        expected_source_sha256=draft["source_sha256"],
        selected_template_id="rq.a_share_daily",
    )
    assert selected["selected_template_id"] == "rq.a_share_daily"
    preserved = operations.save_recipe_draft(
        "sample-project",
        source + "\n",
        expected_source_sha256=selected["source_sha256"],
    )
    assert preserved["selected_template_id"] == "rq.a_share_daily"

    saved = operations.save_recipe_template(
        "custom.example",
        name="Example",
        description="Custom recipe",
        source=source,
    )
    assert operations.get_recipe_template("custom.example") == saved
    assert operations.delete_recipe_template("custom.example") is True


def test_operations_store_migrates_existing_recipe_drafts_for_template_selection(
    tmp_path,
) -> None:
    database = tmp_path / "app" / "dataio.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE data_recipe_drafts (
                project_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )

    operations = OperationsStore(tmp_path)
    source = render_builtin_recipe(
        "rq.a_share_daily",
        start="2021-08-25",
        end="2026-08-25",
    )
    draft = operations.save_recipe_draft(
        "migrated-project",
        source,
        selected_template_id="rq.a_share_daily",
    )

    assert draft["selected_template_id"] == "rq.a_share_daily"


def test_custom_recipe_job_executes_the_saved_source_and_publishes_runtime_data(tmp_path) -> None:
    source = """import pandas as pd

from alphalab.data_sdk.v1 import data_recipe


@data_recipe(id="custom_bars", label="Custom bars")
def custom_bars(context, *, start: str = "2026-08-24"):
    frame = pd.DataFrame(
        {
            "date": [start],
            "symbol": ["510300.XSHG"],
            "open": [4.0],
            "high": [4.2],
            "low": [3.9],
            "close": [4.1],
            "raw_open": [4.0],
            "raw_high": [4.2],
            "raw_low": [3.9],
            "raw_close": [4.1],
            "volume": [1000.0],
            "amount": [4100.0],
        }
    )
    context.publish("rq.bars", frame)
"""

    result = SyncJobManager(tmp_path).run_recipe_now(source, project_id="sample-project")

    assert result["status"] == "succeeded", result["error"]
    assert result["request"]["kind"] == "python_recipe"
    assert result["request"]["recipe_source"] == source
    assert result["request"]["recipe_published"][0]["dataset"] == "rq.bars"
    assert DataCatalog(tmp_path).status("rq.bars")["rows"] == 1


def test_builtin_recipe_executes_visible_rq_commands_and_publishes_results(
    tmp_path,
    monkeypatch,
) -> None:
    class FakeRQ:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def all_instruments(self, **kwargs):
            self.calls.append(("all_instruments", kwargs))
            return pd.DataFrame(
                {
                    "order_book_id": ["510300.XSHG"],
                    "symbol": ["510300"],
                    "listed_date": ["2012-05-28"],
                    "de_listed_date": [None],
                }
            )

        def get_price(self, order_book_ids, **kwargs):
            self.calls.append(("get_price", kwargs.get("adjust_type")))
            values = {
                "order_book_id": [order_book_ids[0]],
                "date": ["2025-01-02"],
            }
            if kwargs.get("fields") is None:
                values.update(
                    {
                        "open": [4.0],
                        "high": [4.2],
                        "low": [3.9],
                        "close": [4.1],
                        "volume": [100_000.0],
                        "total_turnover": [410_000.0],
                    }
                )
            else:
                values.update(
                    {
                        "open": [4.0],
                        "high": [4.2],
                        "low": [3.9],
                        "close": [4.1],
                    }
                )
            return pd.DataFrame(values)

        def is_suspended(self, order_book_ids, **kwargs):
            raise AssertionError("ETF suspension must not use the stock-only RQ API")

    fake = FakeRQ()
    monkeypatch.setattr(RQDataAPI, "_module", lambda _self: fake)
    source = render_builtin_recipe(
        "rq.etf_daily",
        start="2025-01-01",
        end="2025-01-03",
        symbols=["510300.XSHG"],
    )

    result = probe_data_recipe(source, mode="run", root=tmp_path)

    assert result["sync_request"] is None
    assert [name for name, _ in fake.calls] == [
        "all_instruments",
        "get_price",
        "get_price",
        "get_price",
        "get_price",
    ]
    assert {item["dataset"] for item in result["published"]} == {
        "rq.instruments",
        "rq.bars",
        "rq.paused",
    }
    assert DataCatalog(tmp_path).status("rq.bars")["rows"] == 1


def test_default_research_recipe_resolves_an_explicit_etf_without_stock_research(
    tmp_path,
    monkeypatch,
) -> None:
    class FakeRQ:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def all_instruments(self, **kwargs):
            asset_type = kwargs["type"]
            self.calls.append(("all_instruments", asset_type))
            if asset_type != "ETF":
                return pd.DataFrame()
            return pd.DataFrame(
                {
                    "order_book_id": ["510300.XSHG"],
                    "symbol": ["510300"],
                    "listed_date": ["2012-05-28"],
                    "de_listed_date": [None],
                }
            )

        def get_price(self, order_book_ids, **kwargs):
            self.calls.append(("get_price", kwargs.get("adjust_type")))
            values = {
                "order_book_id": [order_book_ids[0]],
                "date": ["2025-01-02"],
            }
            if kwargs.get("fields") is None:
                values.update(
                    {
                        "open": [4.0],
                        "high": [4.2],
                        "low": [3.9],
                        "close": [4.1],
                        "volume": [100_000.0],
                        "total_turnover": [410_000.0],
                    }
                )
            else:
                values.update(
                    {
                        "open": [4.0],
                        "high": [4.2],
                        "low": [3.9],
                        "close": [4.1],
                    }
                )
            return pd.DataFrame(values)

        def is_suspended(self, order_book_ids, **kwargs):
            raise AssertionError("ETF suspension must not use the stock-only RQ API")

    fake = FakeRQ()
    monkeypatch.setattr(RQDataAPI, "_module", lambda _self: fake)
    source = render_builtin_recipe(
        "rq.a_share_research",
        start="2025-01-01",
        end="2025-01-03",
        symbols=["510300.XSHG"],
    )

    result = probe_data_recipe(source, mode="run", root=tmp_path)

    assert [name for name, _ in fake.calls] == [
        "all_instruments",
        "all_instruments",
        "get_price",
        "get_price",
        "get_price",
        "get_price",
    ]
    assert [value for name, value in fake.calls if name == "all_instruments"] == [
        "CS",
        "ETF",
    ]
    assert {item["dataset"] for item in result["published"]} == {
        "rq.instruments",
        "rq.bars",
        "rq.paused",
    }
    bars = DataRecipeContext(mode="run", root=str(tmp_path)).read("rq.bars")
    assert {"raw_open", "raw_high", "raw_low", "raw_close"}.issubset(bars.columns)


def test_untouched_request_only_template_migrates_to_visible_rq_commands() -> None:
    legacy = """from alphalab.data_sdk.v1 import RQSyncRequest, data_recipe, rq


@data_recipe(id="research_data", label='ETF 日线')
def research_data(
    context,
    *,
    start: str = '2021-08-25',
    end: str = '2026-08-25',
    symbols: tuple[str, ...] | None = None,
):
    # rq 透明代理当前安装的 rqdatac，可在自定义配方中调用任意公开查询 API。
    return RQSyncRequest(
        template_id='rq.etf_daily',
        datasets=('instruments', 'bars'),
        start=start,
        end=end,
        symbols=symbols,
    )
"""

    migrated = migrate_legacy_builtin_recipe(legacy)

    assert "RQSyncRequest" not in migrated
    assert "rq.all_instruments(" in migrated
    assert "rq.get_price(" in migrated


def test_rq_proxy_reuses_one_initialized_module(monkeypatch) -> None:
    class FakeModule:
        value = 7

    module = FakeModule()
    calls = 0

    class FakeClient:
        def connect(self):
            nonlocal calls
            calls += 1
            return module

    monkeypatch.setattr(rq_provider.RQDataClient, "from_env", lambda: FakeClient())
    api = RQDataAPI()

    assert api.value == 7
    assert api.value == 7
    assert calls == 1


def test_recipe_process_can_be_cancelled() -> None:
    source = """from alphalab.data_sdk.v1 import data_recipe


@data_recipe(id="waiting_recipe")
def waiting_recipe(context):
    while True:
        pass
"""

    with pytest.raises(DataRecipeError, match="cancelled") as caught:
        execute_data_recipe(source, mode="plan", cancelled=lambda: True)

    assert caught.value.phase == "cancel"


def test_recipe_sync_batches_resume_existing_symbols_and_backfill_new_ones(tmp_path) -> None:
    context = DataRecipeContext(mode="run", root=str(tmp_path))
    existing = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", "2025-01-10", freq="B"),
            "symbol": "000001.SZ",
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.2,
            "raw_open": 10.0,
            "raw_high": 10.5,
            "raw_low": 9.5,
            "raw_close": 10.2,
            "volume": 1000.0,
            "amount": 10000.0,
        }
    )
    context.publish("rq.bars", existing)

    batches = context.sync_batches(
        "rq.bars",
        ["000001.SZ", "600000.SH"],
        start="2025-01-01",
        end="2025-01-20",
        overlap_days=1,
        chunk_days=366,
    )
    starts = {symbol: batch.start for batch in batches for symbol in batch.symbols}

    assert starts["000001.SZ"] == "2025-01-09"
    assert starts["600000.SH"] == "2025-01-01"


def test_recipe_sync_batches_replays_history_after_required_bar_schema_upgrade(tmp_path) -> None:
    context = DataRecipeContext(mode="run", root=str(tmp_path))
    legacy = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", "2025-01-10", freq="B"),
            "symbol": "000001.SZ",
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.2,
            "raw_close": 10.2,
            "volume": 1000.0,
            "amount": 10000.0,
        }
    )
    context.publish("rq.bars", legacy)

    batches = context.sync_batches(
        "rq.bars",
        ["000001.SZ"],
        start="2025-01-01",
        end="2025-01-20",
        overlap_days=1,
        chunk_days=366,
        required_columns=("raw_open", "raw_high", "raw_low", "raw_close"),
    )

    assert len(batches) == 1
    assert batches[0].start == "2025-01-01"


def test_adjusted_and_raw_bar_key_mismatch_is_rejected() -> None:
    adjusted = pd.DataFrame(
        {
            "order_book_id": ["000001.XSHE", "600000.XSHG"],
            "date": ["2025-01-02", "2025-01-02"],
            "open": [10.0, 20.0],
            "high": [10.5, 20.5],
            "low": [9.5, 19.5],
            "close": [10.2, 20.2],
            "volume": [1000.0, 2000.0],
            "total_turnover": [10200.0, 40400.0],
        }
    )
    raw = adjusted.iloc[:1][["order_book_id", "date", "open", "high", "low", "close"]]

    with pytest.raises(DataValidationError, match="key mismatch"):
        normalize_rq_bars(adjusted, raw)
