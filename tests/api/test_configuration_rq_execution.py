"""Real stores, spawn workers and synthetic process-global SDK state only."""

from __future__ import annotations

import hashlib
import io
import json
import multiprocessing
import os
import pickle
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from test_configuration_backend import KEYS, initialize

from alphalab.dataio import rq_profiles
from alphalab.dataio.providers.rq import RQDataClient
from alphalab.dataio.sync import SyncRequest
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_store import ConfigurationStoreError
from apps.api.configuration_app import create_configuration_app
from apps.api.services.configuration_data_service import ConfigurationDataServices
from apps.api.services.configuration_profile_service import ConfigurationProfileServices

PASSWORD = KEYS[2]
ROTATED = "synthetic-isolated-rotated-9101"
ENVIRONMENT = "synthetic-isolated-environment-9102"
_ACTIVE = None


class IsolatedSDK:
    """A second initialization in the same process is a contamination control."""

    def __init__(self, directory=None, *, hold=False, fail=False, crash=False):
        self.directory, self.hold, self.fail, self.crash = directory, hold, fail, crash

    def init(self, user, password, host, *, lazy):
        global _ACTIVE
        assert _ACTIVE is None, "Cross-job SDK process state was reused"
        assert "CONEXUS_PUBLICATION_WORKSPACE_TOKEN" not in os.environ
        assert "UNRELATED_PRIVATE_TOKEN" not in os.environ
        assert password in {PASSWORD, ROTATED, ENVIRONMENT}, (
            "Wrong installation or environment credential"
        )
        _ACTIVE = (user, password, host)
        self.expected = _ACTIVE
        if self.directory:
            directory = Path(self.directory)
            directory.mkdir(exist_ok=True)
            (directory / (str(os.getpid()) + ".json")).write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "rotated": password == ROTATED,
                        "ambient_cleared": "RQ_PASSWORD" not in os.environ,
                        "environment_selected": password == ENVIRONMENT,
                    }
                )
            )
        if self.crash:
            os._exit(27)  # Only a process created by this test's execution binding.
        if self.fail:
            print(password)
            os.write(2, password.encode())
            raise RuntimeError(password)
        deadline = time.monotonic() + 10
        while self.hold and not (Path(self.directory) / "release").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("Synthetic SDK release deadline")
            time.sleep(0.02)

    def all_instruments(self, *, type, market):
        assert _ACTIVE == self.expected
        return pd.DataFrame(
            {
                "order_book_id": ["000001.XSHE"],
                "symbol": ["Fixture"],
                "type": ["CS"],
                "listed_date": ["1991-01-01"],
                "de_listed_date": ["0000-00-00"],
            }
        )

    def get_latest_trading_date(self, *, market):
        assert _ACTIVE == self.expected
        return pd.Timestamp("2025-01-02")


class ReflectingSDK(IsolatedSDK):
    def get_latest_trading_date(self, *, market):
        print(self.expected[1])
        return self.expected[1]


class ReflectingInstrumentsSDK(IsolatedSDK):
    def __init__(self, *, dtype="object", unused=False):
        super().__init__()
        self.dtype, self.unused = dtype, unused

    def all_instruments(self, **kwargs):
        frame = super().all_instruments(**kwargs)
        frame["symbol"] = pd.Series(
            ["Fixture" if self.unused else self.expected[1]], dtype=self.dtype
        )
        if self.unused:
            frame["symbol"] = frame["symbol"].cat.add_categories([self.expected[1]])
        return frame


class SafeCategoricalInstrumentsSDK(IsolatedSDK):
    def all_instruments(self, **kwargs):
        frame = super().all_instruments(**kwargs)
        frame["symbol"] = frame["symbol"].astype("category").cat.add_categories(["Unused safe"])
        frame["numeric_fixture"] = 17.25
        return frame


def _legacy_frame_guard(frame, password):
    """Original predicate, used only inside an isolated negative-control child."""
    from alphalab.dataio.configuration_execution import reject_secret

    reject_secret([list(frame.columns), list(frame.index.names)], password)
    for column in frame.select_dtypes(include=["object", "string"]).columns:
        for value in frame[column]:
            reject_secret(value, password)
    if frame.index.dtype.kind == "O":
        for value in frame.index:
            reject_secret(value, password)


def _legacy_categorical_sdk(*, unused=False):
    import alphalab.dataio.configuration_execution as execution

    assert multiprocessing.current_process().name == "alphalab-captured-rq"
    execution.reject_frame = _legacy_frame_guard
    return ReflectingInstrumentsSDK(dtype="category", unused=unused)


