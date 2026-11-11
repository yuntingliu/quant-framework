"""Private authenticated HTTP streams for the owned production Agent only."""

from __future__ import annotations

import re
import socket
import threading
from urllib.parse import unquote, urlsplit

from alphalab.utils.configuration_store import ConfigurationStoreError


class _HttpRequest:
    __slots__ = ("method", "path", "headers", "body", "read_timeout")

    def __init__(self, method, path, headers, body, read_timeout):
        if type(path) is not str or re.search(r"%(?![0-9a-fA-F]{2})", path):
            raise ConfigurationStoreError("INVALID_ARGUMENT")
        health = method == "GET" and path == "/health"
        decoded = path
        for _ in range(6):
            parts = urlsplit(decoded)
            if (
                parts.scheme
                or parts.netloc
                or (not health and not parts.path.startswith("/api/public/"))
                or "#" in decoded
                or "\\" in decoded
                or any(ord(char) < 32 for char in decoded)
                or any(part in {".", ".."} for part in parts.path.split("/"))
            ):
                raise ConfigurationStoreError("INVALID_ARGUMENT")
            expanded = unquote(decoded)
            if expanded == decoded:
                break
            decoded = expanded
        else:
            raise ConfigurationStoreError("INVALID_ARGUMENT")
        if (
            method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
            or type(body) is not bytes
            or len(body) > 16 * 1024 * 1024
        ):
            raise ConfigurationStoreError("INVALID_ARGUMENT")
        if read_timeout is not None and (
            type(read_timeout) not in {int, float} or not 0 < read_timeout <= 3600
        ):
            raise ConfigurationStoreError("INVALID_ARGUMENT")
        if type(headers) is not dict or any(
            type(key) is not str
            or not re.fullmatch(r"[A-Za-z0-9-]+", key)
            or key.lower() not in {"accept", "content-type", "authorization", "last-event-id"}
            or type(value) is not str
            or len(value) > 16384
            or "\r" in value
            or "\n" in value
            for key, value in headers.items()
        ):
            raise ConfigurationStoreError("INVALID_ARGUMENT")
        self.method, self.path, self.headers, self.body = method, path, headers.copy(), body
        self.read_timeout = read_timeout

    def __repr__(self):
        return "OwnedHttpRequest(<private>)"

    def __reduce_ex__(self, _protocol):
        raise TypeError("Private Agent HTTP requests cannot be serialized.")


class _HttpStream:
    """Retain ownership until actual reads retire, even after cancellation."""

    __slots__ = (
        "_connection",
        "_response",
        "_socket",
        "_borrow",
        "_release",
        "_closed",
        "_active",
        "_lock",
        "status",
        "headers",
    )

    def __init__(self, connection, response, owner, release):
        self._connection, self._response, self._socket = connection, response, connection.sock
        self._borrow = owner.borrow()
        self._borrow.__enter__()
        self._release, self._closed, self._active = release, False, 0
        self._lock = threading.Lock()
        self.status, self.headers = response.status, response.getheaders()

    def __repr__(self):
        return "OwnedHttpStream(<private>)"

    def __reduce_ex__(self, _protocol):
        raise TypeError("Private Agent HTTP responses cannot be serialized.")

    def read(self):
        from alphalab.utils.configuration_agent import _owned_call

        with self._lock:
            if self._closed:
                return b""
            self._active += 1
        try:
            return _owned_call(lambda: self._response.read1(65536))
        finally:
            with self._lock:
                self._active -= 1
                if self._closed and not self._active:
                    self._finish()

    def _finish(self):
        if self._borrow is not None:
            self._release(self)
            self._borrow.__exit__(None, None, None)
            self._borrow = None

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            if self._socket is not None:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self._response.close()
        finally:
            self._connection.close()
            with self._lock:
                if not self._active:
                    self._finish()
