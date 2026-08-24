"""One-time migrations for persisted pipeline contracts.

These migrations only rewrite exact, previously valid persisted component
contracts. They are not runtime aliases and do not broaden the current API.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sqlite3

from alphalab.pipeline.builtins import (
    EXECUTION_FIXED,
    PORTFOLIO_EQUAL_WEIGHT,
    SELECTION_FACTOR_TOP,
    SELECTION_PASS_THROUGH,
)
from alphalab.strategy.python_runtime import python_source_sha256


_EXECUTION_ENTRYPOINT_MIGRATION = "execution-entrypoint-configure-v1"
_FOUR_STAGE_CONTRACT_MIGRATION = "four-stage-pipeline-v1"
_THREE_STAGE_CONTRACT_MIGRATION = "three-stage-pipeline-v1"
_SIGNAL_MODEL_CONTRACT_MIGRATION = "signal-model-ownership-v1"
_SIGNAL_ALLOCATION_CONTRACT_MIGRATION = "signal-allocation-methods-v2"


def migrate_pipeline_contracts(connection: sqlite3.Connection) -> None:
    """Bring persisted components onto the current three-stage contract once."""

    _migrate_execution_entrypoint(connection)
    _migrate_four_stage_projects(connection)
    _migrate_three_stage_projects(connection)
    _migrate_signal_model_contract(connection)
    _migrate_signal_allocation_contract(connection)


def _migrate_signal_model_contract(connection: sqlite3.Connection) -> None:
    """Move decision cadence and effective weights out of execution/factor UI."""

    if connection.execute(
        "SELECT 1 FROM pipeline_contract_migrations WHERE name = ?",
        (_SIGNAL_MODEL_CONTRACT_MIGRATION,),
    ).fetchone():
        return

    rows = connection.execute("SELECT * FROM pipeline_projects").fetchall()
    for row in rows:
        refs = _load_json(row["component_refs_json"], {})
        if not {"selection", "execution"}.issubset(refs):
            continue
        settings = _load_json(row["settings_json"], {})
        stage_parameters = dict(settings.get("stage_parameters") or {})
        selection_overrides = dict(stage_parameters.get("selection") or {})
        execution_overrides = dict(stage_parameters.get("execution") or {})
        selection_version = _component_version(connection, dict(refs["selection"]))
        execution_version = _component_version(connection, dict(refs["execution"]))
        selection_defaults = _load_json(selection_version["parameters_json"], {})
        execution_defaults = _load_json(execution_version["parameters_json"], {})
        frequency = selection_overrides.get(
            "signal_frequency",
            selection_defaults.get(
                "signal_frequency",
                execution_overrides.get(
                    "rebalance_freq", execution_defaults.get("rebalance_freq", "monthly")
                ),
            ),
        )
        count = int(selection_overrides.get("count", selection_defaults.get("count", 20)))
        factors = settings.get("factors") if isinstance(settings.get("factors"), list) else []
        legacy_weights = {
            str(item.get("name")): float(item.get("weight", 1.0))
            for item in factors
            if isinstance(item, dict) and item.get("name")
        }
        selection_overrides.setdefault("signal_frequency", str(frequency))
        selection_overrides.setdefault(
            "normalization", str(selection_defaults.get("normalization", "percentile_rank"))
        )
        selection_overrides.setdefault(
            "exit_rank", int(selection_defaults.get("exit_rank", count))
        )
        selection_overrides.setdefault("factor_weights", legacy_weights)
        execution_overrides.pop("rebalance_freq", None)
        stage_parameters["selection"] = selection_overrides
        if execution_overrides:
            stage_parameters["execution"] = execution_overrides
        else:
            stage_parameters.pop("execution", None)
        settings["stage_parameters"] = stage_parameters
        connection.execute(
            """UPDATE pipeline_projects
               SET revision = ?, settings_json = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (int(row["revision"]) + 1, _json(settings), row["id"]),
        )

    selection = connection.execute(
        "SELECT built_in FROM pipeline_components WHERE id = 'selection-factor-top'"
    ).fetchone()
    if selection is not None and bool(selection["built_in"]):
        selection_parameters = {
            "count": 20,
            "exit_rank": 30,
            "min_factor_coverage": 0.5,
            "signal_frequency": "monthly",
            "normalization": "percentile_rank",
            "factor_weights": {},
        }
        connection.execute(
            """UPDATE pipeline_component_versions
               SET source = ?, source_sha256 = ?, parameters_json = ?,
                   notes = 'migrated once to signal-model ownership'
               WHERE component_id = 'selection-factor-top' AND version = 1""",
            (
                SELECTION_FACTOR_TOP,
                python_source_sha256(SELECTION_FACTOR_TOP),
                _json(selection_parameters),
            ),
        )
        connection.execute(
            """UPDATE pipeline_components
               SET name = '多因子综合排名',
                   description = '按决策频率归一化并组合多因子，使用进出排名缓冲生成信号集合'
               WHERE id = 'selection-factor-top'"""
        )

    execution_parameters = {
        "execution_price": "next_open",
        "cost_bps": 20.0,
        "slippage_bps": 0.0,
        "impact_bps": 0.0,
        "max_participation_rate": 0.1,
        "portfolio_value": 1_000_000.0,
    }
    for component_id, name, description in (
        (
            "execution-monthly",
            "下一交易日成交",
            "信号形成后按下一交易日价格、流动性与成本假设成交",
        ),
        (
            "execution-daily",
            "下一交易日成交（兼容）",
            "保留旧项目组件标识；信号频率现在由信号模型设置",
        ),
    ):
        component = connection.execute(
            "SELECT built_in FROM pipeline_components WHERE id = ?", (component_id,)
        ).fetchone()
        if component is None or not bool(component["built_in"]):
            continue
        connection.execute(
            """UPDATE pipeline_component_versions
               SET source = ?, source_sha256 = ?, parameters_json = ?,
                   notes = 'migrated once to execution-only ownership'
               WHERE component_id = ? AND version = 1""",
            (
                EXECUTION_FIXED,
                python_source_sha256(EXECUTION_FIXED),
                _json(execution_parameters),
                component_id,
            ),
        )
        connection.execute(
            "UPDATE pipeline_components SET name = ?, description = ? WHERE id = ?",
            (name, description, component_id),
        )

    connection.execute(
        "INSERT INTO pipeline_contract_migrations (name) VALUES (?)",
        (_SIGNAL_MODEL_CONTRACT_MIGRATION,),
    )


