"""Explicit private managed-Agent connection, never discovery or activation.

Only this launch context mints a connection. The fixed child receives launch
material over its owned stdin, not argv, environment, health or a connection
file. Each dispatch authenticates a fresh challenge on the very same TCP socket
used to transmit credentials; automatic reconnection and redirects are forbidden.
This assumes trusted local code/OS and private process pipes, not hostile code
running as the same OS user. No persistent backend choice is introduced here.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_store import (
    ConfigurationStoreError,
    _exception_field,
    _interruption_family,
    _raise_redacted,
    _redacted_interruption,
    _safe_call,
)

_ENTRY = Path(__file__).resolve().parents[2] / "integrations/conexus/configuration-agent-host.mjs"
_SLUG = "alphalab-research-agent"
_PROOF = "/api/alphalab/configuration/proof"
_TEST = "/api/alphalab/search/test"
_LIMIT = 65536
_CONTENT_TYPE = "application/vnd.alphalab-agent+json"
_MAX_SEQUENCE = 2**53 - 1


def _unavailable():
    raise ConfigurationStoreError("STORE_UNAVAILABLE")


def _owned_call(action):
    """Redact owner operations without pretending a socket exchange is a store callback.

    Authority reads use their own storage boundaries. Actual store callbacks
    still reject nested reads; no store reentrancy flag is cleared or bypassed.
    """
    code, interruption = "STORE_UNAVAILABLE", None
    try:
        return action()
    except BaseException as error:
        family = _interruption_family(type(error))
        fallback = (
            None
            if family is None
            else family(1 if family is SystemExit else "Agent operation interrupted.")
        )
        try:
            interruption = _redacted_interruption(error)
            if issubclass(type(error), ConfigurationStoreError):
                code = _exception_field(error, "code")
        except BaseException:
            interruption = fallback
    _raise_redacted(interruption if interruption is not None else ConfigurationStoreError(code))


def _auth_json(data):
    """Bounded authentication JSON; decoded aliases and numeric types matter."""

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _unavailable()
            result[key] = value
        return result

    if type(data) not in {str, bytes} or len(data) > _LIMIT:
        _unavailable()
    if type(data) is bytes:
        data = data.decode("utf-8", errors="strict")
    if len(data.encode("utf-8")) > _LIMIT:
        _unavailable()
    value = json.loads(
        data,
        object_pairs_hook=pairs,
        parse_float=lambda _: _unavailable(),
        parse_constant=lambda _: _unavailable(),
    )
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 8 or nodes > 1024:
            _unavailable()
        if type(item) is dict:
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is int and not -_MAX_SEQUENCE <= item <= _MAX_SEQUENCE:
            _unavailable()
    return value


def _origin(value):
    if type(value) is not str:
        _unavailable()
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or value != f"http://127.0.0.1:{parsed.port}"
    ):
        _unavailable()
    return parsed.port


class OwnedAgentConnection:
    """Private, nonserializable capability; descriptors cannot construct one.

    Lifecycle mutation is internal. The root, process, endpoint and launch
    identity are fixed at minting; no setters or ambient lookups are provided.
    Explicit close invalidates first, interrupts sockets, then waits for the
    owned child. The launch context keeps the backend borrowed until actual exit.
    """

    __slots__ = (
        "__owner",
        "__child",
        "__origin",
        "__token",
        "__secret",
        "__identity",
        "__mutex",
        "__operation",
        "__closed",
        "__active",
        "__timeout",
        "__sequence",
    )

    def __init__(self):
        raise TypeError("Use the explicit managed Agent launch context.")

    def __repr__(self):
        return "OwnedAgentConnection(<private>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Private Agent connections cannot be serialized implicitly.")

    def require_owner(self, owner):
        return _owned_call(lambda: self._check(owner))

    def _check(self, owner):
        if type(owner) is not ConfigurationBackend:
            _unavailable()
        with self.__mutex, owner.borrow():
            if owner is not self.__owner or self.__closed or self.__child.poll() is not None:
                _unavailable()

    def _interrupt(self):
        with self.__mutex:
            wire = self.__active
            if wire is not None:
                try:
                    wire.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _exchange(self, settings=None):
        # Serialize private operations with a bounded wait; no process-global
        # registry or shared HTTP pool can rebind an authenticated request.
        def action():
            if not self.__operation.acquire(timeout=self.__timeout):
                _unavailable()
            connection = None
            timer = None
            try:
                self._check(self.__owner)
                connection = http.client.HTTPConnection(
                    "127.0.0.1", _origin(self.__origin), timeout=self.__timeout
                )
                connection.auto_open = 0
                connection.connect()  # Only connection creation, before proof.
                with self.__mutex:
                    if self.__closed:
                        _unavailable()
                    # Keep the actual socket: getresponse() may detach it from
                    # HTTPConnection for a close-delimited response while the
                    # response's file still owns it. The deadline must also
                    # interrupt a trickling body in that state.
                    self.__active = connection.sock
                timer = threading.Timer(self.__timeout, self._interrupt)
                timer.daemon = True
                timer.start()
                if self.__sequence >= _MAX_SEQUENCE:
                    _unavailable()
                self.__sequence += 1
                sequence = self.__sequence
                nonce = secrets.token_hex(32)
                request_mac = hmac.new(
                    bytes.fromhex(self.__secret),
                    json.dumps(
                        ["request", sequence, nonce, self.__token], separators=(",", ":")
                    ).encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                connection.request(
                    "POST",
                    _PROOF,
                    json.dumps({"sequence": sequence, "nonce": nonce, "mac": request_mac}),
                    {"Content-Type": _CONTENT_TYPE},
                )
                response = connection.getresponse()
                body = response.read(_LIMIT + 1)
                if response.status != 200 or len(body) > _LIMIT:
                    _unavailable()
                payload = _auth_json(body)
                expected = {**self.__identity, "nonce": nonce, "sequence": sequence}
                # Fixed ordered array is the cross-language MAC serialization.
                message = json.dumps(
                    [*expected.values(), self.__token], ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                signature = hmac.new(
                    bytes.fromhex(self.__secret), message, hashlib.sha256
                ).hexdigest()
                if (
                    type(payload) is not dict
                    or type(payload.get("mac")) is not str
                    or not hmac.compare_digest(payload.pop("mac"), signature)
                    or any(
                        type(payload.get(key)) is not type(value) for key, value in expected.items()
                    )
                    or payload != expected
                ):
                    _unavailable()
                # A closed keepalive socket MUST NOT reconnect after proof.
                if connection.sock is None:
                    _unavailable()
                self._check(self.__owner)
                if settings is None:
                    return None
                connection.request(
                    "POST",
                    _TEST,
                    json.dumps(settings),
                    {
                        "Content-Type": _CONTENT_TYPE,
                        "Authorization": "Bearer " + self.__token,
                        "X-AlphaLab-Proof-Sequence": str(sequence),
                    },
                )
                response = connection.getresponse()
                body = response.read(_LIMIT + 1)
                if response.status != 200 or len(body) > _LIMIT:
                    _unavailable()
                payload = _auth_json(body)
                self._check(self.__owner)
                # Public results are static. Never reflect arbitrary Agent
                # diagnostics, provider response metadata, or credential values.
                ok = (
                    type(payload) is dict
                    and type(payload.get("result")) is dict
                    and payload["result"].get("success") is True
                )
                return {
                    "ok": ok,
                    "message": "Search responded successfully."
                    if ok
                    else "The local search test failed.",
                }
            finally:
                if timer is not None:
                    timer.cancel()
                    timer.join()
                with self.__mutex:
                    self.__active = None
                if connection is not None:
                    connection.close()
                self.__operation.release()

        # Never expose network/parser exceptions or the private request body in
        # accessible exception chains. Preserve redacted interruption families.
        return _owned_call(action)

    def test_search(self, owner, settings):
        self.require_owner(owner)
        return self._exchange(settings)

    def authenticate(self, owner):
        self.require_owner(owner)
        return self._exchange()

    def close(self):
        with self.__mutex:
            self.__closed = True
        self._interrupt()


class _ChildLease:
    """Counted child borrow without a generator finalizer that could release it.

    Uses the backend's existing private borrow counter. If neither cleanup nor
    an observer can confirm child exit, dropping this record must NOT release
    ownership. That exceptional owner stays fenced, requiring operator review.
    """

    def __init__(self, owner):
        self.owner, self.lock, self.held = owner, threading.Lock(), True
        with owner._mutex:
            if owner._closed:
                _unavailable()
            owner._users += 1

    def release_if_exited(self, child):
        if child.poll() is None:
            return False
        with self.lock:
            if self.held:
                with self.owner._mutex:
                    self.held = False
                    self.owner._users -= 1
        return True


def _cleanup_child(child, connection, helpers, started, lifetime, *, admitted):
    """Attempt every cleanup step; return only a sanitized failure, if any."""
    errors = []

    def attempt(action, default=None):
        try:
            return _safe_call(action)
        except BaseException as error:
            errors.append(error)  # Already redacted by _safe_call.
            return default

    def wait(timeout):
        try:
            child.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    def stop():
        if child.poll() is None:
            try:
                child.terminate()
            except ProcessLookupError:
                pass  # The owned child exited between poll and terminate.

    def was_started(name, thread):
        # A start interrupted after native creation may have set ident even
        # though start() did not return. Never join an actually unstarted thread.
        return name in started or thread.ident is not None

    if connection is not None:
        attempt(connection.close)
    if child is None:
        return errors[0] if errors else None
    writer = helpers.get("handoff")
    writing = writer is not None and was_started("handoff", writer) and writer.is_alive()
    if not admitted or writing:
        attempt(stop)
        attempt(lambda: wait(5))
    else:
        attempt(child.stdin.close)
        if not attempt(lambda: wait(5), False):
            attempt(stop)
            attempt(lambda: wait(5))
    # Raw, unbuffered owned pipes can close without waiting on a helper's
    # buffered-I/O lock, including when termination could not be confirmed.
    for pipe in (child.stdin, child.stdout):
        if pipe is not None:
            attempt(pipe.close)
    for name, thread in helpers.items():
        if was_started(name, thread):
            attempt(lambda thread=thread: thread.join(timeout=1))
            if attempt(thread.is_alive, True):
                errors.append(ConfigurationStoreError("STORE_UNAVAILABLE"))
    exited = attempt(lambda: child.poll() is not None, False)
    if exited and lifetime is not None:
        attempt(lambda: lifetime.release_if_exited(child))
    if not exited:
        errors.append(ConfigurationStoreError("STORE_UNAVAILABLE"))
    return errors[0] if errors else None


@contextmanager
def managed_configuration_agent(backend, *, node=None, startup_timeout=15, request_timeout=5):
    """Launch the fixed inactive host through private stdin, holding its owner.

    This is trusted Python composition only, not an API or deployment launcher.
    No bundle/dependency installation, legacy discovery or environment selection
    occurs. Child EOF initiates graceful shutdown; only our child may be stopped.
    """
    if type(backend) is not ConfigurationBackend or backend.mode != "sqlite":
        _unavailable()
    if any(
        type(v) not in {int, float} or not 0.1 <= v <= 60
        for v in (startup_timeout, request_timeout)
    ):
        _unavailable()
    with backend.borrow():
        child = None
        connection = None
        helpers, started, lifetime, admitted = {}, set(), None, False

        def start(name, action):
            thread = _safe_call(
                lambda: threading.Thread(
                    target=action, daemon=True, name=f"owned-agent-{child.pid}-{name}"
                )
            )
            helpers[name] = thread
            _safe_call(thread.start)
            started.add(name)

        try:
            contract = backend.node_contract()
            secret, token, launch = (
                secrets.token_hex(32),
                secrets.token_hex(32),
                secrets.token_hex(32),
            )
            handoff = {"contract": contract, "secret": secret, "token": token, "launch": launch}
            wire_handoff = json.dumps(handoff).encode("utf-8") + b"\n"
            if len(wire_handoff) > _LIMIT:
                _unavailable()
            executable = node or shutil.which("node")
            if not executable:
                _unavailable()
            child = _safe_call(
                lambda: subprocess.Popen(
                    [str(executable), str(_ENTRY)],
                    cwd=_ENTRY.parents[2],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env={**os.environ, "ALPHALAB_AUTHORITY_PYTHON": sys.executable},
                    bufsize=0,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )
            ready = queue.Queue(maxsize=1)
            lifetime = _safe_call(lambda: _ChildLease(backend))

            def consume():
                try:
                    line = child.stdout.readline(_LIMIT + 1)
                    ready.put_nowait(line if len(line) <= _LIMIT else b"")
                    # Drain owned stdout without logging private child output.
                    while child.stdout.read(4096):
                        pass
                except (OSError, ValueError, queue.Full):
                    pass

            def observe_exit():
                try:
                    child.wait()
                    lifetime.release_if_exited(child)
                except BaseException:
                    # Never print private failures from a daemon thread. The
                    # counted lease stays held until cleanup verifies OS exit.
                    pass

            start("exit", observe_exit)
            start("stdout", consume)

            def send_handoff():
                try:
                    remaining = memoryview(wire_handoff)
                    while remaining:
                        count = child.stdin.write(remaining)
                        if not count:
                            _unavailable()
                        remaining = remaining[count:]
                    child.stdin.flush()
                except BaseException:
                    # A broken private pipe never emits raw exceptions or
                    # bypasses the bounded startup wait on the caller thread.
                    try:
                        ready.put_nowait(b"")
                    except queue.Full:
                        pass

            start("handoff", send_handoff)
            descriptor = _safe_call(lambda: _auth_json(ready.get(timeout=startup_timeout)))
            if type(descriptor) is not dict or set(descriptor) != {"origin"}:
                _unavailable()
            _safe_call(lambda: _origin(descriptor["origin"]))
            connection = object.__new__(OwnedAgentConnection)
            connection._OwnedAgentConnection__owner = backend
            connection._OwnedAgentConnection__child = child
            connection._OwnedAgentConnection__origin = descriptor["origin"]
            connection._OwnedAgentConnection__token = token
            connection._OwnedAgentConnection__secret = secret
            connection._OwnedAgentConnection__identity = {
                "version": 1,
                **contract,
                "origin": descriptor["origin"],
                "slug": _SLUG,
                "pid": child.pid,
                "launch": launch,
            }
            connection._OwnedAgentConnection__mutex = threading.Lock()
            connection._OwnedAgentConnection__operation = threading.Lock()
            connection._OwnedAgentConnection__closed = False
            connection._OwnedAgentConnection__active = None
            connection._OwnedAgentConnection__timeout = request_timeout
            connection._OwnedAgentConnection__sequence = 0
            connection._exchange()  # Descriptor/health is never authorization.
            admitted = True
            yield connection
        finally:
            pending = sys.exc_info()[1]
            error = _cleanup_child(child, connection, helpers, started, lifetime, admitted=admitted)
            if error is not None:
                interruption = (
                    _safe_call(lambda: _redacted_interruption(pending))
                    if pending is not None
                    else None
                )
                _raise_redacted(interruption or error)
