"""Real owned children and SQLite; fake HTTP Agent/provider, no external calls."""

from __future__ import annotations

import io
import json
import os
import pickle
import subprocess
import threading
import time
import traceback
from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from alphalab.utils import configuration_agent as agent
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_store import ConfigurationStoreError
from apps.api.configuration_app import create_configuration_app
from apps.api.services import agent_run_service
from tests.api.test_configuration_backend import KEYS, initialize

REPO = Path(__file__).resolve().parents[2]
PRIVATE_VALUES = ("13" * 32, "24" * 32, "35" * 32)

# Substitute only the optional core. The actual fixed managed entry point,
# auth module, research workspace and coordinated SQLite runtime still execute.
FAKE_CORE = """
import { createServer } from 'node:http';
export const createLocalRuntime = () => ({ adapter: {}, close() {} });
export const createModelCompletion = () => async () => ({ content: 'Synthetic' });
export const compileHarnessWorkspace = () => ({});
export const createLocalHost = async () => {
  const routes = new Map(); let parser;
  const server = createServer(async (request, response) => {
    let body = ''; for await (const part of request) body += part;
    let status = 200;
    const reply = { code(value) { status = value; return this }, send(value) { return value } };
    const parsed = await new Promise((resolve, reject) => parser(request, Buffer.from(body), (error, value) => error ? reject(error) : resolve(value)));
    const value = await routes.get(request.url)({ body: parsed, headers: request.headers, raw:request }, reply);
    response.writeHead(status, { 'Content-Type': 'application/json' }).end(JSON.stringify(value));
  });
  const app = { post(path, handler) { routes.set(path, handler) }, addContentTypeParser(_type, _options, callback) { parser = callback }, async listen(options) {
    await new Promise(resolve => server.listen(options.port, options.host, resolve));
    this.listeningOrigin = `http://127.0.0.1:${server.address().port}`;
  } };
  return { app, close: async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)) } };
};
"""


def no_secrets(value):
    for key in (*KEYS, *PRIVATE_VALUES):
        assert key not in str(value)


@pytest.fixture
def owned(tmp_path, monkeypatch):
    node = os.getenv("ALPHALAB_TEST_NODE")
    if not node:
        pytest.fail("Set ALPHALAB_TEST_NODE to the supported Node 22 executable")
    root = tmp_path / "installation Unicode Ω"
    store = initialize(root)
    owner = ConfigurationBackend.sqlite(root, installation_id=store.read_snapshot().installation_id)
    control, trace = tmp_path / "control.txt", tmp_path / "trace.jsonl"
    original = subprocess.Popen
    children = []
    original_random = agent.secrets.token_hex
    private_values = iter(PRIVATE_VALUES)

    def random_hex(size):
        # Fixed synthetic launch canaries, but fresh random request challenges.
        return next(private_values, None) or original_random(size)

    monkeypatch.setattr(agent.secrets, "token_hex", random_hex)

    def launch(command, **options):
        assert command == [node, str(agent._ENTRY)]
        if control.exists() and control.read_text() == "entry":
            core_url = "data:text/javascript," + quote(FAKE_CORE)
            script = f"""
import {{ registerHooks }} from 'node:module';
globalThis.fetch = async () => Response.json({{ web: {{ results: [{{ title: 'Synthetic', url: 'https://source.example/article' }}] }} }});
registerHooks({{ resolve(specifier, context, next) {{
  if (specifier.endsWith('/build/conexus/apps/local-host/src/index.mjs')) return {{ url: {json.dumps(core_url)}, shortCircuit: true }};
  return next(specifier, context);
}} }});
"""
            args = [node, "--import", "data:text/javascript," + quote(script), str(agent._ENTRY)]
        else:
            args = [
                node,
                str(REPO / "tests/fixtures/configuration_agent.mjs"),
                str(control),
                str(trace),
            ]
        child = original(args, **options)
        children.append(child)
        return child

    monkeypatch.setattr(agent.subprocess, "Popen", launch)
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "local")

    @contextmanager
    def start(*, timeout=2, startup_timeout=15):
        with agent.managed_configuration_agent(
            owner, node=node, request_timeout=timeout, startup_timeout=startup_timeout
        ) as connection:
            yield connection

    def rows():
        return [json.loads(row) for row in trace.read_text().splitlines()] if trace.exists() else []

    try:
        yield owner, store, start, control, rows, children
    finally:
        owner.close()
        assert all(child.poll() is not None for child in children)
        assert not any(
            t.name.startswith(tuple(f"owned-agent-{child.pid}-" for child in children))
            for t in threading.enumerate()
        )


