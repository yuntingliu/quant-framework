"""Inactive service integration against real, isolated protocol-1 SQLite stores."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import pickle
import shutil
import subprocess
import threading
import traceback
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, TypeAdapter
from pydantic_core import PydanticSerializationError

from alphalab.dataio import rq_profiles
from alphalab.utils import configuration_store as storage
from alphalab.utils.configuration_store import ConfigurationStore, ConfigurationStoreError
from apps.api.services import configuration_profile_service as integration
from apps.api.services import data_source_profile_service as data_sources
from apps.api.services import model_provider_service as models
from apps.api.services import search_provider_service as search
from apps.api.services.configuration_profile_service import ConfigurationProfileServices

CANARIES = {
    "models": "fixture-integration-model-key-1701",
    "search": "fixture-integration-brave-key-1702",
    "rqdata": "fixture-integration-rq-password-1703",
    "inactive": "fixture-integration-inactive-key-1704",
    "environment_model": "fixture-integration-environment-model-1705",
    "environment_search": "fixture-integration-environment-brave-1706",
    "environment_rq": "fixture-integration-environment-rq-1707",
    "replacement": "fixture-integration-replacement-1708",
    "exception": "fixture-integration-exception-1709",
}
ENVIRONMENT = {
    "CONEXUS_MODEL_BASE_URL": "https://openrouter.ai/api/v1",
    "CONEXUS_MODEL_ID": "provider/environment-model",
    "CONEXUS_MODEL_API_KEY": CANARIES["environment_model"],
    "BRAVE_SEARCH_API_KEY": CANARIES["environment_search"],
    "RQ_USER": "environment-user",
    "RQ_HOST": "environment.example:16011",
    "RQ_PASSWORD": CANARIES["environment_rq"],
}


def profile(service, **changes):
    defaults = {
        "models": {
            "id": "model",
            "name": "Model",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "provider/model-name",
            "api_key": CANARIES["models"],
        },
        "search": {
            "id": "search",
            "name": "Search",
            "provider": "brave",
            "model_profile_id": "model",
            "brave_api_key": CANARIES["search"],
        },
        "rqdata": {
            "id": "rq",
            "name": "RQ",
            "user": "research-user",
            "host": "rq.example:16011",
            "password": CANARIES["rqdata"],
        },
    }
    return defaults[service] | changes


@pytest.fixture
def setup_store(tmp_path):
    root = tmp_path / "installation"
    documents = {
        key: {
            "version": 2 if key == "search" else 1,
            "active_id": None,
            "profiles": [],
            "private_unrelated": {"keep": key},
        }
        for key in ("models", "search", "rqdata")
    }
    selections = {key: {"kind": "environment"} for key in documents}
    store = ConfigurationStore.initialize(root, documents=documents, selections=selections)
    return root, store, ConfigurationProfileServices(store, environment=ENVIRONMENT)


def populate(services):
    return services.save_profiles({key: profile(key) for key in ("models", "search", "rqdata")})


def safe_error(action, code="INVALID_CONFIGURATION", exception_type=ConfigurationStoreError):
    with pytest.raises(exception_type) as caught:
        action()
    error = caught.value
    if exception_type is ConfigurationStoreError:
        assert error.code == code
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not getattr(error, "__notes__", [])
    visible = (
        str(error) + repr(error) + repr(error.args) + "".join(traceback.format_exception(error))
    )
    visible += repr(pickle.dumps(error))
    for secret in CANARIES.values():
        assert secret not in visible
    return error


def test_independent_writes_preserve_untouched_documents_and_intents(setup_store):
    _, store, services = setup_store
    for service in ("models", "search", "rqdata"):
        before = store.read_snapshot()
        public = getattr(services, service).save_profile(profile(service), activate=True)
        after = store.read_snapshot()
        assert after.revision != before.revision
        assert public["revision"] == after.revision
        assert public["selection"] == {"kind": "profile", "id": profile(service)["id"]}
        assert public["source"] == "saved"
        for other in {"models", "search", "rqdata"} - {service}:
            assert after.documents[other] == before.documents[other]
            assert after.selections[other] == before.selections[other]
        assert after.documents[service]["private_unrelated"] == {"keep": service}
        assert not any(secret in json.dumps(public) for secret in CANARIES.values())


def test_batch_edit_publishes_one_complete_generation(setup_store):
    _, store, services = setup_store
    before = store.read_snapshot()
    result = populate(services)
    assert result.protocol_version == 1
    assert result.installation_id == before.installation_id
    assert all(result.selections[key]["kind"] == "profile" for key in result.selections)
    with closing(__import__("sqlite3").connect(store._path)) as connection:
        assert connection.execute("SELECT count(*) FROM generations").fetchone()[0] == 2
    detached = result.documents.extract_private()
    detached["models"]["profiles"].clear()
    assert result.documents.extract_private()["models"]["profiles"]
    assert not before.documents["models"]["profiles"]


def test_saved_snapshot_validation_and_transactions_never_resolve_live_state(
    setup_store, monkeypatch
):
    _, store, services = setup_store

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "Live JSON/environment resolution is forbidden in SQLite service operations."
        )

    for module in (models, search, rq_profiles):
        for name in ("read_settings", "public_settings", "active_profile", "settings_path"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(search, "_environment_profile", forbidden)
    monkeypatch.setattr(rq_profiles, "load_env_files", forbidden)
    for module in (models, search, rq_profiles):
        original = module.prepare_profile

        def checked(*args, _original=original, **kwargs):
            assert not getattr(storage._LOCAL, "active", False), (
                "Preparation must precede the transaction."
            )
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, "prepare_profile", checked)
    result = populate(services)
    assert result.revision == store.read_snapshot().revision
    assert result.public_settings("search")["configured"]
    assert services.rqdata.active_config().extract_private()["RQ_PASSWORD"] == CANARIES["rqdata"]


@pytest.mark.parametrize(
    "service,secret,clear",
    [
        ("models", "api_key", "clear_api_key"),
        ("search", "brave_api_key", "clear_brave_api_key"),
        ("rqdata", "password", "clear_password"),
    ],
)
def test_retention_replacement_explicit_clear_and_restart(setup_store, service, secret, clear):
    root, store, services = setup_store
    populate(services)
    bound = getattr(services, service)
    bound.save_profile(profile(service, **{secret: "", "name": "Renamed"}), activate=True)
    assert bound.active_profile().extract_private()[secret] == CANARIES[service]
    bound.save_profile(profile(service, **{secret: CANARIES["replacement"]}), activate=False)
    assert bound.active_profile().extract_private()[secret] == CANARIES["replacement"]
    cleared = bound.save_profile(profile(service, **{secret: ""}), activate=True, **{clear: True})
    assert not cleared["configured"]
    assert store.read_snapshot().documents[service]["profiles"][0][secret] == ""
    restarted = ConfigurationProfileServices(
        ConfigurationStore.open_existing(root), environment=ENVIRONMENT
    )
    assert getattr(restarted, service).active_profile() is None
    safe_error(lambda: getattr(restarted, service).active_config())
    safe_error(
        lambda: getattr(restarted, service).resolve_test_profile(profile(service, **{secret: ""}))
    )
    assert store.read_snapshot().selections[service]["kind"] == "profile"


@pytest.mark.parametrize(
    "service,changes",
    [
        ("models", {"api_key": "", "base_url": "https://other.example/v1"}),
        ("rqdata", {"password": "", "user": "other-user"}),
        ("rqdata", {"password": "", "host": "other.example:16011"}),
    ],
)
def test_changed_destinations_do_not_reuse_credentials(setup_store, service, changes):
    _, store, services = setup_store
    populate(services)
    before = store.read_snapshot()
    safe_error(
        lambda: getattr(services, service).save_profile(profile(service, **changes), activate=True)
    )
    assert store.read_snapshot().revision == before.revision


@pytest.mark.parametrize(
    "service,secret", [("models", "api_key"), ("search", "brave_api_key"), ("rqdata", "password")]
)
def test_new_profile_does_not_borrow_other_profiles_or_environment(setup_store, service, secret):
    _, store, services = setup_store
    populate(services)
    before = store.read_snapshot().revision
    safe_error(
        lambda: getattr(services, service).save_profile(
            profile(service, id="unrelated", **{secret: ""}), activate=True
        )
    )
    assert store.read_snapshot().revision == before


@pytest.mark.parametrize(
    "service,secret,clear",
    [
        ("models", "api_key", "clear_api_key"),
        ("search", "brave_api_key", "clear_brave_api_key"),
        ("rqdata", "password", "clear_password"),
    ],
)
def test_clear_plus_replacement_and_clearing_new_profile_are_rejected(
    setup_store, service, secret, clear
):
    _, store, services = setup_store
    populate(services)
    before = store.read_snapshot().revision
    safe_error(
        lambda: getattr(services, service).save_profile(
            profile(service), activate=True, **{clear: True}
        )
    )
    safe_error(
        lambda: getattr(services, service).save_profile(
            profile(service, id="new", **{secret: ""}), activate=True, **{clear: True}
        )
    )
    assert store.read_snapshot().revision == before


@pytest.mark.parametrize("kind", ["profile", "environment", "none"])
@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
def test_selection_intents_survive_restart_without_being_inferred(setup_store, service, kind):
    root, store, services = setup_store
    populate(services)
    choice = {"kind": kind, **({"id": profile(service)["id"]} if kind == "profile" else {})}
    getattr(services, service).select(choice)
    restarted = ConfigurationProfileServices(
        ConfigurationStore.open_existing(root), environment=ENVIRONMENT
    )
    public = getattr(restarted, service).public_settings()
    assert public["selection"] == choice
    assert store.read_snapshot().selections[service] == choice
    if kind == "none":
        assert public["source"] == "none" and not public["configured"]
        assert getattr(restarted, service).active_profile() is None
        safe_error(lambda: getattr(restarted, service).active_config())
    elif kind == "environment":
        assert public["source"] == "environment" and public["configured"]
        secret = {"models": "api_key", "search": "brave_api_key", "rqdata": "password"}[service]
        assert (
            getattr(restarted, service).active_profile().extract_private()[secret]
            == CANARIES[
                "environment_" + {"models": "model", "search": "search", "rqdata": "rq"}[service]
            ]
        )
    else:
        assert public["source"] == "saved" and public["configured"]


def test_explicit_environment_inputs_are_not_saved_or_read_from_os(setup_store, monkeypatch):
    _, store, services = setup_store
    for key, value in ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    no_environment = ConfigurationProfileServices(store)
    assert no_environment.models.active_profile() is None
    assert no_environment.rqdata.active_profile() is None
    assert (
        services.models.active_profile().extract_private()["api_key"]
        == CANARIES["environment_model"]
    )
    assert not any(
        secret in json.dumps(store.read_snapshot().documents) for secret in CANARIES.values()
    )


def test_legacy_first_search_key_capture_is_independent_and_explicit(setup_store):
    _, store, services = setup_store
    resolved = services.search.resolve_settings(
        {"provider": "brave", "brave_api_key": "", "name": None}
    )
    assert resolved.extract_private()["brave_api_key"] == CANARIES["environment_search"]
    assert not store.read_snapshot().documents["search"]["profiles"]
    result = services.search.save_settings({"provider": "brave", "brave_api_key": "", "name": None})
    assert result["active_id"] == "default"
    assert not store.read_snapshot().documents["models"]["profiles"]
    services.search.save_settings(
        {"provider": "brave", "brave_api_key": "", "clear_brave_api_key": True}
    )
    safe_error(lambda: services.search.resolve_settings({"provider": "brave", "brave_api_key": ""}))
    assert services.search.active_profile() is None


def test_explicit_none_prevents_first_capture_even_for_legacy_search_payload(setup_store):
    _, store, services = setup_store
    services.search.select({"kind": "none"})
    before = store.read_snapshot().revision
    safe_error(lambda: services.search.save_settings({"provider": "brave", "brave_api_key": ""}))
    assert store.read_snapshot().revision == before


def test_openrouter_uses_saved_reference_from_the_prospective_same_generation(setup_store):
    _, store, services = setup_store
    populate(services)
    services.models.save_profile(
        profile("models", id="inactive", api_key=CANARIES["inactive"]), activate=False
    )
    services.models.select({"kind": "none"})
    services.search.save_profile(
        profile("search", provider="openrouter", model_profile_id="inactive", brave_api_key=""),
        activate=True,
    )
    assert services.search.active_profile().extract_private()["model_profile_id"] == "inactive"
    safe_error(
        lambda: services.search.save_profile(
            profile("search", provider="openrouter", model_profile_id=None), activate=True
        )
    )
    before = store.read_snapshot().revision
    safe_error(
        lambda: services.search.save_profile(
            profile("search", provider="openrouter", model_profile_id="missing"), activate=True
        )
    )
    assert store.read_snapshot().revision == before
    batch = services.save_profiles(
        {
            "models": profile("models", id="fresh"),
            "search": profile("search", provider="openrouter", model_profile_id="fresh"),
        }
    )
    assert batch.public_settings("search")["configured"]
    assert batch.active_profile("search").extract_private()["model_profile_id"] == "fresh"


def test_follow_active_openrouter_respects_model_selection_and_does_not_borrow(setup_store):
    _, _, services = setup_store
    populate(services)
    services.search.save_profile(
        profile("search", provider="openrouter", model_profile_id=None), activate=True
    )
    services.models.save_profile(profile("models", api_key=""), activate=True, clear_api_key=True)
    assert services.search.active_profile() is None
    assert not services.search.public_settings()["configured"]
    services.models.select({"kind": "environment"})
    assert services.search.active_profile() is not None
    services.models.select({"kind": "none"})
    assert services.search.active_profile() is None


def test_search_off_and_unselected_are_distinct_and_do_not_resolve_credentials(setup_store):
    _, _, services = setup_store
    services.search.save_profile(
        profile("search", provider="off", brave_api_key="", model_profile_id=None), activate=True
    )
    assert services.search.public_settings()["configured"]
    assert services.search.public_settings()["selection"]["kind"] == "profile"
    services.search.select({"kind": "none"})
    assert not services.search.public_settings()["configured"]


def test_legacy_search_read_is_in_memory_and_unrelated_writes_preserve_v1(setup_store):
    root, store, _ = setup_store

    def legacy(current):
        docs, choices = current.documents, current.selections
        docs["search"] = {"version": 1, "provider": "brave", "brave_api_key": CANARIES["search"]}
        choices["search"] = {"kind": "profile", "id": "default"}
        return docs, choices

    store.update(legacy)
    services = ConfigurationProfileServices(ConfigurationStore.open_existing(root))
    before = store.read_snapshot()
    assert services.search.public_settings()["active_id"] == "default"
    assert store.read_snapshot().revision == before.revision
    services.models.save_profile(profile("models"), activate=True)
    assert store.read_snapshot().documents["search"] == before.documents["search"]
    services.search.save_settings({"provider": "off"})
    assert store.read_snapshot().documents["search"]["version"] == 2


@pytest.mark.parametrize(
    "service,changes",
    [
        ("models", {"base_url": "https://user:password@unsafe.example"}),
        ("models", {"id": "../invalid"}),
        ("models", {"model": ""}),
        ("search", {"provider": "invalid"}),
        ("search", {"name": ""}),
        ("rqdata", {"host": "https://unsafe.example/path"}),
        ("rqdata", {"user": ""}),
        ("rqdata", {"id": "INVALID"}),
    ],
)
def test_invalid_updates_leave_all_documents_and_revision_unchanged(setup_store, service, changes):
    _, store, services = setup_store
    populate(services)
    before = store.read_snapshot()
    safe_error(
        lambda: getattr(services, service).save_profile(profile(service, **changes), activate=True)
    )
    assert store.read_snapshot().revision == before.revision
    assert store.read_snapshot().documents == before.documents


@pytest.mark.parametrize(
    "choice",
    [
        {"kind": "none", "id": "model"},
        {"kind": "profile", "id": "missing"},
        {"kind": "environment", "token": CANARIES["exception"]},
        {"kind": "invalid"},
    ],
)
def test_invalid_selection_does_not_publish(setup_store, choice):
    _, store, services = setup_store
    before = store.read_snapshot()
    safe_error(lambda: services.models.select(choice))
    assert store.read_snapshot().revision == before.revision


def test_failed_batch_and_stale_edits_do_not_publish_partial_profiles(setup_store):
    _, store, services = setup_store
    before = store.read_snapshot()
    safe_error(
        lambda: services.save_profiles(
            {"models": profile("models"), "rqdata": profile("rqdata", host="invalid/path")}
        )
    )
    assert store.read_snapshot().documents == before.documents
    services.models.save_profile(profile("models"), activate=True)
    committed = store.read_snapshot()
    safe_error(
        lambda: services.rqdata.save_profile(
            profile("rqdata"), activate=True, expected_revision=before.revision
        ),
        "REVISION_CONFLICT",
    )
    safe_error(
        lambda: services.models.select({"kind": "none"}, expected_revision=before.revision),
        "REVISION_CONFLICT",
    )
    assert store.read_snapshot().revision == committed.revision


def test_concurrent_threads_return_conflicts_without_lost_service_state(setup_store):
    _, store, services = setup_store
    populate(services)

    def edit(index):
        for _ in range(20):
            try:
                return services.rqdata.save_profile(
                    profile("rqdata", id=f"rq-{index}"), activate=False
                )
            except ConfigurationStoreError as error:
                if error.code != "REVISION_CONFLICT":
                    raise
        raise AssertionError("Explicit caller retry budget exhausted.")

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(edit, range(6)))
    assert len({item["revision"] for item in results}) == 6
    assert len(store.read_snapshot().documents["rqdata"]["profiles"]) == 7
    assert services.models.active_profile().extract_private()["api_key"] == CANARIES["models"]


def _process_edit(root, service, revision, barrier, queue):
    try:
        services = ConfigurationProfileServices(ConfigurationStore.open_existing(root))
        barrier.wait(timeout=20)
        result = getattr(services, service).save_profile(
            profile(service), activate=True, expected_revision=revision
        )
        queue.put((service, "ok", result["revision"]))
    except ConfigurationStoreError as error:
        queue.put((service, error.code, None))


def test_concurrent_processes_detect_stale_writers_and_preserve_previous_generation(setup_store):
    root, store, _ = setup_store
    revision = store.read_snapshot().revision
    context = multiprocessing.get_context("spawn")
    barrier, queue = context.Barrier(2), context.Queue()
    children = [
        context.Process(target=_process_edit, args=(str(root), key, revision, barrier, queue))
        for key in ("models", "rqdata")
    ]
    try:
        for child in children:
            child.start()
        results = [queue.get(timeout=35) for _ in children]
        for child in children:
            child.join(timeout=10)
            assert child.exitcode == 0
        assert sorted(row[1] for row in results) == ["REVISION_CONFLICT", "ok"]
        failed = next(row[0] for row in results if row[1] == "REVISION_CONFLICT")
        retry = ConfigurationProfileServices(ConfigurationStore.open_existing(root))
        getattr(retry, failed).save_profile(profile(failed), activate=True)
        assert all(store.read_snapshot().documents[key]["profiles"] for key in ("models", "rqdata"))
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(timeout=10)
        queue.close()
        queue.join_thread()


def test_atomic_batches_and_pinned_views_never_mix_generation_documents(setup_store):
    _, _, services = setup_store
    original = populate(services)
    ready = threading.Event()

    def writer():
        ready.set()
        for index in range(6):
            services.save_profiles(
                {
                    key: profile(key, name=f"Generation {index}")
                    for key in ("models", "search", "rqdata")
                }
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(writer)
        assert ready.wait(5)
        seen = []
        while not pending.done():
            snapshot = services.read_snapshot()
            seen.append(snapshot.revision)
            names = [
                snapshot.documents.extract_private()[key]["profiles"][0]["name"]
                for key in ("models", "search", "rqdata")
            ]
            if snapshot.revision != original.revision:
                assert len(set(names)) == 1
        pending.result()
    assert seen
    assert original.documents.extract_private()["models"]["profiles"][0]["name"] == "Model"


def test_each_job_reads_fresh_rq_selection_without_touching_connections(setup_store):
    _, _, services = setup_store
    populate(services)
    first_job = services.rqdata.active_config()
    services.rqdata.save_profile(profile("rqdata", password=CANARIES["replacement"]), activate=True)
    second_job = services.rqdata.active_config()
    assert first_job.extract_private()["RQ_PASSWORD"] == CANARIES["rqdata"]
    assert second_job.extract_private()["RQ_PASSWORD"] == CANARIES["replacement"]
    services.rqdata.select({"kind": "none"})
    safe_error(lambda: services.rqdata.active_config())


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_open_and_existing_service_handles_fail_closed_without_recreation(setup_store, damage):
    root, store, services = setup_store
    if damage == "missing":
        store._path.unlink()
    else:
        store._path.write_bytes(b"invalid synthetic database")
    before = store._path.read_bytes() if store._path.exists() else None
    code = "STORE_MISSING" if damage == "missing" else "STORE_CORRUPT"
    safe_error(lambda: ConfigurationStore.open_existing(root), code)
    safe_error(lambda: services.models.public_settings(), code)
    safe_error(lambda: services.models.save_profile(profile("models"), activate=True), code)
    assert (store._path.read_bytes() if store._path.exists() else None) == before


def test_two_installations_and_supplied_environment_remain_independent(setup_store, tmp_path):
    _, first, services = setup_store
    source = first.read_snapshot()
    second = ConfigurationStore.initialize(
        tmp_path / "second", documents=source.documents, selections=source.selections
    )
    other = ConfigurationProfileServices(second)
    populate(services)
    assert services.read_snapshot().installation_id != other.read_snapshot().installation_id
    assert other.models.active_profile() is None
    assert not second.read_snapshot().documents["models"]["profiles"]


@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
def test_private_values_and_errors_do_not_expose_credential_canaries(setup_store, service):
    _, _, services = setup_store
    populate(services)
    values = [
        services,
        getattr(services, service),
        services.read_snapshot(),
        services.read_snapshot().documents,
        getattr(services, service).read_settings(),
        getattr(services, service).active_profile(),
        getattr(services, service).resolve_test_profile(profile(service)),
    ]
    for value in values:
        for secret in CANARIES.values():
            assert secret not in repr(value) + str(value)
        with pytest.raises(TypeError):
            pickle.dumps(value)
        with pytest.raises(TypeError):
            json.dumps(value)
    public = json.dumps(getattr(services, service).public_settings())
    assert not any(secret in public for secret in CANARIES.values())


@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
def test_cross_service_and_inactive_credentials_in_metadata_reject_before_publication(
    setup_store, service
):
    _, store, services = setup_store
    populate(services)
    services.models.save_profile(
        profile("models", id="inactive", api_key=CANARIES["inactive"]), activate=False
    )
    before = store.read_snapshot().revision
    for secret in CANARIES.values():
        if secret in {CANARIES["replacement"], CANARIES["exception"]}:
            continue  # Not yet supplied as credentials, so cannot be identified.
        safe_error(
            lambda: getattr(services, service).save_profile(
                profile(service, name=secret), activate=True
            )
        )
        assert store.read_snapshot().revision == before


@pytest.mark.parametrize(
    "kind", [ValueError, RuntimeError, asyncio.CancelledError, KeyboardInterrupt, SystemExit]
)
def test_credential_bearing_validator_failures_are_fresh_and_sanitized(
    setup_store, monkeypatch, kind
):
    _, store, services = setup_store
    before = store.read_snapshot().revision

    def fail(*args, **kwargs):
        error = kind(CANARIES["exception"])
        error.add_note(CANARIES["exception"])
        raise error from RuntimeError(CANARIES["exception"])

    monkeypatch.setattr(models, "prepare_profile", fail)
    safe_error(
        lambda: services.models.save_profile(profile("models"), activate=True),
        exception_type=kind
        if kind in {asyncio.CancelledError, KeyboardInterrupt, SystemExit}
        else ConfigurationStoreError,
    )
    assert store.read_snapshot().revision == before


def test_production_services_remain_json_backed_even_when_sqlite_exists(setup_store, monkeypatch):
    root, store, services = setup_store
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(root))
    for key in ENVIRONMENT:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(rq_profiles, "load_env_files", lambda: None)
    populate(services)
    assert models.read_settings()["profiles"] == []
    assert search.read_settings()["profiles"] == []
    assert rq_profiles.read_settings()["profiles"] == []
    before = store.read_snapshot().revision
    models.save_profile(profile("models", name="JSON only"), activate=True)
    search.save_profile(profile("search", provider="off"), activate=True)
    rq_profiles.save_profile(profile("rqdata"), activate=True)
    assert models.public_settings()["profiles"][0]["name"] == "JSON only"
    assert services.models.public_settings()["profiles"][0]["name"] == "Model"
    assert store.read_snapshot().revision == before


def test_service_edits_are_readable_by_existing_node_reader(setup_store):
    root, store, services = setup_store
    populate(services)
    services.search.save_profile(profile("search", provider="openrouter"), activate=True)
    services.models.select({"kind": "none"})
    node = os.getenv("ALPHALAB_TEST_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node 22.18+ unavailable; set ALPHALAB_TEST_NODE.")
    runtime = subprocess.run(
        [node, "-p", "process.versions.node"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    version = runtime.stdout.strip().split(".")
    if version[0] != "22" or int(version[1]) < 18:
        pytest.skip("This interoperability check requires Node 22.18+ within Node 22.")
    module = (
        Path(__file__).resolve().parents[2] / "integrations/conexus/configuration-store.mjs"
    ).as_uri()
    script = f"""
      import {{ConfigurationStoreReader}} from {json.dumps(module)};
      const reader=new ConfigurationStoreReader(process.argv[1]);
      try {{const s=await reader.readSnapshot();
        console.log(JSON.stringify({{revision:s.revision,installation:s.installation_id,
          modelSelection:s.selections.models,searchSelection:s.selections.search,
          reference:s.documents.search.profiles[0].model_profile_id,
          modelKeyOK:s.documents.models.profiles[0].api_key==={json.dumps(CANARIES["models"])},
          rqdataExposed:'rqdata' in s.documents}}));
      }} finally {{await reader.close()}}
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, str(root)],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    value = json.loads(result.stdout)
    assert value["revision"] == store.read_snapshot().revision
    assert value["installation"] == store.read_snapshot().installation_id
    assert value["modelSelection"] == {"kind": "none"}
    assert value["searchSelection"] == {"kind": "profile", "id": "search"}
    assert value["reference"] == "model" and value["modelKeyOK"] and not value["rqdataExposed"]
    assert not any(secret in result.stdout + result.stderr for secret in CANARIES.values())


