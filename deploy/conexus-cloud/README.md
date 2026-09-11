# AlphaLab on the shared Conexus host

This runbook describes AlphaLab's current shared Conexus deployment. Existing
shared applications stay on that host; a candidate local Web Host is a separate
deployment design that must pass API, persistence, and model-access acceptance
before cutover. Current readiness is recorded in
[Deployment and instances](../../docs/05_DEPLOYMENT.md).

The original AlphaLab Harness is hosted inside the canonical Conexus root
Canvas. Its original workstation route is listed below; dev3 has its own slug
and binding in the independent-instance section.

- Shared Web Host: `conexus-digui-web-1`
- Shared loopback port: `127.0.0.1:3000`
- AlphaLab Harness: `alphalab-research-harness-v1`
- AlphaLab publication slug: `alphalab-research-agent`
- Conexus origin: `https://adminer.cloud`
- AlphaLab Web origin: `https://dev.acetoken.net`
- Private AlphaLab API tunnel endpoint: `127.0.0.1:18000`

The root Canvas may contain other product Harnesses. Harness graphs, Hosting
slugs, exposure policies, billing policies, release histories, and workspaces
remain namespaced even though one Web Host owns the Canvas and serves all
publications. Do not create a `conexus-alphalab` Compose project for this
integration.

The canonical shared-host Compose and deployment files live in the Conexus
repository under `infra/enterprise/`. AlphaLab uses `adminer.cloud` as its
server-side Conexus origin; it does not own a second Conexus hostname.

## Private AlphaLab API — original Pop workstation

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

The authenticated browser service imports
`/home/dev/.config/alphalab-dev/deployment_app.py`. Install the checked-in
`deployment_app.py` there; its SPA fallback applies `no-store` headers to every
HTML shell response so a release switch cannot leave the browser on an old
hashed frontend bundle.

Never point the tunnel at browser port `8100`; that route is protected by the
browser authentication boundary and is not the private Agent API.

Verify the local API before testing the public Harness:

```bash
curl -fsS 'http://127.0.0.1:8101/api/agent/context?backtest_limit=1&sync_job_limit=1'
systemctl --user is-active alphalab-conexus-api.service alphalab-hk-tunnel.service
```

On the Conexus server, verify both the shared host and AlphaLab publication:

```bash
curl -fsS https://adminer.cloud/health
curl -fsS https://adminer.cloud/api/public/harnesses/alphalab-research-agent/descriptor
```

The shared Web Host and both AlphaLab API processes must receive the same
`CONEXUS_PUBLICATION_WORKSPACE_TOKEN`. On the Web Host it is paired with
`CONEXUS_PUBLICATION_WORKSPACE_SLUG=alphalab-research-agent`; unlike
`CONEXUS_WEB_TOKEN`, this credential can access only that publication's durable
run workspace. The hosted publication uses enterprise identity and publisher
billing, so the AlphaLab backend also uses this credential when reading the
full Harness manifest and creating Runs. Keep it in the deployment environment
and never expose it to the browser bundle.

## Independent workstation instances

Copying a browser configuration does not move the published tools. Each
independent database must have its own publication slug, enterprise service
credential, private tunnel port, and `ALPHALAB_INSTANCE_ID`. Never reuse a
publication whose tools still target another workstation.

Stage an independent snapshot with `register_conexus_research_harness.mjs`:
set `CONEXUS_CANVAS_PATH` to a separate Canvas file under the same project,
`ALPHALAB_STAGED_BUNDLE_PATH` to a separate `workspace/harnesses/` directory,
`ALPHALAB_TOOL_API_ORIGIN` to its HTTP loopback tunnel origin, and
`ALPHALAB_INSTANCE_ID` to the target deployment ID. The generated tools bind
that origin into the release and verify `/api/agent/identity` before executing
any command; a host-wide `ALPHALAB_API_ORIGIN` cannot override this binding.
Host that snapshot through the Conexus administrator `harness:host` operation
with a separate slug and its returned publication-scoped enterprise credential.
The enterprise credential authenticates service requests; it does not fund
model calls. When the shared host uses Conexus account funding, sign in to its
`/admin` page as the configured publisher and install a publisher deployment
credential for the new slug as well. An existing credential for the old slug
cannot fund the new publication. Do not change the shared host's global model
funding mode to work around a missing publication credential.

dev3 uses slug `alphalab-research-agent-dev3` and instance ID `dev3`, while the
original slug continues to belong to its original workstation.
For dev3 the route is Conexus `127.0.0.1:18003` → SSH → Mac
`127.0.0.1:8301` → authenticated workstation `127.0.0.1:8300`.
`scripts/serve_conexus_private_api.py --env-file <workstation-env>` supplies
the workstation authentication locally. It only listens on loopback and must
be reached through a restricted SSH key. This reuses the existing backend
process and job queues. It does not start a second strategy runtime against
the same database. The browser credential never enters the tool bundle.

Before changing the workstation's `CONEXUS_PUBLICATION_SLUG` and
`CONEXUS_PUBLICATION_WORKSPACE_TOKEN`, verify the bound tools return the same
project IDs, source revisions, and results as the browser API. Keep the old
publication active for its own workstation.
Complete one read-only Agent run before marking the instance ready. A working
descriptor, manifest, workspace, or direct tool query does not prove the new
publication has a configured model provider.

As of the 2026-09-11 acceptance record, the dev3 project, original frozen
backtest, and 11 report Documents have been recovered and its bound tool reads
pass. Full Agent run creation still returns `public_runtime_unavailable`
because the new slug needs model funding configuration. This is an outstanding
acceptance item, not evidence that Conexus was moved to the Mac or that the
requested `local` branch is compatible.
