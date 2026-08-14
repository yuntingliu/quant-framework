"""Private subprocess worker for trusted local Python strategy hooks."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback


def _bounded(value: str, limit: int = 20_000) -> str:
    return value if len(value) <= limit else value[:limit] + "\n... output truncated"


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        source = request["source"]
        entrypoint = request["entrypoint"]
        contexts = request["contexts"]
        namespace = {"__name__": "alphalab_custom_strategy"}
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(compile(source, "<alphalab-custom-strategy>", "exec"), namespace)
            function = namespace.get(entrypoint)
            if not callable(function):
                raise ValueError(f"entrypoint {entrypoint!r} is not callable")
            values = [function(context) for context in contexts]
        payload = {
            "ok": True,
            "values": values,
            "stdout": _bounded(stdout.getvalue()),
            "stderr": _bounded(stderr.getvalue()),
        }
    except Exception as exc:
        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": _bounded(traceback.format_exc()),
        }
    try:
        sys.stdout.write(json.dumps(payload, allow_nan=False, default=str))
    except Exception as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "error": f"strategy result is not JSON serializable: {exc}",
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
