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
        "StrategyConfig",
        "SignalEngine",
        "run_backtest",
        "ResultStore",
        "list_factors",
        "compute_factor",
        "get_factor",
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
    assert '"trading.auto-trade": disabled("trading.auto-trade")' in text


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


def test_only_generic_strategy_templates_are_bundled():
    root = Path(__file__).resolve().parents[2]
    names = sorted(path.stem for path in (root / "alphalab" / "strategies").glob("*.yaml"))
    assert names == ["balanced", "growth", "low_vol", "momentum", "quality", "value"]
