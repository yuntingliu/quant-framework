"""Installation-scoped ownership over explicit or durable backend decisions.

JSON owns a shared maintenance lease. An explicitly constructed SQLite choice
owns an exclusive lease for its entire lifetime, excluding JSON users and any
second owner. Components borrow the same choice instead of selecting a backend.
Production selection additionally requires verified committed authority and a
private, process-bound owned Agent. All owners close on their
creating thread, after borrowed API/Agent lifetimes and operations have ended.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from alphalab.utils.configuration_maintenance import (
    agent_access,
    checked_root,
    configuration_access,
    execution_access,
)
from alphalab.utils.configuration_store import (
    ConfigurationStore,
    ConfigurationStoreError,
    _safe_call,
)

_JSON_ROOT = ContextVar("explicit_json_configuration_root", default=None)
_OWNER = ContextVar("explicit_configuration_owner", default=None)
_ID = re.compile(r"[0-9a-f]{32}")


def scoped_json_root():
    """Internal JSON binding scope; never an environment/backend switch."""
    return _JSON_ROOT.get()


def scoped_backend():
    return _OWNER.get()


class ConfigurationBackend:
    """One immutable choice plus an explicitly owned maintenance lifetime.

    No environment variable selects SQLite. json()/sqlite() open leases, never
    initialize or migrate settings. sqlite() requires a known installation ID.
    node_contract() is an intentional private handoff to a trusted owned child;
    keep a borrow alive until that child has actually exited. It is not a bearer
    authorization token for untrusted callers or a published activation record.
    """

    __slots__ = (
        "_root",
        "_mode",
        "_decision",
        "_installation",
        "_store",
        "_lease",
        "_thread",
        "_mutex",
        "_users",
        "_closed",
        "_durable",
    )

    def __init__(self):
        raise TypeError("Use explicit configuration backend factories.")

    @classmethod
    def json(cls, root):
        return cls._open(root, "json", None)

    @classmethod
    def sqlite(cls, root, *, installation_id):
        """Explicit inactive composition; never bypass committed authority."""
        if type(installation_id) is not str or not _ID.fullmatch(installation_id):
            raise ConfigurationStoreError("INVALID_ARGUMENT")
        return cls._open(root, "sqlite", installation_id)

    @classmethod
    def production(cls, root):
        """Select once under OS leases; invalid authority never falls back.

        Preliminary observation grants no use. Construction rereads authority
        under the final lease, avoiding any shared-to-exclusive lock upgrade.
        """
        from alphalab.utils.configuration_decision import read_decision_record

        location = _safe_call(lambda: checked_root(root))
        with configuration_access(location):
            record = read_decision_record(location)
        if record is None or record.as_dict()["state"] == "prepared":
            return cls.json(location)
        value = record.as_dict()
        # Orphaned SDK children and Agent helpers must actually exit before a
        # replacement owner may mint fresh launch credentials or recover jobs.
        with execution_access(location, exclusive=True), agent_access(location, exclusive=True):
            return cls._open(location, "sqlite", value["installation_id"], authority=value)

    @classmethod
    def offline_decision(cls, root):
        """Closed administrative request, explicitly NOT a runtime owner/lease.

        Coordinated shutdown revalidates it under its own exclusive OS fences.
        Observation can coexist with an idle API; no owner is stolen or closed.
        """
        from alphalab.utils.configuration_decision import (
            read_decision_record,
            validate_committed_authority,
        )

        location = _safe_call(lambda: checked_root(root))
        with execution_access(location):
            record = read_decision_record(location)
            durable = record is not None and record.as_dict()["state"] == "committed"
            if durable:
                data = validate_committed_authority(location)
            else:
                with configuration_access(location):
                    cls_check = read_decision_record(location)
                    if cls_check is not None and cls_check.as_dict()["state"] != "prepared":
                        raise ConfigurationStoreError("REVISION_CONFLICT")
                data = None
        value = object.__new__(cls)
        value._root, value._mode = location, "sqlite" if durable else "json"
        value._durable = durable
        value._decision = data["decision_id"] if durable else uuid4().hex
        value._installation = data["installation_id"] if durable else None
        value._store, value._lease = None, None
        value._thread, value._mutex = threading.get_ident(), threading.Lock()
        value._users, value._closed = 0, True
        return value

    @classmethod
    def _open(cls, root, mode, installation_id, *, authority=None):
        from alphalab.utils.configuration_decision import check_backend_authority

        location = _safe_call(lambda: checked_root(root))
        lease = configuration_access(location, exclusive=mode == "sqlite")
        lease.__enter__()
        try:
            check_backend_authority(
                location,
                backend="sqlite"
                if authority is not None
                else ("json" if mode == "json" else "inactive_sqlite"),
            )
            if authority is not None:
                from alphalab.utils.configuration_decision import validate_committed_authority

                if (
                    validate_committed_authority(
                        location,
                        installation_id=installation_id,
                        decision_id=authority["decision_id"],
                    )
                    != authority
                ):
                    raise ConfigurationStoreError("REVISION_CONFLICT")
            store = None
            if mode == "sqlite":
                store = ConfigurationStore.open_existing(location)
                if store.read_snapshot().installation_id != installation_id:
                    raise ConfigurationStoreError("INSTALLATION_MISMATCH")
                if authority is not None:
                    store = store.bind_activation(
                        authority["revision"], authority["evidence"]["selection_kinds"]
                    )
            value = object.__new__(cls)
            value._root, value._mode = location, mode
            value._decision, value._installation = uuid4().hex, installation_id
            value._durable = authority is not None
            if authority is not None:
                value._decision = authority["decision_id"]
            value._store, value._lease = store, lease
            value._thread, value._mutex = threading.get_ident(), threading.Lock()
            value._users, value._closed = 0, False
            if value._durable:
                from alphalab.utils.configuration_owner import publish_owner

                _safe_call(lambda: publish_owner(location, value.public_identity()))
            return value
        except BaseException:
            lease.__exit__(None, None, None)
            raise

    def __repr__(self):
        return "ConfigurationBackend(<private>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Configuration backend ownership cannot be serialized implicitly.")

    @property
    def mode(self):
        return self._mode

    @property
    def root(self):
        return self._root

    @property
    def durable(self):
        return self._durable

    def _check_authority(self):
        from alphalab.utils.configuration_decision import (
            check_backend_authority,
            validate_committed_authority,
        )

        if self._durable:
            validate_committed_authority(
                self._root, installation_id=self._installation, decision_id=self._decision
            )
        else:
            check_backend_authority(
                self._root, backend="json" if self._mode == "json" else "inactive_sqlite"
            )

    @property
    def store(self):

        if self._mode != "sqlite" or self._closed:
            raise ConfigurationStoreError("INVALID_CONFIGURATION")
        self._check_authority()
        return self._store

    def authorize_agent(self, pid, secret):
        """Bind launch proof only to the actual privately spawned child PID."""
        from alphalab.utils.configuration_owner import publish_owner, read_owner

        if not self._durable:
            return
        with self.borrow():

            def action():
                current = read_owner(self._root)
                if current["owner_pid"] != os.getpid() or current["child_pid"] is not None:
                    raise ConfigurationStoreError("STORE_BUSY")
                if type(pid) is not int or pid <= 0:
                    raise ConfigurationStoreError("INVALID_ARGUMENT")
                publish_owner(self._root, self.public_identity(), child_pid=pid, secret=secret)

            with self._mutex:
                _safe_call(action)

    def revoke_agent(self, pid):
        from alphalab.utils.configuration_owner import publish_owner, read_owner

        if not self._durable:
            return

        def action():
            current = read_owner(self._root)
            if current["owner_pid"] != os.getpid() or current["child_pid"] != pid:
                raise ConfigurationStoreError("INSTALLATION_MISMATCH")
            publish_owner(self._root, self.public_identity())

        with self._mutex:
            _safe_call(action)

    def public_identity(self):
        return {
            "backend": self._mode,
            "decision_id": self._decision,
            "installation_id": self._installation,
        }

    def node_contract(self):
        with self.borrow():
            return {
                "version": 1,
                "root": str(self._root),
                "root_key": hashlib.sha256(
                    os.path.normcase(str(self._root)).encode("utf-8")
                ).hexdigest(),
                **self.public_identity(),
            }

    def verify_agent_identity(self, identity):
        if type(identity) is not dict or identity != self.public_identity():
            raise ConfigurationStoreError("INSTALLATION_MISMATCH")

    @contextmanager
    def borrow(self):

        with self._mutex:
            if self._closed:
                raise ConfigurationStoreError("STORE_UNAVAILABLE")
            self._users += 1
        try:
            self._check_authority()
            yield self
        finally:
            with self._mutex:
                self._users -= 1

    @contextmanager
    def json_scope(self):
        if self._mode != "json":
            raise ConfigurationStoreError("INVALID_CONFIGURATION")
        with self.borrow():
            token = _JSON_ROOT.set(self._root)
            try:
                yield
            finally:
                _JSON_ROOT.reset(token)

    @contextmanager
    def scope(self):
        """Carry this actual owner across launcher imports, never a mode toggle."""
        with self.borrow():
            owner_token, root_token = _OWNER.set(self), _JSON_ROOT.set(self._root)
            try:
                yield self
            finally:
                _JSON_ROOT.reset(root_token)
                _OWNER.reset(owner_token)

    def close(self):
        with self._mutex:
            if self._closed:
                return
            if self._users:
                raise ConfigurationStoreError("STORE_BUSY")
            if threading.get_ident() != self._thread:
                raise ConfigurationStoreError("INVALID_ARGUMENT")
            self._closed = True
        self._lease.__exit__(None, None, None)

    def __enter__(self):
        if self._closed:
            raise ConfigurationStoreError("STORE_UNAVAILABLE")
        return self

    def __exit__(self, *_exception):
        self.close()