def _migrate_signal_allocation_contract(connection: sqlite3.Connection) -> None:
    """Upgrade the seeded allocator used inside the signal-model workbench."""

    if connection.execute(
        "SELECT 1 FROM pipeline_contract_migrations WHERE name = ?",
        (_SIGNAL_ALLOCATION_CONTRACT_MIGRATION,),
    ).fetchone():
        return
    component = connection.execute(
        "SELECT built_in FROM pipeline_components WHERE id = 'portfolio-equal-weight'"
    ).fetchone()
    if component is not None and bool(component["built_in"]):
        parameters = {
            "optimizer": "equal_weight",
            "rank_decay": 1.0,
            "max_weight": 0.1,
            "max_gross_exposure": 1.0,
        }
        connection.execute(
            """UPDATE pipeline_component_versions
               SET source = ?, source_sha256 = ?, parameters_json = ?,
                   notes = 'migrated once to signal-workbench allocation methods'
               WHERE component_id = 'portfolio-equal-weight' AND version = 1""",
            (
                PORTFOLIO_EQUAL_WEIGHT,
                python_source_sha256(PORTFOLIO_EQUAL_WEIGHT),
                _json(parameters),
            ),
        )
        connection.execute(
            """UPDATE pipeline_components
               SET name = '信号仓位分配',
                   description = '按等权、综合得分或排名衰减生成目标权重，并限制单票权重与总敞口'
               WHERE id = 'portfolio-equal-weight'"""
        )
    connection.execute(
        "INSERT INTO pipeline_contract_migrations (name) VALUES (?)",
        (_SIGNAL_ALLOCATION_CONTRACT_MIGRATION,),
    )


def _migrate_execution_entrypoint(connection: sqlite3.Connection) -> None:

    if connection.execute(
        "SELECT 1 FROM pipeline_contract_migrations WHERE name = ?",
        (_EXECUTION_ENTRYPOINT_MIGRATION,),
    ).fetchone():
        return

    rows = connection.execute(
        """SELECT v.component_id, v.version, v.source
           FROM pipeline_component_versions v
           JOIN pipeline_components c ON c.id = v.component_id
           WHERE c.stage = 'execution' AND v.entrypoint = 'create_orders'"""
    ).fetchall()
    for row in rows:
        source = str(row["source"])
        marker = "def create_orders("
        if source.count(marker) != 1:
            raise ValueError(
                "persisted execution component cannot be migrated automatically: "
                f"{row['component_id']}@{row['version']}"
            )
        migrated = source.replace(marker, "def configure_execution(", 1)
        connection.execute(
            """UPDATE pipeline_component_versions
               SET entrypoint = 'configure_execution', source = ?, source_sha256 = ?
               WHERE component_id = ? AND version = ?""",
            (
                migrated,
                python_source_sha256(migrated),
                row["component_id"],
                row["version"],
            ),
        )
    connection.execute(
        "INSERT INTO pipeline_contract_migrations (name) VALUES (?)",
        (_EXECUTION_ENTRYPOINT_MIGRATION,),
    )


