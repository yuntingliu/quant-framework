from __future__ import annotations

import ast
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from alphalab.dataio import create_default_engine
from alphalab.sdk.v1 import Event, FactorContext
from alphalab.strategy import repository as strategy_repository_module
from alphalab.strategy.builtins import DEFAULT_STRATEGY_SOURCE
from alphalab.strategy.config import ExecutionSpec
from alphalab.strategy.engine import (
    _aggregate_execution_summary,
    _apply_execution_constraints,
    _execution_data_gaps,
    _execution_fidelity_summary,
    _prepare_data,
    _record_signal_evidence,
    _settle_delisted_positions,
    _trade_allowed,
    _trade_rejection_reason,
    run_strategy_backtest,
)
from alphalab.strategy.factor_templates import (
    install_factor_template,
    instantiate_factor_template,
    list_factor_templates,
)
from alphalab.strategy.repository import StrategyRepository
from alphalab.strategy.sdk_runtime import SdkExecutionSession, SdkRuntimeError
from alphalab.strategy.source import (
    StrategySourceError,
    assemble_strategy_source,
    delete_registered_function,
    factor_dependency_snippet,
    factor_field_snippet,
    insert_source,
    inspect_strategy_source,
    merge_data_requirements,
    migrate_default_strategy_components,
    registered_function_source,
    remove_factor_inputs_arguments,
    replace_registered_function,
    split_strategy_source,
    update_parameter_default,
    update_signal_factor_blend,
    update_signal_schedule,
)


def test_explicit_default_component_migration_preserves_old_packages(tmp_path: Path):
    repository = StrategyRepository(tmp_path / "template-migration.db")
    try:
        project = repository.clone_project("sdk-v1-default", "old-stock-project")
        source = project["draft_source"]
        fill = registered_function_source(source, entrypoint_id="fill_missing_market_state")
        source = source.replace(fill, "", 1)
        source, _ = replace_registered_function(
            source,
            entrypoint_id="research_universe",
            function_source="""@universe(id="legacy_universe")
def legacy_universe(context):
    return UniverseResult(symbols=context.universe)
""",
        )
        legacy = repository.update_draft(
            "old-stock-project",
            source,
            expected_source_sha256=project["draft_source_sha256"],
        )
        legacy_package = repository.get_package("old-stock-project", legacy["current_revision"])
        migrated_source, inspection = migrate_default_strategy_components(
            legacy["draft_source"], DEFAULT_STRATEGY_SOURCE
        )
        migrated = repository.update_draft(
            "old-stock-project",
            migrated_source,
            expected_source_sha256=legacy["draft_source_sha256"],
        )

        assert migrated["current_revision"] == legacy["current_revision"] + 1
        assert any(item.kind == "execution_data_fill" for item in inspection.entrypoints)
        assert 'asset_type"].astype(str).str.upper().eq("CS")' in migrated["draft_source"]
        assert (
            repository.get_package("old-stock-project", legacy["current_revision"])["source_sha256"]
            == legacy_package["source_sha256"]
        )
    finally:
        repository.close()


def _payload() -> dict:
    dates = pd.bdate_range("2024-01-01", periods=30)
    bars = pd.DataFrame(
        [
            {
                "date": date,
                "symbol": symbol,
                "open": 100 + index,
                "high": 101 + index,
                "low": 99 + index,
                "close": 100 + index,
                "volume": 1_000_000,
                "amount": 100_000_000,
            }
            for index, date in enumerate(dates)
            for symbol in ("A", "B")
        ]
    )
    return {
        "sessions": tuple(dates),
        "bars": bars,
        "instruments": pd.DataFrame(
            {
                "snapshot_date": [dates[0], dates[0]],
                "symbol": ["A", "B"],
                "asset_type": ["CS", "CS"],
            }
        ),
        "fundamentals": pd.DataFrame(),
    }


def test_builtin_factor_catalog_is_native_sdk_python_and_all_templates_install(tmp_path: Path):
    templates = list_factor_templates()
    assert {item.id for item in templates} == {
        "lower_shadow_recovery",
        "three_white_soldiers",
        "volume_confirmed_breakout",
        "bp",
        "custom_factor",
        "ep",
        "gross_margin",
        "leverage",
        "liquidity_20d",
        "ma_deviation",
        "momentum_20d",
        "momentum_60d",
        "profit_growth",
        "quality_profitability",
        "reversal_5d",
        "range_volatility_20d",
        "revenue_growth",
        "roa",
        "roe",
        "rsi_14",
        "turnover_20d",
        "volatility_20d",
        "volume_ratio",
    }
    assert all(item.source.startswith("@factor(") for item in templates)
    assert all("# " in item.source for item in templates)
    assert all(
        ast.get_docstring(
            next(node for node in ast.parse(item.source).body if isinstance(node, ast.FunctionDef))
        )
        for item in templates
    )

    source = DEFAULT_STRATEGY_SOURCE
    for template in templates:
        if template.id != "momentum_20d":
            source, inspection = install_factor_template(source, template_id=template.id)

    factor_ids = {item.id for item in inspection.entrypoints if item.kind == "factor"}
    assert factor_ids == {item.id for item in templates}
    assert set(inspection.data_requirements["fundamentals"]) == {
        "bp",
        "ep",
        "gross_margin",
        "leverage",
        "profit_growth",
        "revenue_growth",
        "roa",
        "roe",
    }
    assert "def momentum_60d(context" in source
    assert 'return context.fundamental("roe")' in source

    repository = StrategyRepository(tmp_path / "all-templates.db")
    try:
        created = repository.create_project(
            "all-factor-templates", name="All Factor Templates", source=source
        )
        assert created["current_package"]["revision"] == 1
    finally:
        repository.close()


def test_factor_template_instantiation_only_replaces_template_edits() -> None:
    source = instantiate_factor_template(
        "custom_factor",
        factor_id="quality_score",
        label="质量分数",
        parameter_values={"window": 30},
        body='return context.fundamental("roe")',
    )

    assert "@factor(id='quality_score', label='质量分数')" in source
    assert "def quality_score(context, *, window: int = 30):" in source
    assert 'return context.fundamental("roe")' in source
    assert len([node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)]) == 1

    with pytest.raises(StrategySourceError, match="requires an explicit factor_id"):
        instantiate_factor_template("custom_factor")
    with pytest.raises(StrategySourceError, match="not template-editable"):
        instantiate_factor_template("momentum_20d", parameter_values={"missing": 1})


def test_official_default_strategy_explains_every_registered_function() -> None:
    tree = ast.parse(DEFAULT_STRATEGY_SOURCE)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]

    assert ast.get_docstring(tree)
    assert functions
    assert all(ast.get_docstring(function) for function in functions)
    assert DEFAULT_STRATEGY_SOURCE.count("# ") >= len(functions)


def test_immutable_builtin_project_advances_when_official_source_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "builtin-template-refresh.db"
    legacy_source = DEFAULT_STRATEGY_SOURCE.replace(
        '"""SDK v1 教学策略：月末从全市场选择动量最高的标的并在下一交易日开盘成交。"""\n\n',
        "",
        1,
    )
    monkeypatch.setattr(
        strategy_repository_module,
        "DEFAULT_STRATEGY_SOURCE",
        legacy_source,
    )
    seeded = StrategyRepository(database)
    seeded.close()

    monkeypatch.setattr(
        strategy_repository_module,
        "DEFAULT_STRATEGY_SOURCE",
        DEFAULT_STRATEGY_SOURCE,
    )
    refreshed = StrategyRepository(database)
    try:
        project = refreshed.get_project("sdk-v1-default")
        assert project is not None
        assert project["current_revision"] == 2
        assert project["draft_source"] == DEFAULT_STRATEGY_SOURCE
        assert refreshed.get_package("sdk-v1-default", 1)["source"] == legacy_source
        assert refreshed.get_package("sdk-v1-default", 2)["source"] == DEFAULT_STRATEGY_SOURCE
    finally:
        refreshed.close()


def test_factor_template_repeat_install_creates_independent_copies():
    source, inspection = install_factor_template(
        DEFAULT_STRATEGY_SOURCE, template_id="momentum_20d"
    )
    source, inspection = install_factor_template(source, template_id="momentum_20d")

    factors = {item.id: item for item in inspection.entrypoints if item.kind == "factor"}
    assert {"momentum_20d", "momentum_20d_2", "momentum_20d_3"} <= set(factors)
    assert factors["momentum_20d_2"].function == "momentum_20d_2"
    assert factors["momentum_20d_2"].label == "20 日动量（副本 2）"
    assert factors["momentum_20d_3"].label == "20 日动量（副本 3）"

    updated, _ = update_parameter_default(
        source,
        entrypoint_id="momentum_20d_2",
        parameter="window",
        value=37,
    )
    updated_factors = {
        item.id: item
        for item in inspect_strategy_source(updated).entrypoints
        if item.kind == "factor"
    }
    assert updated_factors["momentum_20d"].parameters[0].default == 20
    assert updated_factors["momentum_20d_2"].parameters[0].default == 37