def post(client, provider="brave"):
    response = client.post(
        "/api/search-providers/test", json={"id": "search", "provider": provider}
    )
    no_secrets(response.text)
    return response


def test_owned_endpoint_ignores_ambient_other_agent(owned, monkeypatch):
    owner, store, start, control, rows, children = owned
    received = []

    class OtherAgent(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            received.append(
                KEYS[1].encode() in self.rfile.read(int(self.headers["Content-Length"]))
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

    other = ThreadingHTTPServer(("127.0.0.1", 0), OtherAgent)
    thread = threading.Thread(target=other.serve_forever, daemon=True)
    thread.start()
    try:
        with start() as connection:
            monkeypatch.setattr(
                agent_run_service,
                "web_origin",
                lambda: pytest.fail("Owned dispatch consulted ambient origin"),
            )
            monkeypatch.setenv("CONEXUS_WEB_ORIGIN", f"http://127.0.0.1:{other.server_port}")
            monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "synthetic-other-token")
            with TestClient(create_configuration_app(owner, agent_connection=connection)) as client:
                assert post(client).json()["ok"] is True
            assert received == [], "Private SQLite request reached another Agent"
            assert [row for row in rows() if row["operation"] == "test"] == [
                {"operation": "test", "hasKey": True, "provider": "brave"}
            ]
    finally:
        other.shutdown()
        other.server_close()
        thread.join(timeout=2)


def test_negative_control_detects_old_ambient_dispatch(owned, monkeypatch):
    def vulnerable_test(self, owner, settings):
        # Isolated test-only negative control: synthetic key, local fake Agent.
        # The live implementation is restored on exiting this patch context.
        with httpx.Client(trust_env=False, timeout=2, follow_redirects=False) as client:
            client.post(
                os.environ["CONEXUS_WEB_ORIGIN"] + "/api/alphalab/search/test", json=settings
            )
        return {"ok": True}

    with monkeypatch.context() as patch:
        patch.setattr(agent.OwnedAgentConnection, "test_search", vulnerable_test)
        with pytest.raises(AssertionError, match="Private SQLite request reached another Agent"):
            test_owned_endpoint_ignores_ambient_other_agent(owned, patch)


@pytest.mark.parametrize(
    "mode",
    [
        "wrong-root",
        "wrong-root_key",
        "wrong-backend",
        "wrong-installation_id",
        "wrong-decision_id",
        "wrong-slug",
        "wrong-pid",
        "wrong-launch",
        "wrong-origin",
        "wrong-version",
        "unauthenticated",
        "stale-token",
        "redirect",
        "unavailable",
        "close-proof",
        "duplicate-pid",
        "escaped-pid",
        "duplicate-mac",
        "boolean-version",
        "float-version",
        "string-version",
        "duplicate-origin",
    ],
)
def test_forged_readiness_cannot_mint_connection(owned, mode):
    owner, store, start, control, rows, children = owned
    before = store.read_snapshot()
    control.write_text(mode)
    with pytest.raises(ConfigurationStoreError) as caught, start():
        pytest.fail("Forged readiness authorized a connection")
    assert caught.value.code == "STORE_UNAVAILABLE"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    no_secrets("".join(traceback.format_exception(caught.value)))
    assert not any(row["operation"] == "test" for row in rows())
    assert store.read_snapshot().revision == before.revision


@pytest.mark.parametrize(
    "mode",
    [
        "wrong-origin",
        "replay",
        "stale-token",
        "redirect",
        "close-proof",
        "unavailable",
        "timeout",
        "slow-close",
    ],
)
def test_dispatch_reauthenticates_before_sending_secrets(owned, mode):
    owner, store, start, control, rows, children = owned
    with start(timeout=0.4) as connection:
        control.write_text(mode)
        with TestClient(create_configuration_app(owner, agent_connection=connection)) as client:
            started = time.monotonic()
            assert post(client).status_code == 503
            assert time.monotonic() - started < 3
        assert not any(row["operation"] == "test" for row in rows())


def test_untrusted_descriptor_and_public_identity_never_authorize(owned):
    owner, store, start, control, rows, children = owned
    for value in (
        {"origin": "http://127.0.0.1:1", **owner.public_identity()},
        owner.public_identity(),
        object(),
    ):
        with pytest.raises(ConfigurationStoreError):
            create_configuration_app(owner, agent_connection=value)
    with pytest.raises(TypeError):
        agent.OwnedAgentConnection()
    with TestClient(
        create_configuration_app(owner, agent_identity=owner.public_identity())
    ) as client:
        assert post(client).status_code == 503
        assert post(client, "off").json()["ok"] is True
    assert rows() == []


def test_authenticated_openrouter_uses_owned_model_reference(owned):
    owner, store, start, control, rows, children = owned
    with start() as connection:
        with TestClient(create_configuration_app(owner, agent_connection=connection)) as client:
            assert post(client, "openrouter").json()["ok"] is True
        assert [row for row in rows() if row["operation"] == "test"] == [
            {"operation": "test", "hasKey": False, "provider": "openrouter"}
        ]


def test_matching_public_identity_at_substituted_endpoint_is_not_proof(owned):
    owner, store, start, control, rows, children = owned
    received = []

    class Impostor(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(any(key.encode() in body for key in (*KEYS, *PRIVATE_VALUES)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps({"ok": True, "configuration": owner.public_identity()}).encode()
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Impostor)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    control.write_text(f"descriptor:http://127.0.0.1:{server.server_port}")
    try:
        with pytest.raises(ConfigurationStoreError), start():
            pytest.fail("Public identity from another endpoint authorized a connection")
        assert received == [False]
        assert not any(row["operation"] == "test" for row in rows())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_concurrent_authenticated_api_requests_share_only_the_owned_connection(owned):
    owner, store, start, control, rows, children = owned
    before = store.read_snapshot()
    with start() as connection:
        with TestClient(create_configuration_app(owner, agent_connection=connection)) as client:
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(lambda _: post(client), range(6)))
            assert all(result.status_code == 200 and result.json()["ok"] for result in results)
    assert len([row for row in rows() if row["operation"] == "test" and row["hasKey"]]) == 6
    assert store.read_snapshot().revision == before.revision


def test_private_connection_cannot_be_serialized_or_rebound(owned):
    owner, store, start, control, rows, children = owned
    with start() as connection:
        no_secrets(repr(connection))
        for encode in (json.dumps, jsonable_encoder, pickle.dumps):
            with pytest.raises((TypeError, ValueError)) as caught:
                encode(connection)
            no_secrets(repr(caught.value))
        connection.close()
        with pytest.raises(ConfigurationStoreError):
            connection.require_owner(owner)
        with TestClient(create_configuration_app(owner)) as client:
            assert post(client).status_code == 503


def test_agent_exit_invalidates_connection_and_owner_is_held_until_exit(owned):
    owner, store, start, control, rows, children = owned
    with start() as connection:
        with pytest.raises(ConfigurationStoreError) as caught:
            owner.close()
        assert caught.value.code == "STORE_BUSY"
        children[-1].terminate()
        children[-1].wait(timeout=5)
        with pytest.raises(ConfigurationStoreError):
            connection.require_owner(owner)
    owner.close()
    with pytest.raises(ConfigurationStoreError):
        connection.require_owner(owner)


def test_concurrent_dispatch_and_close_is_bounded(owned):
    owner, store, start, control, rows, children = owned
    with start(timeout=0.4) as connection:
        control.write_text("timeout")
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(
                    connection.test_search, owner, {"provider": "brave", "brave_api_key": KEYS[1]}
                )
                for _ in range(3)
            ]
            time.sleep(0.05)
            connection.close()
            connection.close()
            for future in futures:
                with pytest.raises(ConfigurationStoreError):
                    future.result(timeout=3)
        assert not any(row["operation"] == "test" for row in rows())


@pytest.mark.parametrize("mode", ["response-secret", "test-redirect", "normal"])
def test_public_results_never_reflect_private_agent_responses(owned, mode, caplog, capsys):
    owner, store, start, control, rows, children = owned
    with start() as connection:
        control.write_text(mode)
        with TestClient(create_configuration_app(owner, agent_connection=connection)) as client:
            response = post(client)
            assert response.status_code == (503 if mode == "test-redirect" else 200)
    captured = capsys.readouterr()
    no_secrets(captured.out + captured.err + caplog.text)


def test_restart_never_reuses_retired_connection(owned):
    owner, store, start, control, rows, children = owned
    with start() as retired:
        pass
    with start() as replacement:
        replacement.require_owner(owner)
        with pytest.raises(ConfigurationStoreError):
            retired.require_owner(owner)
        with TestClient(create_configuration_app(owner, agent_connection=replacement)) as client:
            assert post(client).json()["ok"] is True
    assert len(children) == 2 and children[0].pid != children[1].pid


def test_unresponsive_managed_startup_has_bounded_owned_cleanup(owned):
    owner, store, start, control, rows, children = owned
    control.write_text("startup-timeout")
    started = time.monotonic()
    with pytest.raises(ConfigurationStoreError), start(startup_timeout=0.1):
        pytest.fail("Unresponsive child authorized a connection")
    assert time.monotonic() - started < 8
    assert rows() == [] and children[-1].poll() is not None


def test_connection_cannot_be_lent_to_other_owner(owned, tmp_path):
    owner, store, start, control, rows, children = owned
    other_root = tmp_path / "other-installation"
    other_store = initialize(other_root)
    with (
        start() as connection,
        ConfigurationBackend.sqlite(
            other_root, installation_id=other_store.read_snapshot().installation_id
        ) as other,
    ):
        with pytest.raises(ConfigurationStoreError):
            create_configuration_app(other, agent_connection=connection)
        assert not any(row["operation"] == "test" for row in rows())


def test_actual_managed_entry_with_substituted_core_only(owned):
    owner, store, start, control, rows, children = owned
    control.write_text("entry")
    with start() as connection:
        with TestClient(create_configuration_app(owner, agent_connection=connection)) as client:
            assert post(client).json()["ok"] is True
    assert children[-1].poll() == 0  # Actual parent EOF lifecycle, no terminate.


def test_failed_agreement_precedes_private_profile_resolution(owned, monkeypatch):
    owner, store, start, control, rows, children = owned
    with start() as connection:
        app = create_configuration_app(owner, agent_connection=connection)
        control.write_text("wrong-installation_id")
        monkeypatch.setattr(
            type(app.state.configuration_bindings.search),
            "resolve_test_profile",
            lambda *_a, **_k: pytest.fail("Extracted private settings before agreement"),
        )
        with TestClient(app) as client:
            assert post(client).status_code == 503
        assert not any(row["operation"] == "test" for row in rows())


def test_unavailable_bundle_does_not_mint_connection(tmp_path, monkeypatch):
    # Exercise the actual fixed entrypoint, not the fake-Agent launch seam.
    node = os.environ["ALPHALAB_TEST_NODE"]
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "local")
    if (REPO / "build/conexus/UPSTREAM.json").exists():
        pytest.skip("Missing-bundle path requires the bundle to be absent")
    store = initialize(tmp_path)
    with ConfigurationBackend.sqlite(
        tmp_path, installation_id=store.read_snapshot().installation_id
    ) as owner:
        with (
            pytest.raises(ConfigurationStoreError) as caught,
            agent.managed_configuration_agent(owner, node=node),
        ):
            pytest.fail("Unavailable core authorized an Agent")
        assert caught.value.__context__ is None
        no_secrets("".join(traceback.format_exception(caught.value)))
        with TestClient(create_configuration_app(owner)) as client:
            assert post(client).status_code == 503