def _publication_recipe(boundary):
    write = (
        'context.publish("rq.instruments", frame)'
        if boundary == "publish"
        else 'RuntimeStore(context._root).write("rq.instruments", frame)'
    )
    return f"""from alphalab.data_sdk.v1 import RQSyncRequest, data_recipe, rq
from alphalab.dataio.runtime import RuntimeStore
import pandas as pd
@data_recipe(id="fixture.reflected.frame")
def recipe(context):
    frame = rq.all_instruments(type="CS", market="cn")
    frame = frame.rename(columns={{"symbol": "name"}})
    frame["snapshot_date"] = pd.Timestamp("2025-01-02")
    frame["symbol"] = "000001.SZ"
    {write}
    return RQSyncRequest(
        template_id="rq.a_share_daily", datasets=("instruments",),
        start="2025-01-02", end="2025-01-02", symbols=("000001.SZ",),
    )
"""


_FRAME_CASES = [
    pytest.param("object", False, id="object"),
    pytest.param("string", False, id="string"),
    pytest.param("category", False, id="category"),
    pytest.param("category", True, id="unused-category"),
]


def fallback_sdk():
    """Negative control, only in a spawned test child: remove explicit acquirer."""
    from alphalab.dataio.sync import RQSyncService

    original = RQSyncService.__init__

    def missing_capture(self, *args, **kwargs):
        kwargs.pop("acquirer", None)
        original(self, *args, **kwargs)

    RQSyncService.__init__ = missing_capture
    return IsolatedSDK()


RECIPE = """from alphalab.data_sdk.v1 import data_recipe, rq
@data_recipe(id="fixture.isolated")
def recipe(context):
    if context.mode == "plan":
        context.expect("rq.instruments", "synthetic query")
        return
    context.output("calendar", str(rq.get_latest_trading_date(market="cn")))
"""


@pytest.fixture
def configured(tmp_path, monkeypatch):
    root = tmp_path / "installation"
    store = initialize(root)
    monkeypatch.setenv("RQ_PASSWORD", ENVIRONMENT)
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "local")
    monkeypatch.setenv("RQ_USER", "wrong-environment-user")
    monkeypatch.setenv("RQ_HOST", "wrong.example:16011")
    monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "synthetic-unrelated-agent-token")
    monkeypatch.setenv("UNRELATED_PRIVATE_TOKEN", "synthetic-unrelated-private-token")
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(root))
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as owner:
        yield root, store, owner, ConfigurationProfileServices(owner.store)


def request():
    return SyncRequest(
        datasets=["instruments"], symbols=["000001.SZ"], start="2025-01-02", end="2025-01-02"
    )


def wait_job(data, identifier):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        value = data.manager.operations.get_job(identifier)
        if value["status"] in {"succeeded", "failed", "cancelled"}:
            return value
        time.sleep(0.03)
    raise AssertionError("Test-owned RQData job failed to settle")


def wait_marker(directory):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        markers = list(directory.glob("*.json"))
        if markers:
            return json.loads(markers[0].read_text())
        time.sleep(0.03)
    raise AssertionError("Test-owned SDK did not initialize")


def assert_private(value):
    for key in (*KEYS, ROTATED, ENVIRONMENT):
        assert key not in str(value) + repr(value)
    for token in ("synthetic-unrelated-agent-token", "synthetic-unrelated-private-token"):
        assert token not in str(value) + repr(value)


def test_verified_system_envelope_does_not_exempt_user_metadata():
    from alphalab.dataio.configuration_execution import reject_secret, request_metadata

    installation = "ab" + "1" * 30
    revision = "ab" + "2" * 30
    value = {
        "configuration_backend": "sqlite",
        "configuration_installation_id": installation,
        "configuration_capture": {
            "installation_id": installation,
            "revision": revision,
            "attempt_id": "ab" + "3" * 30,
        },
        "name": "safe metadata",
    }
    reject_secret(request_metadata(value, installation, revision), "ab")
    assert "configuration_capture" in value  # Projection must not mutate input.
    for name in ("name", "id"):
        unsafe = {**value, name: "%61%62"}
        with pytest.raises(ValueError, match="Private RQData output was rejected"):
            reject_secret(request_metadata(unsafe, installation, revision), "ab")
    with pytest.raises(ValueError):
        request_metadata(value, "4" * 32, revision)
    with pytest.raises(ValueError):
        request_metadata(value, installation, "5" * 32)


def rotate(profiles):
    profiles.rqdata.save_profile(
        {
            "id": "rq",
            "name": "RQ",
            "user": "rotated-user",
            "host": "rq.example:16011",
            "password": ROTATED,
        },
        activate=True,
    )