def _migrate_four_stage_projects(connection: sqlite3.Connection) -> None:
    if connection.execute(
        "SELECT 1 FROM pipeline_contract_migrations WHERE name = ?",
        (_FOUR_STAGE_CONTRACT_MIGRATION,),
    ).fetchone():
        return

    # The active built-in keeps its stable component identity. Historical
    # backtests already contain their complete frozen source, so rewriting this
    # exact seeded version cannot alter a saved run.
    component = connection.execute(
        "SELECT built_in FROM pipeline_components WHERE id = 'portfolio-equal-weight'"
    ).fetchone()
    if component is not None and bool(component["built_in"]):
        connection.execute(
            """UPDATE pipeline_component_versions
               SET source = ?, source_sha256 = ?, parameters_json = ?,
                   notes = 'migrated once to four-stage constrained equal weight'
               WHERE component_id = 'portfolio-equal-weight' AND version = 1""",
            (
                PORTFOLIO_EQUAL_WEIGHT,
                python_source_sha256(PORTFOLIO_EQUAL_WEIGHT),
                _json({"max_weight": 0.1, "max_gross_exposure": 1.0}),
            ),
        )

    rows = connection.execute("SELECT * FROM pipeline_projects").fetchall()
    for row in rows:
        refs = _load_json(row["component_refs_json"], {})
        if "timing" not in refs and "risk" not in refs:
            continue
        if bool(row["built_in"]) and str(row["id"]) == "six-stage-default":
            connection.execute(
                "DELETE FROM pipeline_projects WHERE id = ?", (row["id"],)
            )
            continue

        required = {"universe", "selection", "portfolio", "execution"}
        if not required.issubset(refs) or "risk" not in refs:
            raise ValueError(
                f"persisted pipeline project cannot be migrated automatically: {row['id']}"
            )
        portfolio_ref = dict(refs["portfolio"])
        risk_ref = dict(refs["risk"])
        portfolio_version = _component_version(connection, portfolio_ref)
        risk_version = _component_version(connection, risk_ref)
        migrated_id = _migrated_portfolio_id(str(row["id"]))
        source = _combined_portfolio_source(
            str(portfolio_version["source"]), str(risk_version["source"])
        )
        settings = _load_json(row["settings_json"], {})
        stage_parameters = dict(settings.get("stage_parameters") or {})
        portfolio_parameters = {
            **_load_json(portfolio_version["parameters_json"], {}),
            **dict(stage_parameters.get("portfolio") or {}),
        }
        risk_parameters = {
            **_load_json(risk_version["parameters_json"], {}),
            **dict(stage_parameters.get("risk") or {}),
        }
        connection.execute(
            """INSERT OR IGNORE INTO pipeline_components
               (id, stage, name, description, built_in)
               VALUES (?, 'portfolio', ?, ?, 0)""",
            (
                migrated_id,
                f"{row['name']} · migrated portfolio",
                "One-time combination of the persisted portfolio and risk components.",
            ),
        )
        connection.execute(
            """INSERT OR IGNORE INTO pipeline_component_versions
               (component_id, version, entrypoint, source, source_sha256,
                parameters_json, notes)
               VALUES (?, 1, 'construct_portfolio', ?, ?, ?, ?)""",
            (
                migrated_id,
                source,
                python_source_sha256(source),
                _json(
                    {
                        "portfolio": portfolio_parameters,
                        "risk": risk_parameters,
                    }
                ),
                "Generated by four-stage-pipeline-v1; legacy timing is fixed at full exposure.",
            ),
        )
        refs = {
            "universe": refs["universe"],
            "selection": refs["selection"],
            "portfolio": {"component_id": migrated_id, "version": 1},
            "execution": refs["execution"],
        }
        stage_parameters.pop("timing", None)
        stage_parameters.pop("risk", None)
        stage_parameters.pop("portfolio", None)
        settings["stage_parameters"] = stage_parameters
        settings["pipeline_migration"] = {
            "name": _FOUR_STAGE_CONTRACT_MIGRATION,
            "removed_timing_component": dict(
                _load_json(row["component_refs_json"], {}).get("timing") or {}
            ),
            "combined_portfolio_component": portfolio_ref,
            "combined_risk_component": risk_ref,
            "note": "Timing was removed and its exposure fixed at 1.0; review this revision before reuse.",
        }
        revision = int(row["revision"]) + 1
        connection.execute(
            """UPDATE pipeline_projects
               SET revision = ?, component_refs_json = ?, settings_json = ?,
                   updated_at = datetime('now')
               WHERE id = ?""",
            (revision, _json(refs), _json(settings), row["id"]),
        )

    connection.execute(
        "INSERT INTO pipeline_contract_migrations (name) VALUES (?)",
        (_FOUR_STAGE_CONTRACT_MIGRATION,),
    )


