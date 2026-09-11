#!/usr/bin/env bash
set -euo pipefail

# The Monaco/Python editor bundle can exceed Node's default 2 GiB heap.
# Keep the limit configurable for hosts with a different build RAM budget.
export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=4096}"

SOURCE_DIR="${ALPHALAB_SOURCE_DIR:-$HOME/src/quant-framework}"
RELEASES_DIR="${ALPHALAB_RELEASES_DIR:-$HOME/releases}"
CURRENT_LINK="${ALPHALAB_CURRENT_LINK:-$HOME/apps/quant-framework}"
RUNTIME_DIR="${ALPHALAB_RUNTIME_DIR:-$HOME/.local/share/alphalab/runtime}"
ENV_FILE="${ALPHALAB_ENV_FILE:-$HOME/.config/alphalab/alphalab.env}"
SERVICE_NAME="${ALPHALAB_SERVICE_NAME:-alphalab.service}"
HEALTH_URL="${ALPHALAB_HEALTH_URL:-http://127.0.0.1:8000/api/health}"
REF="${1:-origin/main}"

STATE_DIR="${ALPHALAB_STATE_DIR:-$HOME/.local/state/alphalab}"
BACKUP_DIR="${ALPHALAB_BACKUP_DIR:-$HOME/backups/alphalab}"
mkdir -p "$STATE_DIR" "$RELEASES_DIR" "$(dirname "$CURRENT_LINK")" "$BACKUP_DIR"
exec 9>"$STATE_DIR/deploy.lock"
flock -n 9 || { echo "Another AlphaLab deployment is running." >&2; exit 1; }

for command in git python3 npm systemctl tar; do
  command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
test -d "$SOURCE_DIR/.git" || { echo "Source checkout is missing: $SOURCE_DIR" >&2; exit 1; }
test -f "$ENV_FILE" || { echo "Environment file is missing: $ENV_FILE" >&2; exit 1; }
if [[ -e "$CURRENT_LINK" && ! -L "$CURRENT_LINK" ]]; then
  echo "Current path must be migrated to a symlink before deployment: $CURRENT_LINK" >&2
  exit 1
fi

git -C "$SOURCE_DIR" fetch --prune origin
COMMIT="$(git -C "$SOURCE_DIR" rev-parse "${REF}^{commit}")"
RELEASE="$RELEASES_DIR/$COMMIT"
STAGING="$RELEASES_DIR/.${COMMIT}.staging.$$"
PREVIOUS=""
if [[ -L "$CURRENT_LINK" ]]; then
  PREVIOUS="$(readlink -e "$CURRENT_LINK" 2>/dev/null || true)"
fi
BUILDING_RELEASE=0

cleanup_staging() {
  if [[ -d "$STAGING" && "$STAGING" == "$RELEASES_DIR"/.*.staging.* ]]; then
    rm -rf -- "$STAGING"
  fi
  if [[ "$BUILDING_RELEASE" == "1" && -d "$RELEASE" && "$RELEASE" == "$RELEASES_DIR/$COMMIT" ]]; then
    rm -rf -- "$RELEASE"
  fi
}
trap cleanup_staging EXIT

if [[ ! -d "$RELEASE" ]]; then
  mkdir -p "$STAGING"
  git -C "$SOURCE_DIR" archive "$COMMIT" | tar -x -C "$STAGING"
  mv -- "$STAGING" "$RELEASE"
  BUILDING_RELEASE=1
  python3 -m venv "$RELEASE/.venv"
  "$RELEASE/.venv/bin/python" -m pip install --upgrade pip
  "$RELEASE/.venv/bin/python" -m pip install -e "${RELEASE}[dev,dashboard,rq]"
  TEST_RUNTIME="$RELEASE/.test-runtime"
  (
    cd "$RELEASE"
    npm --prefix dashboard/frontend ci
    npm --prefix dashboard/frontend run lint
    npm --prefix dashboard/frontend run build:web
    npm --prefix dashboard/frontend audit --omit=dev --audit-level=high
    ALPHALAB_ENV_FILE="$RELEASE/.test.env" \
      ALPHALAB_RUNTIME_DIR="$TEST_RUNTIME" \
      ALPHALAB_WEB_AUTH_ENABLED=0 \
      .venv/bin/python -m pytest tests -q
    ALPHALAB_ENV_FILE="$RELEASE/.test.env" \
      ALPHALAB_RUNTIME_DIR="$TEST_RUNTIME" \
      ALPHALAB_WEB_AUTH_ENABLED=0 \
      .venv/bin/python scripts/check_facade_imports.py
  )
  rm -rf -- "$TEST_RUNTIME"
  rm -rf -- "$RELEASE/dashboard/frontend/node_modules"
  "$RELEASE/.venv/bin/alphalab" --help >/dev/null
  "$RELEASE/.venv/bin/python" - "$RELEASE/.alphalab-release.json" "$COMMIT" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