def test_strategy_source_units_round_trip_without_factor_leak():
    strategy_source, factor_units = split_strategy_source(DEFAULT_STRATEGY_SOURCE)

    assert "@factor" not in strategy_source
    assert "@signal" in strategy_source
    assert "@portfolio" in strategy_source
    assert "@execution" in strategy_source
    assert [item.path for item in factor_units] == ["factors/momentum_20d.py"]
    assert factor_units[0].source.startswith('@factor(id="momentum_20d"')

    bundled, inspection = assemble_strategy_source(
        strategy_source,
        [item.source for item in factor_units],
    )
    assert bundled == DEFAULT_STRATEGY_SOURCE
    assert {item.id for item in inspection.entrypoints if item.kind == "factor"} == {"momentum_20d"}


def test_template_data_requirements_merge_without_replacing_safety_fields():
    strategy_source, _ = split_strategy_source(DEFAULT_STRATEGY_SOURCE)

    updated, inspection = merge_data_requirements(
        strategy_source,
        {"bars": ["close", "turnover"], "fundamentals": ["roe"]},
    )

    assert inspection.data_requirements["bars"].count("close") == 1
    assert "turnover" in inspection.data_requirements["bars"]
    assert inspection.data_requirements["instruments"] == ["asset_type"]
    assert inspection.data_requirements["fundamentals"] == ["roe"]
    assert "DATA_REQUIREMENTS" in updated


def test_atomic_project_creation_uses_sdk_prelude_and_one_revision(tmp_path: Path):
    strategy_source, factor_units = split_strategy_source(DEFAULT_STRATEGY_SOURCE)
    strategy_source = strategy_source.replace("    factor,\n", "", 1)
    repository = StrategyRepository(tmp_path / "atomic-create.db")
    try:
        project = repository.create_project_from_units(
            "atomic-create",
            name="Atomic Create",
            strategy_source=strategy_source,
            factor_sources=[factor_units[0].source],
        )
        assert project["current_revision"] == 1
        assert project["dirty"] is False
        assert [item["revision"] for item in repository.list_packages("atomic-create")] == [1]
        assert {item["path"] for item in project["source_units"]} == {
            "strategy.py",
            "factors/momentum_20d.py",
        }
    finally:
        repository.close()


def test_factor_units_cannot_depend_on_strategy_imports(tmp_path: Path):
    strategy_source, factor_units = split_strategy_source(DEFAULT_STRATEGY_SOURCE)
    strategy_source = strategy_source.replace(
        "from typing import Annotated\n", "from typing import Annotated\nimport pandas as pd\n", 1
    )
    repository = StrategyRepository(tmp_path / "factor-isolation.db")
    try:
        with pytest.raises(StrategySourceError, match="undeclared global dependencies: pd"):
            repository.create_project_from_units(
                "implicit-factor-import",
                name="Implicit Factor Import",
                strategy_source=strategy_source,
                factor_sources=[
                    factor_units[0].source,
                    '@factor(id="implicit_pd")\n'
                    "def implicit_pd(context):\n"
                    "    return pd.Series({symbol: 1.0 for symbol in context.universe})\n",
                ],
            )
        assert repository.get_project("implicit-factor-import") is None

        created = repository.create_project_from_units(
            "self-contained-factor",
            name="Self-contained Factor",
            strategy_source=strategy_source,
            factor_sources=[
                factor_units[0].source,
                '@factor(id="local_pd")\n'
                "def local_pd(context):\n"
                "    import pandas as pd\n"
                "    return pd.Series({symbol: 1.0 for symbol in context.universe})\n",
            ],
        )
        assert created["current_revision"] == 1
        assert created["dirty"] is False
    finally:
        repository.close()


def test_repository_edits_strategy_and_factor_units_independently(tmp_path: Path):
    repository = StrategyRepository(tmp_path / "source-units.db")
    try:
        project = repository.clone_project("sdk-v1-default", "source-units")
        assert "@factor" not in project["strategy_source"]
        assert {item["path"] for item in project["source_units"]} == {
            "strategy.py",
            "factors/momentum_20d.py",
        }
        package = repository.get_package("source-units", 1)
        assert package is not None
        frozen_strategy = next(
            item for item in package["source_units"] if item["kind"] == "strategy"
        )
        assert "@factor" not in frozen_strategy["source"]

        strategy_source = project["strategy_source"].replace("top_n: int = 10", "top_n: int = 7")
        project = repository.update_strategy_source(
            "source-units",
            strategy_source,
            expected_source_sha256=project["draft_source_sha256"],
        )
        assert "top_n: int = 7" in project["strategy_source"]
        assert "@factor" not in project["strategy_source"]
        assert "def momentum_20d(" in project["draft_source"]
        assert project["current_revision"] == 2
        assert project["dirty"] is False

        project = repository.add_factor_source(
            "source-units",
            """@factor(id="close_level", label="收盘价")
def close_level(context):
    return context.current("close")
""",
            expected_source_sha256=project["draft_source_sha256"],
        )
        assert "@factor" not in project["strategy_source"]
        assert "def close_level(context)" in project["draft_source"]
        assert {item["path"] for item in project["source_units"]} == {
            "strategy.py",
            "factors/momentum_20d.py",
            "factors/close_level.py",
        }
        assert project["current_revision"] == 3
        assert project["dirty"] is False
        close_source = repository.get_factor_source("source-units", "close_level")
        assert close_source["source"].startswith('@factor(id="close_level"')
        project = repository.replace_factor_source(
            "source-units",
            "close_level",
            close_source["source"].replace("close_level", "latest_close"),
            expected_source_sha256=project["draft_source_sha256"],
        )
        assert "factors/close_level.py" not in {item["path"] for item in project["source_units"]}
        assert "factors/latest_close.py" in {item["path"] for item in project["source_units"]}
        assert project["current_revision"] == 4
        assert project["dirty"] is False
    finally:
        repository.close()


def test_legacy_pipeline_migration_preserves_all_factors_and_weights(tmp_path: Path):
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE pipeline_projects (
               id TEXT PRIMARY KEY,
               name TEXT NOT NULL,
               description TEXT NOT NULL,
               settings_json TEXT NOT NULL,
               built_in INTEGER NOT NULL DEFAULT 0
           )"""
    )
    settings = {
        "universe": {"symbols": ["AAA", "BBB"]},
        "factors": [
            {"name": "momentum_20d", "weight": 0.6, "direction": "long"},
            {"name": "volatility_20d", "weight": 0.4, "direction": "short"},
        ],
        "stage_parameters": {
            "selection": {
                "count": 7,
                "factor_weights": {"momentum_20d": 0.6, "volatility_20d": 0.4},
                "signal_frequency": "weekly",
                "normalization": "zscore",
            }
        },
    }
    connection.execute(
        "INSERT INTO pipeline_projects VALUES (?, ?, ?, ?, 0)",
        ("legacy-multi", "Legacy Multi", "old pipeline", json.dumps(settings)),
    )
    connection.commit()
    connection.close()

    repository = StrategyRepository(database)
    try:
        project = repository.get_project("legacy-multi")
        assert project is not None
        source = project["draft_source"]
        factors = {
            item["id"] for item in project["inspection"]["entrypoints"] if item["kind"] == "factor"
        }
        assert factors == {"momentum_20d", "volatility_20d"}
        assert "weights={'momentum_20d': 0.6, 'volatility_20d': -0.4}" in source
        assert "normalization='zscore'" in source
        assert "Weekly.last_trading_day" in source
        assert "top_n: int = 7" in source
    finally:
        repository.close()


def test_cst_parameter_and_schedule_edits_change_only_canonical_source():
    source = DEFAULT_STRATEGY_SOURCE.replace(
        '@factor(id="momentum_20d", label="20 日动量")',
        '# preserve this comment\n@factor(id="momentum_20d", label="20 日动量")',
    )
    updated, inspection = update_parameter_default(
        source,
        entrypoint_id="momentum_20d",
        parameter="window",
        value=30,
    )
    assert "# preserve this comment" in updated
    assert "] = 30" in updated
    assert inspection.source_sha256 != inspect_strategy_source(source).source_sha256
    scheduled, inspected = update_signal_schedule(
        updated,
        signal_id="monthly_momentum",
        frequency="weekly",
        selector="last_trading_day",
        at="close",
    )
    assert "Weekly.last_trading_day" in scheduled
    signal = next(item for item in inspected.entrypoints if item.kind == "signal")
    assert signal.metadata["schedule"]["frequency"] == "weekly"


def test_factor_blend_projects_single_factor_and_converts_one_call():
    source = DEFAULT_STRATEGY_SOURCE.replace(
        """    scores = context.combine_factors(
        weights={"momentum_20d": 1.0},
        normalization="raw",
        parameters={"momentum_20d": {"window": 20}},
    ).dropna()
