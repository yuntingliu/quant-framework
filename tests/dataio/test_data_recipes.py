from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from alphalab.data_sdk.v1 import RQDataAPI
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
from alphalab.dataio.runtime import OperationsStore
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
    assert "RQSyncRequest" not in source
    assert "# 1. 直接调用 rq.all_instruments" in source
    assert "# 3. 分批查询日线" in source
    assert "template 参数只用于界面识别" in source
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
                "close": [4.1],
            }
            if kwargs.get("fields") is None:
                values.update(
                    {
                        "open": [4.0],
                        "high": [4.2],
                        "low": [3.9],
                        "volume": [100_000.0],
                        "total_turnover": [410_000.0],
                    }
                )
            return pd.DataFrame(values)

    fake = FakeRQ()
    monkeypatch.setattr(RQDataAPI, "_module", lambda _self: fake)
    source = render_builtin_recipe(
        "rq.etf_daily",
        start="2025-01-01",
        end="2025-12-31",
        symbols=["510300.XSHG"],
    )

    result = probe_data_recipe(source, mode="run", root=tmp_path)

    assert result["sync_request"] is None
    assert [name for name, _ in fake.calls] == [
        "all_instruments",
        "get_price",
        "get_price",
    ]
    assert {item["dataset"] for item in result["published"]} == {
        "rq.instruments",
        "rq.bars",
    }
    assert DataCatalog(tmp_path).status("rq.bars")["rows"] == 1


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
