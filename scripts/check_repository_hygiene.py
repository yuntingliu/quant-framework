"""Fail CI when local runtime data or credential material is tracked."""
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
ACTIVE_FORBIDDEN = {
    "broker-specific source": re.compile(
        r"(?i)\b(?:IBKR|TWS|ib_async)\b|Interactive Brokers"
    ),
    "QMT sync source": re.compile(
        r"(?i)(?:/data/sync/qmt|/data-sync/qmt|\bqmt_sync\b|\bsync_qmt\b)"
    ),
    "tunnel or SSH source": re.compile(r"(?i)\b(?:cloudflared?|tunnel|ssh)\b"),
}
ACTIVE_ROOTS = (
    ROOT / "alphalab",
    ROOT / "dashboard" / "backend",
    ROOT / "dashboard" / "frontend" / "src",
)


def main() -> int:
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
    ).decode("utf-8").split("\0")
    failures: list[str] = []
    for relative in filter(None, tracked):
        normalized = relative.replace("\\", "/")
        if normalized == ".env" or normalized.startswith("data/runtime/"):
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
    for root in ACTIVE_ROOTS:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".py", ".ts", ".tsx"}:
                continue
            content = path.read_text(encoding="utf-8")
            for label, pattern in ACTIVE_FORBIDDEN.items():
                if pattern.search(content):
                    failures.append(f"{label}: {path.relative_to(ROOT).as_posix()}")
    if failures:
        raise SystemExit("\n".join(failures))
    print("Repository hygiene check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
