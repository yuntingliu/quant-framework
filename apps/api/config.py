"""Backend process configuration."""

from __future__ import annotations

from alphalab.utils.configuration_maintenance import json_configuration_access
from alphalab.utils.env import load_env_files
from alphalab.utils.private_settings import runtime_root

with json_configuration_access(runtime_root()):
    LOADED_ENV_FILES = tuple(load_env_files())

__all__ = ["LOADED_ENV_FILES"]
