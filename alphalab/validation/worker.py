"""Private subprocess entry point for validation.py execution."""

from __future__ import annotations

import json
import math
import pickle
import sys
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from alphalab.validation_sdk import ValidationContext


def _json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 24:
        raise ValueError("validation output nesting is too deep")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.generic):
        return _json_value(value.item(), depth=depth + 1)
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_value(item, depth=depth + 1) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item, depth=depth + 1) for item in value]
    raise TypeError(
        f"validation outputs must be JSON-compatible; found {type(value).__name__}"
    )


def main(input_name: str, output_name: str) -> int:
    output_path = Path(output_name)
    try:
        payload = pickle.loads(Path(input_name).read_bytes())
        context = ValidationContext.from_payload(payload)
        namespace = {"__name__": "alphalab_project_validation", "__file__": "<validation.py>"}
        exec(compile(payload["source"], "<validation.py>", "exec"), namespace, namespace)
        outputs = {}
        for entrypoint in payload["entrypoints"]:
            function = namespace.get(entrypoint["function"])
            registration = getattr(function, "__alphalab_analysis__", None)
            if not callable(function) or not isinstance(registration, dict):
                raise RuntimeError(f"registered analysis {entrypoint['id']} was not loaded")
            outputs[entrypoint["id"]] = _json_value(function(context))
        message = {"ok": True, "outputs": outputs}
        encoded = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > int(payload["max_output_bytes"]):
            raise RuntimeError("validation.py output exceeded 2000000 bytes")
    except BaseException as exc:
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        message = {"ok": False, "error": detail[:4000]}
        encoded = json.dumps(message, ensure_ascii=False).encode("utf-8")
    output_path.write_bytes(encoded)
    return 0 if message.get("ok") else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