def test_saved_profile_named_environment_is_not_environment_fallback(setup_store):
    _, store, services = setup_store
    services.models.save_profile(profile("models", id="environment"), activate=True)
    assert services.models.active_profile().extract_private()["api_key"] == CANARIES["models"]
    assert store.read_snapshot().selections["models"] == {"kind": "profile", "id": "environment"}
    services.models.save_profile(
        profile("models", id="environment", api_key=""), activate=True, clear_api_key=True
    )
    assert services.models.active_profile() is None


def test_first_search_clear_does_not_store_environment_key(setup_store):
    _, store, services = setup_store
    result = services.search.save_settings({"provider": "brave", "clear_brave_api_key": True})
    assert not result["configured"] and not result["has_brave_api_key"]
    assert not store.read_snapshot().documents["search"]["profiles"][0]["brave_api_key"]
    safe_error(lambda: services.search.resolve_settings({"provider": "brave"}))


def test_fault_before_commit_rolls_back_service_edits(setup_store, monkeypatch):
    _, store, services = setup_store
    before = store.read_snapshot()
    original = storage._Session.execute
    reached = []

    def interrupted(session, sql, parameters=()):
        if sql == "COMMIT":
            reached.append(True)
            raise RuntimeError(CANARIES["exception"])
        return original(session, sql, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(storage._Session, "execute", interrupted)
        safe_error(lambda: populate(services), "STORE_UNAVAILABLE")
    assert reached == [True]
    after = store.read_snapshot()
    assert after.revision == before.revision and after.documents == before.documents


@pytest.mark.parametrize(
    "service,key,value",
    [
        ("models", "api_key", None),
        ("rqdata", "password", None),
        ("models", "api_key", 1),
        ("search", "brave_api_key", {"password": CANARIES["exception"]}),
        ("rqdata", "host", [CANARIES["exception"]]),
    ],
)
def test_malformed_private_inputs_reject_without_coercion(setup_store, service, key, value):
    _, store, services = setup_store
    before = store.read_snapshot().revision
    safe_error(
        lambda: getattr(services, service).save_profile(
            profile(service, **{key: value}), activate=True
        )
    )
    assert store.read_snapshot().revision == before


def test_environment_dictionary_is_detached_and_never_interpreted_as_runtime_switch(setup_store):
    _, store, _ = setup_store
    supplied = ENVIRONMENT.copy()
    services = ConfigurationProfileServices(store, environment=supplied)
    supplied["BRAVE_SEARCH_API_KEY"] = CANARIES["replacement"]
    assert (
        services.search.active_profile().extract_private()["brave_api_key"]
        == CANARIES["environment_search"]
    )
    assert not store.read_snapshot().documents["search"]["profiles"]


@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
def test_profile_limit_rejects_new_profiles_but_allows_existing_edits(setup_store, service):
    _, store, services = setup_store
    populate(services)
    for index in range(19):
        getattr(services, service).save_profile(
            profile(service, id=f"saved-{index}"), activate=False
        )
    before = store.read_snapshot().revision
    safe_error(
        lambda: getattr(services, service).save_profile(
            profile(service, id="overflow"), activate=False
        )
    )
    assert store.read_snapshot().revision == before
    getattr(services, service).save_profile(
        profile(service, name="Renamed existing"), activate=False
    )
    assert len(store.read_snapshot().documents[service]["profiles"]) == 20


def test_publication_rechecks_preflight_revision_inside_the_transaction(setup_store, monkeypatch):
    _, store, services = setup_store
    original = ConfigurationStore.update
    intervened = []

    def raced(handle, mutate, *, expected_revision=None):
        if not intervened:

            def other_edit(current):
                documents, choices = current.documents, current.selections
                documents["rqdata"]["profiles"] = [
                    rq_profiles.validate_profile_input(profile("rqdata"))
                ]
                documents["rqdata"]["active_id"] = "rq"
                choices["rqdata"] = {"kind": "profile", "id": "rq"}
                return documents, choices

            intervened.append(original(handle, other_edit).revision)
        return original(handle, mutate, expected_revision=expected_revision)

    monkeypatch.setattr(ConfigurationStore, "update", raced)
    safe_error(
        lambda: services.models.save_profile(profile("models"), activate=True), "REVISION_CONFLICT"
    )
    after = store.read_snapshot()
    assert after.revision == intervened[0]
    assert not after.documents["models"]["profiles"]
    assert after.documents["rqdata"]["profiles"][0]["password"] == CANARIES["rqdata"]


def test_unknown_private_native_fields_are_preserved_but_never_public(setup_store):
    _, store, services = setup_store
    populate(services)

    def private_extension(current):
        documents = current.documents
        for document in documents.values():
            document["runtime_private"] = CANARIES["exception"]
            document["profiles"][0]["unrelated_token"] = CANARIES["exception"]
        return documents, current.selections

    store.update(private_extension)
    for service in ("models", "search", "rqdata"):
        public = getattr(services, service).public_settings()
        assert CANARIES["exception"] not in json.dumps(public)
        assert not any(key.startswith("runtime_") for key in public)
    services.rqdata.save_profile(profile("rqdata", name="Changed"), activate=False)
    assert (
        store.read_snapshot().documents["models"]["profiles"][0]["unrelated_token"]
        == CANARIES["exception"]
    )


@pytest.mark.parametrize(
    "provider,expected", [("  BRAVE  ", "brave"), ("invalid", "brave"), ("off", "off")]
)
def test_explicit_search_environment_normalization_matches_legacy(setup_store, provider, expected):
    _, store, _ = setup_store
    services = ConfigurationProfileServices(
        store, environment={**ENVIRONMENT, "ALPHALAB_WEB_SEARCH_PROVIDER": provider}
    )
    assert services.search.public_settings()["provider"] == expected


def test_factory_requires_explicit_open_store_and_does_not_create_paths(tmp_path):
    path = tmp_path / "not-created"
    safe_error(lambda: ConfigurationProfileServices(path))
    assert not path.exists()


def test_sqlite_service_edits_never_overwrite_existing_json_files(setup_store, monkeypatch):
    root, _, services = setup_store
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(root))
    monkeypatch.setattr(rq_profiles, "load_env_files", lambda: None)
    models.save_profile(profile("models"), activate=True)
    search.save_profile(profile("search", provider="off"), activate=True)
    rq_profiles.save_profile(profile("rqdata"), activate=True)
    paths = [module.settings_path() for module in (models, search, rq_profiles)]
    previous = {path: path.read_bytes() for path in paths}
    populate(services)
    services.models.select({"kind": "none"})
    services.rqdata.save_profile(profile("rqdata", password=""), activate=True, clear_password=True)
    services.search.save_settings(
        {"provider": "brave", "brave_api_key": "", "clear_brave_api_key": True}
    )
    assert {path: path.read_bytes() for path in paths} == previous


