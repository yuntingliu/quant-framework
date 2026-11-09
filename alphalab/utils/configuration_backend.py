"""Explicit, installation-scoped runtime choice; no persistent activation API.

JSON owns a shared maintenance lease. An explicitly constructed SQLite choice
owns an exclusive lease for its entire lifetime, excluding JSON users and any
second owner. Components borrow the same choice instead of selecting a backend.
This conservative single-owner SQLite composition is preparation only, not the
future multi-process production activation protocol. All owners close on their
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

from alphalab.utils.configuration_maintenance import checked_root, configuration_access
from alphalab.utils.configuration_store import (
    ConfigurationStore,
    ConfigurationStoreError,
    _safe_call,
)

_JSON_ROOT = ContextVar("explicit_json_configuration_root", default=None)
_ID = re.compile(r"[0-9a-f]{32}")


def scoped_json_root():
    """Internal JSON binding scope; never an environment/backend switch."""
    return _JSON_ROOT.get()


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
    )

    def __init__(self):
        raise TypeError("Use explicit configuration backend factories.")

    @classmethod
    def json(cls, root):
        return cls._open(root, "json", None)

    @classmethod
    def sqlite(cls, root, *, installation_id):
        if type(installation_id) is not str or not _ID.fullmatch(installation_id):
            raise ConfigurationStoreError("INVALID_ARGUMENT")
        return cls._open(root, "sqlite", installation_id)

    @classmethod
    def _open(cls, root, mode, installation_id):
        location = _safe_call(lambda: checked_root(root))
        lease = configuration_access(location, exclusive=mode == "sqlite")
        lease.__enter__()
        try:
            store = None
            if mode == "sqlite":
                store = ConfigurationStore.open_existing(location)
                if store.read_snapshot().installation_id != installation_id:
                    raise ConfigurationStoreError("INSTALLATION_MISMATCH")
            value = object.__new__(cls)
            value._root, value._mode = location, mode
            value._decision, value._installation = uuid4().hex, installation_id
            value._store, value._lease = store, lease
            value._thread, value._mutex = threading.get_ident(), threading.Lock()
            value._users, value._closed = 0, False
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
    def store(self):
        if self._mode != "sqlite" or self._closed:
            raise ConfigurationStoreError("INVALID_CONFIGURATION")
        return self._store

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