def test_sync_is_spawned_pinned_and_never_uses_json_or_environment(
    configured, tmp_path, monkeypatch, caplog
):
    root, store, owner, _ = configured
    marker = tmp_path / "observations"
    monkeypatch.setattr(rq_profiles, "read_settings", lambda: pytest.fail("Legacy JSON read"))
    monkeypatch.setattr(
        RQDataClient, "from_env", lambda: pytest.fail("Parent SDK/environment fallback")
    )
    with ConfigurationDataServices(owner, sdk_factory=partial(IsolatedSDK, str(marker))) as data:
        result = data.manager.run_now(request())
        assert result["status"] == "succeeded", data.public_job(result)
        capture = result["request"]["configuration_capture"]
        assert capture["revision"] == store.read_snapshot().revision
        observation = wait_marker(marker)
        assert observation["pid"] != os.getpid() and observation["ambient_cleared"]
        assert not observation["rotated"]
        assert_private(result)
        assert_private(data.public_job(result))
        assert_private(caplog.text)
    assert not (root / "app/secrets/rqdata-profiles.json").exists()


def test_queued_jobs_capture_latest_profile_when_execution_begins(configured):
    _, store, owner, profiles = configured
    import threading

    release = threading.Event()
    with ConfigurationDataServices(owner, sdk_factory=IsolatedSDK) as data:
        blocker = data.manager._executor.submit(lambda: release.wait(10))
        try:
            queued = data.manager.submit(request())
            assert "configuration_capture" not in queued["request"]
            rotate(profiles)
            revision = store.read_snapshot().revision
        finally:
            release.set()
            blocker.result(timeout=12)
        result = wait_job(data, queued["id"])
        assert result["status"] == "succeeded"
        assert result["request"]["configuration_capture"]["revision"] == revision


def test_running_rotation_affects_only_next_job_and_new_sdk_process(configured, tmp_path):
    _, store, owner, profiles = configured
    directory = tmp_path / "gated"
    with ConfigurationDataServices(
        owner, sdk_factory=partial(IsolatedSDK, str(directory), hold=True)
    ) as data:
        queued = data.manager.submit(request())
        first = wait_marker(directory)
        first_revision = data.manager.operations.get_job(queued["id"])["request"][
            "configuration_capture"
        ]["revision"]
        rotate(profiles)
        assert store.read_snapshot().revision != first_revision
        (directory / "release").touch()
        assert wait_job(data, queued["id"])["status"] == "succeeded"
        result = data.manager.run_now(request())
        assert result["status"] == "succeeded"
        observations = [json.loads(path.read_text()) for path in directory.glob("*.json")]
        assert {row["rotated"] for row in observations} == {False, True}
        assert len({row["pid"] for row in observations}) == 2
        assert first["rotated"] is False


@pytest.mark.parametrize("state", ["none", "clear", "incomplete", "corrupt"])
def test_unavailable_credentials_fail_without_fallback(configured, state):
    root, store, owner, profiles = configured
    if state == "none":
        profiles.rqdata.select({"kind": "none"})
    elif state == "clear":
        profiles.rqdata.save_profile(
            {"id": "rq", "name": "RQ", "user": "fixture-user", "host": "rq.example:16011"},
            activate=True,
            clear_password=True,
        )
    elif state == "incomplete":

        def patch(current):
            docs = current.documents
            docs["rqdata"]["profiles"][0]["password"] = ""
            return docs, current.selections

        store.update(patch)
    with ConfigurationDataServices(owner, sdk_factory=IsolatedSDK) as data:
        if state == "corrupt":
            (root / "app/secrets/configuration-store.sqlite3").write_bytes(b"broken")
            # A job already admitted before storage vanished still fails closed.
            identifier = data.manager.operations.create_job(
                request().model_dump(mode="json")
                | {
                    "configuration_backend": "sqlite",
                    "configuration_installation_id": owner.public_identity()["installation_id"],
                }
            )
            result = data._run(root, identifier)
        else:
            result = data.manager.run_now(request())
        assert result["status"] == "failed"
        assert_private(result)


def test_restart_recaptures_current_generation_and_rejects_legacy_or_foreign_jobs(configured):
    _, store, owner, profiles = configured
    with ConfigurationDataServices(owner, sdk_factory=IsolatedSDK) as first:
        payload = request().model_dump(mode="json")
        first._validate_submission(payload)
        payload["configuration_capture"] = {
            "installation_id": store.read_snapshot().installation_id,
            "revision": store.read_snapshot().revision,
            "attempt_id": "a" * 32,
        }
        identifier = first.manager.operations.create_job(payload)
        first.manager.operations.update_job(identifier, status="running")
        legacy = first.manager.operations.create_job(request().model_dump(mode="json"))
        foreign = first.manager.operations.create_job(
            payload | {"configuration_installation_id": "b" * 32}
        )
    rotate(profiles)
    with ConfigurationDataServices(owner, sdk_factory=IsolatedSDK) as second:
        result = wait_job(second, identifier)
        assert result["status"] == "succeeded"
        assert (
            result["request"]["configuration_capture"]["revision"] == store.read_snapshot().revision
        )
        assert result["request"]["configuration_capture"]["attempt_id"] != "a" * 32
        assert wait_job(second, legacy)["status"] == "failed"
        assert wait_job(second, foreign)["status"] == "failed"
        assert second.public_job(second.manager.operations.get_job(legacy)) is None


