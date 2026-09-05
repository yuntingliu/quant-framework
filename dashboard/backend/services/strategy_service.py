"""Application service for canonical Strategy SDK v1 projects and runs."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import date
from typing import Any, Mapping

import pandas as pd

from alphalab.analytics import (
    FACTOR_NAMES,
    equal_weight_benchmark,
)
from alphalab.dataio import MissingDataError
from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.recipes import (
    inspect_data_recipe_source,
    migrate_legacy_builtin_recipe,
    render_builtin_recipe,
    update_recipe_parameters,
)
from alphalab.dataio.runtime import OperationsStore, RuntimeStore
from alphalab.provenance import build_research_provenance
from alphalab.store import ResultStore
from alphalab.strategy.engine import (
    evaluate_factor_history,
    evaluate_factor_snapshot,
    preview_strategy,
    run_strategy_backtest,
)
from alphalab.strategy.factor_templates import (
    get_factor_template,
    install_factor_template,
    instantiate_factor_template,
    list_factor_templates,
)
from alphalab.strategy.repository import (
    DEFAULT_PROJECT_ID,
    StrategyRepository,
    normalize_project_id,
)
from alphalab.strategy.source import (
    SourceInspection,
    StrategySourceError,
    delete_registered_function,
    factor_dependency_snippet,
    factor_field_snippet,
    insert_source,
    inspect_strategy_source,
    merge_data_requirements,
    migrate_default_strategy_components,
    registered_function_source,
    replace_registered_function,
    split_strategy_source,
    update_parameter_default,
    update_signal_factor_blend,
    update_signal_schedule,
)
from alphalab.validation.repository import ValidationRepository
from alphalab.validation.runtime import execute_validation
from alphalab.validation.source import (
    inspect_validation_source,
    update_validation_parameters,
)
from dashboard.backend.services.data_service import _engine, _profile_range


def repository() -> StrategyRepository:
    return StrategyRepository()


def operations_store() -> OperationsStore:
    return OperationsStore()


def _project_recipe_bounds() -> dict[str, str]:
    bars = DataCatalog().status("rq.bars")
    if bars["status"] == "ready" and bars["date_start"] and bars["date_end"]:
        return {"start": bars["date_start"], "end": bars["date_end"]}
    today = date.today()
    try:
        fallback_start = today.replace(year=today.year - 5)
    except ValueError:
        fallback_start = today.replace(year=today.year - 5, day=28)
    return {"start": fallback_start.isoformat(), "end": today.isoformat()}


def list_projects() -> list[dict[str, Any]]:
    repo = repository()
    try:
        return repo.list_projects()
    finally:
        repo.close()


def get_project(project_id: str) -> dict[str, Any] | None:
    repo = repository()
    try:
        return repo.get_project(project_id)
    finally:
        repo.close()


def create_project(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Create one editable project from the current built-in default project."""

    values = dict(payload)
    replacements = list(values.pop("function_replacements", ()) or ())
    data_requirements = dict(values.pop("data_requirements", {}) or {})
    supplied_factors = values.pop("factors", None)
    recipe_parameters = dict(values.pop("recipe_parameters", {}) or {})
    validation_edits = list(values.pop("validation_parameter_edits", ()) or ())
    project_id = normalize_project_id(str(values["project_id"]))
    values["project_id"] = project_id
    operations = operations_store()
    if operations.get_recipe_draft(project_id) is not None:
        raise FileExistsError(f"project recipe already exists for {project_id!r}")
    repo = repository()
    try:
        default_project = repo.get_project(DEFAULT_PROJECT_ID)
        if default_project is None:
            raise RuntimeError("built-in default project is not initialized")
        strategy_source, default_factors = split_strategy_source(default_project["draft_source"])
        if supplied_factors is None:
            factor_sources = [item.source for item in default_factors]
        else:
            factor_sources = []
            for spec in supplied_factors:
                factor_spec = dict(spec)
                factor_template_id = str(factor_spec["template_id"])
                factor_template = get_factor_template(factor_template_id)
                factor_sources.append(
                    instantiate_factor_template(
                        factor_template_id,
                        factor_id=factor_spec.get("factor_id"),
                        label=factor_spec.get("label"),
                        parameter_values=factor_spec.get("parameter_values"),
                        body=factor_spec.get("body"),
                    )
                )
                for dataset, fields in factor_template.requirements.items():
                    current = data_requirements.setdefault(dataset, [])
                    current.extend(field for field in fields if field not in current)
        strategy_source, _ = merge_data_requirements(strategy_source, data_requirements)
        seen_entrypoints: set[str] = set()
        for replacement in replacements:
            entrypoint_id = str(replacement["entrypoint_id"]).strip()
            if entrypoint_id in seen_entrypoints:
                raise StrategySourceError(
                    f"default entrypoint {entrypoint_id!r} may be replaced only once",
                    phase="edit",
                )
            seen_entrypoints.add(entrypoint_id)
            strategy_source, _ = replace_registered_function(
                strategy_source,
                entrypoint_id=entrypoint_id,
                function_source=str(replacement["function_source"]),
            )

        validation_repo = ValidationRepository(repo.path)
        try:
            validation_source = str(validation_repo.get_or_create(DEFAULT_PROJECT_ID)["source"])
        finally:
            validation_repo.close()
        if validation_edits:
            validation_source, validation_inspection = update_validation_parameters(
                validation_source, validation_edits
            )
        else:
            validation_inspection = inspect_validation_source(validation_source)

        source_recipe = operations.get_recipe_draft(DEFAULT_PROJECT_ID)
        if source_recipe is None:
            bounds = _project_recipe_bounds()
            recipe_source = render_builtin_recipe(
                "rq.a_share_research", start=bounds["start"], end=bounds["end"]
            )
            selected_recipe_template_id = "rq.a_share_research"
        else:
            recipe_source = migrate_legacy_builtin_recipe(str(source_recipe["source"]))
            selected_recipe_template_id = source_recipe.get("selected_template_id")
            if recipe_source != source_recipe["source"]:
                source_recipe = operations.save_recipe_draft(
                    DEFAULT_PROJECT_ID,
                    recipe_source,
                    expected_source_sha256=source_recipe["source_sha256"],
                    selected_template_id=selected_recipe_template_id,
                )
        parameter_values = {
            key: (tuple(value) if key == "symbols" and value else value)
            for key, value in recipe_parameters.items()
            if value is not None
        }
        if parameter_values:
            recipe_source, _ = update_recipe_parameters(recipe_source, parameter_values)
        inspect_data_recipe_source(recipe_source)

        project = repo.create_project_from_units(
            **values,
            strategy_source=strategy_source,
            factor_sources=factor_sources,
            validation_source=validation_source,
            validation_inspection=validation_inspection.to_dict(),
        )
        try:
            operations.save_recipe_draft(
                project["id"],
                recipe_source,
                selected_template_id=selected_recipe_template_id,
            )
        except Exception:
            repo.delete_project(project["id"])
            operations.delete_recipe_draft(project["id"])
            raise
        return project
    finally:
        repo.close()


