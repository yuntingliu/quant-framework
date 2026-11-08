"""Real-store, synthetic SDK tests for explicitly captured RQData jobs."""

from __future__ import annotations

import json
import pickle
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from alphalab.dataio import rq_profiles
from alphalab.dataio.providers.rq import RQDataConfig
from alphalab.dataio.sync import SyncRequest
from alphalab.utils.configuration_store import ConfigurationStore, ConfigurationStoreError
from apps.api.services.configuration_profile_service import ConfigurationProfileServices
from apps.api.services.configuration_runtime_service import RQJobConfiguration

PASSWORD = "runtime-python-rq-secret-5101"
ROTATED = "runtime-python-rq-secret-5102"
ENV_PASSWORD = "runtime-python-rq-environment-5103"


def rq_profile(**changes):
    return {
        "id": "rq",
        "name": "RQ",
        "user": "fixture-user",
        "host": "rq.example:16011",
        "password": PASSWORD,
        **changes,
    }


@pytest.fixture
def configured(tmp_path):
    documents = {
        key: {"version": 2 if key == "search" else 1, "active_id": None, "profiles": []}
        for key in ("models", "search", "rqdata")
    }
    choices = {key: {"kind": "none"} for key in documents}
    root = tmp_path / "configuration"
    store = ConfigurationStore.initialize(root, documents=documents, selections=choices)
    services = ConfigurationProfileServices(
        store,
        environment={
            "RQ_USER": "environment-user",
            "RQ_HOST": "environment.example:16011",
            "RQ_PASSWORD": ENV_PASSWORD,
        },
    )
    services.rqdata.save_profile(rq_profile(), activate=True)
    return root, store, services


class FakeSDK:
    def __init__(self, started=None, release=None, fail=False):
        self.connections = []
        self.started, self.release, self.fail = started, release, fail

    def init(self, user, password, host, *, lazy):
        if self.fail:
            raise RuntimeError(password)
        self.connections.append((user, password, host, lazy))

    def all_instruments(self, *, type, market):
        if self.started:
            self.started.set()
        if self.release:
            assert self.release.wait(10), "Test-owned SDK release was not signalled."
        return pd.DataFrame(
            {
                "order_book_id": ["000001.XSHE"],
                "symbol": ["Fixture"],
                "type": ["CS"],
                "listed_date": ["1991-01-01"],
                "de_listed_date": ["0000-00-00"],
            }
        )


def assert_private(value):
    text = repr(value) + str(value)
    assert not any(secret in text for secret in (PASSWORD, ROTATED, ENV_PASSWORD))


def test_job_captures_revision_credentials_and_is_lazy(configured):
    _, store, services = configured
    sdk = FakeSDK()
    first = RQJobConfiguration(services)
    client = first.client(module=sdk)
    assert not sdk.connections
    assert first.revision == store.read_snapshot().revision
    services.rqdata.save_profile(rq_profile(password=ROTATED, user="rotated-user"), activate=True)
    second = RQJobConfiguration(services)
    client.connect()
    second.client(module=sdk).connect()
    assert sdk.connections[0][:2] == ("fixture-user", PASSWORD)
    assert sdk.connections[1][:2] == ("rotated-user", ROTATED)
    assert first.revision != second.revision
    assert first.installation_id == second.installation_id
    for value in (first, second, client.config):
        assert_private(value)
    with pytest.raises(TypeError):
        json.dumps(first)
    with pytest.raises(TypeError):
        pickle.dumps(first)


@pytest.mark.parametrize("selection", ["none", "environment", "cleared"])
def test_job_selection_does_not_fall_back_or_resurrect_passwords(configured, selection):
    _, store, services = configured
    captured = RQJobConfiguration(services)
    if selection == "cleared":
        services.rqdata.save_profile(rq_profile(password=""), activate=True, clear_password=True)
    else:
        services.rqdata.select({"kind": selection})
    if selection == "environment":
        sdk = FakeSDK()
        RQJobConfiguration(services).client(module=sdk).connect()
        assert sdk.connections[0][1] == ENV_PASSWORD
    else:
        before = store.read_snapshot()
        with pytest.raises(ConfigurationStoreError):
            RQJobConfiguration(services)
        assert store.read_snapshot().revision == before.revision
    sdk = FakeSDK()
    captured.client(module=sdk).connect()
    assert sdk.connections[0][1] == PASSWORD  # An already captured job stays pinned.


