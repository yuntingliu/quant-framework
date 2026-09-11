# Linux and Cloudflare Operations

AlphaLab runs as an unprivileged Linux user and exposes no public origin port.
Cloudflare Tunnel publishes the web application and SSH from loopback services.
Host-specific service files, credentials, passwords, and tunnel configuration
must remain on the server and outside this repository.

## Access Model

The maintained hostnames are:

```text
alphalab.acetoken.net -> http://127.0.0.1:8000
ssh.acetoken.net      -> ssh://127.0.0.1:22
```

The preferred long-term setup protects both hostnames with Cloudflare Access
and individual identities. A temporary shared-password setup is also supported:

- AlphaLab Web uses the optional HTTP Basic credentials below.
- SSH uses the shared Linux `dev` password.
- The two passwords must be different.
- Cloudflare Tunnel transports traffic but does not provide shared-password
  authentication or per-member identity in this mode.

Client SSH configuration:

```sshconfig
Host ssh.acetoken.net
    User dev
    ProxyCommand cloudflared access ssh --hostname %h
```

`cloudflared` is required on both ends for a published SSH route. On Pop!_OS it
runs as the persistent tunnel service. On a collaborator workstation it is only
a command-line helper started by OpenSSH for the duration of the connection; it
does not need to be installed as a background service.

Windows client setup:

```powershell
winget install --id Cloudflare.cloudflared --exact --scope user
cloudflared --version
ssh dev@ssh.acetoken.net
```

Restart the terminal once after `winget` updates `PATH`. If the package manager
does not expose the command through `PATH`, use the absolute path to
`cloudflared.exe` in `ProxyCommand`.

Native `ssh dev@ssh.acetoken.net` without this `ProxyCommand` cannot traverse a
standard Cloudflare Tunnel published SSH route. Avoid opening public port 22 as
a workaround.

## Host Layout

Use these persistent locations:

```text
/home/dev/apps/quant-framework
/home/dev/src/quant-framework
/home/dev/releases/<commit-sha>
/home/dev/.config/alphalab/alphalab.env
/home/dev/.config/systemd/user/alphalab.service
/home/dev/.local/share/alphalab/runtime
```

The environment file should have mode `0600` and contain only locally required
values:

```dotenv
ALPHALAB_RUNTIME_DIR=/home/dev/.local/share/alphalab/runtime
PYTHONDONTWRITEBYTECODE=1
ALPHALAB_WEB_AUTH_ENABLED=true
ALPHALAB_WEB_USERNAME=alphalab
ALPHALAB_WEB_PASSWORD=
RQ_USER=
RQ_PASSWORD=
RQ_HOST=
CONEXUS_WEB_ORIGIN=
CONEXUS_PUBLICATION_SLUG=
```

The user service is intentionally host-local and is not checked into Git:

```ini
[Unit]
Description=AlphaLab workstation
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/dev/apps/quant-framework
EnvironmentFile=/home/dev/.config/alphalab/alphalab.env
ExecStart=/home/dev/apps/quant-framework/.venv/bin/python -m uvicorn dashboard.backend.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=default.target
```

The administrator enables lingering once so the user service starts without an
interactive login:

```bash
sudo loginctl enable-linger dev
```

## SSH Boundary

Keep password authentication disabled for direct network connections and
enable it only for `dev` when cloudflared reaches sshd through loopback:

```sshdconfig
# /etc/ssh/sshd_config.d/90-alphalab-cloudflare.conf
PasswordAuthentication no
PermitRootLogin prohibit-password

Match User dev Address 127.0.0.1,::1
    PasswordAuthentication yes

Match all
```

Validate every context before reloading sshd:

```bash
sudo sshd -t
sudo sshd -T -C user=dev,host=pop-os,addr=127.0.0.1 |
  grep passwordauthentication
sudo sshd -T -C user=dev,host=pop-os,addr=192.168.60.50 |
  grep passwordauthentication
sudo systemctl reload ssh
```

The expected values are `yes` for loopback and `no` for direct LAN/public
connections. Keep the administrator's public-key route working before closing
the existing session.

When UFW is used without changing policies for unrelated host services, allow
the administrator networks before denying direct SSH:

