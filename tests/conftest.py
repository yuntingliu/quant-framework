"""Isolate default stores before test collection imports application modules."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# An autouse fixture would run too late: repository defaults are bound at import.
# Spawned strategy workers inherit these paths, as do unpatched API services.
_state = tempfile.TemporaryDirectory(prefix="alphalab-tests-", ignore_cleanup_errors=True)
_previous = {}
for _variable, _directory in (
    ("ALPHALAB_APP_DATA_DIR", "app"),
    ("ALPHALAB_CACHE_DIR", "cache"),
    ("ALPHALAB_RUNTIME_DIR", "runtime"),
):
    _previous[_variable] = os.environ.get(_variable)
    os.environ[_variable] = str(Path(_state.name) / _directory)


def pytest_unconfigure(config):
    for variable, value in _previous.items():
        if value is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = value
    _state.cleanup()
