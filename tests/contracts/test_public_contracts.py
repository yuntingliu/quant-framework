from __future__ import annotations

from pathlib import Path

import alphalab

REMOVED_BROKER_TERMS = [
    "I" + "BKR",
    "T" + "WS",
    "ib" + "_async",
    "Interactive " + "Brokers",
]


def test_public_facade_exports_core_loop():
    for name in [
        "DataEngine",
        "create_default_engine",
        "create_runtime_engine",
        "create_rq_engine_from_env",
        "RQDataConfig",
        "RQDataProvider",
        "StrategyConfig",
        "SignalEngine",
        "BacktestResult",
        "run_backtest",
        "run_backtest_detailed",
        "ResultStore",
        "list_factors",
        "compute_factor",
        "get_factor",
        "evaluate_factor",
        "ExecutionSpec",
    ]:
        assert hasattr(alphalab, name)


def test_forbidden_product_lines_removed_from_core_and_backend():
    root = Path(__file__).resolve().parents[2]
    active_roots = [root / "alphalab", root / "dashboard" / "backend"]
    forbidden = REMOVED_BROKER_TERMS + [
        "DolphinDB",
        "AkShare",
        "Level-2",
        "daily_brief",
        "auto_trade",
        "xianren",
    ]
    hits: list[str] = []
    for base in active_roots:
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".py", ".ts", ".tsx", ".md", ".toml", ".json"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                for term in forbidden:
                    if term in text:
                        hits.append(f"{path.relative_to(root)}:{term}")
    assert hits == []


def test_frontend_preserves_gui_with_disabled_adapter_placeholders():
    root = Path(__file__).resolve().parents[2]
    components = root / "dashboard" / "frontend" / "src" / "widgets" / "registry" / "components.tsx"
    text = components.read_text(encoding="utf-8")
    assert "AdapterDisabledWidget" in text
    assert "widgetCatalog.map((widget) => [widget.id, disabled(widget.id)])" in text


def test_frontend_visible_catalog_excludes_removed_broker():
    root = Path(__file__).resolve().parents[2]
    visible_files = [
        root / "dashboard" / "frontend" / "src" / "widgets" / "registry" / "catalog.ts",
        root / "dashboard" / "frontend" / "src" / "workspace" / "modes.ts",
        root / "dashboard" / "frontend" / "src" / "layouts" / "presets.ts",
        root / "dashboard" / "frontend" / "src" / "components" / "CommandPalette.tsx",
    ]
    hits = [
        f"{path.relative_to(root)}:{term}"
        for path in visible_files
        for term in [item.lower() for item in REMOVED_BROKER_TERMS]
        if term in path.read_text(encoding="utf-8", errors="ignore").lower()
    ]
    assert hits == []


def test_frontend_exposes_five_customizable_workstation_modes():
    root = Path(__file__).resolve().parents[2]
    presets = (
        root / "dashboard" / "frontend" / "src" / "layouts" / "presets.ts"
    ).read_text(encoding="utf-8")
    modes = (
        root / "dashboard" / "frontend" / "src" / "workspace" / "modes.ts"
    ).read_text(encoding="utf-8")
    expected = ["data", "factor", "strategy", "backtest", "report"]
    assert 'export type WorkspaceMode = "data" | "factor" | "strategy" | "backtest" | "report"' in presets
    for mode in expected:
        assert f'{mode}: createWorkbenchPreset("{mode}"' in presets
        assert f'{mode}: {{ icon:' in modes
    assert 'export const DEFAULT_MODE: WorkspaceMode = "data"' in presets


def test_primary_workbench_surfaces_subscribe_to_language_context():
    root = Path(__file__).resolve().parents[2]
    frontend = root / "dashboard" / "frontend" / "src"
    localized_surfaces = [
        "widgets/data/DataWorkbench.tsx",
        "widgets/research/FactorWorkbench.tsx",
        "widgets/research/FactorResearchLab.tsx",
        "widgets/research/FactorLibrary.tsx",
        "widgets/research/StrategyWorkbench.tsx",
        "widgets/backtest/BacktestWorkbench.tsx",
        "widgets/backtest/BacktestCompare.tsx",
        "widgets/research/ReportWorkbench.tsx",
        "widgets/market/AnalyticsControls.tsx",
        "widgets/market/AnnualReturns.tsx",
        "widgets/market/CorrelationMatrix.tsx",
        "widgets/market/CumulativeReturns.tsx",
        "widgets/market/DrawdownAnalysis.tsx",
        "widgets/market/FactorStats.tsx",
        "widgets/market/VolatilityAnalysis.tsx",
    ]
    missing = [
        path
        for path in localized_surfaces
        if "useLanguage" not in (frontend / path).read_text(encoding="utf-8")
    ]
    assert missing == []


def test_only_generic_strategy_templates_are_bundled():
    root = Path(__file__).resolve().parents[2]
    names = sorted(path.stem for path in (root / "alphalab" / "strategies").glob("*.yaml"))
    assert names == ["balanced", "growth", "low_vol", "momentum", "quality", "value"]
