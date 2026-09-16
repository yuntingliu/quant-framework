# Linux Service Deployment

This example uses a systemd user service for a single AlphaLab backend. See [Deployment](../05_DEPLOYMENT.md) for build, authentication, and persistence requirements.

## Prerequisites

The service user needs a working systemd user session, repository read access, Python >= 3.10 with `venv` and `pip`, Node.js 22/npm, `flock`, `tar`, and GNU file utilities. Put Node 22 on the `PATH` used to run the release script. Package installation varies by distribution; first verify:

```bash
python3 --version
python3 -m venv --help
node --version
npm --version
systemctl --user status
```

## Directories and configuration

Default paths are relative to the service user's home directory and can be overridden:

| Variable | Default |
| --- | --- |
| `ALPHALAB_SOURCE_DIR` | `$HOME/src/quant-framework` |
| `ALPHALAB_RELEASES_DIR` | `$HOME/releases` |
| `ALPHALAB_CURRENT_LINK` | `$HOME/apps/quant-framework` |
| `ALPHALAB_RUNTIME_DIR` | `$HOME/.local/share/alphalab/runtime` |
| `ALPHALAB_ENV_FILE` | `$HOME/.config/alphalab/alphalab.env` |
| `ALPHALAB_SERVICE_NAME` | `alphalab.service` |
| `ALPHALAB_HEALTH_URL` | `http://127.0.0.1:8000/api/health` |
| `ALPHALAB_STATE_DIR` | `$HOME/.local/state/alphalab` |
| `ALPHALAB_BACKUP_DIR` | `$HOME/backups/alphalab` |

The service user must be able to read the environment file; set its permissions to `0600`. Configure authentication, RQ, and Conexus as needed. The release script and service process must use the same absolute runtime path.

For the default layout, prepare the source checkout and configuration directories:

```bash
mkdir -p "$HOME/src" "$HOME/.config/alphalab" "$HOME/.config/systemd/user"
git clone https://github.com/yuntingliu/quant-framework.git "$HOME/src/quant-framework"
```

Create `$HOME/.config/alphalab/alphalab.env` with your username and a nonempty password. Both systemd and the release script read this file. Use JSON double-quoted values, without shell `export` statements or variable expansion:

```dotenv
ALPHALAB_WEB_AUTH_ENABLED="true"
ALPHALAB_WEB_USERNAME="your-username"
ALPHALAB_WEB_PASSWORD=""
```

```bash
chmod 600 "$HOME/.config/alphalab/alphalab.env"
```

RQ and Conexus can be configured after application acceptance. Enabling authentication with an empty password causes a configuration error and fails the release health check.

## User service

Save the following as `$HOME/.config/systemd/user/alphalab.service`, adjusting paths as needed:

```ini
[Unit]
Description=AlphaLab workstation
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/apps/quant-framework
EnvironmentFile=%h/.config/alphalab/alphalab.env
Environment=ALPHALAB_RUNTIME_DIR=%h/.local/share/alphalab/runtime
ExecStart=%h/apps/quant-framework/.venv/bin/python -m uvicorn dashboard.backend.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=default.target
```

systemd expands `%h` to the service user's home directory. An administrator can enable lingering for that user if the service must survive logout.

## Release script

Prepare the environment file, user service, and checkout in `ALPHALAB_SOURCE_DIR`. Confirm that the checkout's `origin` points to the intended repository. Then run:

```bash
systemctl --user daemon-reload
cd "$HOME/src/quant-framework"
bash scripts/deploy_linux.sh origin/main
```

The script fetches the requested ref, builds the web client and Python environment in a separate release directory, runs checks, switches the current-release symlink, restarts the service, and checks the authenticated health endpoint. On failure it restores the previous valid release; a failed first deployment stops the new service.

The build defaults to a 4 GiB Node heap limit, configurable through `NODE_OPTIONS`. The script requires Linux systemd, `flock`, and GNU utilities; it is not an installer for other operating systems.

The script automatically backs up `app/alphalab.db`. Complete migration or recovery also requires backups of `dataio.db`, `agent-conversations.sqlite3`, research partitions, and the Conexus workspace. Code rollback does not replace data backups.

## Service management

```bash
systemctl --user enable alphalab.service
systemctl --user status alphalab.service
journalctl --user -u alphalab.service
```

Use the configured service name if it differs. External access requires a TLS reverse proxy or tunnel with WebSocket and event-stream forwarding. Maintain domains, SSH routes, access policies, and host service files in the deployment environment.
