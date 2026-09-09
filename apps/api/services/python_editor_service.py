"""Local Python editor capabilities and bounded LSP process bridging."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal

from alphalab.dataio.recipes import DataRecipeError, inspect_data_recipe_source
from alphalab.strategy.repository import StrategyRepository
from alphalab.strategy.source import (
    StrategySourceError,
    assemble_strategy_source,
    inspect_strategy_source,
)
from alphalab.utils.paths import RUNTIME_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIRROR_ROOT = RUNTIME_DIR / "editor"
MAX_LSP_MESSAGE_BYTES = 4_000_000
_SAFE_DOCUMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class LanguageServer:
    id: Literal["pyrefly", "ruff"]
    argv: tuple[str, ...] | None
    version: str | None

    @property
    def available(self) -> bool:
        return self.argv is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "available": self.available,
            "version": self.version,
            "command": list(self.argv) if self.argv else None,
        }


@dataclass
class LanguageServerProcess:
    """LSP child isolated from the ASGI server's event-loop policy.

    Uvicorn reload mode installs a selector loop on Windows, and that loop
    cannot create asyncio subprocesses. Blocking pipe I/O is therefore kept in
    worker threads so editor tooling behaves identically with and without
    ``--reload``.
    """

    process: subprocess.Popen[bytes]

    @property
    def returncode(self) -> int | None:
        return self.process.poll()

    async def write_message(self, message: str) -> None:
        payload = _encode_lsp_message(message)
        await asyncio.to_thread(self._write, payload)

    def _write(self, payload: bytes) -> None:
        writer = self.process.stdin
        if writer is None:
            raise EOFError("language server stdin is closed")
        writer.write(payload)
        writer.flush()

    async def read_message(self) -> str:
        reader = self.process.stdout
        if reader is None:
            raise EOFError("language server stdout is closed")
        return await asyncio.to_thread(_read_lsp_message_blocking, reader)

    async def drain_stderr(self) -> None:
        reader = self.process.stderr
        if reader is not None:
            await asyncio.to_thread(_drain_binary_stream, reader)

    async def wait(self) -> int:
        return await asyncio.to_thread(self.process.wait)

    async def close(self) -> None:
        await asyncio.to_thread(_close_process, self.process)


def _executable(name: str) -> str | None:
    suffix = ".exe" if os.name == "nt" else ""
    adjacent = Path(sys.executable).with_name(f"{name}{suffix}")
    if adjacent.is_file():
        return str(adjacent)
    return shutil.which(name)


def _module_command(name: str) -> tuple[str, ...] | None:
    if importlib.util.find_spec(name) is None:
        return None
    return (sys.executable, "-m", name)


def language_server(server_id: str) -> LanguageServer:
    if server_id not in {"pyrefly", "ruff"}:
        raise KeyError(server_id)
    executable = _executable(server_id)
    base = (executable,) if executable else _module_command(server_id)
    argv = (
        (*base, "lsp") if base and server_id == "pyrefly" else ((*base, "server") if base else None)
    )
    version = _server_version(base) if base else None
    return LanguageServer(server_id, argv, version)  # type: ignore[arg-type]


def _server_version(base: tuple[str, ...]) -> str | None:
    try:
        result = subprocess.run(
            [*base, "--version"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    rendered = (result.stdout or result.stderr).strip().splitlines()
    return rendered[0][:120] if rendered else None


def capabilities() -> dict[str, Any]:
    servers = {name: language_server(name).to_dict() for name in ("pyrefly", "ruff")}
    return {
        "python_executable": sys.executable,
        "workspace_uri": PROJECT_ROOT.resolve().as_uri(),
        "mirror_root": str(MIRROR_ROOT),
        "rqdata_operations": _rqdata_operations(),
        "servers": servers,
        "ready": all(item["available"] for item in servers.values()),
        "install_hint": f'"{sys.executable}" -m pip install -e ".[dev]"',
    }


def _rqdata_operations() -> list[dict[str, str]]:
    try:
        import rqdatac  # type: ignore[import-not-found]
    except ImportError:
        return []
    operations: list[dict[str, str]] = []
    for name in sorted(item for item in dir(rqdatac) if not item.startswith("_")):
        try:
            value = getattr(rqdatac, name)
        except Exception:
            continue
        if not callable(value):
            continue
        try:
            signature = str(inspect.signature(value))
        except (TypeError, ValueError):
            signature = "(...)"
        documentation = inspect.getdoc(value) or ""
        operations.append(
            {
                "name": name,
                "signature": signature[:500],
                "documentation": documentation.splitlines()[0][:300] if documentation else "",
            }
        )
    return operations


def mirror_document(kind: str, document_id: str, source: str) -> dict[str, str]:
    filenames = {
        "strategy": "strategy.py",
        "function": "function.py",
        "factor": "factor.py",
        "data": "recipe.py",
        "validation": "validation.py",
    }
    if kind not in filenames:
        raise ValueError("document kind must be strategy, function, factor, data, or validation")
    if not _SAFE_DOCUMENT_ID.fullmatch(document_id):
        raise ValueError("invalid Python editor document id")
    if len(source.encode("utf-8")) > 300_000:
        raise ValueError("Python editor source must not exceed 300000 bytes")
    target = (MIRROR_ROOT / kind / document_id / filenames[kind]).resolve()
    if MIRROR_ROOT.resolve() not in target.parents:
        raise ValueError("Python editor mirror escaped its runtime root")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.read_text(encoding="utf-8") != source:
        target.write_text(source, encoding="utf-8", newline="\n")
    return {
        "kind": kind,
        "document_id": document_id,
        "uri": target.as_uri(),
        "workspace_uri": PROJECT_ROOT.resolve().as_uri(),
    }


def source_diagnostics(
    kind: str, source: str, *, document_id: str | None = None
) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    try:
        if kind == "strategy":
            diagnostic_source = source
            if document_id:
                project_id = document_id.split(".", 1)[0]
                repo = StrategyRepository()
                try:
                    factors = [
                        item["source"]
                        for item in repo.list_source_units(project_id)
                        if item["kind"] == "factor"
                    ]
                finally:
                    repo.close()
                diagnostic_source, _ = assemble_strategy_source(source, factors)
            inspection = inspect_strategy_source(diagnostic_source)
        elif kind == "function":
            _validate_function_document(source)
            return {"valid": True, "diagnostics": diagnostics}
        elif kind == "factor":
            _validate_factor_document(source)
            return {"valid": True, "diagnostics": diagnostics}
        elif kind == "data":
            inspection = inspect_data_recipe_source(source)
        elif kind == "validation":
            from alphalab.validation.source import inspect_validation_source

            inspection = inspect_validation_source(source)
        else:
            raise ValueError(
                "diagnostic kind must be strategy, function, factor, data, or validation"
            )
    except (StrategySourceError, DataRecipeError, SyntaxError, ValueError) as exc:
        diagnostics.append(
            {
                "severity": "error",
                "line": _error_line(exc),
                "column": int(getattr(exc, "offset", 1) or 1),
                "code": getattr(exc, "phase", "contract"),
                "message": str(exc),
                "source": "AlphaLab SDK",
            }
        )
        return {"valid": False, "diagnostics": diagnostics}
    for warning in inspection.warnings:
        diagnostics.append(
            {
                "severity": "warning",
                "line": max(1, int(warning.get("line") or 1)),
                "column": 1,
                "code": str(warning.get("code") or "contract"),
                "message": str(warning.get("message") or "AlphaLab SDK warning"),
                "source": "AlphaLab SDK",
            }
        )
    return {"valid": True, "diagnostics": diagnostics}


def _validate_factor_document(source: str) -> None:
    """Validate the isolated editor projection; full-module validation happens on save."""

    tree = ast.parse(source)
    functions = [item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(tree.body) != 1 or len(functions) != 1:
        raise StrategySourceError(
            "factor editor source must contain exactly one @factor function",
            phase="register",
        )
    function = functions[0]
    registered = False
    for decorator in function.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (
            isinstance(target, ast.Name)
            and target.id == "factor"
            or isinstance(target, ast.Attribute)
            and target.attr == "factor"
        ):
            registered = True
            break
    if not registered:
        raise StrategySourceError(
            "factor editor source must remain a registered @factor function",
            phase="register",
        )


def _validate_function_document(source: str) -> None:
    """Validate one isolated registered strategy function before module-level save."""

    tree = ast.parse(source)
    functions = [item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(tree.body) != 1 or len(functions) != 1:
        raise StrategySourceError(
            "function editor source must contain exactly one registered strategy function",
            phase="register",
        )
    allowed = {"universe", "factor", "schedule", "signal", "portfolio", "on_event", "execution"}
    for decorator in functions[0].decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id in allowed:
            return
        if isinstance(target, ast.Attribute) and target.attr in allowed:
            return
    raise StrategySourceError(
        "function editor source must remain a registered strategy function",
        phase="register",
    )


def _error_line(exc: Exception) -> int:
    line = getattr(exc, "lineno", None)
    if isinstance(line, int) and line > 0:
        return line
    match = re.search(r"\bline\s+(\d+)\b", str(exc), flags=re.IGNORECASE)
    return max(1, int(match.group(1))) if match else 1


async def write_lsp_message(writer: asyncio.StreamWriter, message: str) -> None:
    writer.write(_encode_lsp_message(message))
    await writer.drain()


def _encode_lsp_message(message: str) -> bytes:
    payload = message.encode("utf-8")
    if len(payload) > MAX_LSP_MESSAGE_BYTES:
        raise ValueError("LSP message is too large")
    decoded = json.loads(message)
    if not isinstance(decoded, dict):
        raise ValueError("LSP message must be a JSON object")
    return f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload


async def read_lsp_message(reader: asyncio.StreamReader) -> str:
    length: int | None = None
    while True:
        header = await reader.readline()
        if not header:
            raise EOFError("language server closed stdout")
        if header in {b"\r\n", b"\n"}:
            break
        name, separator, value = header.decode("ascii", errors="strict").partition(":")
        if not separator:
            raise ValueError("invalid LSP header")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    if length is None or length < 0 or length > MAX_LSP_MESSAGE_BYTES:
        raise ValueError("invalid LSP Content-Length")
    payload = await reader.readexactly(length)
    text = payload.decode("utf-8")
    json.loads(text)
    return text


def _read_lsp_message_blocking(reader: BinaryIO) -> str:
    length: int | None = None
    for _ in range(100):
        header = reader.readline(8193)
        if not header:
            raise EOFError("language server closed stdout")
        if len(header) > 8192:
            raise ValueError("LSP header is too large")
        if header in {b"\r\n", b"\n"}:
            break
        name, separator, value = header.decode("ascii", errors="strict").partition(":")
        if not separator:
            raise ValueError("invalid LSP header")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    else:
        raise ValueError("too many LSP headers")
    if length is None or length < 0 or length > MAX_LSP_MESSAGE_BYTES:
        raise ValueError("invalid LSP Content-Length")
    payload = bytearray()
    while len(payload) < length:
        chunk = reader.read(length - len(payload))
        if not chunk:
            raise EOFError("language server closed stdout")
        payload.extend(chunk)
    text = bytes(payload).decode("utf-8")
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise ValueError("LSP message must be a JSON object")
    return text


def _drain_binary_stream(reader: BinaryIO) -> None:
    while reader.read(65536):
        pass


def _close_process(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None:
        with contextlib.suppress(OSError):
            process.stdin.close()
    if process.poll() is None:
        with contextlib.suppress(OSError):
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                process.kill()
            process.wait(timeout=2)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            with contextlib.suppress(OSError):
                stream.close()


async def start_language_server(server_id: str) -> LanguageServerProcess:
    server = language_server(server_id)
    if not server.argv:
        raise FileNotFoundError(f"{server_id} is not installed in the current Python environment")
    environment = os.environ.copy()
    current_path = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(PROJECT_ROOT) + (
        os.pathsep + current_path if current_path else ""
    )
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        server.argv,
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        creationflags=creation_flags,
    )
    return LanguageServerProcess(process)


__all__ = [
    "MAX_LSP_MESSAGE_BYTES",
    "LanguageServerProcess",
    "capabilities",
    "language_server",
    "mirror_document",
    "read_lsp_message",
    "source_diagnostics",
    "start_language_server",
    "write_lsp_message",
]