def private_failure():
    error = RuntimeError(KEYS[0])
    if hasattr(error, "add_note"):
        error.add_note(PRIVATE_VALUES[0])
    error.__cause__ = RuntimeError(KEYS[1])
    return error


def assert_private_error(error):
    assert type(error) is ConfigurationStoreError
    assert error.code == "STORE_UNAVAILABLE"
    assert error.__context__ is None and error.__cause__ is None
    no_secrets("".join(traceback.format_exception(error)))
    no_secrets(repr(error) + str(error.args) + str(getattr(error, "__notes__", [])))
    no_secrets(pickle.dumps(error).decode("utf-8", errors="replace"))


@pytest.mark.parametrize("stage", ["exit", "stdout", "handoff"])
@pytest.mark.parametrize("after", [False, True])
def test_helper_start_failure_reaps_child_closes_pipes_and_releases_borrow(
    owned, monkeypatch, stage, after
):
    owner, store, start, control, rows, children = owned
    original = threading.Thread.start
    cleanup = agent._cleanup_child
    cleanups = []

    def fail(thread):
        if thread.name.startswith("owned-agent-") and thread.name.endswith("-" + stage):
            if after:
                original(thread)
            raise private_failure()
        return original(thread)

    def record(*args, **kwargs):
        cleanups.append((args, kwargs))
        return cleanup(*args, **kwargs)

    monkeypatch.setattr(threading.Thread, "start", fail)
    monkeypatch.setattr(agent, "_cleanup_child", record)
    with pytest.raises(ConfigurationStoreError) as caught, start():
        pytest.fail("Failed helper startup yielded a connection")
    assert_private_error(caught.value)
    assert all(
        child.poll() is not None and child.stdin.closed and child.stdout.closed
        for child in children
    )
    assert len(cleanups) == 1
    assert cleanup(*cleanups[0][0], **cleanups[0][1]) is None
    assert cleanup(*cleanups[0][0], **cleanups[0][1]) is None
    assert owner._users == 0
    owner.close()


