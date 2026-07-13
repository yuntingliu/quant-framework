"""Backend process configuration."""
from __future__ import annotations

from alphalab.utils.env import load_env_files

LOADED_ENV_FILES = tuple(load_env_files())

__all__ = ["LOADED_ENV_FILES"]