def _encoded_credential(value, depth):
    # Break the literal credential while leaving identities/names under their
    # length limits. Encoding an entire URL would test an invalid authority.
    if not depth:
        return value
    value = f"%{ord(value[0]):02X}" + value[1:]
    for _ in range(depth - 1):
        value = value.replace("%", "%25")
    return value


@pytest.mark.parametrize("source", ["models", "search", "rqdata"])
@pytest.mark.parametrize("depth", [0, 1, 3, 5])
@pytest.mark.parametrize(
    "target,field",
    [
        ("models", "base_url"),
        ("models", "model"),
        ("models", "name"),
        ("search", "name"),
        ("rqdata", "name"),
        ("rqdata", "user"),
    ],
)
def test_known_credentials_in_public_metadata_reject_before_commit(
    setup_store, source, depth, target, field
):
    root, store, services = setup_store
    populate(services)
    before = store.read_snapshot()
    value = _encoded_credential(CANARIES[source], depth)
    if field == "base_url":
        value = "https://gateway.example/v1/;API_Key=" + value
        assert urlsplit(value).hostname == "gateway.example"
    payload = profile(target, **{field: value})
    safe_error(lambda: getattr(services, target).save_profile(payload, activate=True))
    after = store.read_snapshot()
    assert after.revision == before.revision and after.documents == before.documents
    restarted = ConfigurationProfileServices(
        ConfigurationStore.open_existing(root), environment=ENVIRONMENT
    )
    for service in ("models", "search", "rqdata"):
        public = getattr(restarted, service).public_settings()
        assert not any(secret in json.dumps(public) for secret in CANARIES.values())