def test_job_bindings_drive_existing_sync_service_without_persisting_credentials(
    configured, tmp_path, monkeypatch
):
    _, _, services = configured
    started, release = threading.Event(), threading.Event()
    sdk = FakeSDK(started, release)
    context = RQJobConfiguration(services)
    runner = context.sync_service(tmp_path / "research-runtime", module=sdk)
    request = SyncRequest(
        datasets=["instruments"], symbols=["000001.SZ"], start="2024-01-01", end="2024-01-03"
    )
    job_id = runner.operations.create_job(request.model_dump(mode="json"))

    def forbidden(*args, **kwargs):
        raise AssertionError("A bound SQLite job must not resolve JSON/environment settings.")

    monkeypatch.setattr(rq_profiles, "active_config", forbidden)
    monkeypatch.setattr(RQDataConfig, "from_active_settings", forbidden)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(runner.run, job_id)
        try:
            assert started.wait(10)
            services.rqdata.save_profile(rq_profile(password=ROTATED), activate=True)
        finally:
            release.set()
        result = pending.result(timeout=15)
    assert result["status"] == "succeeded"
    assert sdk.connections[0][1] == PASSWORD
    other_sdk = FakeSDK()
    next_job = RQJobConfiguration(services).sync_service(
        tmp_path / "next-runtime", module=other_sdk
    )
    second_id = next_job.operations.create_job(request.model_dump(mode="json"))
    assert next_job.run(second_id)["status"] == "succeeded"
    assert other_sdk.connections[0][1] == ROTATED
    visible = json.dumps(result) + json.dumps(next_job.operations.get_job(second_id))
    assert not any(secret in visible for secret in (PASSWORD, ROTATED, ENV_PASSWORD))


def test_sdk_errors_have_sanitized_chains_and_public_job_results(configured, tmp_path):
    _, _, services = configured
    context = RQJobConfiguration(services)
    client = context.client(module=FakeSDK(fail=True))
    with pytest.raises(Exception) as caught:
        client.connect()
    visible = "".join(traceback.format_exception(caught.value))
    assert PASSWORD not in visible
    cause = caught.value
    while cause is not None:
        assert_private(cause)
        cause = cause.__cause__ or cause.__context__
    runner = context.sync_service(tmp_path / "failed-runtime", module=FakeSDK(fail=True))
    job_id = runner.operations.create_job(
        SyncRequest(
            datasets=["instruments"], symbols=["000001.SZ"], start="2024-01-01", end="2024-01-03"
        ).model_dump(mode="json")
    )
    result = runner.run(job_id)
    assert result["status"] == "failed"
    assert PASSWORD not in json.dumps(result)


@pytest.mark.parametrize("missing", [True, False])
def test_job_capture_fails_closed_on_unavailable_stores(configured, missing):
    _, store, services = configured
    if missing:
        store._path.unlink()
    else:
        store._path.write_bytes(b"synthetic corrupt store")
    with pytest.raises(ConfigurationStoreError) as caught:
        RQJobConfiguration(services)
    assert caught.value.code == ("STORE_MISSING" if missing else "STORE_CORRUPT")
    assert_private(caught.value)
    if missing:
        assert not store._path.exists()


def test_jobs_in_two_installations_remain_independent(configured, tmp_path):
    _, store, first = configured
    original = store.read_snapshot()
    other = ConfigurationStore.initialize(
        tmp_path / "other", documents=original.documents, selections=original.selections
    )
    second = ConfigurationProfileServices(other)
    second.rqdata.save_profile(rq_profile(password=ROTATED), activate=True)
    a, b = RQJobConfiguration(first), RQJobConfiguration(second)
    assert a.installation_id != b.installation_id
    module_a, module_b = FakeSDK(), FakeSDK()
    a.client(module=module_a).connect()
    b.client(module=module_b).connect()
    assert module_a.connections[0][1] == PASSWORD
    assert module_b.connections[0][1] == ROTATED


def test_json_rqdata_contract_remains_default_when_sqlite_is_disabled(configured, monkeypatch):
    root, store, services = configured
    services.rqdata.select({"kind": "none"})
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(root))
    rq_profiles.save_profile(rq_profile(password=ROTATED), activate=True)
    config = RQDataConfig.from_active_settings()
    assert config.password == ROTATED
    assert store.read_snapshot().selections["rqdata"] == {"kind": "none"}
    with pytest.raises(ConfigurationStoreError):
        RQJobConfiguration(services)
