"""Backend process configuration."""

from __future__ import annotations

from alphalab.utils.configuration_backend import scoped_backend
from alphalab.utils.configuration_decision import check_backend_authority, read_decision_record
from alphalab.utils.configuration_maintenance import configuration_access
from alphalab.utils.env import load_env_files
from alphalab.utils.private_settings import runtime_root

_owner = scoped_backend()
with _owner.borrow() if _owner is not None else configuration_access(runtime_root()):
    _decision = read_decision_record(runtime_root())
    check_backend_authority(
        runtime_root(),
        backend="sqlite"
        if _decision is not None and _decision.as_dict()["state"] == "committed"
        else "json",
    )
    # Hosting/authentication environment remains compatible. SQLite profile
    # services receive an empty environment and never resolve ambient secrets.
    LOADED_ENV_FILES = tuple(load_env_files())
del _owner, _decision

__all__ = ["LOADED_ENV_FILES"]