@pytest.mark.parametrize("source", ["models", "search", "rqdata"])
@pytest.mark.parametrize("depth", [0, 2])
def test_native_normalized_credentials_are_rejected_on_reads_and_restart(
    setup_store, source, depth
):
    root, store, services = setup_store
    populate(services)

    def unsafe_native(current):
        documents = current.documents
        field = integration._SECRET[source]
        documents[source]["profiles"][0][field] = " \t" + CANARIES[source] + "\n "
        documents["models"]["profiles"][0]["name"] = _encoded_credential(CANARIES[source], depth)
        return documents, current.selections

    store.update(unsafe_native)
    before = store.read_snapshot()
    restarted = ConfigurationProfileServices(
        ConfigurationStore.open_existing(root), environment=ENVIRONMENT
    )
    for owner in (services, restarted):
        safe_error(owner.read_snapshot)
        for service in ("models", "search", "rqdata"):
            safe_error(getattr(owner, service).public_settings)
            safe_error(getattr(owner, service).read_settings)
    assert store.read_snapshot().revision == before.revision
    assert store.read_snapshot().documents == before.documents


@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
@pytest.mark.parametrize("action", ["replace", "clear"])
@pytest.mark.parametrize("depth", [0, 2])
def test_old_credentials_in_metadata_reject_replacement_and_deletion(
    setup_store, service, action, depth
):
    _, store, services = setup_store
    populate(services)

    def padded(current):
        documents = current.documents
        documents[service]["profiles"][0][integration._SECRET[service]] = (
            "  " + CANARIES[service] + "  "
        )
        return documents, current.selections

    store.update(padded)
    before = store.read_snapshot()
    secret = "" if action == "clear" else CANARIES["replacement"] + "-" + service
    payload = profile(
        service,
        name=_encoded_credential(CANARIES[service], depth),
        **{integration._SECRET[service]: secret},
    )
    flag = {"models": "clear_api_key", "search": "clear_brave_api_key", "rqdata": "clear_password"}[
        service
    ]
    safe_error(
        lambda: getattr(services, service).save_profile(
            payload, activate=True, **{flag: action == "clear"}
        )
    )
    assert store.read_snapshot().revision == before.revision
    assert store.read_snapshot().documents == before.documents