def _migrate_three_stage_projects(connection: sqlite3.Connection) -> None:
    if connection.execute(
        "SELECT 1 FROM pipeline_contract_migrations WHERE name = ?",
        (_THREE_STAGE_CONTRACT_MIGRATION,),
    ).fetchone():
        return

    for component_id, source in (
        ("selection-factor-top", SELECTION_FACTOR_TOP),
        ("selection-pass-through", SELECTION_PASS_THROUGH),
    ):
        component = connection.execute(
            "SELECT built_in FROM pipeline_components WHERE id = ?", (component_id,)
        ).fetchone()
        if component is not None and bool(component["built_in"]):
            connection.execute(
                """UPDATE pipeline_component_versions
                   SET source = ?, source_sha256 = ?,
                       notes = 'migrated once to direct eligible-candidate selection'
                   WHERE component_id = ? AND version = 1""",
                (source, python_source_sha256(source), component_id),
            )

    rows = connection.execute("SELECT * FROM pipeline_projects").fetchall()
    for row in rows:
        refs = _load_json(row["component_refs_json"], {})
        if "universe" not in refs:
            continue
        if bool(row["built_in"]) and str(row["id"]) == "four-stage-default":
            connection.execute("DELETE FROM pipeline_projects WHERE id = ?", (row["id"],))
            continue

        required = {"selection", "portfolio", "execution"}
        if not required.issubset(refs):
            raise ValueError(
                f"persisted pipeline project cannot be migrated automatically: {row['id']}"
            )
        universe_ref = dict(refs["universe"])
        selection_ref = dict(refs["selection"])
        universe_version = _component_version(connection, universe_ref)
        selection_version = _component_version(connection, selection_ref)
        migrated_id = _migrated_selection_id(str(row["id"]))
        source = _combined_selection_source(
            str(universe_version["source"]), str(selection_version["source"])
        )
        settings = _load_json(row["settings_json"], {})
        stage_parameters = dict(settings.get("stage_parameters") or {})
        universe_parameters = {
            **_load_json(universe_version["parameters_json"], {}),
            **dict(stage_parameters.get("universe") or {}),
        }
        selection_parameters = {
            **_load_json(selection_version["parameters_json"], {}),
            **dict(stage_parameters.get("selection") or {}),
        }
        connection.execute(
            """INSERT OR IGNORE INTO pipeline_components
               (id, stage, name, description, built_in)
               VALUES (?, 'selection', ?, ?, 0)""",
            (
                migrated_id,
                f"{row['name']} · migrated selection",
                "One-time combination of the persisted stock-pool and selection components.",
            ),
        )
        connection.execute(
            """INSERT OR IGNORE INTO pipeline_component_versions
               (component_id, version, entrypoint, source, source_sha256,
                parameters_json, notes)
               VALUES (?, 1, 'select_assets', ?, ?, ?, ?)""",
            (
                migrated_id,
                source,
                python_source_sha256(source),
                _json(
                    {
                        "stock_pool": universe_parameters,
                        "selection": selection_parameters,
                    }
                ),
                "Generated by three-stage-pipeline-v1; the removed stock-pool source is internal to selection.",
            ),
        )
        refs = {
            "selection": {"component_id": migrated_id, "version": 1},
            "portfolio": refs["portfolio"],
            "execution": refs["execution"],
        }
        stage_parameters.pop("universe", None)
        stage_parameters.pop("selection", None)
        settings["stage_parameters"] = stage_parameters
        previous_migration = settings.get("pipeline_migration")
        settings["pipeline_migration"] = {
            "name": _THREE_STAGE_CONTRACT_MIGRATION,
            "removed_universe_component": universe_ref,
            "combined_selection_component": selection_ref,
            "previous": previous_migration if isinstance(previous_migration, dict) else None,
            "note": (
                "The stock pool is now project configuration plus a core eligibility gate; "
                "the removed component source was folded into selection for continuity."
            ),
        }
        revision = int(row["revision"]) + 1
        connection.execute(
            """UPDATE pipeline_projects
               SET revision = ?, component_refs_json = ?, settings_json = ?,
                   updated_at = datetime('now')
               WHERE id = ?""",
            (revision, _json(refs), _json(settings), row["id"]),
        )

    connection.execute(
        "INSERT INTO pipeline_contract_migrations (name) VALUES (?)",
        (_THREE_STAGE_CONTRACT_MIGRATION,),
    )