def test_recipe_plan_and_run_use_private_child_proxy_and_discard_logs(configured):
    _, _, owner, _ = configured
    noisy = RECIPE.replace(
        'context.output("calendar",', 'print("synthetic recipe output"); context.output("calendar",'
    )
    with ConfigurationDataServices(owner, sdk_factory=IsolatedSDK) as data:
        plan = data.plan_recipe(noisy)
        assert plan["planned"] and plan["stdout"] == plan["stderr"] == ""
        result = data.manager.run_recipe_now(noisy)
        assert result["status"] == "succeeded", data.public_job(result)
        assert result["request"]["recipe_outputs"][0]["name"] == "calendar"
        assert result["request"]["recipe_stdout"] == result["request"]["recipe_stderr"] == ""
        assert_private(result)


def test_recipe_followup_sync_reuses_one_captured_sdk_and_revision(configured, tmp_path):
    _, store, owner, _ = configured
    directory = tmp_path / "followup-sdk"
    source = """from alphalab.data_sdk.v1 import RQSyncRequest, data_recipe, rq
@data_recipe(id="fixture.followup")
def recipe(context):
    context.output("calendar", str(rq.get_latest_trading_date(market="cn")))
    return RQSyncRequest(
        template_id="rq.a_share_daily", datasets=("instruments",),
        start="2025-01-02", end="2025-01-02", symbols=("000001.SZ",),
    )
"""
    with ConfigurationDataServices(owner, sdk_factory=partial(IsolatedSDK, str(directory))) as data:
        result = data.manager.run_recipe_now(source)
        assert result["status"] == "succeeded", data.public_job(result)
        assert result["request"]["datasets"] == ["instruments"]
        assert (
            result["request"]["configuration_capture"]["revision"] == store.read_snapshot().revision
        )
        assert result["request"]["recipe_outputs"][0]["name"] == "calendar"
        assert len(list(directory.glob("*.json"))) == 1
        assert_private(result)


@pytest.mark.parametrize("failure", ["sdk-error", "crash", "timeout", "cancel"])
def test_worker_failures_cancel_and_timeout_are_sanitized_and_cleaned(
    configured, tmp_path, failure, caplog, capfd
):
    _, _, owner, _ = configured
    directory = tmp_path / "failure-marker"
    factory = partial(
        IsolatedSDK,
        str(directory),
        fail=failure == "sdk-error",
        crash=failure == "crash",
        hold=failure in {"timeout", "cancel"},
    )
    with ConfigurationDataServices(
        owner, sdk_factory=factory, timeout_seconds=6 if failure == "timeout" else 15
    ) as data:
        queued = data.manager.submit(request())
        if failure in {"cancel", "timeout"}:
            wait_marker(directory)
        if failure == "cancel":
            data.manager.cancel(queued["id"])
        result = wait_job(data, queued["id"])
        assert result["status"] == ("cancelled" if failure == "cancel" else "failed")
        assert_private(result)
        assert_private(caplog.text)
        output = capfd.readouterr()
        assert_private(output.out + output.err)


def test_concurrent_independent_installations_never_share_sdk_state(configured, tmp_path):
    _, _, owner, _ = configured
    other_root = tmp_path / "other"
    other_store = initialize(other_root)
    with ConfigurationBackend.sqlite(
        other_root, installation_id=other_store.read_snapshot().installation_id
    ) as other:
        rotate(ConfigurationProfileServices(other.store))
        with (
            ConfigurationDataServices(owner, sdk_factory=IsolatedSDK) as one,
            ConfigurationDataServices(other, sdk_factory=IsolatedSDK) as two,
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda item: item.manager.run_now(request()), [one, two]))
            assert [item["status"] for item in results] == ["succeeded", "succeeded"]
            assert (
                results[0]["request"]["configuration_capture"]["installation_id"]
                != results[1]["request"]["configuration_capture"]["installation_id"]
            )
            for result in results:
                assert_private(result)


def test_negative_control_missing_explicit_client_is_blocked_in_spawned_child(configured):
    _, _, owner, _ = configured
    with ConfigurationDataServices(owner, sdk_factory=fallback_sdk) as data:
        result = data.manager.run_now(request())
        assert result["status"] == "failed"
        assert "A SQLite job requires an explicitly captured client" in result["error"]
        assert_private(result)


def test_known_credentials_cannot_enter_persisted_requests_or_recipe_sources(configured):
    _, _, owner, _ = configured
    with ConfigurationDataServices(owner, sdk_factory=IsolatedSDK) as data:
        for source in [
            RECIPE.replace("synthetic query", PASSWORD),
            RECIPE.replace("synthetic query", KEYS[0]),
        ]:
            with pytest.raises(ConfigurationStoreError):
                data.manager.submit_recipe(source, project_id="test")
        assert data.manager.operations.list_jobs() == []
        assert_private(data)
        with pytest.raises(TypeError):
            pickle.dumps(data)