@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
@pytest.mark.parametrize("depth", [0, 2])
def test_replacement_credential_in_metadata_is_rejected(setup_store, service, depth):
    _, store, services = setup_store
    populate(services)
    before = store.read_snapshot()
    secret = CANARIES["replacement"] + "-" + service
    payload = profile(
        service,
        name=_encoded_credential(secret, depth),
        **{integration._SECRET[service]: " " + secret + " "},
    )
    safe_error(lambda: getattr(services, service).save_profile(payload, activate=True))
    assert store.read_snapshot().revision == before.revision
    assert store.read_snapshot().documents == before.documents


@pytest.mark.parametrize("source", ["models", "search", "rqdata"])
def test_known_credentials_in_rq_host_are_rejected(setup_store, source):
    _, store, services = setup_store
    populate(services)
    before = store.read_snapshot().revision
    # A lexical hostname with no scheme/userinfo; failure must be the scanner.
    safe_error(
        lambda: services.rqdata.save_profile(
            profile("rqdata", host=CANARIES[source] + ".example"), activate=True
        )
    )
    assert store.read_snapshot().revision == before


@pytest.mark.parametrize("source", ["models", "search", "rqdata"])
def test_raw_and_normalized_environment_credentials_protect_read_views(setup_store, source):
    _, store, services = setup_store
    populate(services)
    key = {
        "models": "CONEXUS_MODEL_API_KEY",
        "search": "BRAVE_SEARCH_API_KEY",
        "rqdata": "RQ_PASSWORD",
    }[source]
    secret = CANARIES[
        "environment_" + {"models": "model", "search": "search", "rqdata": "rq"}[source]
    ]
    environment = {**ENVIRONMENT, key: "  " + secret + "  "}
    assert integration._credential_values({}, environment).issuperset({environment[key], secret})

    def unsafe_native(current):
        documents = current.documents
        documents["models"]["profiles"][0]["name"] = _encoded_credential(secret, 2)
        return documents, current.selections

    store.update(unsafe_native)
    before = store.read_snapshot().revision
    supplied = ConfigurationProfileServices(store, environment=environment)
    safe_error(supplied.read_snapshot)
    safe_error(supplied.models.public_settings)
    assert store.read_snapshot().revision == before