def _component_version(connection: sqlite3.Connection, ref: dict) -> sqlite3.Row:
    row = connection.execute(
        """SELECT source, parameters_json FROM pipeline_component_versions
           WHERE component_id = ? AND version = ?""",
        (ref.get("component_id"), int(ref.get("version", 0))),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"missing persisted component version: {ref.get('component_id')}@{ref.get('version')}"
        )
    return row


def _combined_portfolio_source(portfolio_source: str, risk_source: str) -> str:
    portfolio, portfolio_entrypoint = _namespace_source(
        portfolio_source, "_migrated_portfolio_", "construct_portfolio"
    )
    risk, risk_entrypoint = _namespace_source(
        risk_source, "_migrated_risk_", "apply_risk"
    )
    wrapper = f'''def construct_portfolio(context):
    """Run the once-migrated portfolio and static risk contracts without timing."""
    parameters = context.get("parameters", {{}})
    proposed = {portfolio_entrypoint}({{
        **context,
        "parameters": parameters.get("portfolio", {{}}),
        "timing": {{"exposure": 1.0, "signal": "removed_by_migration"}},
    }})
    constrained = {risk_entrypoint}({{
        **context,
        "parameters": parameters.get("risk", {{}}),
        "timing": {{"exposure": 1.0, "signal": "removed_by_migration"}},
        "portfolio": proposed,
    }})
    return {{
        "weights": dict(constrained.get("weights", {{}})),
        "gross_exposure": constrained.get("gross_exposure"),
    }}
'''
    return f"{portfolio}\n\n{risk}\n\n{wrapper}"


def _combined_selection_source(universe_source: str, selection_source: str) -> str:
    universe, universe_entrypoint = _namespace_source(
        universe_source, "_migrated_stock_pool_", "build_universe"
    )
    selection, selection_entrypoint = _namespace_source(
        selection_source, "_migrated_selection_", "select_assets"
    )
    wrapper = f'''def select_assets(context):
    """Run the once-migrated stock-pool filter inside the selection component."""
    parameters = context.get("parameters", {{}})
    stock_pool = {universe_entrypoint}({{
        **context,
        "parameters": parameters.get("stock_pool", {{}}),
    }})
    allowed = {{str(symbol).strip().upper() for symbol in stock_pool.get("symbols", [])}}
    candidates = [
        item for item in context.get("candidates", [])
        if str(item.get("symbol", "")).strip().upper() in allowed
    ]
    return {selection_entrypoint}({{
        **context,
        "parameters": parameters.get("selection", {{}}),
        "candidates": candidates,
        "universe_symbols": sorted(allowed),
    }})
'''
    return f"{universe}\n\n{selection}\n\n{wrapper}"


def _namespace_source(source: str, prefix: str, entrypoint: str) -> tuple[str, str]:
    tree = ast.parse(source)
    names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    if entrypoint not in names:
        raise ValueError(f"persisted component is missing {entrypoint}")
    mapping = {name: f"{prefix}{name}" for name in names}

    class Renamer(ast.NodeTransformer):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
            node.name = mapping.get(node.name, node.name)
            return self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
            node.name = mapping.get(node.name, node.name)
            return self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
            node.name = mapping.get(node.name, node.name)
            return self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> ast.AST:
            if node.id in mapping:
                node.id = mapping[node.id]
            return node

    migrated = ast.fix_missing_locations(Renamer().visit(tree))
    return ast.unparse(migrated).strip() + "\n", mapping[entrypoint]


def _migrated_portfolio_id(project_id: str) -> str:
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:10]
    readable = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in project_id.lower()
    ).strip("-_")
    return f"migrated-{readable[:42]}-{digest}"[:64]


def _migrated_selection_id(project_id: str) -> str:
    digest = hashlib.sha256(f"selection:{project_id}".encode("utf-8")).hexdigest()[:10]
    readable = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in project_id.lower()
    ).strip("-_")
    return f"migrated-selection-{readable[:32]}-{digest}"[:64]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None, default: object) -> object:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


__all__ = ["migrate_pipeline_contracts"]
