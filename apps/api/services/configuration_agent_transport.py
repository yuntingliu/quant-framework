"""HTTPX adapter with same-socket owner authentication and no ambient origin."""

from urllib.parse import urlsplit

import anyio
import httpx

from alphalab.utils.configuration_store import ConfigurationStoreError


class _Body(httpx.AsyncByteStream):
    def __init__(self, stream, release):
        self._stream, self._release = stream, release

    async def __aiter__(self):
        while True:
            failed = False
            try:
                data = await anyio.to_thread.run_sync(self._stream.read, abandon_on_cancel=True)
            except ConfigurationStoreError:
                failed = True
            if failed:
                raise httpx.ReadError("The owned Agent response is unavailable.")
            if not data:
                return
            yield data

    async def aclose(self):
        from alphalab.utils.configuration_agent import _owned_call

        try:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(lambda: _owned_call(self._stream.close))
        finally:
            self._release(self)


class OwnedAgentTransport(httpx.AsyncBaseTransport):
    def __init__(self, owner, agent):
        self._owner, self._agent, self._bodies = owner, agent, set()

    async def handle_async_request(self, request):
        failed, stream = False, None
        try:
            origin, _, _ = self._agent.extract_http_private(self._owner)
            expected = urlsplit(origin)
            if (
                request.url.scheme != "http"
                or request.url.host != "127.0.0.1"
                or request.url.port != expected.port
                or request.url.username
                or request.url.password
            ):
                raise ConfigurationStoreError("INSTALLATION_MISMATCH")
            body = await request.aread()
            headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower() in {"accept", "content-type", "authorization", "last-event-id"}
            }
            timeout = request.extensions.get("timeout", {}).get("read", 5.0)
            stream = await anyio.to_thread.run_sync(
                self._agent.http_request,
                self._owner,
                request.method,
                request.url.raw_path.decode("ascii"),
                headers,
                body,
                timeout,
            )
        except (ConfigurationStoreError, UnicodeError, ValueError):
            failed = True
        if failed:
            raise httpx.ConnectError("The owned Agent is unavailable.")
        result = _Body(stream, self._bodies.discard)
        self._bodies.add(result)
        return httpx.Response(stream.status, headers=stream.headers, stream=result)

    async def aclose(self):
        from alphalab.utils.configuration_store import _raise_redacted

        failed = None
        for body in tuple(self._bodies):
            try:
                await body.aclose()
            except BaseException as error:
                failed = error
        self._bodies.clear()
        if failed is not None:
            _raise_redacted(failed)


def agent_http_client(**options):
    from apps.api.services.configuration_production_service import current_configuration

    value = current_configuration()
    if value is not None and value.backend.mode == "sqlite":
        if value.agent is None:
            raise ConfigurationStoreError("STORE_UNAVAILABLE")
        options = {
            **options,
            "transport": OwnedAgentTransport(value.backend, value.agent),
            "follow_redirects": False,
            "trust_env": False,
        }
    else:
        from alphalab.utils.configuration_maintenance import json_configuration_access
        from alphalab.utils.private_settings import runtime_root

        with json_configuration_access(runtime_root()):
            pass
    return httpx.AsyncClient(**options)