Path(sys.argv[1]).write_text(
    json.dumps(
        {"commit_sha": sys.argv[2], "deployed_at": datetime.now(timezone.utc).isoformat()},
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
  BUILDING_RELEASE=0
fi

DB="$RUNTIME_DIR/app/alphalab.db"
if [[ -f "$DB" ]]; then
  BACKUP="$BACKUP_DIR/alphalab-$(date -u +%Y%m%dT%H%M%SZ).db"
  "$RELEASE/.venv/bin/python" - "$DB" "$BACKUP" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
  chmod 600 "$BACKUP"
fi

NEXT_LINK="${CURRENT_LINK}.next"
ln -sfn "$RELEASE" "$NEXT_LINK"
mv -Tf "$NEXT_LINK" "$CURRENT_LINK"

restart_and_check() {
  systemctl --user restart "$SERVICE_NAME"
  "$RELEASE/.venv/bin/python" - "$ENV_FILE" "$COMMIT" "$HEALTH_URL" <<'PY'
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

values = {}
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if "=" in raw and not raw.lstrip().startswith("#"):
        key, value = raw.split("=", 1)
        value = value.strip()
        if value.startswith('"'):
            value = json.loads(value)
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[key.strip()] = value
headers = {}
if values.get("ALPHALAB_WEB_AUTH_ENABLED", "").lower() in {"1", "true", "yes", "on"}:
    token = base64.b64encode(
        f"{values.get('ALPHALAB_WEB_USERNAME', '')}:{values.get('ALPHALAB_WEB_PASSWORD', '')}".encode()
    ).decode()
    headers["Authorization"] = f"Basic {token}"
health_url = sys.argv[3]
if urlsplit(health_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise SystemExit("Release health checks must use a loopback origin")
request = urllib.request.Request(health_url, headers=headers)
expected_commit = sys.argv[2]
for _ in range(30):
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            body = json.load(response)
        if (
            response.status == 200
            and body.get("status") == "ok"
            and body.get("commit_sha") == expected_commit
        ):
            raise SystemExit(0)
    except (OSError, ValueError, urllib.error.URLError):
        time.sleep(1)
raise SystemExit("AlphaLab health check failed")
PY
}

if ! restart_and_check; then
  if [[ -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
    ln -sfn "$PREVIOUS" "$NEXT_LINK"
    mv -Tf "$NEXT_LINK" "$CURRENT_LINK"
    systemctl --user restart "$SERVICE_NAME"
    echo "Deployment failed; previous release restored." >&2
  else
    systemctl --user stop "$SERVICE_NAME"
    if [[ -L "$CURRENT_LINK" && "$(readlink "$CURRENT_LINK")" == "$RELEASE" ]]; then
      rm -- "$CURRENT_LINK"
    fi
    echo "Deployment failed; new service stopped and release link removed." >&2
  fi
  exit 1
fi

"$RELEASE/.venv/bin/python" - "$RELEASES_DIR" "$RELEASE" <<'PY'
import re
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
current = Path(sys.argv[2]).resolve()
releases = sorted(
    (
        path
        for path in root.iterdir()
        if path.is_dir() and re.fullmatch(r"[0-9a-f]{40}", path.name)
    ),
    key=lambda path: path.stat().st_mtime,
    reverse=True,
)
keep = {path.resolve() for path in releases[:3]}
keep.add(current)
for path in releases:
    resolved = path.resolve()
    if resolved not in keep and resolved.parent == root:
        shutil.rmtree(resolved)
PY

echo "AlphaLab deployed: $COMMIT"
