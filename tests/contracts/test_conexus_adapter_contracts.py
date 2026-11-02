"""Hold the adapter contract document to the code on both sides of it.

``integrations/conexus/contracts/adapter-contracts.v1.json`` is the single
declaration of what AlphaLab promises the pinned Conexus core. The Node side
validates against it with a hand-written validator, because the contract tests
have to run on a fresh clone, before ``build_conexus_runtime.py`` has installed
the ``ajv`` the core itself uses.

A hand-written validator is only trustworthy if something independent agrees
with it, so this module replays the document's whole example corpus through
``jsonschema`` and compares verdicts with the Node validator's. A divergence
fails here rather than silently passing a malformed adapter response.

It also pins the three ways the document can go stale without anyone noticing:
a core upgrade introducing an error code the taxonomy does not declare, the
shipped tool bundle growing a top-level field the request contract rejects, and
the search adapter returning a code nobody wrote down.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "integrations/conexus/contracts/adapter-contracts.v1.json"
LOCK = ROOT / "integrations/conexus/runtime.lock.json"
TOOLS = ROOT / "integrations/conexus/alphalab-research-agent/tools"

DOCUMENT = json.loads(CONTRACTS.read_text(encoding="utf-8"))
CONTRACT_IDS = sorted(DOCUMENT["contracts"])
DECLARED_CODES = {code for family in DOCUMENT["errorCodes"].values() for code in family["codes"]}


def contract(contract_id: str) -> dict:
    return DOCUMENT["contracts"][contract_id]


def instance_path(error: jsonschema.ValidationError) -> str:
    """Render a jsonschema error path the way ajv and validate.mjs render it."""
    return "/" + "/".join(str(part) for part in error.absolute_path) if error.absolute_path else "/"


def violations(schema: dict, value: object) -> list[tuple[str, str]]:
    validator = jsonschema.Draft202012Validator(schema)
    return [(instance_path(error), error.validator) for error in validator.iter_errors(value)]


def test_document_identity_matches_the_pinned_core() -> None:
    assert DOCUMENT["schema"] == "alphalab.conexus.adapter-contracts.v1"
    assert DOCUMENT["version"] == 1
    revision = json.loads(LOCK.read_text(encoding="utf-8"))["revision"]
    # A core upgrade must re-verify the contracts and move this field with the
    # lock, which is what makes "compatibility tested" more than a claim.
    assert DOCUMENT["verifiedCoreRevision"] == revision, (
        "The adapter contracts were verified against a different core revision. "
        "Re-run the upgrade verification and update verifiedCoreRevision."
    )
    assert set(DOCUMENT["retryableCodes"]) <= DECLARED_CODES


@pytest.mark.parametrize("contract_id", CONTRACT_IDS)
def test_every_contract_is_a_valid_json_schema(contract_id: str) -> None:
    entry = contract(contract_id)
    jsonschema.Draft202012Validator.check_schema(entry["schema"])
    assert entry["owner"] in {"core", "application"}
    assert entry["direction"] in {"core-to-adapter", "adapter-to-core", "host-to-client", "storage"}
    assert entry["notes"].strip()


@pytest.mark.parametrize("contract_id", CONTRACT_IDS)
def test_declared_examples_agree_with_jsonschema(contract_id: str) -> None:
    entry = contract(contract_id)
    for index, value in enumerate(entry["examples"]["valid"]):
        assert violations(entry["schema"], value) == [], f"{contract_id} valid[{index}]"
    for index, expected in enumerate(entry["examples"]["invalid"]):
        reported = violations(entry["schema"], expected["value"])
        assert (expected["path"], expected["keyword"]) in reported, (
            f"{contract_id} invalid[{index}] expected "
            f"{expected['path']} {expected['keyword']}, jsonschema reported {reported}"
        )


def test_the_node_validator_agrees_with_jsonschema() -> None:
    """The two validators must reach the same verdict on every example."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to cross-check the contract validator.")
    script = """
import { CONTRACT_DOCUMENT, CONTRACT_IDS, checkContract }
  from './integrations/conexus/contracts/adapters.mjs'
const verdicts = {}
for (const id of CONTRACT_IDS) {
  const { valid, invalid } = CONTRACT_DOCUMENT.contracts[id].examples
  verdicts[id] = {
    valid: valid.map(value => checkContract(id, value).length === 0),
    invalid: invalid.map(entry => checkContract(id, entry.value).map(d => [d.path, d.keyword])),
  }
}
process.stdout.write(JSON.stringify(verdicts))
"""
    completed = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        cwd=ROOT, capture_output=True, text=True, timeout=60, check=True,
    )
    reported = json.loads(completed.stdout)
    assert sorted(reported) == CONTRACT_IDS

    for contract_id in CONTRACT_IDS:
        entry = contract(contract_id)
        assert all(reported[contract_id]["valid"]), f"{contract_id}: Node rejected a valid example"
        for index, expected in enumerate(entry["examples"]["invalid"]):
            node_details = {tuple(detail) for detail in reported[contract_id]["invalid"][index]}
            python_details = set(violations(entry["schema"], expected["value"]))
            assert (expected["path"], expected["keyword"]) in node_details
            # Both validators must agree the value is invalid, and agree on the
            # declared failure. Either may report extra violations the other
            # does not, so only the declared one is compared exactly.
            assert python_details, f"{contract_id} invalid[{index}]: jsonschema accepted it"
            assert node_details, f"{contract_id} invalid[{index}]: the Node validator accepted it"