def test_explicit_data_api_is_bound_and_search_guard_remains_closed(configured):
    _, _, owner, _ = configured
    app = create_configuration_app(
        owner, include_data=True, data_options={"sdk_factory": IsolatedSDK}
    )
    with TestClient(app) as client:
        response = client.post("/api/data-sync/connection-test", json={})
        assert response.status_code == 200, response.text
        assert response.json()["connected"] is True
        assert_private(response.text)
        body = request().model_dump(mode="json")
        response = client.post("/api/data-sync/jobs", json=body)
        assert response.status_code == 202, response.text
        data = app.state.configuration_data
        wait_job(data, response.json()["id"])
        result = client.get("/api/data-sync/jobs/" + response.json()["id"])
        assert result.json()["status"] == "succeeded"
        assert_private(result.text)
        assert client.get("/api/data-source-profiles").status_code == 200
        assert (
            client.post(
                "/api/search-providers/test", json={"id": "search", "provider": "brave"}
            ).status_code
            == 503
        )
        assert client.get("/api/data-sync/recipe-templates").status_code == 404


def test_missing_or_stale_owner_is_rejected_and_normal_json_factory_does_not_mount_data(tmp_path):
    with ConfigurationBackend.json(tmp_path) as json_owner:
        with pytest.raises(ConfigurationStoreError):
            create_configuration_app(json_owner, include_data=True)
        with TestClient(create_configuration_app(json_owner)) as client:
            assert (
                client.post(
                    "/api/data-sync/jobs", json=request().model_dump(mode="json")
                ).status_code
                == 404
            )
    store = initialize(tmp_path / "closed")
    owner = ConfigurationBackend.sqlite(
        tmp_path / "closed", installation_id=store.read_snapshot().installation_id
    )
    owner.close()
    with pytest.raises(ConfigurationStoreError):
        ConfigurationDataServices(owner, sdk_factory=IsolatedSDK)


@pytest.mark.parametrize("operation", ["cancel", "timeout"])
def test_recipe_cancellation_and_timeout_clean_owned_processes(configured, tmp_path, operation):
    _, _, owner, _ = configured
    directory = tmp_path / "recipe-control"
    with ConfigurationDataServices(
        owner,
        sdk_factory=partial(IsolatedSDK, str(directory), hold=True),
        timeout_seconds=6 if operation == "timeout" else 15,
    ) as data:
        queued = data.manager.submit_recipe(RECIPE, project_id="test")
        wait_marker(directory)
        if operation == "cancel":
            data.manager.cancel(queued["id"])
        result = wait_job(data, queued["id"])
        assert result["status"] == ("cancelled" if operation == "cancel" else "failed")
        assert_private(result)


def test_shutdown_waits_for_synchronous_worker_and_releases_owner(configured, tmp_path):
    _, _, owner, _ = configured
    directory = tmp_path / "shutdown"
    data = ConfigurationDataServices(
        owner, sdk_factory=partial(IsolatedSDK, str(directory), hold=True)
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(data.connection_test)
        wait_marker(directory)
        started = time.monotonic()
        data.close()
        assert time.monotonic() - started < 8
        with pytest.raises(ConfigurationStoreError):
            future.result(timeout=2)
    data.close()
    with pytest.raises(ConfigurationStoreError):
        data.connection_test()


def test_process_startup_failure_is_sanitized_and_job_terminal(configured):
    _, _, owner, _ = configured
    with ConfigurationDataServices(owner, sdk_factory=lambda: None) as data:
        result = data.manager.run_now(request())
        assert result["status"] == "failed"
        assert_private(result)


def test_explicit_environment_intent_uses_only_authorized_snapshot(configured, tmp_path):
    _, _, owner, profiles = configured
    profiles.rqdata.select({"kind": "environment"})
    directory = tmp_path / "explicit-environment"
    with ConfigurationDataServices(
        owner,
        environment={
            "RQ_USER": "authorized",
            "RQ_HOST": "rq.example:16011",
            "RQ_PASSWORD": ENVIRONMENT,
        },
        sdk_factory=partial(IsolatedSDK, str(directory)),
    ) as data:
        assert data.connection_test()["connected"] is True
        assert wait_marker(directory)["environment_selected"]
    with ConfigurationDataServices(owner, sdk_factory=IsolatedSDK) as empty:
        with pytest.raises(ConfigurationStoreError):
            empty.connection_test()


def test_recipe_api_uses_same_authority_and_keeps_plan_non_mutating(configured):
    root, _, owner, _ = configured
    app = create_configuration_app(
        owner, include_data=True, data_options={"sdk_factory": IsolatedSDK}
    )
    with TestClient(app) as client:
        assert (
            client.put(
                "/api/data-sync/recipes/test", json={"source": RECIPE, "confirm_write": True}
            ).status_code
            == 200
        )
        before = {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file() and path.suffix != ".lock"
        }
        result = client.post(
            "/api/data-sync/recipes/test/plan", json={"confirm_python_execution": True}
        )
        assert result.status_code == 200 and result.json()["planned"]
        after = {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file() and path.suffix != ".lock"
        }
        assert after == before
        response = client.post(
            "/api/data-sync/recipes/test/jobs", json={"confirm_python_execution": True}
        )
        assert response.status_code == 202, response.text
        item = wait_job(app.state.configuration_data, response.json()["id"])
        assert item["status"] == "succeeded"
        assert_private(item)


def _shared_sdk_negative_control(pipe):
    from alphalab.dataio.providers.rq import RQDataConfig

    sdk = IsolatedSDK()
    one = RQDataClient(RQDataConfig("one", PASSWORD, "rq.example:16011"), module=sdk)
    two = RQDataClient(RQDataConfig("two", ROTATED, "rq.example:16011"), module=sdk)
    os.environ.pop("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", None)
    os.environ.pop("UNRELATED_PRIVATE_TOKEN", None)
    one.connect()
    try:
        two.connect()
    except Exception as error:
        pipe.send(
            {
                "detected": True,
                "specific_reason": str(error.__cause__) == "Cross-job SDK process state was reused",
            }
        )
    else:
        pipe.send({"detected": False})
    finally:
        pipe.close()


def test_negative_control_distinct_client_objects_do_not_isolate_sdk_globals():
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_shared_sdk_negative_control, args=(child,))
    try:
        process.start()
        child.close()
        assert parent.poll(15)
        assert parent.recv() == {"detected": True, "specific_reason": True}
        process.join(timeout=3)
        assert process.exitcode == 0
    finally:
        parent.close()
        child.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
        process.close()