def test_failure_before_helper_creation_and_child_exit_during_cleanup(owned, monkeypatch):
    owner, store, start, control, rows, children = owned
    control.write_text("exit-startup")
    with pytest.raises(ConfigurationStoreError), start():
        pytest.fail("Exited child yielded a connection")
    assert all(
        child.poll() is not None and child.stdin.closed and child.stdout.closed
        for child in children
    )
    assert owner._users == 0

    def fail(_owner):
        raise private_failure()

    monkeypatch.setattr(agent, "_ChildLease", fail)
    with pytest.raises(ConfigurationStoreError) as caught, start():
        pytest.fail("Pre-helper failure yielded a connection")
    assert_private_error(caught.value)
    assert owner._users == 0
    assert all(
        child.poll() is not None and child.stdin.closed and child.stdout.closed
        for child in children
    )


def test_cleanup_failure_does_not_skip_owned_resource_cleanup(owned, monkeypatch):
    owner, store, start, control, rows, children = owned
    original = agent.OwnedAgentConnection.close

    def fail(connection):
        original(connection)
        raise private_failure()

    monkeypatch.setattr(agent.OwnedAgentConnection, "close", fail)
    with pytest.raises(ConfigurationStoreError) as caught, start():
        pass
    assert_private_error(caught.value)
    assert owner._users == 0
    assert all(
        child.poll() is not None and child.stdin.closed and child.stdout.closed
        for child in children
    )


