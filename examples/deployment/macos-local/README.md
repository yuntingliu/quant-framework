# macOS management for an existing local dev3 workstation

These are credential-free examples of the management programs used by dev3.
They manage an existing authenticated workstation; they do not provision a
Cloudflare account, copy instance credentials, recover private reports, or
install the private forwarding/tunnel configuration on a fresh machine.
For a new personal workstation, begin with the
[local launcher](../../../docs/07_LOCAL_DEPLOYMENT.md).

## Expected layout

| Path under the service user's home | Purpose |
| --- | --- |
| `src/quant-framework-dev3` | Clean source checkout; `deploy/dev3-local-20261019` retains the local fixes. |
| `releases/alphalab-dev3/<commit>` | Built release with its own virtual environment and validation record. |
| `apps/alphalab-dev3` | Symlink to the active release. |
| `.config/alphalab-dev3` | Private environment, local management programs and optional operator-only migration inputs. |
| `.local/share/alphalab-dev3/runtime` | Persistent research data, Agent state and credentials. |
| `.local/state/alphalab-dev3` | Private logs, operation locks and verified backups. |

The example programs are deliberately scoped to this layout and instance. Adapt
their constants before using them for another workstation. Keep instance state
outside the source checkout.

## Install or maintain the management entry point

Preserve the existing private environment and LaunchAgent files. Compare changes
to the examples before replacing installed management programs. The existing
private configuration must be valid before using these operations.

Install `dev3_manager.py` and `build_local_release.py` in
`~/.config/alphalab-dev3/` with permissions `0600`. A private shell entry point
at `~/.local/bin/alphalab-dev3` can invoke:

```sh
#!/bin/sh
exec "$HOME/apps/alphalab-dev3/.venv/bin/python" "$HOME/.config/alphalab-dev3/dev3_manager.py" "$@"
```

Set its permissions to `0700`. The private `alphalab.env` uses JSON-quoted string
values, as shown in `alphalab.env.example`; fill it locally and keep it `0600`.
Do not replace an existing populated file with the blank example.

The manager writes the main application LaunchAgent and starts the bundled
Conexus Host with the Python service. It expects two pre-existing, operator-owned
LaunchAgents:

- `net.acetoken.alphalab-dev3-agent-api`: the authenticated loopback forwarder,
  using `scripts/serve_conexus_private_api.py` with private port 8301 and backend
  port 8300. Its existing environment-file reference stays private.
- `net.acetoken.alphalab-dev3-tunnel`: the website tunnel to loopback port 8300.
  Cloudflare credentials and account configuration remain outside Git.

The old `net.acetoken.alphalab-dev3-agent-tunnel` SSH Agent route is disabled on
local activation. Do not confuse it with the website tunnel, which stays active.

Configure model credentials through **Model providers** in the authenticated UI.
The active profile is stored privately in
`runtime/conexus/secrets/model-providers.json`. Updating another computer's
environment variables does not automatically update that saved profile.
For a remote model behind the operator's proxy, configure the proxy locally;
the example does not contain a proxy credential.

For the operator-approved OpenRouter search route, set the private host value
`ALPHALAB_WEB_SEARCH_PROVIDER="openrouter"` and restart when no research job is
active. The active model profile must point to `https://openrouter.ai/api/v1`.
No separate Brave key is needed. The search adapter uses one Exa search per
invocation with at most ten sources and bills search/model usage to OpenRouter.
Use `brave` with a privately configured `BRAVE_SEARCH_API_KEY` for the alternative
Brave route; never add either provider's key to the example.

## Operations

```bash
~/.local/bin/alphalab-dev3 status
~/.local/bin/alphalab-dev3 logs
~/.local/bin/alphalab-dev3 start
~/.local/bin/alphalab-dev3 stop
~/.local/bin/alphalab-dev3 restart
~/.local/bin/alphalab-dev3 backup
~/.local/bin/alphalab-dev3 update
~/.local/bin/alphalab-dev3 rollback
```

`update` fetches upstream, merges the requested ref (default `origin/main`) into
the local deployment branch, builds and validates the candidate, and activates
it only after a private snapshot. It does not push or force-update GitHub.
Merge conflicts stop the update before deployment. The existing release remains
active while building.

`deploy <commit>` requires a validated release. It verifies the release against
its `release-manifest.json` and the active release with `alphalab release verify`;
refuses active Agent, backtest, research and synchronization work through
`alphalab release tasks`; snapshots data and configuration; switches the release
symlink; and checks that authenticated service/Agent readiness reports the
manifest's commit, version and core revision. A failed activation restores the
previous release and its matching state.

`backup` uses SQLite's backup API and verifies the saved databases. Backups also
contain populated configuration, model credentials and research files, so keep
the whole backup private. `rollback` saves the current state before restoring
the last pre-deployment snapshot. An explicit verified snapshot path may be
supplied. The state being replaced is preserved separately.

The build helper's default path runs the complete release checks. Its optional
reuse arguments are for maintainers: reuse is accepted only for an allowed
source-diff scope and a validated base artifact. A frontend-only reuse still
runs frontend tests/lint/build and Python contract/facade checks; it inherits
the unchanged core's earlier validation rather than claiming a fresh core run.

Private historical-report migration inputs and the one-time importer are not
distributed here. Normal updates preserve the existing runtime. This example
does not carry a user's report data or reconnect an old cloud publication.
