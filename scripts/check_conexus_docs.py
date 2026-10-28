"""Validate the Conexus framework documentation and its runnable examples.

Documentation about a pinned snapshot goes stale silently: a path moves, a
symbol is renamed, a command is removed, and the prose keeps reading fine. This
check makes those failures loud. It is deliberately cheap and needs no Node
build, so it runs in the ordinary Python quality job.

What it enforces:

* Every repository-relative Markdown link in the Conexus documents resolves.
* Every ``code entry point`` named in a table row resolves to a real file, and
  every symbol the documents attribute to a file is actually defined there.
* Every shell command the documents tell a reader to run exists as a script or
  an example file.
* The reserved two-agent directory stays documentation-only, which is this
  issue's scope boundary.
* The pinned core revision quoted in prose matches runtime.lock.json.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = [
    "docs/10_CONEXUS_FRAMEWORK.md",
    "docs/11_CONEXUS_EXECUTION_LIFECYCLE.md",
    "docs/12_CONEXUS_EXTENSION_GUIDE.md",
    "examples/conexus/README.md",
    "examples/conexus/minimal-agent/README.md",
    "examples/conexus/financial-integration/README.md",
    "examples/conexus/two-agent/README.md",
    "examples/conexus/two-agent/INTERFACE.md",
]

# Symbols the documents attribute to a specific core file. A rename upstream
# must surface here rather than in a reader's confusion.
ATTRIBUTED_SYMBOLS = {
    "vendor/conexus/packages/runtime-core/src/agent/agent-loop.ts": ["runAgentLoop"],
    "vendor/conexus/packages/runtime-core/src/agent/agent-completion.ts": [
        "AGENT_COMPLETION_TOOL",
        "parseAcceptedAgentCompletion",
    ],
    "vendor/conexus/packages/runtime-core/src/agent/agent-run-controller.ts": [
        "AgentRunController",
        "resolveInteraction",
        "drainContinuations",
    ],
    "vendor/conexus/packages/runtime-core/src/hosted/hosted-runtime.ts": [
        "executeHostedRelease",
        "inspectHostedRelease",
        "trackBackgroundRun",
        "HostedRuntimeAdapter",
    ],
    "vendor/conexus/packages/runtime-core/src/release/harness-workspace-release.ts": [
        "OMITTED_RUNTIME_FIELDS",
        "buildHarnessPackage",
    ],
    "vendor/conexus/packages/runtime-core/src/capabilities/node-capability-contracts.ts": [
        "getNodeCapabilityContracts",
        "tool.invoke",
        "agent.run",
        "run.wait",
        "run.cancel",
    ],
    "vendor/conexus/packages/node-host-runtime/src/tools/tool-node-executor.ts": ["executeToolNode"],
    "vendor/conexus/apps/local-host/src/index.mjs": [
        "createLocalHost",
        "agent.interaction_requested",
        "host_restarted",
    ],
    "vendor/conexus/apps/local-host/src/local-runtime.mjs": ["createLocalRuntime"],
    "vendor/conexus/apps/local-host/src/model-provider.mjs": ["createModelCompletion"],
    "vendor/conexus/apps/local-host/src/state.mjs": ["openState"],
}

# Native Agent tools the documents list as a complete set. A tool added
# upstream must not be described as the whole ABI.
NATIVE_AGENT_TOOLS = ["find", "observe", "create", "edit", "use", "request_user_input", "complete"]

# The architecture document publishes the local Host's HTTP surface as a
# complete table. These routes are the ones it is allowed to omit.
UNDOCUMENTED_ROUTES_ALLOWED: set[str] = set()

# Third-party runners invoked as `python -m <name>`; not repository files.
EXTERNAL_MODULES = {"pytest", "uvicorn", "ruff", "compileall", "pip", "build"}

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
FENCE = re.compile(r"```(?:bash|sh|console)\n(.*?)```", re.DOTALL)
COMMAND = re.compile(r"^\s*(python3?|node)\s+(.+)$", re.MULTILINE)
ROUTE_ROW = re.compile(r"^\| `(?:GET|POST) ([^`]+)` \|", re.MULTILINE)


def resolve_link(doc: Path, target: str) -> Path | None:
    """Return the path a Markdown link points at, or None when it is external."""
    if target.startswith(("http://", "https://", "mailto:")):
        return None
    path = target.split("#", 1)[0]
    if not path:
        return None
    return (doc.parent / path).resolve()


def check_links(failures: list[str]) -> None:
    for name in DOCS:
        doc = ROOT / name
        for target in LINK.findall(doc.read_text(encoding="utf-8")):
            resolved = resolve_link(doc, target)
            if resolved is None:
                continue
            if not resolved.exists():
                failures.append(f"{name}: broken link {target}")
            elif not resolved.is_relative_to(ROOT):
                failures.append(f"{name}: link escapes the repository: {target}")


def check_attributed_symbols(failures: list[str]) -> None:
    for name, symbols in ATTRIBUTED_SYMBOLS.items():
        path = ROOT / name
        if not path.is_file():
            failures.append(f"documented core file is missing: {name}")
            continue
        source = path.read_text(encoding="utf-8")
        for symbol in symbols:
            if symbol not in source:
                failures.append(f"{name}: documented symbol not found: {symbol}")


def check_complete_sets(failures: list[str]) -> None:
    schemas = ROOT / "vendor/conexus/packages/runtime-core/src/tools/node-agent-tools.ts"
    source = schemas.read_text(encoding="utf-8")
    declared = set(re.findall(r"^\s*name: '([a-z_]+)',$", source, re.MULTILINE))
    # `complete` is defined in agent-completion.ts, not here.
    expected = set(NATIVE_AGENT_TOOLS) - {"complete"}
    if declared != expected:
        failures.append(
            "the native Agent tool set changed; update docs/10_CONEXUS_FRAMEWORK.md: "
            f"declared={sorted(declared)} documented={sorted(expected)}"
        )

    host = (ROOT / "vendor/conexus/apps/local-host/src/index.mjs").read_text(encoding="utf-8")
    # Routes are template literals interpolating the Harness slug; the document
    # writes `:slug` for it, as the run-id and interaction-id placeholders do.
    declared = {
        route.replace("${slug}", ":slug")
        for route in re.findall(r"app\.(?:get|post)\(\s*[`'\"]([^`'\"]+)", host)
    }
    architecture = (ROOT / "docs/10_CONEXUS_FRAMEWORK.md").read_text(encoding="utf-8")
    documented = set(ROUTE_ROW.findall(architecture))
    if missing := declared - documented - UNDOCUMENTED_ROUTES_ALLOWED:
        failures.append(
            "the local Host exposes a route the HTTP surface table omits; update "
            f"docs/10_CONEXUS_FRAMEWORK.md: {sorted(missing)}"
        )
    if stale := documented - declared:
        failures.append(
            "docs/10_CONEXUS_FRAMEWORK.md documents a route the local Host no longer "
            f"exposes: {sorted(stale)}"
        )


def command_target(arguments: str) -> tuple[bool, str] | None:
    """Return (is_module, target) for a documented command, or None if there is none."""
    tokens = shlex.split(arguments, comments=True)
    is_module = False
    for index, token in enumerate(tokens):
        if token == "-m":
            is_module = True
            continue
        if token.startswith("-"):
            continue
        if is_module or index == 0 or not tokens[index - 1].startswith("-") or tokens[index - 1] == "--test":
            return is_module, token
    return None


def check_commands(failures: list[str]) -> None:
    """Every command a reader is told to run must exist.

    Only fenced shell blocks are scanned. Scanning prose matches phrases like
    "node content" and produces nonsense failures.
    """
    for name in DOCS:
        doc = ROOT / name
        for block in FENCE.findall(doc.read_text(encoding="utf-8")):
            for _interpreter, arguments in COMMAND.findall(block):
                parsed = command_target(arguments)
                if parsed is None:
                    continue
                is_module, target = parsed
                if is_module:
                    if target in EXTERNAL_MODULES:
                        continue
                    dotted = target.replace(".", "/")
                    if (ROOT / f"{dotted}.py").is_file() or (ROOT / dotted / "__init__.py").is_file():
                        continue
                    failures.append(f"{name}: documented module does not exist: {target}")
                    continue
                if any(character in target for character in "*?["):
                    # `node --test 'dir/*.test.mjs'` must match something.
                    if not list(ROOT.glob(target)):
                        failures.append(f"{name}: documented command matches no file: {target}")
                    continue
                if not (ROOT / target.rstrip("/")).exists():
                    failures.append(f"{name}: documented command target does not exist: {target}")


def check_reserved_two_agent(failures: list[str]) -> None:
    """Issue #6 reserves this directory without introducing a second Agent loop."""
    reserved = ROOT / "examples/conexus/two-agent"
    allowed = {"README.md", "INTERFACE.md"}
    present = {path.name for path in reserved.iterdir() if path.is_file()}
    if extra := present - allowed:
        failures.append(
            "examples/conexus/two-agent must stay documentation-only until the "
            f"multi-Agent work lands; found: {sorted(extra)}"
        )
    if subdirectories := [path.name for path in reserved.iterdir() if path.is_dir()]:
        failures.append(f"examples/conexus/two-agent must contain no subdirectories; found: {subdirectories}")