def migrate_project_default(
    project_id: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Explicitly advance default-owned strategy functions in a new revision."""

    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    if project.get("built_in"):
        raise PermissionError("built-in projects cannot be edited")
    if expected_source_sha256 and project["draft_source_sha256"] != expected_source_sha256:
        raise RuntimeError("draft changed since it was inspected")
    repo = repository()
    try:
        default_project = repo.get_project(DEFAULT_PROJECT_ID)
        if default_project is None:
            raise RuntimeError("built-in default project is not initialized")
        migrated_source, _ = migrate_default_strategy_components(
            project["draft_source"],
            default_project["draft_source"],
        )
        return repo.update_draft(
            project_id,
            migrated_source,
            expected_source_sha256=project["draft_source_sha256"],
        )
    finally:
        repo.close()


def update_draft(
    project_id: str,
    source: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.update_draft(
            project_id,
            source,
            expected_source_sha256=expected_source_sha256,
        )
    finally:
        repo.close()


def update_strategy_source(
    project_id: str,
    source: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.update_strategy_source(
            project_id,
            source,
            expected_source_sha256=expected_source_sha256,
        )
    finally:
        repo.close()


def add_project_factor_source(
    project_id: str,
    source: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    before = {
        item["id"] for item in project["inspection"]["entrypoints"] if item["kind"] == "factor"
    }
    repo = repository()
    try:
        updated = repo.add_factor_source(
            project_id,
            source,
            expected_source_sha256=expected_source_sha256,
        )
    finally:
        repo.close()
    factor = next(
        item
        for item in updated["inspection"]["entrypoints"]
        if item["kind"] == "factor" and item["id"] not in before
    )
    return {"project": updated, "factor": factor}


def update_metadata(project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.update_metadata(project_id, **dict(payload))
    finally:
        repo.close()


def save_revision(
    project_id: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    repo = repository()
    try:
        return repo.save_revision(
            project_id,
            expected_source_sha256=expected_source_sha256,
        )
    finally:
        repo.close()


def list_revisions(project_id: str) -> list[dict[str, Any]]:
    repo = repository()
    try:
        return repo.list_packages(project_id)
    finally:
        repo.close()


def get_revision(project_id: str, revision: int) -> dict[str, Any] | None:
    repo = repository()
    try:
        return repo.get_package(project_id, revision)
    finally:
        repo.close()


def delete_project(project_id: str) -> bool:
    project_id = normalize_project_id(project_id)
    repo = repository()
    try:
        deleted = repo.delete_project(project_id)
        if deleted:
            operations_store().delete_recipe_draft(project_id)
        return deleted
    finally:
        repo.close()


def validate_source(source: str) -> dict[str, Any]:
    return inspect_strategy_source(source).to_dict()


def factor_template_catalog() -> dict[str, Any]:
    return {"templates": [item.to_dict() for item in list_factor_templates()]}


def add_project_factor_template(
    project_id: str,
    template_id: str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    if expected_source_sha256 and expected_source_sha256 != project["draft_source_sha256"]:
        raise RuntimeError("draft changed since it was inspected")
    existing_factor_ids = {
        item.id
        for item in inspect_strategy_source(project["draft_source"]).entrypoints
        if item.kind == "factor"
    }
    updated, inspection = install_factor_template(project["draft_source"], template_id=template_id)
    updated_project = update_draft(
        project_id,
        updated,
        expected_source_sha256=project["draft_source_sha256"],
    )
    inspection_payload = inspection.to_dict()
    installed_factor = next(
        item
        for item in inspection_payload["entrypoints"]
        if item["kind"] == "factor" and item["id"] not in existing_factor_ids
    )
    return {
        "project": updated_project,
        "inspection": inspection_payload,
        "factor": installed_factor,
        "template": get_factor_template(template_id).to_dict(),
    }


def get_entrypoint_source(project_id: str, entrypoint_id: str) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    entrypoint = next(
        (item for item in project["inspection"]["entrypoints"] if item["id"] == entrypoint_id),
        None,
    )
    if entrypoint is None:
        raise KeyError(entrypoint_id)
    if entrypoint["kind"] == "factor":
        repo = repository()
        try:
            source = repo.get_factor_source(project_id, entrypoint_id)["source"]
        finally:
            repo.close()
    else:
        source = registered_function_source(project["strategy_source"], entrypoint_id=entrypoint_id)
    return {
        "project_id": project_id,
        "entrypoint_id": entrypoint_id,
        "source_sha256": project["draft_source_sha256"],
        "source": source,
    }


def _apply_structured_edit(source: str, payload: Mapping[str, Any]) -> tuple[str, SourceInspection]:
    operation = str(payload.get("operation") or "")
    if operation == "parameter":
        return update_parameter_default(
            source,
            entrypoint_id=str(payload["entrypoint_id"]),
            parameter=str(payload["parameter"]),
            value=payload.get("value"),
        )
    if operation == "schedule":
        return update_signal_schedule(
            source,
            signal_id=str(payload["entrypoint_id"]),
            frequency=str(payload["frequency"]),
            selector=str(payload.get("selector") or "every"),
            at=str(payload["at"]),
        )
    if operation == "factor_blend":
        return update_signal_factor_blend(
            source,
            signal_id=str(payload["entrypoint_id"]),
            factor_weights=dict(payload["factor_weights"]),
            normalization=str(payload["normalization"]),
        )
    if operation == "replace_function":
        return replace_registered_function(
            source,
            entrypoint_id=str(payload["entrypoint_id"]),
            function_source=str(payload["function_source"]),
        )
    if operation == "delete_function":
        return delete_registered_function(
            source,
            entrypoint_id=str(payload["entrypoint_id"]),
        )
    raise ValueError("unsupported structured edit operation")


def preview_structured_edits(project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    expected = payload.get("expected_source_sha256")
    if expected and expected != project["draft_source_sha256"]:
        raise RuntimeError("draft changed since it was inspected")
    updated = project["draft_source"]
    inspection = inspect_strategy_source(updated)
    for edit in payload.get("edits") or ():
        updated, inspection = _apply_structured_edit(updated, edit)
    strategy_source, _ = split_strategy_source(updated)
    return {
        "project_id": project_id,
        "base_source_sha256": project["draft_source_sha256"],
        "source": strategy_source,
        "source_sha256": inspection.source_sha256,
        "inspection": inspection.to_dict(),
    }


def structured_edit(project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    expected = payload.get("expected_source_sha256")
    if expected and expected != project["draft_source_sha256"]:
        raise RuntimeError("draft changed since it was inspected")
    operation = str(payload.get("operation") or "")
    entrypoint_id = str(payload.get("entrypoint_id") or "")
    entrypoint = next(
        (item for item in project["inspection"]["entrypoints"] if item["id"] == entrypoint_id),
        None,
    )
    if operation in {"replace_function", "delete_function"} and entrypoint is None:
        raise KeyError(entrypoint_id)
    if operation == "replace_function" and entrypoint["kind"] == "factor":
        repo = repository()
        try:
            updated_project = repo.replace_factor_source(
                project_id,
                entrypoint_id,
                str(payload["function_source"]),
                expected_source_sha256=project["draft_source_sha256"],
            )
        finally:
            repo.close()
        return {
            "project": updated_project,
            "inspection": updated_project["inspection"],
        }
    if operation == "delete_function" and entrypoint["kind"] == "factor":
        repo = repository()
        try:
            updated_project = repo.delete_factor_source(
                project_id,
                entrypoint_id,
                expected_source_sha256=project["draft_source_sha256"],
            )
        finally:
            repo.close()
        return {
            "project": updated_project,
            "inspection": updated_project["inspection"],
        }

    source = project["draft_source"]
    if operation == "batch":
        updated = source
        inspection = inspect_strategy_source(source)
        for edit in payload.get("edits") or ():
            updated, inspection = _apply_structured_edit(updated, edit)
    else:
        updated, inspection = _apply_structured_edit(source, payload)
    project = update_draft(
        project_id,
        updated,
        expected_source_sha256=project["draft_source_sha256"],
    )
    return {
        "project": project,
        "inspection": inspection.to_dict(),
    }


def insertion(project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    expected = payload.get("expected_source_sha256")
    if expected and expected != project["draft_source_sha256"]:
        raise RuntimeError("draft changed since it was inspected")
    kind = str(payload.get("kind") or "source")
    if kind == "field":
        snippet = factor_field_snippet(str(payload["value"]))
    elif kind == "factor":
        snippet = factor_dependency_snippet(
            str(payload["value"]), **dict(payload.get("parameters") or {})
        )
    elif kind == "source":
        snippet = str(payload["value"])
    else:
        raise ValueError("insertion kind must be field, factor, or source")
    updated, inspection = insert_source(
        project["draft_source"],
        cursor=int(payload["cursor"]),
        snippet=snippet,
    )
    if inspection is None:
        return {
            "project_id": project_id,
            "source": updated,
            "valid": False,
            "persisted": False,
            "snippet": snippet,
        }
    updated_project = update_draft(
        project_id,
        updated,
        expected_source_sha256=project["draft_source_sha256"],
    )
    return {
        "project": updated_project,
        "inspection": inspection.to_dict(),
        "valid": True,
        "persisted": True,
        "snippet": snippet,
    }


def preview_project(
    project_id: str,
    *,
    operation: str,
    profile: str,
    as_of_date: str | None,
    revision: int | None,
) -> dict[str, Any]:
    if profile != "runtime":
        raise ValueError("strategy previews use the runtime data profile")
    _, profile_end = _profile_range(profile)
    decision_date = as_of_date or profile_end
    if pd.Timestamp(decision_date) > pd.Timestamp(profile_end):
        decision_date = profile_end
    repo = repository()
    try:
        result = preview_strategy(
            repo,
            project_id,
            _engine(profile),
            decision_date,
            operation=operation,
            revision=revision,
        )
        return {**result, "profile": profile}
    finally:
        repo.close()


def factor_snapshot(
    project_id: str,
    factor_id: str,
    *,
    profile: str,
    as_of_date: str,
    revision: int | None,
    parameters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if profile != "runtime":
        raise ValueError("factor evaluations use the runtime data profile")
    repo = repository()
    try:
        return evaluate_factor_snapshot(
            repo,
            project_id,
            factor_id,
            _engine(profile),
            as_of_date,
            revision=revision,
            parameters=parameters,
        )
    finally:
        repo.close()


def factor_history(
    project_id: str,
    factor_id: str,
    *,
    profile: str,
    start_date: str,
    end_date: str,
    revision: int | None,
    parameters: Mapping[str, Any] | None,
    frequency: str,
) -> dict[str, Any]:
    if profile != "runtime":
        raise ValueError("factor evaluations use the runtime data profile")
    repo = repository()
    try:
        return evaluate_factor_history(
            repo,
            project_id,
            factor_id,
            _engine(profile),
            start_date,
            end_date,
            revision=revision,
            parameters=parameters,
            frequency=frequency,
        )
    finally:
        repo.close()


def run_project_backtest(
    project_id: str,
    start_date: str,
    end_date: str,
    profile: str,
    revision: int | None = None,
    validation_revision: int | None = None,
    backtest_id: str | None = None,
) -> dict[str, Any]:
    if profile not in {"demo", "runtime"}:
        raise ValueError("profile must be demo or runtime")
    repo = repository()
    try:
        validation_repo = ValidationRepository(repo.path)
        try:
            validation_package = validation_repo.get_package(project_id, validation_revision)
        finally:
            validation_repo.close()
        package = repo.get_package(project_id, revision)
        if package is None:
            raise KeyError(f"{project_id}@{revision}")
        pinned_revision = int(package["revision"])
        runtime_datasets = _runtime_datasets_for_backtest(package)
        engine = _engine(profile)
        with ExitStack() as data_locks:
            if profile == "runtime":
                runtime_store = RuntimeStore()
                for dataset in runtime_datasets:
                    data_locks.enter_context(
                        runtime_store.dataset_read_lock(dataset, timeout_seconds=30.0)
                    )
            provenance = build_research_provenance(
                profile,
                strategy_python=package["source"],
                runtime_datasets=runtime_datasets,
            )
            run = run_strategy_backtest(
                repo,
                project_id,
                start_date,
                end_date,
                engine,
                revision=pinned_revision,
                execution_data_policy="strict" if profile == "runtime" else "illustrative",
            )
            returns = run.returns
            weights = run.weights
            configured_benchmark = run.project["settings"].get("benchmark_symbols")
            symbols = list(configured_benchmark or run.benchmark_symbols)
            benchmark = (
                equal_weight_benchmark(
                    engine,
                    symbols,
                    start_date,
                    end_date,
                    frequency="daily",
                    execution_price="next_open",
                ).reindex(returns.index)
                if symbols
                else pd.Series(0.0, index=returns.index, name="benchmark")
            )
            try:
                attribution_factors = engine.get_factors(
                    [*FACTOR_NAMES, "rf"],
                    start_date,
                    end_date,
                    freq="1M",
                    strict=False,
                    use_cache=False,
                )
            except MissingDataError:
                attribution_factors = pd.DataFrame()
            validation_output = execute_validation(
                validation_package["source"],
                returns=returns,
                benchmark_returns=benchmark,
                weights=weights,
                factor_returns=attribution_factors,
                executions=run.executions,
                settings=dict(run.project["settings"]),
                diagnostics=_compact_run_diagnostics(run.diagnostics),
            )
    finally:
        repo.close()
    metrics = dict(validation_output["performance"])
    attribution = dict(validation_output["alpha_beta"])
    research_assessment = validation_output.get("research_quality")
    research_valid = research_assessment["passed"] if research_assessment is not None else None
    research_invalid_reasons = (
        list(research_assessment["reasons"])
        if research_assessment is not None else ["RESEARCH_QUALITY_NOT_EVALUATED"]
    )
    run_warnings = list(dict.fromkeys([
        *run.diagnostics.get("warnings", []),
        *(research_assessment or {}).get("warnings", []),
    ]))
    frozen_strategy_manifest = [
        {
            **dict(item),
            "parameters": dict(
                (run.package.get("parameters") or {}).get(str(item.get("id"))) or {}
            ),
        }
        for item in run.package["manifest"]
    ]
    provenance["strategy_source_package"] = run.diagnostics["strategy_source_package"]
    provenance["validation_source_package"] = {
        "revision": validation_package["revision"],
        "source_sha256": validation_package["source_sha256"],
    }
    provenance["benchmark"] = {
        "kind": "equal_weight_universe",
        "symbols": symbols,
        "source": "project_settings" if configured_benchmark else "prepared_instrument_master",
        "frequency": "daily",
        "execution_price": "next_open",
    }
    store = ResultStore()
    try:
        store.ensure_backtest_subject(
            run.project["id"],
            f"sqlite:strategy_projects/{run.project['id']}",
            run.project["description"],
        )
        backtest_id = store.save_backtest(
            returns,
            metrics,
            strategy_id=run.project["id"],
            benchmark=benchmark,
            weights=weights,
            start_date=start_date,
            end_date=end_date,
            tags=[f"profile:{profile}", "strategy:sdk-v1"],
            provenance=provenance,
            executions=run.executions,
            events=[
                *list(run.diagnostics.get("events") or ()),
                *[
                    {"kind": "delisting_settlement", **item}
                    for item in run.diagnostics.get("delisting_settlements") or ()
                ],
            ],
            strategy_source=run.package["source"],
            settings=dict(run.project["settings"]),
            attribution=attribution,
            strategy_project_id=run.project["id"],
            strategy_revision=run.package["revision"],
            strategy_source_sha256=run.package["source_sha256"],
            strategy_manifest=frozen_strategy_manifest,
            validation_source=validation_package["source"],
            validation_revision=validation_package["revision"],
            validation_source_sha256=validation_package["source_sha256"],
            validation_output=validation_output,
            run_diagnostics=_compact_run_diagnostics(run.diagnostics),
            backtest_id=backtest_id,
        )
    finally:
        store.close()
    return {
        "id": backtest_id,
        "project_id": run.project["id"],
        "strategy_id": run.project["id"],
        "strategy_type": "sdk_v1",
        "revision": run.package["revision"],
        "start_date": start_date,
        "end_date": end_date,
        "profile": profile,
        "source_sha256": run.package["source_sha256"],
        "validation_revision": validation_package["revision"],
        "validation_source_sha256": validation_package["source_sha256"],
        "metrics": metrics,
        "returns": [
            {"date": str(date)[:10], "value": float(value)} for date, value in returns.items()
        ],
        "weights_count": int((weights.abs() > 1e-12).sum().sum()) if not weights.empty else 0,
        "counts": {
            "return_rows": len(returns),
            "weight_rows": int((weights.abs() > 1e-12).sum().sum()) if not weights.empty else 0,
            "executions": len(run.executions),
            "events": len(run.diagnostics.get("events") or ())
            + len(run.diagnostics.get("delisting_settlements") or ()),
            "delisting_settlements": len(run.diagnostics.get("delisting_settlements") or ()),
        },
        **dict(run.diagnostics.get("execution_summary") or {}),
        "warnings": run_warnings,
        "execution_reliable": run.diagnostics["execution_reliable"],
        "execution_invalid_reasons": list(run.diagnostics["execution_invalid_reasons"]),
        "research_valid": research_valid,
        "research_invalid_reasons": research_invalid_reasons,
        "research_assessment": research_assessment,
        "execution_fidelity": dict(run.diagnostics.get("execution_fidelity") or {}),
        "execution": run.diagnostics,
        "strategy_manifest": frozen_strategy_manifest,
        "attribution": attribution,
        "validation_output": validation_output,
        "provenance": provenance,
    }


def _compact_run_diagnostics(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    """Persist summary diagnostics without duplicating paged daily events."""

    return {
        key: diagnostics.get(key)
        for key in (
            "sdk_version",
            "project_id",
            "revision",
            "periods",
            "warnings",
            "execution_data_policy",
            "execution_reliable",
            "execution_invalid_reasons",
            "execution_data_exclusions",
            "execution_data_fill",
            "execution_summary",
            "execution_fidelity",
            "signal_evidence",
        )
        if key in diagnostics
    } | {"delisting_settlement_count": len(diagnostics.get("delisting_settlements") or ())}


def _runtime_datasets_for_backtest(package: Mapping[str, Any]) -> tuple[str, ...]:
    requirements = package.get("data_requirements") or {}
    datasets = {
        "rq.instruments",
        "rq.bars",
        "rq.paused",
        "runtime.factor_returns",
    }
    bar_fields = set(requirements.get("bars") or ())
    has_execution_data_fill = any(
        item.get("kind") == "execution_data_fill" for item in package.get("manifest") or ()
    )
    if "is_st" in bar_fields or has_execution_data_fill:
        datasets.add("rq.is_st")
    if requirements.get("daily_factors"):
        datasets.add("rq.daily_factors")
    if requirements.get("index_components"):
        datasets.add("rq.index_components")
    if requirements.get("fundamentals"):
        datasets.add("canonical.fundamentals")
    return tuple(sorted(datasets))


__all__ = [
    "add_project_factor_source",
    "add_project_factor_template",
    "create_project",
    "delete_project",
    "factor_history",
    "factor_snapshot",
    "factor_template_catalog",
    "get_entrypoint_source",
    "preview_structured_edits",
    "get_project",
    "get_revision",
    "insertion",
    "list_projects",
    "migrate_project_default",
    "list_revisions",
    "preview_project",
    "run_project_backtest",
    "save_revision",
    "structured_edit",
    "update_draft",
    "update_strategy_source",
    "update_metadata",
    "validate_source",
]