""",
        '    scores = context.factor("momentum_20d", window=20).dropna()\n',
    ).replace(
        "\n\n@signal(",
        """

@factor(id="quality", label="质量")
def quality(context):
    return context.factor("momentum_20d") * -1.0


@signal(""",
    )
    initial = inspect_strategy_source(source)
    signal = next(item for item in initial.entrypoints if item.id == "monthly_momentum")
    assert signal.metadata["factor_blend"] == {
        "mode": "single",
        "weights": {"momentum_20d": 1.0},
        "normalization": "raw",
        "parameters": {"momentum_20d": {"window": 20}},
    }

    updated, inspection = update_signal_factor_blend(
        source,
        signal_id="monthly_momentum",
        factor_weights={"momentum_20d": 0.7, "quality": -0.3},
        normalization="rank",
    )
    assert "context.combine_factors(" in updated
    assert 'context.factor("momentum_20d", window=20).dropna()' not in updated
    assert "parameters={'momentum_20d': {'window': 20}}" in updated
    projected = next(
        item for item in inspection.entrypoints if item.id == "monthly_momentum"
    ).metadata["factor_blend"]
    assert projected["mode"] == "structured"
    assert projected["weights"] == {"momentum_20d": 0.7, "quality": -0.3}
    assert projected["normalization"] == "rank"


def test_context_combines_factors_with_signed_rank_weights():
    context = FactorContext(
        event=Event.SESSION_CLOSE,
        as_of="2024-01-02",
        sessions=["2024-01-02"],
        symbols=["A", "B"],
        bars=pd.DataFrame({"date": ["2024-01-02", "2024-01-02"], "symbol": ["A", "B"]}),
        factor_resolver=lambda factor_id, parameters: {
            "momentum": pd.Series({"A": 1.0, "B": 2.0}),
            "volatility": pd.Series({"A": 2.0, "B": 1.0}),
        }[factor_id],
    )
    scores = context.combine_factors({"momentum": 1.0, "volatility": -1.0}, normalization="rank")
    assert scores.to_dict() == {"A": -0.25, "B": 0.25}


def test_custom_factor_formula_stays_python_only():
    source = DEFAULT_STRATEGY_SOURCE.replace(
        """    scores = context.combine_factors(
        weights={"momentum_20d": 1.0},
        normalization="raw",
        parameters={"momentum_20d": {"window": 20}},
    ).dropna()
""",
        """    fast = context.factor("momentum_20d", window=10)
    slow = context.factor("momentum_20d", window=40)
    scores = (fast.where(fast > 0, 0.0) - slow).dropna()
""",
    )
    signal = next(
        item
        for item in inspect_strategy_source(source).entrypoints
        if item.id == "monthly_momentum"
    )
    assert signal.metadata["factor_blend"] == {
        "mode": "custom",
        "factor_ids": ["momentum_20d"],
    }
    try:
        update_signal_factor_blend(
            source,
            signal_id="monthly_momentum",
            factor_weights={"momentum_20d": 1.0},
            normalization="rank",
        )
    except StrategySourceError as exc:
        assert exc.phase == "edit"
        assert "custom factor logic" in str(exc)
    else:
        raise AssertionError("custom Python formula must not be structurally rewritten")


def test_structured_blend_edit_preserves_unrelated_call_parameters():
    source = DEFAULT_STRATEGY_SOURCE.replace(
        'parameters={"momentum_20d": {"window": 20}},',
        """parameters={
            # Keep factor-specific tuning with the factor call.
            "momentum_20d": {"window": 20},
        },""",
    )
    updated, inspection = update_signal_factor_blend(
        source,
        signal_id="monthly_momentum",
        factor_weights={"momentum_20d": 1.0},
        normalization="zscore",
    )
    assert "# Keep factor-specific tuning with the factor call." in updated
    assert "normalization='zscore'" in updated
    blend = next(item for item in inspection.entrypoints if item.id == "monthly_momentum").metadata[
        "factor_blend"
    ]
    assert blend["parameters"] == {"momentum_20d": {"window": 20}}


def test_registered_function_source_is_exact_replaceable_unit():
    function_source = registered_function_source(
        DEFAULT_STRATEGY_SOURCE, entrypoint_id="monthly_momentum"
    )
    assert function_source.startswith("@signal(")
    assert "def monthly_momentum" in function_source
    assert "def equal_weight" not in function_source
    updated, inspection = replace_registered_function(
        DEFAULT_STRATEGY_SOURCE,
        entrypoint_id="monthly_momentum",
        function_source=function_source.replace("top_n: int = 10", "top_n: int = 5"),
    )
    assert "top_n: int = 5" in updated
    assert next(item for item in inspection.entrypoints if item.id == "monthly_momentum")


@pytest.mark.parametrize(
    "prefix",
    [
        "TOP_N = 5\n",
        "from math import sqrt\n",
        "print('unexpected top-level statement')\n",
    ],
)
def test_replacing_registered_function_rejects_other_top_level_statements(prefix: str):
    function_source = registered_function_source(
        DEFAULT_STRATEGY_SOURCE, entrypoint_id="monthly_momentum"
    )

    with pytest.raises(StrategySourceError, match="exactly one registered function"):
        replace_registered_function(
            DEFAULT_STRATEGY_SOURCE,
            entrypoint_id="monthly_momentum",
            function_source=prefix + function_source,
        )


def test_deprecated_factor_inputs_are_removed_without_changing_function_code():
    legacy = DEFAULT_STRATEGY_SOURCE.replace(
        '@factor(id="momentum_20d", label="20 日动量")',
        '@factor(id="momentum_20d", label="20 日动量", inputs=["close"])',
        1,
    )
    updated, inspection = remove_factor_inputs_arguments(legacy)

    assert "inputs=" not in updated
    assert 'close = context.history("close", window=window + 1)' in updated
    factor_spec = next(item for item in inspection.entrypoints if item.id == "momentum_20d")
    assert factor_spec.metadata == {}


def test_replacing_factor_function_renames_static_sdk_references():
    function_source = registered_function_source(
        DEFAULT_STRATEGY_SOURCE, entrypoint_id="momentum_20d"
    ).replace('id="momentum_20d"', 'id="momentum_30d"')
    updated, inspection = replace_registered_function(
        DEFAULT_STRATEGY_SOURCE,
        entrypoint_id="momentum_20d",
        function_source=function_source,
    )

    factor_ids = {item.id for item in inspection.entrypoints if item.kind == "factor"}
    assert "momentum_30d" in factor_ids
    assert "momentum_20d" not in factor_ids
    signal = next(item for item in inspection.entrypoints if item.id == "monthly_momentum")
    assert signal.metadata["factor_blend"]["weights"] == {"momentum_30d": 1.0}
    assert signal.metadata["factor_blend"]["parameters"] == {"momentum_30d": {"window": 20}}


def test_replacing_factor_function_renames_factor_dependencies():
    source = DEFAULT_STRATEGY_SOURCE.replace(
        "\n@signal",
        """
@factor(id="momentum_copy")
def momentum_copy(context):
    return context.factor("momentum_20d")

@signal""",
        1,
    )
    function_source = registered_function_source(source, entrypoint_id="momentum_20d").replace(
        'id="momentum_20d"', 'id="momentum_30d"'
    )
    updated, inspection = replace_registered_function(
        source,
        entrypoint_id="momentum_20d",
        function_source=function_source,
    )

    assert "context.factor('momentum_30d')" in updated
    assert next(item for item in inspection.entrypoints if item.id == "momentum_copy")


def test_replacing_registered_function_cannot_change_its_kind():
    function_source = registered_function_source(
        DEFAULT_STRATEGY_SOURCE, entrypoint_id="momentum_20d"
    ).replace("@factor(", "@signal(", 1)
    try:
        replace_registered_function(
            DEFAULT_STRATEGY_SOURCE,
            entrypoint_id="momentum_20d",
            function_source=function_source,
        )
    except StrategySourceError as exc:
        assert exc.phase == "edit"
        assert "must remain a registered @factor function" in str(exc)
    else:
        raise AssertionError("function-level editing must preserve the entrypoint kind")


def test_deleting_an_unused_factor_removes_only_that_function():
    source = DEFAULT_STRATEGY_SOURCE.replace(
        "\n@signal",
        """
@factor(id="unused_factor")
def unused_factor(context):
    return context.current("close")

@signal""",
        1,
    )
    updated, inspection = delete_registered_function(source, entrypoint_id="unused_factor")

    assert "def unused_factor" not in updated
    assert "unused_factor" not in {
        item.id for item in inspection.entrypoints if item.kind == "factor"
    }
    assert next(item for item in inspection.entrypoints if item.id == "momentum_20d")


def test_deleting_a_referenced_factor_is_rejected():
    try:
        delete_registered_function(DEFAULT_STRATEGY_SOURCE, entrypoint_id="momentum_20d")
    except StrategySourceError as exc:
        assert exc.phase == "edit"
        assert "monthly_momentum" in str(exc)
    else:
        raise AssertionError("referenced factors must not be deleted")


def test_annotated_parameter_metadata_is_projected_and_enforced():
    inspection = inspect_strategy_source(DEFAULT_STRATEGY_SOURCE)
    factor_spec = next(item for item in inspection.entrypoints if item.id == "momentum_20d")
    window = next(item for item in factor_spec.parameters if item.name == "window")
    assert window.label == "窗口"
    assert window.minimum == 2
    assert window.maximum == 500
    assert window.step == 1
    try:
        update_parameter_default(
            DEFAULT_STRATEGY_SOURCE,
            entrypoint_id="momentum_20d",
            parameter="window",
            value=1,
        )
    except StrategySourceError as exc:
        assert "below its minimum" in str(exc)
    else:
        raise AssertionError("out-of-range structured parameter must be rejected")


def test_default_execution_fill_exposes_editable_market_rules():
    inspection = inspect_strategy_source(DEFAULT_STRATEGY_SOURCE)
    entrypoint = next(
        item for item in inspection.entrypoints if item.id == "fill_missing_market_state"
    )
    parameters = {item.name: item for item in entrypoint.parameters}

    assert entrypoint.kind == "execution_data_fill"
    assert set(parameters) == {
        "main_board_limit_rate",
        "main_board_st_limit_rate_before_change",
        "main_board_st_limit_rate",
        "main_board_st_change_date",
        "star_market_limit_rate",
        "chinext_limit_rate",
        "beijing_limit_rate",
        "etf_limit_rate",
        "ipo_unlimited_sessions",
        "beijing_ipo_unlimited_sessions",
        "state_lookback_sessions",
        "fill_unknown_suspension_as_tradable",
        "reference_price_lookback_sessions",
    }
    assert all(item.editable for item in parameters.values())
    assert parameters["state_lookback_sessions"].default == 120
    assert parameters["fill_unknown_suspension_as_tradable"].default is True


def test_default_execution_fill_uses_raw_close_board_st_and_ipo_rules():
    dates = pd.bdate_range("2026-06-08", periods=20)
    as_of = dates[-1]
    symbols = [
        "600000.SH",
        "688001.SH",
        "300001.SZ",
        "430001.BJ",
        "001234.SZ",
        "518880.SH",
    ]
    bars = pd.DataFrame(
        [
            {
                "date": date,
                "symbol": symbol,
                "close": 100.0,
                "raw_close": 10.0,
                "is_suspended": False,
                "is_st": symbol in {"600000.SH", "518880.SH"},
            }
            for date in dates
            for symbol in symbols
        ]
    )
    listed_dates = [
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2020-01-01"),
        dates[-5],
        pd.Timestamp("2020-01-01"),
    ]
    payload = {
        "sessions": tuple(dates),
        "bars": bars,
        "instruments": pd.DataFrame(
            {
                "snapshot_date": [dates[0]] * len(symbols),
                "symbol": symbols,
                "asset_type": ["CS", "CS", "CS", "CS", "CS", "ETF"],
                "listed_date": listed_dates,
            }
        ),
    }
    execution_rows = [
        {
            "date": as_of,
            "symbol": symbol,
            "is_suspended": pd.NA,
            "limit_up": pd.NA,
            "limit_down": pd.NA,
        }
        for symbol in symbols
    ]

    with SdkExecutionSession(DEFAULT_STRATEGY_SOURCE) as session:
        session.configure(payload)
        result = session.execute(
            "execution_data_fill",
            {
                "event": "session_open",
                "as_of": as_of,
                "available_symbols": symbols,
                "execution_state_rows": execution_rows,
            },
        ).value

    filled = pd.DataFrame(result["rows"]).set_index("symbol")
    assert filled.loc["600000.SH", ["limit_up", "limit_down"]].tolist() == [10.5, 9.5]
    assert filled.loc["688001.SH", ["limit_up", "limit_down"]].tolist() == [12.0, 8.0]
    assert filled.loc["300001.SZ", ["limit_up", "limit_down"]].tolist() == [12.0, 8.0]
    assert filled.loc["430001.BJ", ["limit_up", "limit_down"]].tolist() == [13.0, 7.0]
    assert filled.loc["001234.SZ", ["limit_up", "limit_down"]].tolist() == [0.0, 0.0]
    assert filled.loc["518880.SH", ["limit_up", "limit_down"]].tolist() == [11.0, 9.0]
    assert filled["is_suspended"].eq(False).all()

    trade_rows = pd.DataFrame(
        [
            {
                "symbol": "001234.SZ",
                "open": 10.0,
                "volume": 1_000_000.0,
                "amount": 10_000_000.0,
                "is_suspended": False,
                "limit_up": 0.0,
                "limit_down": 0.0,
            }
        ]
    ).set_index("symbol")
    assert (
        _execution_data_gaps(
            trade_rows,
            ["001234.SZ"],
            execution_field="open",
        )
        == {}
    )
    assert _trade_allowed(
        trade_rows,
        "001234.SZ",
        "open",
        side="buy",
        strict_execution_data=True,
    )


def test_510500_exit_on_2021_11_01_uses_unadjusted_execution_price():
    trade_rows = pd.DataFrame(
        [
            {
                "symbol": "510500.SH",
                "date": pd.Timestamp("2021-11-01"),
                "open": 6.484,
                "raw_open": 7.90,
                "volume": 1_000_000.0,
                "amount": 10_000_000.0,
                "is_suspended": False,
                "limit_up": 8.68,
                "limit_down": 7.11,
            }
        ]
    ).set_index("symbol")

    assert _trade_allowed(
        trade_rows,
        "510500.SH",
        "open",
        side="sell",
        strict_execution_data=True,
    )
    assert (
        _execution_data_gaps(
            trade_rows,
            ["510500.SH"],
            execution_field="open",
        )
        == {}
    )


def test_execution_fill_rejects_a_single_zero_price_limit_marker():
    source, _ = replace_registered_function(
        DEFAULT_STRATEGY_SOURCE,
        entrypoint_id="fill_missing_market_state",
        function_source="""@execution_data_fill(id="fill_missing_market_state")
def fill_missing_market_state(context, rows):
    filled = rows.copy()
    filled.loc[filled["limit_up"].isna(), "limit_up"] = 0.0
    return filled
""",
    )
    dates = pd.bdate_range("2026-01-01", periods=2)
    payload = {
        "sessions": tuple(dates),
        "bars": pd.DataFrame(
            {
                "date": [dates[0]],
                "symbol": ["600000.SH"],
                "raw_close": [10.0],
                "is_st": [False],
                "is_suspended": [False],
            }
        ),
        "instruments": pd.DataFrame(),
    }
    with SdkExecutionSession(source) as session:
        session.configure(payload)
        with pytest.raises(SdkRuntimeError, match="both limit_up and limit_down"):
            session.execute(
                "execution_data_fill",
                {
                    "event": "session_open",
                    "as_of": dates[-1],
                    "available_symbols": ["600000.SH"],
                    "execution_state_rows": [
                        {
                            "date": dates[-1],
                            "symbol": "600000.SH",
                            "is_suspended": False,
                            "limit_up": pd.NA,
                            "limit_down": pd.NA,
                        }
                    ],
                },
            )


def test_requirement_manifests_reject_invalid_shapes_and_specifiers():
    bad_data = DEFAULT_STRATEGY_SOURCE.replace(
        "DATA_REQUIREMENTS = {",
        'DATA_REQUIREMENTS = {"bars": "close"}\n\nIGNORED_REQUIREMENTS = {',
        1,
    )
    bad_runtime = DEFAULT_STRATEGY_SOURCE.replace(
        "DATA_REQUIREMENTS = {",
        'RUNTIME_REQUIREMENTS = {"pandas": "definitely-not-a-specifier"}\n\nDATA_REQUIREMENTS = {',
    )
    unsupported_dataset = DEFAULT_STRATEGY_SOURCE.replace(
        "DATA_REQUIREMENTS = {",
        'DATA_REQUIREMENTS = {"future_news": ["text"],',
    )
    for source in (bad_data, bad_runtime, unsupported_dataset):
        try:
            inspect_strategy_source(source)
        except StrategySourceError as exc:
            assert exc.phase == "register"
        else:
            raise AssertionError("invalid requirement manifest must be rejected")


def test_context_hides_future_rows():
    bars = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "symbol": ["A", "A"],
            "close": [1.0, 99.0],
        }
    )
    context = FactorContext(
        event=Event.SESSION_CLOSE,
        as_of="2024-01-01",
        sessions=["2024-01-01", "2024-01-02"],
        symbols=["A"],
        bars=bars,
    )
    assert context.history("close", window=10).iloc[-1, 0] == 1.0


def test_worker_uses_one_saved_factor_for_snapshot_and_event():
    data = _payload()
    with SdkExecutionSession(DEFAULT_STRATEGY_SOURCE) as session:
        session.configure(data)
        factor = session.execute(
            "factor",
            {
                "event": "session_close",
                "as_of": data["sessions"][-1],
                "available_symbols": ["A", "B"],
                "factor_id": "momentum_20d",
                "parameters": {"window": 20},
                "portfolio": {},
                "state": {},
            },
        ).value
        event = session.execute(
            "event",
            {
                "event": "session_close",
                "as_of": data["sessions"][-1],
                "available_symbols": ["A", "B"],
                "portfolio": {},
                "state": {},
                "force_signal": True,
                "limits": {"max_weight": 1.0, "max_gross_exposure": 1.0},
            },
        ).value
    assert factor["factor_id"] == "momentum_20d"
    assert "momentum_20d" in factor["invoked"]
    assert event["signal"]["scores"] == {item["symbol"]: item["value"] for item in factor["values"]}


def test_default_stock_template_excludes_non_stock_instruments():
    data = _payload()
    data["instruments"].loc[data["instruments"]["symbol"].eq("B"), "asset_type"] = "ETF"
    with SdkExecutionSession(DEFAULT_STRATEGY_SOURCE) as session:
        session.configure(data)
        event = session.execute(
            "event",
            {
                "event": "session_close",
                "as_of": data["sessions"][-1],
                "available_symbols": ["A", "B"],
                "portfolio": {},
                "state": {},
                "force_signal": True,
                "limits": {"max_weight": 1.0, "max_gross_exposure": 1.0},
            },
        ).value

    assert event["universe"] == ["A"]
    assert set(event["signal"]["scores"]) == {"A"}


def test_repository_atomically_creates_a_new_immutable_revision(tmp_path: Path):
    repository = StrategyRepository(tmp_path / "sdk.db")
    try:
        project = repository.clone_project("sdk-v1-default", "test-project")
        package = repository.get_package("test-project", 1)
        updated, _ = update_parameter_default(
            project["draft_source"],
            entrypoint_id="momentum_20d",
            parameter="window",
            value=40,
        )
        changed = repository.update_draft("test-project", updated)
        frozen = repository.get_package("test-project", 1)
        assert frozen["source"] == package["source"]
        assert frozen["source_sha256"] == package["source_sha256"]
        assert changed["current_revision"] == 2
        assert changed["dirty"] is False
        assert (
            repository.get_package("test-project", 2)["source_sha256"]
            == changed["draft_source_sha256"]
        )
    finally:
        repository.close()


def test_repository_uses_rq_profile_and_migrates_existing_projects(tmp_path: Path):
    database = tmp_path / "rq-profile.db"
    repository = StrategyRepository(database)
    try:
        assert repository.get_project("sdk-v1-default")["profile"] == "runtime"
        cloned = repository.clone_project("sdk-v1-default", "rq-project")
        assert cloned["profile"] == "runtime"
    finally:
        repository.close()

    connection = sqlite3.connect(database)
    connection.execute("UPDATE strategy_projects SET profile = 'demo'")
    connection.execute(
        "DELETE FROM strategy_contract_migrations WHERE name = ?",
        ("strategy-sdk-v1-rq-profile",),
    )
    connection.commit()
    connection.close()

    repository = StrategyRepository(database)
    try:
        assert {item["profile"] for item in repository.list_projects()} == {"runtime"}
    finally:
        repository.close()


def test_repository_removes_deprecated_factor_inputs_from_mutable_drafts(tmp_path: Path):
    database = tmp_path / "factor-inputs.db"
    repository = StrategyRepository(database)
    repository.close()
    legacy = DEFAULT_STRATEGY_SOURCE.replace(
        '@factor(id="momentum_20d", label="20 日动量")',
        '@factor(id="momentum_20d", label="20 日动量", inputs=["close"])',
        1,
    )
    legacy_hash = inspect_strategy_source(legacy).source_sha256
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE strategy_projects SET draft_source = ?, draft_source_sha256 = ?",
        (legacy, legacy_hash),
    )
    connection.execute(
        "DELETE FROM strategy_contract_migrations WHERE name = ?",
        ("strategy-sdk-v1-remove-factor-inputs",),
    )
    connection.commit()
    connection.close()

    migrated = StrategyRepository(database)
    try:
        project = migrated.get_project("sdk-v1-default")
        assert project is not None
        assert "inputs=" not in project["draft_source"]
        assert project["draft_source_sha256"] != legacy_hash
    finally:
        migrated.close()


def test_repository_backfills_default_risk_python_for_clean_legacy_projects(tmp_path: Path):
    database = tmp_path / "default-risk.db"
    repository = StrategyRepository(database)
    try:
        repository.clone_project("sdk-v1-default", "legacy-risk-project")
    finally:
        repository.close()

    start = DEFAULT_STRATEGY_SOURCE.index("@on_event(Event.SESSION_CLOSE")
    end = DEFAULT_STRATEGY_SOURCE.index("@execution(", start)
    legacy_source = DEFAULT_STRATEGY_SOURCE[:start] + DEFAULT_STRATEGY_SOURCE[end:]
    legacy_hash = inspect_strategy_source(legacy_source).source_sha256
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE strategy_projects SET draft_source = ?, draft_source_sha256 = ? WHERE id = ?",
        (legacy_source, legacy_hash, "legacy-risk-project"),
    )
    connection.execute(
        "UPDATE strategy_source_packages SET source = ?, source_sha256 = ? "
        "WHERE project_id = ? AND revision = 1",
        (legacy_source, legacy_hash, "legacy-risk-project"),
    )
    connection.execute(
        "DELETE FROM strategy_contract_migrations WHERE name = ?",
        ("strategy-sdk-v1-default-risk-event",),
    )
    connection.commit()
    connection.close()

    migrated = StrategyRepository(database)
    try:
        project = migrated.get_project("legacy-risk-project")
        assert project is not None
        assert (
            '@on_event(Event.SESSION_CLOSE, id="holding_period_risk"' in project["strategy_source"]
        )
        assert project["current_revision"] == 2
        assert project["dirty"] is False
        assert migrated.get_package("legacy-risk-project", 1)["source_sha256"] == legacy_hash
        assert (
            migrated.get_package("legacy-risk-project", 2)["source_sha256"]
            == project["draft_source_sha256"]
        )
    finally:
        migrated.close()


def test_concurrent_revision_saves_are_serialized(tmp_path: Path):
    database = tmp_path / "revision-race.db"
    setup = StrategyRepository(database)
    try:
        setup.create_project(
            "revision-race",
            name="Revision Race",
            source=DEFAULT_STRATEGY_SOURCE + "\n",
        )
        project = setup.get_project("revision-race")
        assert project is not None
        setup.update_draft(
            "revision-race",
            project["draft_source"] + "\n",
            expected_source_sha256=project["draft_source_sha256"],
        )
    finally:
        setup.close()

    repositories = [StrategyRepository(database), StrategyRepository(database)]
    barrier = threading.Barrier(2)
    for repository in repositories:
        original_probe = repository._probe_source

        def synchronized_probe(source, inspection, *, _probe=original_probe):
            _probe(source, inspection)
            barrier.wait(timeout=10)

        repository._probe_source = synchronized_probe
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            packages = list(
                executor.map(
                    lambda repository: repository.save_revision("revision-race"),
                    repositories,
                )
            )
        assert {package["revision"] for package in packages} == {2}
        assert [item["revision"] for item in repositories[0].list_packages("revision-race")] == [
            2,
            1,
        ]
    finally:
        for repository in repositories:
            repository.close()


def test_event_backtest_runs_the_frozen_source_package(tmp_path: Path):
    repository = StrategyRepository(tmp_path / "backtest.db")
    try:
        result = run_strategy_backtest(
            repository,
            "sdk-v1-default",
            "2025-01-01",
            "2025-03-31",
            create_default_engine(),
            execution_data_policy="illustrative",
        )
    finally:
        repository.close()
    assert not result.returns.empty
    assert result.diagnostics["source_sha256"] == result.package["source_sha256"]
    assert any(item["signal_due"] for item in result.diagnostics["events"])


def test_field_and_factor_click_snippets_insert_as_valid_python():
    source = DEFAULT_STRATEGY_SOURCE
    marker = source.index("    return close.iloc[-1]") + 4
    field = factor_field_snippet("volume")
    assert factor_field_snippet("raw_open") == (
        'raw_open = context.history("raw_open", window=window)'
    )
    updated, inspection = insert_source(
        source,
        cursor=marker,
        snippet=f"{field}\n    ",
    )
    assert inspection is not None
    dependency = factor_dependency_snippet("momentum_20d", window=10)
    marker = updated.index("\n@signal")
    updated, inspection = insert_source(
        updated,
        cursor=marker,
        snippet=(
            '\n\n@factor(id="momentum_copy")\n'
            "def momentum_copy(context):\n"
            f"    {dependency}\n"
            "    return momentum_20d\n"
        ),
    )
    assert inspection is not None
    assert field in updated and dependency in updated


def test_static_validation_rejects_factor_dependency_cycles():
    source = DEFAULT_STRATEGY_SOURCE.replace(
        "@signal(",
        """@factor(id="cycle_a")
def cycle_a(context):
    return context.factor("cycle_b")


@factor(id="cycle_b")
def cycle_b(context):
    return context.factor("cycle_a")


@signal(""",
    )
    try:
        inspect_strategy_source(source)
    except StrategySourceError as exc:
        assert exc.phase == "register"
        assert "cycle_a -> cycle_b -> cycle_a" in str(exc)
    else:
        raise AssertionError("factor dependency cycle was accepted")


def test_save_probe_executes_even_unused_registered_factors(tmp_path: Path):
    source = DEFAULT_STRATEGY_SOURCE.replace(
        "@signal(",
        """@factor(id="unused_bad")
def unused_bad(context):
    return {"not": "a series"}


@signal(""",
    )
    repository = StrategyRepository(tmp_path / "probe.db")
    try:
        try:
            repository.create_project("bad-factor", name="Bad", source=source)
        except Exception as exc:
            assert "must return pandas.Series" in str(exc)
        else:
            raise AssertionError("unused invalid factor passed the save probe")
    finally:
        repository.close()


def test_failed_cross_file_probe_leaves_source_and_revision_unchanged(tmp_path: Path):
    repository = StrategyRepository(tmp_path / "atomic-probe.db")
    try:
        original = repository.clone_project("sdk-v1-default", "atomic-probe")
        with pytest.raises(Exception, match="must return pandas.Series"):
            repository.add_factor_source(
                "atomic-probe",
                '@factor(id="bad")\ndef bad(context):\n    return {"not": "a series"}\n',
                expected_source_sha256=original["draft_source_sha256"],
            )
        unchanged = repository.get_project("atomic-probe")
        assert unchanged is not None
        assert unchanged["draft_source_sha256"] == original["draft_source_sha256"]
        assert unchanged["current_revision"] == 1
        assert unchanged["dirty"] is False
        assert [item["revision"] for item in repository.list_packages("atomic-probe")] == [1]
    finally:
        repository.close()


class _SuspensionEngine:
    def __init__(self):
        self.dates = pd.bdate_range("2024-01-01", periods=4)
        self.bars = pd.DataFrame(
            [
                {
                    "date": date,
                    "symbol": "A",
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "raw_open": 10.0,
                    "raw_high": 10.0,
                    "raw_low": 10.0,
                    "raw_close": 10.0,
                    "volume": 0.0 if index == 1 else 1_000_000.0,
                    "amount": 0.0 if index == 1 else 10_000_000.0,
                    # Nullable market-state columns are not a suspension.
                    "is_suspended": pd.NA,
                    "is_st": False,
                }
                for index, date in enumerate(self.dates)
            ]
        )

    def get_instruments(self, as_of_date):
        return pd.DataFrame(
            {"snapshot_date": [self.dates[0]], "symbol": ["A"], "asset_type": ["CS"]}
        )

    def get_bars(self, symbols, start_date, end_date, **kwargs):
        return self.bars.copy()


class _PartialExecutionDataEngine:
    def __init__(self):
        self.dates = pd.bdate_range("2024-01-01", periods=4)
        self.bars = pd.DataFrame(
            [
                {
                    "date": date,
                    "symbol": symbol,
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "raw_open": 10.0,
                    "raw_high": 10.0,
                    "raw_low": 10.0,
                    "raw_close": 10.0,
                    "volume": 1_000_000.0,
                    "amount": 100_000_000.0,
                    "is_suspended": False,
                    "is_st": False,
                    "limit_up": pd.NA if symbol == "A" else 11.0,
                    "limit_down": 9.0,
                }
                for date in self.dates
                for symbol in ("A", "B")
            ]
        )

    def get_instruments(self, as_of_date):
        return pd.DataFrame(
            {
                "snapshot_date": [self.dates[0], self.dates[0]],
                "symbol": ["A", "B"],
                "asset_type": ["CS", "CS"],
            }
        )

    def get_bars(self, symbols, start_date, end_date, **kwargs):
        return self.bars.copy()


def _daily_universe_strategy_source() -> str:
    return (
        DEFAULT_STRATEGY_SOURCE.replace(
            "    ExecutionPolicy,\n",
            "    Daily,\n    ExecutionPolicy,\n",
        )
        .replace(
            'Monthly.last_trading_day(at="close")',
            'Daily.at("close")',
        )
        .replace(
            """    scores = context.combine_factors(
        weights={"momentum_20d": 1.0},
        normalization="raw",
        parameters={"momentum_20d": {"window": 20}},
    ).dropna()
""",
            "    scores = __import__('pandas').Series({symbol: 1.0 for symbol in context.universe}, dtype=float)\n",
        )
    )


def _without_execution_data_fill(source: str) -> str:
    return source.replace(
        '@execution_data_fill(id="fill_missing_market_state", label="补齐缺失交易状态")\n',
        "",
        1,
    )


def test_rejected_fill_does_not_change_actual_positions(tmp_path: Path):
    source = (
        DEFAULT_STRATEGY_SOURCE.replace(
            "    ExecutionPolicy,\n",
            "    Daily,\n    ExecutionPolicy,\n",
        )
        .replace(
            'Monthly.last_trading_day(at="close")',
            'Daily.at("close")',
        )
        .replace(
            """    scores = context.combine_factors(
        weights={"momentum_20d": 1.0},
        normalization="raw",
        parameters={"momentum_20d": {"window": 20}},
    ).dropna()
""",
            "    scores = __import__('pandas').Series({symbol: 1.0 for symbol in context.universe}, dtype=float)\n",
        )
    )
    repository = StrategyRepository(tmp_path / "reject.db")
    try:
        repository.create_project("reject-fill", name="Reject", source=source)
        result = run_strategy_backtest(
            repository,
            "reject-fill",
            "2024-01-01",
            "2024-01-05",
            _SuspensionEngine(),
            execution_data_policy="illustrative",
        )
    finally:
        repository.close()
    rejected = next(item for item in result.executions if item["entry_date"] == "2024-01-02")
    assert rejected["traded_weight"] == 0.0
    assert rejected["executed_weights"] == {}
    assert result.weights.loc[pd.Timestamp("2024-01-02")].sum() == 0.0


def test_equal_buy_deltas_use_symbol_as_a_deterministic_tie_breaker():
    date = pd.Timestamp("2024-01-02")
    bars = pd.DataFrame(
        {
            "date": [date, date],
            "symbol": ["B", "A"],
            "open": [10.0, 10.0],
            "close": [10.0, 10.0],
            "volume": [1_000_000.0, 1_000_000.0],
            "amount": [100_000_000.0, 100_000_000.0],
        }
    )
    executed, audit = _apply_execution_constraints(
        {"B": 0.5, "A": 0.5},
        {},
        bars,
        date,
        SimpleNamespace(
            execution=ExecutionSpec(
                cost_bps=2.5,
                slippage_bps=2.0,
                portfolio_value=1_000_000.0,
                max_participation_rate=1.0,
            )
        ),
    )

    assert executed["A"] == 0.5
    assert executed["B"] < 0.5
    assert audit["constrained_symbols"] == ["B"]
    assert audit["cash_rejection_symbols"] == ["B"]
    assert audit["capacity_rejection_symbols"] == []


def test_execution_rejections_are_classified_by_system_cause():
    rows = pd.DataFrame(
        [
            {
                "symbol": "SUSPENDED",
                "open": 10.0,
                "raw_open": 10.0,
                "volume": 0.0,
                "amount": 0.0,
                "is_suspended": True,
                "limit_up": 11.0,
                "limit_down": 9.0,
            },
            {
                "symbol": "LIMIT_UP",
                "open": 10.0,
                "raw_open": 11.0,
                "volume": 1_000.0,
                "amount": 10_000.0,
                "is_suspended": False,
                "limit_up": 11.0,
                "limit_down": 9.0,
            },
            {
                "symbol": "MISSING",
                "open": 10.0,
                "raw_open": 10.0,
                "volume": 1_000.0,
                "amount": 10_000.0,
                "is_suspended": pd.NA,
                "limit_up": 11.0,
                "limit_down": 9.0,
            },
        ]
    ).set_index("symbol")

    assert (
        _trade_rejection_reason(rows, "SUSPENDED", "open", side="sell", strict_execution_data=True)
        == "suspension"
    )
    assert (
        _trade_rejection_reason(rows, "LIMIT_UP", "open", side="buy", strict_execution_data=True)
        == "limit_up"
    )
    assert (
        _trade_rejection_reason(rows, "MISSING", "open", side="buy", strict_execution_data=True)
        == "market_state"
    )
    assert (
        _trade_rejection_reason(rows, "ABSENT", "open", side="buy", strict_execution_data=True)
        == "market_state"
    )


def test_execution_fidelity_reports_facts_without_hidden_quality_thresholds():
    executions = [
        {
            "attempted_trade_count": 1,
            "successful_trade_count": 0,
            "execution_fidelity": 0.0,
            "target_weight_deviation": 0.5,
            "exit_failure_symbols": ["510500.SH"],
            "limit_down_rejection_count": 1,
        },
        {
            "attempted_trade_count": 1,
            "successful_trade_count": 0,
            "execution_fidelity": 0.0,
            "target_weight_deviation": 0.5,
            "exit_failure_symbols": ["510500.SH"],
            "limit_down_rejection_count": 1,
        },
    ]

    fidelity = _execution_fidelity_summary(executions)
    summary = _aggregate_execution_summary(executions)

    assert fidelity == {
        "mean": 0.0, "minimum": 0.0, "attempted_period_count": 2,
        "maximum_target_weight_deviation": 0.5,
    }
    assert summary["limit_down_rejection_count"] == 2
    assert summary["attempted_trade_count"] == 2


def test_sdk_signal_evidence_resolves_ic_without_persisting_score_vectors():
    rows: list[dict] = []
    first = {
        "signal_due": True,
        "universe": ["A", "B", "C", "D"],
        "signal": {
            "selected": ["C", "D"],
            "scores": {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0},
        },
    }
    pending = _record_signal_evidence(
        first,
        pd.Timestamp("2024-01-31"),
        {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0},
        rows,
        None,
    )
    pending = _record_signal_evidence(
        first,
        pd.Timestamp("2024-02-29"),
        {"A": 10.1, "B": 10.2, "C": 10.3, "D": 10.4},
        rows,
        pending,
    )

    assert pending is not None
    assert rows[0]["ic"] == pytest.approx(1.0)
    assert rows[0]["ic_observations"] == 4
    assert rows[0]["coverage"] == 1.0
    assert "scores" not in rows[0]


class _FutureInstrumentSnapshotEngine:
    """RQ-like master whose retrieval stamp is later than its bar history."""

    def __init__(self):
        self.dates = pd.bdate_range("2024-01-01", periods=45)
        self.bars = pd.DataFrame(
            [
                {
                    "date": date,
                    "symbol": symbol,
                    "open": 100.0 + index,
                    "high": 101.0 + index,
                    "low": 99.0 + index,
                    "close": 100.0 + index + (1.0 if symbol == "B" else 0.0),
                    "raw_open": 100.0 + index,
                    "raw_high": 101.0 + index,
                    "raw_low": 99.0 + index,
                    "raw_close": 100.0 + index + (1.0 if symbol == "B" else 0.0),
                    "volume": 1_000_000.0,
                    "amount": 100_000_000.0,
                    "is_st": False,
                }
                for index, date in enumerate(self.dates)
                for symbol in ("A", "B")
            ]
        )

    def get_instruments(self, as_of_date=None):
        return pd.DataFrame(
            {
                "snapshot_date": [pd.Timestamp("2026-08-25")] * 2,
                "symbol": ["A", "B"],
                "asset_type": ["ETF", "ETF"],
                "listed_date": [self.dates[0], self.dates[25]],
                "de_listed_date": [pd.NaT, pd.NaT],
            }
        )

    def get_bars(self, symbols, start_date, end_date, **kwargs):
        requested = set(symbols)
        return self.bars.loc[
            self.bars["symbol"].isin(requested)
            & self.bars["date"].between(pd.Timestamp(start_date), pd.Timestamp(end_date))
        ].copy()


class _SplitInstrumentSnapshotEngine(_FutureInstrumentSnapshotEngine):
    """Latest snapshot is stocks, while an earlier slice owns the ETFs."""

    def get_instruments(self, as_of_date=None):
        return pd.DataFrame(
            {
                "snapshot_date": [pd.Timestamp("2026-08-25")],
                "symbol": ["B"],
                "asset_type": ["CS"],
                "listed_date": [self.dates[0]],
                "de_listed_date": [pd.NaT],
            }
        )

    def get_instrument_master(self):
        return pd.DataFrame(
            {
                "snapshot_date": [
                    pd.Timestamp("2026-08-12"),
                    pd.Timestamp("2026-08-25"),
                ],
                "symbol": ["A", "B"],
                "asset_type": ["ETF", "CS"],
                "listed_date": [self.dates[0], self.dates[0]],
                "de_listed_date": [pd.NaT, pd.NaT],
            }
        )


def test_prepared_run_keeps_instruments_from_partial_snapshot_slices():
    engine = _SplitInstrumentSnapshotEngine()

    prepared = _prepare_data(
        engine,
        {"data_requirements": {"bars": ["open", "close", "volume", "amount"]}},
        engine.dates[0],
        engine.dates[-1],
    )

    assert prepared.all_symbols == ("A", "B")
    assert set(prepared.instruments["source_snapshot_date"]) == {
        pd.Timestamp("2026-08-12"),
        pd.Timestamp("2026-08-25"),
    }


def test_backtest_uses_listing_intervals_when_instrument_snapshot_is_later(
    tmp_path: Path,
):
    engine = _FutureInstrumentSnapshotEngine()
    repository = StrategyRepository(tmp_path / "future-snapshot.db")
    try:
        repository.create_project(
            "future-snapshot", name="Future Snapshot", source=DEFAULT_STRATEGY_SOURCE
        )
        result = run_strategy_backtest(
            repository,
            "future-snapshot",
            engine.dates[0].strftime("%Y-%m-%d"),
            engine.dates[-1].strftime("%Y-%m-%d"),
            engine,
            execution_data_policy="illustrative",
        )
    finally:
        repository.close()

    assert len(result.returns) == len(engine.dates)
    assert result.executions
    assert result.diagnostics["execution_reliable"] is False
    assert result.diagnostics["warnings"]
    assert result.benchmark_symbols == ("A", "B")


def test_strict_backtest_with_no_candidates_reports_evidence_for_project_validation(tmp_path: Path):
    source = _daily_universe_strategy_source().replace(
        "symbols=[symbol for symbol in context.universe if symbol in common_stocks]",
        "symbols=[]",
    )
    engine = _PartialExecutionDataEngine()
    repository = StrategyRepository(tmp_path / "no-candidates.db")
    try:
        repository.create_project("no-candidates", name="No candidates", source=source)
        result = run_strategy_backtest(
            repository,
            "no-candidates",
            engine.dates[0].strftime("%Y-%m-%d"),
            engine.dates[-1].strftime("%Y-%m-%d"),
            engine,
        )
    finally:
        repository.close()

    assert result.diagnostics["execution_summary"]["attempted_trade_count"] == 0
    assert "research_valid" not in result.diagnostics
    assert all(row["selected_count"] == 0 for row in result.diagnostics["signal_evidence"]["rows"])
    assert any("NO_TRADABLE_CANDIDATES" in item for item in result.diagnostics["warnings"])


def test_strict_backtest_excludes_candidates_with_missing_execution_data(tmp_path: Path):
    engine = _PartialExecutionDataEngine()
    repository = StrategyRepository(tmp_path / "strict-data.db")
    try:
        repository.create_project(
            "strict-data",
            name="Strict Data",
            source=_without_execution_data_fill(_daily_universe_strategy_source()),
        )
        result = run_strategy_backtest(
            repository,
            "strict-data",
            engine.dates[0].strftime("%Y-%m-%d"),
            engine.dates[-1].strftime("%Y-%m-%d"),
            engine,
        )
    finally:
        repository.close()

    assert any(item["executed_weights"].get("B", 0.0) > 0 for item in result.executions)
    assert all("A" in item["target_weights"] for item in result.executions)
    exclusions = result.diagnostics["execution_data_exclusions"]
    assert exclusions["symbol_date_count"] == len(engine.dates) - 1
    assert exclusions["unique_symbol_count"] == 1
    assert exclusions["symbols_sample"] == ["A"]
    assert result.diagnostics["execution_reliable"] is False
    assert result.diagnostics["execution_invalid_reasons"] == ["MISSING_EXECUTION_STATE"]
    assert any(
        warning.startswith("PARTIAL_MARKET_STATE:") for warning in result.diagnostics["warnings"]
    )


def test_project_python_fills_missing_execution_state_before_strict_checks(tmp_path: Path):
    engine = _PartialExecutionDataEngine()
    repository = StrategyRepository(tmp_path / "filled-execution-data.db")
    try:
        repository.create_project(
            "filled-execution-data",
            name="Filled Execution Data",
            source=_daily_universe_strategy_source(),
        )
        result = run_strategy_backtest(
            repository,
            "filled-execution-data",
            engine.dates[0].strftime("%Y-%m-%d"),
            engine.dates[-1].strftime("%Y-%m-%d"),
            engine,
        )
    finally:
        repository.close()

    assert any(item["executed_weights"].get("A", 0.0) > 0 for item in result.executions)
    assert result.diagnostics["execution_data_exclusions"]["symbol_date_count"] == 0
    fill = result.diagnostics["execution_data_fill"]
    assert fill["value_count"] == len(engine.dates) - 1
    assert fill["fields"] == {"limit_up": len(engine.dates) - 1}
    assert fill["source_counts"]["strategy_fill"] == {"limit_up": len(engine.dates) - 1}
    assert all(
        value["source"] == "strategy_fill"
        for execution in result.executions
        for value in execution["execution_data_fill"].get("filled_values", [])
    )
    assert result.diagnostics["execution_summary"]["synthetic_state_count"] == (
        len(engine.dates) - 1
    )
    assert any(
        warning.startswith("CUSTOM_EXECUTION_DATA_FILL:")
        for warning in result.diagnostics["warnings"]
    )


def test_missing_state_without_an_actual_trade_does_not_change_a_holding(tmp_path: Path):
    engine = _PartialExecutionDataEngine()
    engine.bars["is_suspended"] = engine.bars["is_suspended"].astype("boolean")
    missing_dates = engine.dates[2:]
    missing_held_state = engine.bars["symbol"].eq("B") & engine.bars["date"].isin(missing_dates)
    engine.bars.loc[missing_held_state, "is_suspended"] = pd.NA
    repository = StrategyRepository(tmp_path / "strict-held-data.db")
    try:
        repository.create_project(
            "strict-held-data",
            name="Strict Held Data",
            source=_without_execution_data_fill(_daily_universe_strategy_source()),
        )
        result = run_strategy_backtest(
            repository,
            "strict-held-data",
            engine.dates[0].strftime("%Y-%m-%d"),
            engine.dates[-1].strftime("%Y-%m-%d"),
            engine,
        )
    finally:
        repository.close()

    assert result.weights.loc[engine.dates[-1], "B"] > 0.0
    assert any(
        missing["symbol"] == "B"
        for item in result.executions
        for missing in item["missing_execution_data"]
    )


def test_held_delisting_writes_the_position_off_at_zero():
    instruments = pd.DataFrame(
        {
            "symbol": ["A"],
            "de_listed_date": [pd.Timestamp("2024-01-03")],
        }
    )
    asset_values = {"A": 0.5, "B": 0.25}
    entry_prices = {"A": 10.0, "B": 20.0}
    previous_close_prices = {"A": 9.0, "B": 19.0}

    settlements = _settle_delisted_positions(
        asset_values,
        entry_prices,
        previous_close_prices,
        instruments,
        pd.Timestamp("2024-01-03"),
    )

    assert settlements == [{"symbol": "A", "written_off_value": 0.5}]
    assert asset_values == {"B": 0.25}
    assert entry_prices == {"B": 20.0}
    assert previous_close_prices == {"B": 19.0}


def test_factor_execution_handles_empty_universe_and_short_history():
    payload = _payload()
    dates = payload["sessions"]
    with SdkExecutionSession(DEFAULT_STRATEGY_SOURCE) as session:
        session.configure(payload)
        empty = session.execute(
            "factor",
            {
                "event": "session_close",
                "as_of": dates[-1],
                "available_symbols": [],
                "factor_id": "momentum_20d",
                "parameters": {"window": 20},
                "portfolio": {},
                "state": {},
                "limits": {"max_weight": 1.0, "max_gross_exposure": 1.0},
            },
        )
        short = session.execute(
            "factor",
            {
                "event": "session_close",
                "as_of": dates[0],
                "available_symbols": ["A", "B"],
                "factor_id": "momentum_20d",
                "parameters": {"window": 20},
                "portfolio": {},
                "state": {},
                "limits": {"max_weight": 1.0, "max_gross_exposure": 1.0},
            },
        )

    assert empty.value["values"] == []
    assert short.value["values"] == [
        {"symbol": "A", "value": None},
        {"symbol": "B", "value": None},
    ]


def test_decision_event_gets_prior_state_and_actual_portfolio():
    source = DEFAULT_STRATEGY_SOURCE.replace(
        "    execution,\n",
        "    Event,\n    on_event,\n    execution,\n",
    ).replace(
        '@execution(id="next_open"',
        '''@on_event(Event.DECISION, id="decision_audit")
def decision_audit(context, state):
    state["decision_count"] = int(state.get("decision_count", 0)) + 1
    state["actual_positions"] = len(context.portfolio.positions)
    return None


@execution(id="next_open"''',
    )
    data = _payload()
    with SdkExecutionSession(source) as session:
        session.configure(data)
        first = session.execute(
            "event",
            {
                "event": "session_close",
                "as_of": data["sessions"][-1],
                "available_symbols": ["A", "B"],
                "portfolio": {"positions": [{"symbol": "A", "weight": 0.5}], "cash_weight": 0.5},
                "state": {"prior": 7},
                "force_signal": True,
                "limits": {"max_weight": 1.0, "max_gross_exposure": 1.0},
            },
        ).value
    assert first["state"] == {"prior": 7, "decision_count": 1, "actual_positions": 1}
    assert "decision_audit" in first["invoked"]


def test_strategy_context_exposes_daily_factors_and_index_membership() -> None:
    as_of = pd.Timestamp("2025-01-03")
    context = FactorContext(
        event="session_close",
        as_of=as_of,
        sessions=pd.bdate_range("2025-01-01", "2025-01-03"),
        symbols=["000001.SZ", "600000.SH"],
        bars=pd.DataFrame(
            {
                "date": [as_of, as_of],
                "symbol": ["000001.SZ", "600000.SH"],
                "close": [10.0, 20.0],
            }
        ),
        daily_factors=pd.DataFrame(
            {
                "date": ["2025-01-02", "2025-01-03", "2025-01-03"],
                "symbol": ["000001.SZ", "000001.SZ", "600000.SH"],
                "field": ["roe", "roe", "roe"],
                "value": [0.1, 0.2, 0.3],
            }
        ),
        index_components=pd.DataFrame(
            {
                "date": ["2024-12-31", "2024-12-31"],
                "index_symbol": ["000300.SH", "000300.SH"],
                "symbol": ["000001.SZ", "600000.SH"],
            }
        ),
    )

    assert context.daily_factor("roe").to_dict() == {
        "000001.SZ": 0.2,
        "600000.SH": 0.3,
    }
    assert context.index_components("000300.SH") == ("000001.SZ", "600000.SH")