@pytest.mark.parametrize("kind", [CancelledError, KeyboardInterrupt, SystemExit])
def test_interrupted_startup_preserves_redacted_interruption_and_reaps_child(
    owned, monkeypatch, kind
):
    owner, store, start, control, rows, children = owned
    original = threading.Thread.start

    def fail(thread):
        if thread.name.startswith("owned-agent-") and thread.name.endswith("-handoff"):
            raise kind(KEYS[0])
        return original(thread)

    monkeypatch.setattr(threading.Thread, "start", fail)
    with pytest.raises(kind) as caught, start():
        pytest.fail("Interrupted startup yielded a connection")
    assert type(caught.value) is kind
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    no_secrets("".join(traceback.format_exception(caught.value)))
    assert owner._users == 0
    assert all(
        child.poll() is not None and child.stdin.closed and child.stdout.closed
        for child in children
    )


def test_unconfirmed_child_exit_preserves_ownership_until_confirmed(owned):
    owner, store, start, control, rows, children = owned

    class Child:
        exited = False
        stdin, stdout = io.BytesIO(), io.BytesIO()

        def poll(self):
            return 0 if self.exited else None

        def terminate(self):
            raise private_failure()

        def wait(self, timeout):
            if not self.exited:
                raise subprocess.TimeoutExpired(["synthetic-owned-child"], timeout)
            return 0

    child, lifetime = Child(), agent._ChildLease(owner)
    error = agent._cleanup_child(child, None, {}, set(), lifetime, admitted=False)
    assert_private_error(error)
    assert child.stdin.closed and child.stdout.closed
    with pytest.raises(ConfigurationStoreError) as caught:
        owner.close()
    assert caught.value.code == "STORE_BUSY"
    child.exited = True
    assert agent._cleanup_child(child, None, {}, set(), lifetime, admitted=False) is None
    assert agent._cleanup_child(child, None, {}, set(), lifetime, admitted=False) is None
    assert owner._users == 0


