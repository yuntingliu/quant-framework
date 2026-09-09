"""Fail CI when runtime data or credential material is tracked."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_ASSIGNMENT = re.compile(
    r"(?im)^(RQ_PASSWORD|ALPHALAB_WEB_PASSWORD|CONEXUS_WEB_TOKEN|TUNNEL_TOKEN)"
    r"[ \t]*=[ \t]*(?![ \t]*(?:$|<|\"\"|'')).+?$"
)
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
LOCAL_PREFIXES = ("data/app/", "data/runtime/", "data/cache/", ".pytest_cache/")


def main() -> int:
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8").split("\0")
    failures: list[str] = []
    for relative in filter(None, tracked):
        normalized = relative.replace("\\", "/")
        if normalized == ".env" or normalized.startswith(LOCAL_PREFIXES):
            failures.append(f"tracked local state: {normalized}")
            continue
        path = ROOT / relative
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if PRIVATE_KEY.search(content):
            failures.append(f"private key material: {normalized}")
        if SECRET_ASSIGNMENT.search(content):
            failures.append(f"non-empty credential assignment: {normalized}")
    if failures:
        raise SystemExit("\n".join(failures))
    print("Repository hygiene check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
