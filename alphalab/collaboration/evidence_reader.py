"""One bounded read-only local Host request over stdio; no HTTP service or key."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .contracts import CollaborationError, decode_json, encode_json, require
from .evidence import EvidenceReader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(65_537)
        require(len(raw) <= 65_536, "EVIDENCE_LIMIT")
        envelope = decode_json(raw)
        require(isinstance(envelope, dict) and set(envelope) == {"scope", "request"})
        scope, request = envelope["scope"], envelope["request"]
        require(isinstance(scope, dict) and set(scope) == {"task_id", "manifests"})
        require(isinstance(request, dict) and set(request) <= {"operation", "manifest_id", "artifact_id", "locator", "offset", "limit"})
        reader = EvidenceReader(args.store_root, scope["task_id"], scope["manifests"])
        if request.get("operation") == "manifest":
            require(set(request) == {"operation", "manifest_id"})
            result = reader.manifest(request["manifest_id"])
        else:
            require(request.get("operation") == "read" and {"manifest_id", "artifact_id"} <= request.keys())
            result = reader.read(request["manifest_id"], request["artifact_id"],
                                 **{key: request[key] for key in ("locator", "offset", "limit") if key in request})
        response = {"status": "succeeded", "result": result}
        code = 0
    except CollaborationError as error:
        response, code = error.as_dict(), 1
    except Exception:
        response, code = CollaborationError("STORAGE_UNAVAILABLE").as_dict(), 1
    sys.stdout.buffer.write(encode_json(response) + b"\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
