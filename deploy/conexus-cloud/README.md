# AlphaLab on the shared Conexus host

AlphaLab is published as an independent Harness inside the canonical Conexus
root Canvas. It does not own a second Web Host, Compose project, project volume,
administrator session, or deployment timer.

- Shared Web Host: `conexus-digui-web-1`
- Shared loopback port: `127.0.0.1:3000`
- AlphaLab Harness: `alphalab-research-harness-v1`
- AlphaLab publication slug: `alphalab-research-agent`
- Public origin: `https://alphalab.43.154.239.41.nip.io`
- Private AlphaLab API tunnel endpoint: `127.0.0.1:18000`

The root Canvas may contain other product Harnesses. Harness graphs, Hosting
slugs, exposure policies, billing policies, release histories, and workspaces
remain namespaced even though one Web Host owns the Canvas and serves all
publications. Do not create a `conexus-alphalab` Compose project for this
integration.

The canonical shared-host Compose and deployment files live in the Conexus
repository under `infra/enterprise/`. `Caddyfile.alphalab` only adds the
AlphaLab hostname to that existing listener; it must proxy port `3000`.

## Private AlphaLab API

AlphaLab data, project, strategy, and backtest operations remain on the hosted
research workstation. Conexus tools reach them through a restricted reverse
SSH tunnel:

- workstation `127.0.0.1:8101`: private FastAPI listener;
- cloud `127.0.0.1:18000`: reverse-tunnel endpoint forwarded to port `8101`.

Install `alphalab-conexus-api.service` and `alphalab-hk-tunnel.service` as user
services. The API unit is `PartOf=alphalab-dev.service`, so switching the
AlphaLab release and restarting the browser service also restarts the private
API against the same release. The SSH tunnel can remain connected while the
loopback API restarts.

Never point the tunnel at browser port `8100`; that route is protected by the
browser authentication boundary and is not the private Agent API.

Verify the local API before testing the public Harness:

```bash
curl -fsS 'http://127.0.0.1:8101/api/agent/context?backtest_limit=1&sync_job_limit=1'
systemctl --user is-active alphalab-conexus-api.service alphalab-hk-tunnel.service
```

On the Conexus server, verify both the shared host and AlphaLab publication:

```bash
curl -fsS http://127.0.0.1:3000/health
curl -fsS http://127.0.0.1:3000/api/public/harnesses/alphalab-research-agent/descriptor
```

The shared Web Host and both AlphaLab API processes must receive the same
`CONEXUS_PUBLICATION_WORKSPACE_TOKEN`. On the Web Host it is paired with
`CONEXUS_PUBLICATION_WORKSPACE_SLUG=alphalab-research-agent`; unlike
`CONEXUS_WEB_TOKEN`, this credential can access only that publication's durable
run workspace. Keep it in the deployment environment and never expose it to
the browser bundle.
