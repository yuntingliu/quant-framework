"""Explicit, inactive RQData job bindings over the SQLite profile services.

No global manager or production route constructs these objects. Capture once
at the job execution boundary, then give its acquirer to the existing sync
service. Credentials never enter a persisted job request or process environment.
"""

from __future__ import annotations

import importlib

from alphalab.dataio.providers.rq import RQDataClient, RQDataConfig
from alphalab.dataio.rq_sync import RQAcquirer
from alphalab.dataio.sync import RQSyncService
from apps.api.services.configuration_profile_service import ConfigurationProfileServices, _guard


class _PrivateRQModule:
    """Lazy explicit SDK dependency with sanitized failures, never JSON/env reads."""

    __slots__ = ("_module",)

    def __init__(self, module):
        self._module = module

    @_guard
    def __getattr__(self, name):
        if self._module is None:
            self._module = importlib.import_module("rqdatac")
        value = getattr(self._module, name)
        return _guard(value) if callable(value) else value

    def __repr__(self):
        return "PrivateRQModule(<private>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Private RQData bindings cannot be serialized implicitly.")


class RQJobConfiguration:
    """One job's immutable credential capture; each client is fresh and lazy.

    This does not isolate the real SDK's global connection state. Future cutover
    must keep the single execution lane/process isolation; never overlap two
    differently configured clients on one live rqdatac module.
    """

    __slots__ = ("_configuration", "_revision", "_installation_id")

    @_guard
    def __init__(self, services: ConfigurationProfileServices):
        if type(services) is not ConfigurationProfileServices:
            raise ValueError
        snapshot = services.read_snapshot()
        self._configuration = snapshot.active_config("rqdata")
        self._revision, self._installation_id = snapshot.revision, snapshot.installation_id

    @property
    def revision(self):
        return self._revision

    @property
    def installation_id(self):
        return self._installation_id

    @_guard
    def client(self, *, module=None):
        values = self._configuration.extract_private()
        config = RQDataConfig(
            user=values["RQ_USER"], password=values["RQ_PASSWORD"], host=values["RQ_HOST"]
        )
        return RQDataClient(config, module=_PrivateRQModule(module))

    @_guard
    def sync_service(self, root, *, module=None):
        """Bind the existing sync runner explicitly, without scheduling a job."""
        return RQSyncService(root, acquirer=RQAcquirer(self.client(module=module)))

    def __repr__(self):
        return "RQJobConfiguration(<private>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Private RQData bindings cannot be serialized implicitly.")