def check_alphalab_command_count(failures: list[str]) -> None:
    """The documents quote how many commands AlphaLab's two Tool nodes expose."""
    bundle = ROOT / "integrations/conexus/alphalab-research-agent/tools"
    total = 0
    for path in sorted(bundle.glob("*.tool.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))["inputSchema"]
        total += len(schema["properties"].get("command", {}).get("enum", []))
    quoted = re.compile(r"(\d+) commands")
    for name in DOCS + ["examples/conexus/financial-integration/harness.mjs"]:
        text = (ROOT / name).read_text(encoding="utf-8")
        for value in {int(match) for match in quoted.findall(text)}:
            # Five is the example's own command surface, not AlphaLab's.
            if value not in {total, 5}:
                failures.append(
                    f"{name}: quotes {value} AlphaLab commands but the bundle declares {total}"
                )


def check_pinned_revision(failures: list[str]) -> None:
    lock = json.loads((ROOT / "integrations/conexus/runtime.lock.json").read_text(encoding="utf-8"))
    short = lock["revision"][:7]
    for name in DOCS:
        text = (ROOT / name).read_text(encoding="utf-8")
        for quoted in set(re.findall(r"`([0-9a-f]{7})`", text)):
            if quoted != short:
                failures.append(
                    f"{name}: quotes core revision {quoted} but runtime.lock.json pins {short}"
                )


def main() -> int:
    failures: list[str] = []
    missing = [name for name in DOCS if not (ROOT / name).is_file()]
    if missing:
        print("\n".join(f"documented file is missing: {name}" for name in missing), file=sys.stderr)
        return 1

    check_links(failures)
    check_attributed_symbols(failures)
    check_complete_sets(failures)
    check_commands(failures)
    check_reserved_two_agent(failures)
    check_alphalab_command_count(failures)
    check_pinned_revision(failures)

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Conexus documentation check passed: {len(DOCS)} documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