@pytest.mark.parametrize(
    "base_url",
    [
        "https://gateway.example/v1/safe%20path",
        "https://gateway.example/v1/percent%25",
        "http://127.0.0.1:8000/v1/percent%25",
        "https://gateway.example/v1/%2573afe",
    ],
)
def test_safe_encoded_endpoints_and_provider_model_ids_remain_valid(setup_store, base_url):
    _, _, services = setup_store
    populate(services)
    public = services.models.save_profile(
        profile("models", base_url=base_url, model="provider/model-name"), activate=True
    )
    assert public["configured"]
    assert public["profiles"][0]["base_url"] == base_url
    assert public["profiles"][0]["model"] == "provider/model-name"


def test_metadata_decoding_limit_is_separate_from_secret_detection(setup_store):
    _, store, services = setup_store
    populate(services)
    before = store.read_snapshot().revision
    # Safe content still has unresolved encoding beyond the Phase 4A bound.
    value = "https://gateway.example/v1/" + _encoded_credential("safe", 6)
    safe_error(
        lambda: services.models.save_profile(profile("models", base_url=value), activate=True)
    )
    assert store.read_snapshot().revision == before


def test_empty_credentials_do_not_reject_legitimate_metadata(setup_store):
    _, store, _ = setup_store

    def empty(current):
        documents = current.documents
        for service in ("models", "search", "rqdata"):
            documents[service]["profiles"] = [
                profile(service, **{integration._SECRET[service]: " \t "})
            ]
            documents[service]["active_id"] = profile(service)["id"]
        documents["models"]["profiles"][0]["base_url"] = "http://localhost:8000/v1"
        documents["search"]["profiles"][0]["provider"] = "off"
        return documents, {
            service: {"kind": "profile", "id": profile(service)["id"]} for service in documents
        }

    store.update(empty)
    services = ConfigurationProfileServices(store)
    assert not integration._credential_values(store.read_snapshot().documents)
    assert services.models.public_settings()["configured"]
    assert services.search.public_settings()["configured"]
    assert not services.rqdata.public_settings()["configured"]