```bash
sudo ufw allow in on tailscale0 to any port 22 proto tcp
sudo ufw allow from 192.168.60.0/24 to any port 22 proto tcp
sudo ufw deny in to any port 22 proto tcp
sudo ufw --force enable
```

Adjust the LAN CIDR for the host. The loopback connection used by cloudflared
remains available.

`/home/dev/apps/quant-framework` is an atomic symlink to one successful release.
The Git checkout and immutable release directories are separate so a failed
build cannot modify the running service.

## Build and Release

Create the source checkout once as `dev`, then deploy an exact `origin/main`
commit:

```bash
git clone git@github.com:yuntingliu/quant-framework.git \
  /home/dev/src/quant-framework
cd /home/dev/src/quant-framework
bash scripts/deploy_linux.sh origin/main
```

The script archives the exact commit, builds Web without Electron dependencies,
runs the quality gate, backs up SQLite with the online backup API, switches the
symlink, restarts the service, and checks `/api/health`. A failed health check
restores the previous link. The most recent three successful releases are kept,
and `node_modules` is removed after the Web bundle is built.

The bundled database is a read-only seed. The first application store is copied
to `$ALPHALAB_RUNTIME_DIR/app/alphalab.db`; subsequent research, signals,
backtests, strategies, and paper activity remain in runtime storage.

### Independent development instance

Give each instance its own checkout, release directory, runtime, environment
file, systemd service, and loopback port. For example, a second development
service can listen on `127.0.0.1:8200` and use these deployment overrides:

```bash
export ALPHALAB_SOURCE_DIR=/home/dev/src/quant-framework-dev2
export ALPHALAB_RELEASES_DIR=/home/dev/releases-dev2
export ALPHALAB_CURRENT_LINK=/home/dev/apps/alphalab-dev2
export ALPHALAB_RUNTIME_DIR=/home/dev/.local/share/alphalab-dev2/runtime
export ALPHALAB_ENV_FILE=/home/dev/.config/alphalab-dev2/alphalab.env
export ALPHALAB_SERVICE_NAME=alphalab-dev2.service
export ALPHALAB_HEALTH_URL=http://127.0.0.1:8200/api/health
export ALPHALAB_STATE_DIR=/home/dev/.local/state/alphalab-dev2
export ALPHALAB_BACKUP_DIR=/home/dev/backups/alphalab-dev2
bash "$ALPHALAB_SOURCE_DIR/scripts/deploy_linux.sh" origin/main
```

Create the corresponding user service before deploying, with its working
directory and executable under `ALPHALAB_CURRENT_LINK`, the instance environment
file, and the matching port. Run `systemctl --user daemon-reload` after creating
the unit, then enable it after the deployment passes its authenticated health
check. Add the new hostname to the existing Cloudflare tunnel, targeting that
loopback origin.

To carry research data into the new instance, copy market datasets and back up
the live SQLite databases with SQLite's online backup API before first startup.
SDK strategy definitions, validation records, and result records share the
instance runtime database. Do not point two development instances at the same
writable database. External Conexus publication configuration is separate from
local runtime isolation; reusing it retains its existing remote publication.

## Secret Handling

- Set the Linux password interactively with `passwd`; never place it in a file.
- Generate a separate high-entropy AlphaLab web password and keep it only in
  the mode `0600` environment file. Never reuse the Linux password.
- Give the server a dedicated GitHub SSH key attached to its separate GitHub
  account. Do not use a GitHub password for Git operations.
- Store the Cloudflare tunnel token in a root-only token file and reference it
  with `cloudflared tunnel run --token-file`. Never put the token in `ExecStart`.
- When individual access is enabled later, keep member emails in the
  Cloudflare dashboard, not in Git.
- Do not expose ports 22 or 8000 through a router or public firewall rule.

## Verification

Verify the web application challenges for the AlphaLab credentials through
Cloudflare from a non-local network, then verify SSH prompts for the `dev`
password. Confirm direct public access to ports 22 and 8000 is unavailable.
Existing `code.acetoken.net`, `canvas.acetoken.net`, and
`files.acetoken.net` routes must remain unchanged.
