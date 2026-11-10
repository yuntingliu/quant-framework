"""Private JSON settings helpers with atomic writes and strict validation."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Callable, TypeVar

from alphalab.utils.configuration_backend import scoped_json_root
from alphalab.utils.configuration_maintenance import json_configuration_access
from alphalab.utils.paths import RUNTIME_DIR

T = TypeVar("T")

_GLOBAL_LOCK = threading.RLock()


def runtime_root() -> Path:
    """Use the explicit JSON binding root, otherwise the existing environment root."""

    scoped = scoped_json_root()
    return (
        scoped
        if scoped is not None
        else Path(os.getenv("ALPHALAB_RUNTIME_DIR", str(RUNTIME_DIR))).expanduser()
    )


def app_secret_path(filename: str) -> Path:
    """Return the standard application-owned secret-settings path."""

    return runtime_root() / "app" / "secrets" / filename


def read_json_settings(
    path: Path,
    *,
    default: Callable[[], object],
    validate: Callable[[object], T],
) -> T:
    """Read one validated JSON settings file, or its validated default value."""

    with _profile_access(path) as path:
        if not path.exists():
            return validate(default())
        value = json.loads(path.read_text(encoding="utf-8"))
        return validate(value)


def write_json_settings(
    path: Path,
    value: object,
    *,
    validate: Callable[[object], T],
) -> T:
    """Validate and atomically write one JSON settings file."""

    with _profile_access(path) as path, _GLOBAL_LOCK:
        validated = validate(value)
        _prepare_private_directory(path.parent)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}-",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(validated, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, path)
            _harden_private_file(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    return validated


@contextmanager
def _profile_access(path):
    # Resolve the location actually used below, including Windows case/8.3
    # aliases and relative components. Never guard one spelling and open another.
    if os.name == "nt" and ":" in Path(path).name:
        from alphalab.utils.configuration_store import ConfigurationStoreError

        raise ConfigurationStoreError("UNSAFE_STORAGE")
    path = Path(os.path.realpath(path))
    if path.exists() and path.stat().st_nlink != 1:
        from alphalab.utils.configuration_store import ConfigurationStoreError

        raise ConfigurationStoreError("UNSAFE_STORAGE")
    native = os.path.normcase(path.name) in {
        "model-providers.json",
        "search-providers.json",
        "rqdata-profiles.json",
    }
    with json_configuration_access(path.parents[2]) if native else nullcontext():
        yield path


def _prepare_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def _harden_private_file(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)


__all__ = ["app_secret_path", "read_json_settings", "runtime_root", "write_json_settings"]