@pytest.mark.parametrize(
    "wire",
    [
        '{"pid":1,"pid":2}',
        '{"pid":1,"\\u0070id":2}',
        '{"nested":{"nonce":1,"nonce":2}}',
        '{"sequence":1.0}',
        '{"sequence":1e0}',
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"sequence":9007199254740992}',
        "{",
        "[" * 10 + "0" + "]" * 10,
        " " * 65537,
    ],
)
def test_python_authentication_json_rejects_ambiguous_tokens(wire):
    with pytest.raises(ConfigurationStoreError) as caught:
        agent._safe_call(lambda: agent._auth_json(wire))
    assert_private_error(caught.value)


def assert_ambiguous_readiness_rejected(start, control):
    control.write_text("escaped-pid")
    rejected = False
    try:
        with start():
            pass
    except ConfigurationStoreError:
        rejected = True
    assert rejected, "Ambiguous authentication was accepted"


def test_negative_control_ordinary_python_json_accepts_ambiguous_proof(owned, monkeypatch):
    owner, store, start, control, rows, children = owned
    with monkeypatch.context() as patch:
        patch.setattr(agent, "_auth_json", json.loads)
        with pytest.raises(AssertionError, match="Ambiguous authentication was accepted"):
            assert_ambiguous_readiness_rejected(start, control)
    assert_ambiguous_readiness_rejected(start, control)


def test_negative_control_unstarted_join_skips_child_cleanup(owned, monkeypatch):
    owner, store, start, control, rows, children = owned
    original_start, cleanup = threading.Thread.start, agent._cleanup_child
    captured = []

    def fail(thread):
        if thread.name.startswith("owned-agent-") and thread.name.endswith("-handoff"):
            raise private_failure()
        return original_start(thread)

    def vulnerable(*args, **kwargs):
        captured.append((args, kwargs))
        args[2]["handoff"].join(timeout=0.1)  # Original unstarted-thread failure.

    try:
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", fail)
            patch.setattr(agent, "_cleanup_child", vulnerable)
            with pytest.raises(RuntimeError):
                with start():
                    pytest.fail("Unexpected connection")
        with pytest.raises(AssertionError, match="Owned startup child was left alive"):
            assert all(child.poll() is not None for child in children), (
                "Owned startup child was left alive"
            )
    finally:
        assert len(captured) == 1
        assert cleanup(*captured[0][0], **captured[0][1]) is None
    assert owner._users == 0
    assert all(child.stdin.closed and child.stdout.closed for child in children)