def _abrupt_api_owner(root, directory):
    from alphalab.utils.configuration_store import ConfigurationStore

    store = ConfigurationStore.open_existing(root)
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as owner:
        with ConfigurationDataServices(
            owner, sdk_factory=partial(IsolatedSDK, directory, hold=True)
        ) as data:
            data.manager.submit(request())
            wait_marker(Path(directory))
            os._exit(29)  # Only the API-owner test process created below.


@pytest.mark.skipif(os.name != "nt", reason="Windows process-handle watchdog verification")
def test_parent_loss_exits_only_its_owned_sdk_worker(tmp_path):
    import ctypes
    from ctypes import wintypes

    root, directory = tmp_path / "crashed-owner", tmp_path / "owner-markers"
    initialize(root)
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_abrupt_api_owner, args=(str(root), str(directory)))
    try:
        process.start()
        marker = wait_marker(directory)
        process.join(timeout=10)
        assert process.exitcode == 29
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, marker["pid"])  # SYNCHRONIZE, read/wait only.
        if handle:
            try:
                assert kernel.WaitForSingleObject(handle, 5000) == 0
            finally:
                kernel.CloseHandle(handle)
        else:
            assert ctypes.get_last_error() == 87  # Test worker already exited.
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
        process.close()


@pytest.mark.parametrize("field", ["root_key", "installation_id", "revision", "oversized"])
def test_private_worker_rejects_mismatched_or_oversized_capture_before_sdk(
    configured, tmp_path, field
):
    from alphalab.dataio.configuration_execution import MAX_PRIVATE_BYTES, _worker

    _, store, owner, _ = configured
    snapshot = store.read_snapshot()
    directory = tmp_path / "ipc-marker"
    payload = {
        "protocol": 1,
        "root_key": hashlib.sha256(os.path.normcase(str(owner.root)).encode()).hexdigest(),
        "installation_id": snapshot.installation_id,
        "revision": snapshot.revision,
        "rq": {"RQ_USER": "fixture", "RQ_PASSWORD": PASSWORD, "RQ_HOST": "rq.example:16011"},
    }
    if field != "oversized":
        payload[field] = ("0" if payload[field][0] != "0" else "1") + payload[field][1:]
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_worker,
        args=(
            child,
            str(owner.root),
            snapshot.installation_id,
            snapshot.revision,
            "connection",
            None,
            None,
            partial(IsolatedSDK, str(directory)),
        ),
    )
    try:
        process.start()
        child.close()
        assert parent.poll(15) and parent.recv_bytes(32) == b"ready"
        # Still valid JSON and a valid profile if the frame-size guard is
        # removed; rejection must not come from JSON/schema/key-length errors.
        data = (b" " * (MAX_PRIVATE_BYTES + 1) if field == "oversized" else b"") + json.dumps(
            payload
        ).encode()
        try:
            parent.send_bytes(data)
            assert parent.poll(5)
            response = json.loads(parent.recv_bytes(1024))
            assert response == {"ok": False, "reason": "worker_failed"}
            assert_private(response)
        except (BrokenPipeError, EOFError, OSError):
            # Windows may reject the oversized message and close the peer
            # while its asynchronous writer is completing. No SDK may start.
            assert field == "oversized"
        assert not directory.exists()
        process.join(timeout=3)
        assert process.exitcode == 0
    finally:
        parent.close()
        child.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
        process.close()