class _PrivateResponse(BaseModel):
    payload: Any


@dataclass
class _DataclassEnvelope:
    payload: Any


def _assert_serialization_refused(action):
    with pytest.raises((TypeError, ValueError, PydanticSerializationError)) as caught:
        action()
    pending, seen = [caught.value], set()
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        visible = (
            str(error) + repr(error) + repr(error.args) + "".join(traceback.format_exception(error))
        )
        visible += repr(getattr(error, "__notes__", []))
        assert not any(secret in visible for secret in CANARIES.values())
        pending.extend(item for item in (error.__cause__, error.__context__) if item is not None)


@pytest.mark.parametrize(
    "kind",
    [
        "snapshot",
        "services",
        "bound_service",
        "saved_settings",
        "documents",
        "model_profile",
        "search_profile",
        "rq_profile",
        "rq_connection",
        "rq_test",
        "model_test",
        "search_test",
    ],
)
def test_opaque_private_values_reject_framework_serialization(setup_store, kind):
    _, _, services = setup_store
    populate(services)
    value = {
        "snapshot": services.read_snapshot,
        "services": lambda: services,
        "bound_service": lambda: services.rqdata,
        "saved_settings": services.rqdata.read_settings,
        "documents": lambda: services.read_snapshot().documents,
        "model_profile": services.models.active_profile,
        "search_profile": services.search.active_profile,
        "rq_profile": services.rqdata.active_profile,
        "rq_connection": services.rqdata.active_config,
        "rq_test": lambda: services.rqdata.resolve_test_profile(profile("rqdata")),
        "model_test": lambda: services.models.resolve_test_profile(profile("models")),
        "search_test": lambda: services.search.resolve_settings({"provider": "brave"}),
    }[kind]()
    assert not isinstance(value, Mapping)
    assert not any(
        hasattr(value, name) for name in ("__iter__", "__getitem__", "__dict__", "keys", "items")
    )
    response = _PrivateResponse(payload=value)
    assert response.model_dump()["payload"] is value  # Python mode retains an opaque object.
    for action in (
        lambda: json.dumps(value),
        lambda: jsonable_encoder(value),
        lambda: TypeAdapter(Any).dump_json(value),
        response.model_dump_json,
        lambda: response.model_dump(mode="json"),
        lambda: asdict(value),
        lambda: asdict(_DataclassEnvelope(value)),
        lambda: pickle.dumps(value),
        lambda: dict(value),
        lambda: list(value),
    ):
        _assert_serialization_refused(action)
    if isinstance(value, integration.PrivateConfiguration):
        original = value.extract_private()
        changed = value.extract_private()
        changed.clear()
        assert value.extract_private() == original
    assert not any(secret in repr(value) + str(value) for secret in CANARIES.values())


def test_fastapi_encoder_cannot_extract_private_documents(setup_store):
    _, _, services = setup_store
    populate(services)
    _assert_serialization_refused(lambda: jsonable_encoder(services.read_snapshot().documents))
    _assert_serialization_refused(lambda: jsonable_encoder(services.rqdata.active_config()))


def test_rqdata_test_resolution_has_exact_connection_shape(setup_store, monkeypatch):
    _, store, services = setup_store
    populate(services)
    native = store.read_snapshot().documents
    request = profile("rqdata", password="")
    with monkeypatch.context() as patched:
        patched.setattr(rq_profiles, "read_settings", lambda: native["rqdata"])
        expected = data_sources.resolve_test_profile(request)
    before = store.read_snapshot().revision
    result = services.rqdata.resolve_test_profile(request)
    assert result.extract_private() == expected
    assert set(expected) == {"RQ_USER", "RQ_PASSWORD", "RQ_HOST"}
    assert services.rqdata.active_config().extract_private() == expected
    assert store.read_snapshot().revision == before


@pytest.mark.parametrize("provider", ["off", "brave", "openrouter"])
def test_search_test_resolution_has_exact_provider_shape(setup_store, monkeypatch, provider):
    _, store, services = setup_store
    populate(services)
    native = store.read_snapshot().documents
    request = {"provider": provider, "model_profile_id": "model", "brave_api_key": ""}
    with monkeypatch.context() as patched:
        patched.setattr(search, "read_settings", lambda: native["search"])
        patched.setattr(models, "read_settings", lambda: native["models"])
        expected = search.resolve_settings(request)
    before = store.read_snapshot().revision
    result = services.search.resolve_settings(request).extract_private()
    assert result == expected
    expected_fields = {
        "off": {"provider"},
        "brave": {"provider", "brave_api_key"},
        "openrouter": {"provider", "model_profile_id"},
    }
    assert set(result) == expected_fields[provider]
    if provider != "brave":
        assert "brave_api_key" not in result
    assert store.read_snapshot().revision == before


def test_model_test_resolution_keeps_exact_legacy_profile_shape(setup_store, monkeypatch):
    _, store, services = setup_store
    populate(services)
    native = store.read_snapshot().documents
    request = profile("models", api_key="")
    with monkeypatch.context() as patched:
        patched.setattr(models, "read_settings", lambda: native["models"])
        expected = models.resolve_test_profile(request)
    assert services.models.resolve_test_profile(request).extract_private() == expected


