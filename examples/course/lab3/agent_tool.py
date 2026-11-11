"""Show or replay the JSON for an agent call to AlphaLab's Monte Carlo tool."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


TOOL = "options_lab.monte_carlo"
PREFIX = "/api/agent/data-tools"


def discover(base_url: str) -> dict:
    with urlopen(base_url.rstrip("/") + PREFIX, timeout=15) as response:
        tools = json.load(response)["tools"]
    match = next((item for item in tools if item["name"] == TOOL), None)
    if match is None:
        raise ValueError("Register options_lab.monte_carlo in the local AlphaLab data-tool registry")
    return match


def invoke(base_url: str, tool_call: dict) -> dict:
    if (not isinstance(tool_call, dict) or set(tool_call) != {"name", "arguments"}
            or tool_call["name"] != TOOL):
        raise ValueError(f"Expected an agent call with name {TOOL} and arguments")
    if not isinstance(tool_call["arguments"], dict):
        raise ValueError("arguments must be a JSON object")
    path = f"{PREFIX}/{TOOL}/invoke"
    body = {"input": tool_call["arguments"]}
    request = Request(
        base_url.rstrip("/") + path, data=json.dumps(body, allow_nan=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            status, output = response.status, json.load(response)
    except HTTPError as exc:
        status, output = exc.code, json.load(exc)
    return {"tool_call": tool_call, "request": {"method": "POST", "path": path, "body": body},
            "response": {"status": status, "body": output}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8765")
    parser.add_argument("--call", type=Path, help="Agent-produced name and arguments JSON")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        result = (invoke(args.api, json.loads(args.call.read_text(encoding="utf-8")))
                  if args.call else discover(args.api))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    raw = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(raw, encoding="utf-8")
    else:
        print(raw, end="")


if __name__ == "__main__":
    main()