def test_api_clear_and_stale_revision_never_borrow_environment(configured, tmp_path):
    _, _, owner, _ = configured
    directory = tmp_path / "clear-api-marker"
    app = create_configuration_app(
        owner, include_data=True, data_options={"sdk_factory": partial(IsolatedSDK, str(directory))}
    )
    with TestClient(app) as client:
        before = client.get("/api/data-source-profiles").json()
        body = {
            "id": "rq",
            "name": "RQ",
            "user": "fixture-user",
            "host": "rq.example:16011",
            "clear_password": True,
            "expected_revision": before["revision"],
        }
        result = client.put("/api/data-source-profiles", json=body)
        assert result.status_code == 200, result.text
        assert result.json()["configured"] is False
        assert result.json()["profiles"][0]["has_password"] is False
        stale = client.put("/api/data-source-profiles", json=body)
        assert stale.status_code == 409
        assert client.post("/api/data-sync/connection-test", json={}).status_code == 422
        assert not directory.exists()
        assert_private(result.text + stale.text)


def test_factory_freezes_explicit_environment_and_rejects_env_secrets_in_source(
    configured, tmp_path
):
    _, _, owner, profiles = configured
    profiles.rqdata.select({"kind": "environment"})
    environment = {"RQ_USER": "authorized", "RQ_PASSWORD": PASSWORD, "RQ_HOST": "rq.example:16011"}
    directory = tmp_path / "snapshot-env"
    app = create_configuration_app(
        owner,
        include_data=True,
        environment=environment,
        data_options={"sdk_factory": partial(IsolatedSDK, str(directory))},
    )
    environment["RQ_PASSWORD"] = ENVIRONMENT
    with TestClient(app) as client:
        assert client.post("/api/data-sync/connection-test", json={}).status_code == 200
        assert wait_marker(directory)["environment_selected"] is False
    with ConfigurationDataServices(
        owner, environment={"RQ_PASSWORD": ENVIRONMENT}, sdk_factory=IsolatedSDK
    ) as data:
        with pytest.raises(ConfigurationStoreError):
            data.manager.submit_recipe(
                RECIPE.replace("synthetic query", ENVIRONMENT), project_id="test"
            )
        assert data.manager.operations.list_jobs() == []


def test_reflected_credentials_never_reach_connection_results_recipe_audit_or_logs(
    configured, caplog, capsys
):
    _, _, owner, _ = configured
    with ConfigurationDataServices(owner, sdk_factory=ReflectingSDK) as data:
        with pytest.raises(ConfigurationStoreError) as caught:
            data.connection_test()
        assert_private(caught.value)
        assert caught.value.__cause__ is None and caught.value.__context__ is None
        result = data.manager.run_recipe_now(RECIPE)
        assert result["status"] == "failed"
        assert_private(result)
    captured = capsys.readouterr()
    assert_private(caplog.text + captured.out + captured.err)


def test_json_recovery_and_legacy_runners_cannot_execute_sqlite_jobs(configured, monkeypatch):
    from alphalab.dataio.sync import RQSyncService, SyncJobManager, _run_recipe_job

    root, _, owner, _ = configured
    with ConfigurationDataServices(owner, sdk_factory=IsolatedSDK) as data:
        payload = request().model_dump(mode="json")
        data._validate_submission(payload)
        recovery = data.manager.operations.create_job(payload)
        direct = data.manager.operations.create_job(payload)
        recipe_payload = {
            "source": "rq",
            "kind": "python_recipe",
            "project_id": "test",
            "recipe_source": RECIPE,
        }
        data._validate_submission(recipe_payload)
        recipe = data.manager.operations.create_job(recipe_payload)
    monkeypatch.setattr(
        RQDataClient, "from_env", lambda: pytest.fail("JSON fallback during SQLite replay")
    )
    assert RQSyncService(root).run(direct)["status"] == "failed"
    assert _run_recipe_job(root, recipe)["status"] == "failed"
    legacy = SyncJobManager(root)
    try:
        result = legacy.operations.get_job(recovery)
        assert result["status"] == "failed"
        assert result["error"] == "This job requires its explicit configuration backend."
    finally:
        legacy.shutdown()


