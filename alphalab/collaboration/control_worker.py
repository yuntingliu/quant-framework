"""Bounded Host-only stdio control bridge. Never expose this worker as a tool."""

import argparse
import sys
from pathlib import Path

from .contracts import CollaborationError, decode_json, encode_json, require
from .control import ControlStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    store = None
    while raw := sys.stdin.buffer.readline(600_001):
        try:
            if store is None:
                store = ControlStore(args.store_root, args.evidence_root)
            response = handle(store, raw)
        except CollaborationError as error:
            response = error.as_dict()
        except Exception:
            response = CollaborationError("STORAGE_UNAVAILABLE").as_dict()
        sys.stdout.buffer.write(encode_json(response, maximum=8 * 1024 * 1024) + b"\n")
        sys.stdout.buffer.flush()
        if len(raw) > 600_000:
            break
    return 0


def handle(store, raw):
    require(len(raw) <= 600_000, "EVIDENCE_LIMIT")
    request = decode_json(raw)
    require(set(request) == {"operation", "task_id", "args"})
    if request["operation"] == "register":
        result = store.register(request["task_id"], **request["args"])
    elif request["operation"] == "context":
        result = store.context(request["task_id"], **request["args"])
    else:
        result = store.request(request["task_id"], request["operation"], **request["args"])
    return {"status": "succeeded", "result": result}


if __name__ == "__main__":
    raise SystemExit(main())
