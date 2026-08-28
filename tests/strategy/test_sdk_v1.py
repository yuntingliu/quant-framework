from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from alphalab.dataio import create_default_engine
from alphalab.sdk.v1 import Event, FactorContext
from alphalab.strategy.builtins import DEFAULT_STRATEGY_SOURCE
from alphalab.strategy.config import ExecutionSpec
from alphalab.strategy.engine import _apply_execution_constraints, run_strategy_backtest
from alphalab.strategy.factor_templates import (
    install_factor_template,
    list_factor_templates,
)
from alphalab.strategy.repository import StrategyRepository
from alphalab.strategy.sdk_runtime import SdkExecutionSession
from alphalab.strategy.source import (
    StrategySourceError,
    assemble_strategy_source,
    delete_registered_function,
    factor_dependency_snippet,
    factor_field_snippet,
    insert_source,
    inspect_strategy_source,
    registered_function_source,
    remove_factor_inputs_arguments,
    replace_registered_function,
    split_strategy_source,
    update_parameter_default,
    update_signal_factor_blend,
    update_signal_schedule,
)


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
                "asset_type": ["ETF", "ETF"],
            }
        ),
        "fundamentals": pd.DataFrame(),
    }


def test_builtin_factor_catalog_is_native_sdk_python_and_all_templates_install(tmp_path: Path):
    templates = list_factor_templates()
    assert {item.id for item in templates} == {
        "bp",
        "ep",
        "gross_margin",
        "leverage",
        "ma_deviation",
        "momentum_20d",
        "momentum_60d",
        "profit_growth",
        "reversal_5d",
        "revenue_growth",
        "roa",
        "roe",
        "rsi_14",
        "turnover_20d",
        "volatility_20d",
        "volume_ratio",
    }
    assert all(item.source.startswith("@factor(") for item in templates)

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
    assert {item.id for item in inspection.entrypoints if item.kind == "factor"} == {
        "momentum_20d"
    }


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

        strategy_source = project["strategy_source"].replace(
            "top_n: int = 10", "top_n: int = 7"
        )
        project = repository.update_strategy_source(
            "source-units",
            strategy_source,
            expected_source_sha256=project["draft_source_sha256"],
        )
        assert "top_n: int = 7" in project["strategy_source"]
        assert "@factor" not in project["strategy_source"]
        assert "def momentum_20d(" in project["draft_source"]

        project = repository.add_factor_source(
            "source-units",
            '''@factor(id="close_level", label="收盘价")
def close_level(context):
    return context.current("close")
''',
            expected_source_sha256=project["draft_source_sha256"],
        )
        assert "@factor" not in project["strategy_source"]
        assert "def close_level(context)" in project["draft_source"]
        assert {item["path"] for item in project["source_units"]} == {
            "strategy.py",
            "factors/momentum_20d.py",
            "factors/close_level.py",
        }
        close_source = repository.get_factor_source("source-units", "close_level")
        assert close_source["source"].startswith('@factor(id="close_level"')
        project = repository.replace_factor_source(
            "source-units",
            "close_level",
            close_source["source"].replace("close_level", "latest_close"),
            expected_source_sha256=project["draft_source_sha256"],
        )
        assert "factors/close_level.py" not in {
            item["path"] for item in project["source_units"]
        }
        assert "factors/latest_close.py" in {
            item["path"] for item in project["source_units"]
        }
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
    assert signal.metadata["factor_blend"]["parameters"] == {
        "momentum_30d": {"window": 20}
    }


def test_replacing_factor_function_renames_factor_dependencies():
    source = DEFAULT_STRATEGY_SOURCE.replace(
        "\n@signal",
        '''
@factor(id="momentum_copy")
def momentum_copy(context):
    return context.factor("momentum_20d")

@signal''',
        1,
    )
    function_source = registered_function_source(
        source, entrypoint_id="momentum_20d"
    ).replace('id="momentum_20d"', 'id="momentum_30d"')
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
        '''
@factor(id="unused_factor")
def unused_factor(context):
    return context.current("close")

@signal''',
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


def test_requirement_manifests_reject_invalid_shapes_and_specifiers():
    bad_data = DEFAULT_STRATEGY_SOURCE.replace(
        '"bars": ["open", "high", "low", "close", "volume", "amount"]',
        '"bars": "close"',
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


def test_repository_keeps_immutable_revision_after_draft_change(tmp_path: Path):
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
        repository.update_draft("test-project", updated)
        frozen = repository.get_package("test-project", 1)
        assert frozen["source"] == package["source"]
        assert frozen["source_sha256"] == package["source_sha256"]
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
        assert '@on_event(Event.SESSION_CLOSE, id="holding_period_risk"' in project[
            "strategy_source"
        ]
        assert project["current_revision"] == 2
        assert project["dirty"] is False
        assert migrated.get_package("legacy-risk-project", 1)["source_sha256"] == legacy_hash
        assert migrated.get_package("legacy-risk-project", 2)["source_sha256"] == project[
            "draft_source_sha256"
        ]
    finally:
        migrated.close()


def test_event_backtest_runs_the_frozen_source_package(tmp_path: Path):
    repository = StrategyRepository(tmp_path / "backtest.db")
    try:
        result = run_strategy_backtest(
            repository,
            "sdk-v1-default",
            "2025-01-01",
            "2025-03-31",
            create_default_engine(),
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
                    "volume": 0.0 if index == 1 else 1_000_000.0,
                    "amount": 0.0 if index == 1 else 10_000_000.0,
                    # Nullable market-state columns are not a suspension.
                    "is_suspended": pd.NA,
                }
                for index, date in enumerate(self.dates)
            ]
        )

    def get_instruments(self, as_of_date):
        return pd.DataFrame(
            {"snapshot_date": [self.dates[0]], "symbol": ["A"], "asset_type": ["ETF"]}
        )

    def get_bars(self, symbols, start_date, end_date, **kwargs):
        return self.bars.copy()


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
                    "volume": 1_000_000.0,
                    "amount": 100_000_000.0,
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
        )
    finally:
        repository.close()

    assert len(result.returns) == len(engine.dates)
    assert result.executions
    assert result.diagnostics["warnings"] == []


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