def _assert_rejected_before_dataset_write(data, result, root):
    from alphalab.dataio.catalog import DataCatalog

    assert not DataCatalog(root).files("rq.instruments"), (
        "Credential-bearing dataset reached persistence"
    )
    assert result["status"] == "failed"
    assert "recipe_published" not in result["request"]
    assert "recipe_outputs" not in result["request"]
    assert_private(result)
    assert_private(data.public_job(result))
    assert_private(data.manager.operations.get_job(result["id"]))


@pytest.mark.parametrize("dtype,unused", _FRAME_CASES)
def test_regular_sync_rejects_reflected_password_before_canonical_publication(
    configured, dtype, unused, caplog, capfd
):
    root, _, owner, _ = configured
    with ConfigurationDataServices(
        owner, sdk_factory=partial(ReflectingInstrumentsSDK, dtype=dtype, unused=unused)
    ) as data:
        result = data.manager.run_now(request())
        _assert_rejected_before_dataset_write(data, result, root)
    output = capfd.readouterr()
    assert_private(caplog.text + output.out + output.err)


@pytest.mark.parametrize("dtype,unused", _FRAME_CASES)
@pytest.mark.parametrize("boundary", ["publish", "write"])
def test_recipe_rejects_reflected_frame_before_publication_and_followup(
    configured, dtype, unused, boundary, caplog, capfd
):
    root, _, owner, _ = configured
    source = _publication_recipe(boundary)
    assert_private(source)  # The credential comes from the SDK, never recipe source.
    with ConfigurationDataServices(
        owner, sdk_factory=partial(ReflectingInstrumentsSDK, dtype=dtype, unused=unused)
    ) as data:
        result = data.manager.run_recipe_now(source)
        _assert_rejected_before_dataset_write(data, result, root)
        assert "datasets" not in result["request"]  # Rejection precedes recipe-to-sync handoff.
    output = capfd.readouterr()
    assert_private(caplog.text + output.out + output.err)


@pytest.mark.parametrize("boundary", ["sync", "publish", "write"])
def test_safe_categorical_and_numeric_data_persist_without_conversion(configured, boundary):
    from alphalab.dataio.runtime import RuntimeStore

    root, _, owner, _ = configured
    with ConfigurationDataServices(owner, sdk_factory=SafeCategoricalInstrumentsSDK) as data:
        result = (
            data.manager.run_now(request())
            if boundary == "sync"
            else data.manager.run_recipe_now(_publication_recipe(boundary))
        )
        assert result["status"] == "succeeded", data.public_job(result)
        frame = RuntimeStore(root).read("rq.instruments")
        assert isinstance(frame["name"].dtype, pd.CategoricalDtype)
        assert list(frame["name"]) == ["Fixture"]
        assert list(frame["name"].cat.categories) == ["Fixture", "Unused safe"]
        assert list(frame["numeric_fixture"]) == [17.25]
        assert pd.api.types.is_float_dtype(frame["numeric_fixture"].dtype)
        assert_private(data.public_job(result))


@pytest.mark.parametrize("encoded", [False, True])
def test_unused_credential_categories_are_serialized_and_must_be_rejected(encoded):
    from alphalab.dataio.configuration_execution import reject_frame

    secret = "".join(f"%{ord(char):02x}" for char in PASSWORD) if encoded else PASSWORD
    frame = pd.DataFrame({"name": pd.Categorical(["Fixture"], categories=["Fixture", secret])})
    # No file is written: prove the unused dictionary survives real Parquet serialization.
    restored = pd.read_parquet(io.BytesIO(frame.to_parquet()))
    assert secret not in list(restored["name"])
    assert secret in list(restored["name"].cat.categories)
    with pytest.raises(ValueError, match="Private RQData output was rejected") as caught:
        reject_frame(frame, PASSWORD)
    assert_private(caught.value)


@pytest.mark.parametrize("unused", [False, True], ids=["used", "unused"])
def test_negative_control_original_dtype_guard_persists_credential_in_owned_child(
    configured, unused
):
    import alphalab.dataio.configuration_execution as execution
    from alphalab.dataio.runtime import RuntimeStore

    root, _, owner, _ = configured
    corrected = execution.reject_frame
    # Only this spawned test child restores the old guard; production source and
    # the parent remain corrected, and the child exits before assertions below.
    with ConfigurationDataServices(
        owner, sdk_factory=partial(_legacy_categorical_sdk, unused=unused)
    ) as data:
        result = data.manager.run_recipe_now(_publication_recipe("publish"))
        with pytest.raises(AssertionError, match="Credential-bearing dataset reached persistence"):
            _assert_rejected_before_dataset_write(data, result, root)
        assert result["status"] == "succeeded"
        frame = RuntimeStore(root).read("rq.instruments")
        assert any(value == PASSWORD for value in frame["name"].cat.categories)
        if unused:
            assert not any(value == PASSWORD for value in frame["name"])
    assert execution.reject_frame is corrected
    with pytest.raises(ValueError, match="Private RQData output was rejected"):
        corrected(frame, PASSWORD)