@pytest.mark.parametrize("mode", ["inactive-reference", "follow-active", "follow-environment"])
def test_openrouter_test_resolution_preserves_reference_semantics_without_brave_keys(
    setup_store, mode
):
    _, store, services = setup_store
    populate(services)
    reference = None
    if mode == "inactive-reference":
        services.models.save_profile(
            profile("models", id="inactive", api_key=CANARIES["inactive"]), activate=False
        )
        services.models.select({"kind": "none"})
        reference = "inactive"
    elif mode == "follow-environment":
        services.models.select({"kind": "environment"})
    before = store.read_snapshot().revision
    result = services.search.resolve_settings(
        {"provider": "openrouter", "model_profile_id": reference}
    ).extract_private()
    assert result == {"provider": "openrouter", "model_profile_id": reference}
    assert not any(secret in json.dumps(result) for secret in CANARIES.values())
    assert store.read_snapshot().revision == before


@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
def test_credential_collection_retains_original_and_runtime_spellings(service):
    original = " \t" + CANARIES[service] + "\n "
    document = {
        "version": 2 if service == "search" else 1,
        "active_id": None,
        "profiles": [profile(service, **{integration._SECRET[service]: original})],
    }
    assert integration._credential_values({service: document}) == {original, CANARIES[service]}
    empty = {**document, "profiles": [profile(service, **{integration._SECRET[service]: "\t "})]}
    assert not integration._credential_values({service: empty})


def _short_credential_store(tmp_path, monkeypatch, *, installation_collision=False):
    documents = {
        service: {
            "version": 2 if service == "search" else 1,
            "active_id": profile(service)["id"],
            "profiles": [profile(service)],
        }
        for service in ("models", "search", "rqdata")
    }
    selections = {
        service: {"kind": "profile", "id": profile(service)["id"]} for service in documents
    }
    installation = "ab" + "0" * 30 if installation_collision else "1" * 32
    with monkeypatch.context() as controlled:
        identifiers = iter([UUID(hex=installation), UUID(hex="2" * 32)])
        controlled.setattr(storage, "uuid4", lambda: next(identifiers))
        store = ConfigurationStore.initialize(
            tmp_path / "short-key", documents=documents, selections=selections
        )
    services = ConfigurationProfileServices(store)
    with monkeypatch.context() as controlled:
        controlled.setattr(storage, "uuid4", lambda: UUID(hex="3" * 32))
        services.models.save_profile(profile("models", api_key="ab"), activate=True)
    return store, services


def _assert_public_metadata_has_no_short_key(public):
    # Independent storage UUIDs intentionally share a substring with this short
    # synthetic key. Only those two validated envelope values may coincide.
    configurable = {
        key: value for key, value in public.items() if key not in {"revision", "installation_id"}
    }
    assert "ab" not in json.dumps(configurable)
    for row in public["profiles"]:
        assert not {"api_key", "brave_api_key", "password"}.intersection(row)
    assert not any(secret in json.dumps(public) for secret in CANARIES.values())


@pytest.mark.parametrize("collision", ["revision", "installation_id", "both"])
def test_committed_save_and_restart_accept_opaque_identifier_collisions(
    tmp_path, monkeypatch, collision
):
    store, services = _short_credential_store(
        tmp_path, monkeypatch, installation_collision=collision != "revision"
    )
    before = store.read_snapshot()
    revision = "ab" + "4" * 30 if collision != "installation_id" else "4" * 32
    public, error = None, None
    with monkeypatch.context() as controlled:
        controlled.setattr(storage, "uuid4", lambda: UUID(hex=revision))
        try:
            public = services.models.save_profile(
                profile("models", api_key="", name="Edited"), activate=True
            )
        except ConfigurationStoreError as failure:
            error = failure.code
    after = store.read_snapshot()
    assert after.revision == revision and after.revision != before.revision
    assert after.documents["models"]["profiles"][0]["name"] == "Edited"
    assert after.documents["models"]["profiles"][0]["api_key"] == "ab"
    assert after.selections == before.selections
    for service in ("search", "rqdata"):
        assert after.documents[service] == before.documents[service]
    assert error is None, "Save must not report validation failure after the edit was committed."
    assert public["revision"] == revision
    assert public["installation_id"] == after.installation_id
    _assert_public_metadata_has_no_short_key(public)
    restarted = ConfigurationProfileServices(ConfigurationStore.open_existing(store._root))
    for owner in (services, restarted):
        for service in ("models", "search", "rqdata"):
            result = getattr(owner, service).public_settings()
            assert result["revision"] == revision
            assert result["installation_id"] == after.installation_id
            _assert_public_metadata_has_no_short_key(result)


@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
@pytest.mark.parametrize("field,encoded", [("id", False), ("name", False), ("name", True)])
def test_storage_identifier_exemption_never_applies_to_profile_metadata(
    tmp_path, monkeypatch, service, field, encoded
):
    store, services = _short_credential_store(tmp_path, monkeypatch)
    before = store.read_snapshot()
    # Both literals are otherwise valid opaque-looking profile IDs/names. The
    # encoded variant is a valid name, so it cannot fail profile-ID validation.
    value = ("%61b" if encoded else "ab") + "0" * 30
    payload = profile(service, **{field: value})
    if service == "models":
        payload["api_key"] = "ab"
    safe_error(lambda: getattr(services, service).save_profile(payload, activate=True))
    after = store.read_snapshot()
    assert after.revision == before.revision
    assert after.documents == before.documents and after.selections == before.selections


@pytest.mark.parametrize("field", ["revision", "installation_id"])
@pytest.mark.parametrize("invalid", [None, 7, "ab", "A" * 32, "ab" + "g" * 30])
def test_storage_identifier_exemption_requires_valid_opaque_identifiers(
    tmp_path, monkeypatch, field, invalid
):
    store, _ = _short_credential_store(tmp_path, monkeypatch)
    current = store.read_snapshot()
    # A forged internal view exercises the projection's defensive check; real
    # store reads already validate these identities before constructing a view.
    forged = storage.ConfigurationSnapshot(
        invalid if field == "installation_id" else current.installation_id,
        invalid if field == "revision" else current.revision,
        current._texts,
    )
    view = integration.ProfileSnapshot(forged, {})
    safe_error(lambda: view.public_settings("models"))
    after = store.read_snapshot()
    assert after.revision == current.revision
    assert after.documents == current.documents and after.selections == current.selections


@pytest.mark.parametrize("field", ["revision", "installation_id"])
def test_profile_requests_cannot_supply_exempt_storage_identifiers(tmp_path, monkeypatch, field):
    store, services = _short_credential_store(tmp_path, monkeypatch)
    before = store.read_snapshot()
    payload = profile("models", api_key="ab", **{field: "ab" + "0" * 30})
    safe_error(lambda: services.models.save_profile(payload, activate=True))
    after = store.read_snapshot()
    assert after.revision == before.revision
    assert after.documents == before.documents and after.selections == before.selections
