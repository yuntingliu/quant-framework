from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .core import Adapter, BudgetGuard, Config, Limits, Task

TOOL_FILES = ("Project-Files.tool.json", "Project-Run.tool.json")


def tools_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "integrations" / "conexus" / "alphalab-research-agent" / "tools"


def load_tool_jsons(names: Tuple[str, ...] = TOOL_FILES, base_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    base = Path(base_dir) if base_dir is not None else tools_dir()
    loaded: Dict[str, Dict[str, Any]] = {}
    for name in names:
        path = base / name
        if not path.exists():
            raise FileNotFoundError(f"Cannot load required tool file: {path}")
        loaded[name] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _call_end(code: str, open_index: int) -> int:
    """Return the index just past the closing parenthesis of a balanced call."""
    depth = 0
    quote: Optional[str] = None
    escaped = False
    i = open_index
    while i < len(code):
        ch = code[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(code)


def _split_top_level(text: str, separator: str = ",") -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    quote: Optional[str] = None
    escaped = False
    for ch in text:
        if quote is not None:
            current.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
            current.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == separator and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _iter_invocations(code: str) -> Iterator[Tuple[str, str, int]]:
    """Yield (helper, argument text, position) for every call()/request() in the tool code."""
    for match in re.finditer(r"\b(call|request)\s*\(", code):
        open_index = match.end() - 1
        end = _call_end(code, open_index)
        yield match.group(1), code[open_index + 1:end - 1], match.start()


_ASSIGNMENT = re.compile(r"(?:^|[;,({\n])\s*(?:const\s+|let\s+|var\s+)?([A-Za-z_$][\w$]*)\s*=")


def _read_expression(code: str, start: int) -> str:
    depth = 0
    quote: Optional[str] = None
    escaped = False
    i = start
    while i < len(code):
        ch = code[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and ch in ";,\n":
            break
        i += 1
    return code[start:i].strip()


def _assignment_rhs(code: str, name: str, before: int) -> Optional[str]:
    """Last assignment to ``name`` before ``before`` (the usual lexical definition)."""
    start: Optional[int] = None
    for match in _ASSIGNMENT.finditer(code[:before]):
        if match.group(1) == name:
            start = match.end()
    if start is None:
        return None
    return _read_expression(code, start)


def _normalise_endpoint(path: str) -> str:
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def _literal_text(part: str) -> Optional[str]:
    match = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"|`([^`]*)`", part.strip())
    if match is None:
        return None
    return next(group for group in match.groups() if group is not None)


def _top_level_index(text: str, character: str) -> Optional[int]:
    depth = 0
    quote: Optional[str] = None
    escaped = False
    for index, ch in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == character and depth == 0:
            return index
    return None


def _resolve_fragments(part: str, code: str, position: int, depth: int = 0) -> List[str]:
    """Resolve one concatenation fragment to the possible path text it contributes."""
    part = part.strip()
    if not part or depth > 4:
        return [""] if not part else ["{arg}"]
    text = _literal_text(part)
    if text is not None:
        return [text]
    if part.startswith("(") and _call_end(part, 0) == len(part):
        inner = part[1:-1]
        question = _top_level_index(inner, "?")
        if question is not None:
            colon = _top_level_index(inner[question + 1:], ":")
            if colon is not None:
                rest = inner[question + 1:]
                left = _resolve_fragments(rest[:colon], code, position, depth + 1)
                right = _resolve_fragments(rest[colon + 1:], code, position, depth + 1)
                return left + right
        return _resolve_fragments(inner, code, position, depth + 1)
    # A bare identifier that holds a path prefix (e.g. ``base``) is inlined; anything else
    # (query parameter, id, action name) becomes a dynamic segment.
    if re.fullmatch(r"[A-Za-z_$][\w$]*", part):
        rhs = _assignment_rhs(code, part, position)
        if rhs is not None:
            resolved = _resolve_paths(rhs, code, position, depth + 1)
            if resolved:
                return sorted(resolved)
    return ["{arg}"]


def _resolve_paths(expression: str, code: str, position: int, depth: int = 0) -> set:
    """Resolve a call argument to the set of endpoint templates it can request."""
    if depth > 4:
        return set()
    expression = expression.strip()
    if not expression:
        return set()
    if re.fullmatch(r"[A-Za-z_$][\w$]*", expression):
        rhs = _assignment_rhs(code, expression, position)
        return set() if rhs is None else _resolve_paths(rhs, code, position, depth + 1)
    if expression.startswith("(") and _call_end(expression, 0) == len(expression):
        return _resolve_paths(expression[1:-1], code, position, depth + 1)
    parts = _split_top_level(expression, "+")
    kept: List[str] = []
    for part in parts:
        text = _literal_text(part)
        if text is not None and "?" in text:
            prefix = text.partition("?")[0]
            kept.append("'" + prefix.replace("\\", "\\\\").replace("'", "\\'") + "'")
            break
        kept.append(part)
    templates = [""]
    for part in kept:
        options = _resolve_fragments(part, code, position, depth)
        templates = [prefix + option for prefix in templates for option in options]
    return {_normalise_endpoint(item) for item in templates if item.startswith("/api/")}


def extract_tool_endpoints(tool_files: Optional[Tuple[str, ...]] = None,
                           base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Parse the request definitions in the Conexus tool JSONs into endpoint templates.

    Dynamic path segments are normalised to ``{arg}``. Only ``call()``/``request()`` helpers are
    scanned, so unrelated string literals in the tool code are ignored.
    """
    loaded = load_tool_jsons(tool_files or TOOL_FILES, base_dir)
    endpoints = set()
    for data in loaded.values():
        code = data.get("code") or ""
        for _helper, invocation, position in _iter_invocations(code):
            arguments = _split_top_level(invocation)
            if not arguments:
                continue
            method_match = re.search(r"method\s*:\s*['\"]([A-Z]+)['\"]", invocation)
            method = method_match.group(1) if method_match else "GET"
            for endpoint in _resolve_paths(arguments[0], code, position):
                endpoints.add((endpoint, method))
    return [{"endpoint": endpoint, "method": method} for endpoint, method in sorted(endpoints)]


class ConexusAdapter(Adapter):
    def __init__(self, base_dir: Optional[Path] = None):
        self._base_dir = Path(base_dir) if base_dir is not None else tools_dir()
        self.financial_entry_points: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []

    def setup(self, task: Task, limits: Limits, initial_state: Dict[str, Any], use_doubles: bool) -> None:
        if not self.financial_entry_points:
            self.financial_entry_points = extract_tool_endpoints(base_dir=self._base_dir)

    def run(self, task: Task, limits: Limits, budget_guard: BudgetGuard) -> Iterator[Dict[str, Any]]:
        ev = {"type": "conexus.start", "task_id": task.task_id}
        self.events.append(ev)
        yield ev
        yield {"type": "conexus.done", "task_id": task.task_id}

    def stop(self) -> None:
        pass

    def get_capabilities(self) -> Dict[str, Any]:
        return {"has_financial_tools": True}

    def get_financial_entry_points(self) -> List[Dict[str, Any]]:
        return self.financial_entry_points


class OpenCodeAdapter(Adapter):
    def __init__(self, config: Config, base_dir: Optional[Path] = None):
        if config not in (Config.A, Config.B):
            raise ValueError("OpenCodeAdapter only for A or B")
        self.config = config  # A or B
        self._base_dir = Path(base_dir) if base_dir is not None else tools_dir()
        self.financial_entry_points: List[Dict[str, Any]] = []
        self.tool_files_loaded = False
        self.loaded_tools: Dict[str, Dict[str, Any]] = {}

    def _load_tool_jsons(self) -> None:
        self.loaded_tools = load_tool_jsons(TOOL_FILES, self._base_dir)
        self.tool_files_loaded = True

    def setup(self, task: Task, limits: Limits, initial_state: Dict[str, Any], use_doubles: bool) -> None:
        if self.config == Config.B:
            self._load_tool_jsons()
            self.financial_entry_points = extract_tool_endpoints(base_dir=self._base_dir)
        else:
            # No financial tools
            self.financial_entry_points = []

    def run(self, task: Task, limits: Limits, budget_guard: BudgetGuard) -> Iterator[Dict[str, Any]]:
        yield {"type": "opencode.start", "config": self.config.value, "task_id": task.task_id}
        if self.config == Config.A:
            yield {"type": "opencode.docs_only", "task_id": task.task_id}
        else:
            yield {"type": "opencode.financial_tools_loaded", "loaded": list(self.loaded_tools.keys())}
        yield {"type": "opencode.done", "task_id": task.task_id}

    def stop(self) -> None:
        pass

    def get_capabilities(self) -> Dict[str, Any]:
        return {"has_financial_tools": self.config == Config.B, "loads_tool_jsons": self.config == Config.B}

    def get_financial_entry_points(self) -> List[Dict[str, Any]]:
        return self.financial_entry_points


def create_adapter(config: Config) -> Adapter:
    if config == Config.C:
        return ConexusAdapter()
    if config in (Config.A, Config.B):
        return OpenCodeAdapter(config)
    raise ValueError(f"Unsupported config {config}")