def test_search_adapter_returns_only_declared_codes() -> None:
    source = (ROOT / "integrations/conexus/web-search.mjs").read_text(encoding="utf-8")
    returned = set(re.findall(r"failure\('([a-z_]+)'", source))
    assert returned, "no failure codes found; has the search adapter been restructured?"
    undeclared = returned - DECLARED_CODES
    assert not undeclared, f"the search adapter returns undeclared codes: {sorted(undeclared)}"
    # And the declared set must not drift past what the adapter can produce.
    assert set(DOCUMENT["errorCodes"]["search"]["codes"]) == returned


def test_core_error_codes_are_declared() -> None:
    """A core upgrade that adds an error code must update the taxonomy."""
    sources = [
        ROOT / "vendor/conexus/apps/local-host/src/model-provider.mjs",
        ROOT / "vendor/conexus/packages/runtime-core/src/hosted/hosted-runtime.ts",
    ]
    raised = set()
    for path in sources:
        raised |= set(re.findall(r"HostedRuntimeError\([^)]*?'([a-z_]+)'\s*[,)]",
                                 path.read_text(encoding="utf-8"), re.DOTALL))
    assert raised, "no HostedRuntimeError codes found; has the core layout changed?"
    undeclared = raised - DECLARED_CODES
    assert not undeclared, (
        f"the pinned core raises codes the contracts do not declare: {sorted(undeclared)}"
    )

    parser = ROOT / "vendor/conexus/packages/runtime-core/src/hosted/openai-completion.ts"
    parse_codes = set(re.findall(r"^\s*\|\s*'([a-z_]+)'", parser.read_text(encoding="utf-8"), re.MULTILINE))
    assert parse_codes == set(DOCUMENT["errorCodes"]["modelParse"]["codes"])


@pytest.mark.parametrize("path", sorted(TOOLS.glob("*.tool.json")), ids=lambda path: path.name)
def test_shipped_tool_bundle_satisfies_the_request_contract(path: Path) -> None:
    """The real financial Tool nodes must accept nothing the contract rejects."""
    schema = json.loads(path.read_text(encoding="utf-8"))["inputSchema"]
    declared = contract("financial.tool.request")["schema"]["properties"]
    extra = set(schema["properties"]) - set(declared)
    assert not extra, (
        f"{path.name} accepts top-level fields the request contract rejects: {sorted(extra)}. "
        "Declare them in adapter-contracts.v1.json or move them into args."
    )
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["command"]

    # Every command the bundle exposes is a legal command name, and each one
    # validates as a request, so a benchmark can issue any of them.
    request_schema = contract("financial.tool.request")["schema"]
    for command in schema["properties"]["command"]["enum"]:
        assert violations(request_schema, {"command": command}) == [], command

    # Confirmation flags are top-level only. A nested one must be rejected.
    for flag in (name for name in declared if name.startswith("confirm_")):
        assert violations(request_schema, {"command": "data.status", "args": {flag: True}}), flag


def shipped_commands() -> set[str]:
    commands: set[str] = set()
    for path in sorted(TOOLS.glob("*.tool.json")):
        commands |= set(json.loads(path.read_text(encoding="utf-8"))["inputSchema"]["properties"]["command"]["enum"])
    return commands


def test_the_declared_financial_boundary_matches_the_shipped_bundle() -> None:
    """The response contracts describe the shipped tool, not a simplified stand-in.

    An earlier revision of this work derived them from the example tool in
    examples/conexus/financial-integration, whose `{success, command}` envelope
    the shipped tool never produces. The checks below are what makes the
    difference visible: every command the document names must be a real command,
    and the reduced surface a benchmark gets must be declared rather than
    discovered.
    """
    financial = DOCUMENT["financialTool"]
    commands = shipped_commands()

    surface = financial["reducedCommandSurface"]["commands"]
    assert surface, "the double's command surface is undeclared"
    for command, declared in surface.items():
        assert command in commands, f"{command} is not a shipped command"
        assert isinstance(declared["requiredArgs"], list)
        expected = declared["contract"]
        assert expected == "transport-passthrough" or expected in DOCUMENT["contracts"], expected
        if declared["confirm"] is not None:
            assert declared["confirm"].startswith("confirm_")

    for command in financial["transportPassthroughCommands"]:
        assert command in commands, f"{command} is not a shipped command"

    # The failure codes the tools can originate themselves. These are
    # SCREAMING_CASE and deliberately live outside `errorCodes`, whose members
    # are the lowercase codes the core and the search adapter use.
    for path in sorted(TOOLS.glob("*.tool.json")):
        code = json.loads(path.read_text(encoding="utf-8"))["code"]
        for literal in re.findall(r"error_code:String\(error\.code\|\|'([A-Z_]+)'\)", code):
            assert literal in financial["failureCodes"], literal
        for literal in re.findall(r"error_code:'([A-Z_]+)'", code):
            assert literal in financial["failureCodes"], literal
    assert not set(financial["failureCodes"]) & DECLARED_CODES, (
        "financial failure codes must stay out of the lowercase core taxonomy"
    )


def test_the_financial_response_contracts_reject_the_example_envelope() -> None:
    """A regression guard for the defect this replaced.

    The example tool's envelope must not validate against the production
    contracts. If it does, the contracts have drifted back toward describing
    the example rather than the shipped tool.
    """
    example = {"success": True, "command": "data.status", "result": {"coverage": 1}}
    for contract_id in ("financial.tool.failure", "financial.tool.job", "financial.tool.result"):
        assert violations(contract(contract_id)["schema"], example), contract_id

    # And the shapes the shipped tool really returns must validate.
    assert violations(contract("financial.tool.failure")["schema"], {
        "status": "failed", "error_code": "PROJECT_RUN_FAILED",
        "error_summary": "backtest.run requires args.project_id",
        "error_details": {}, "log_reference": None,
    }) == []
    assert violations(contract("financial.tool.job")["schema"], {
        "status": "queued", "job_id": "job-1", "strategy_id": "main",
    }) == []
