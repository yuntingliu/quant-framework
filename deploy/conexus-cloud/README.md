# Conexus configuration

AlphaLab connects to a compatible Conexus Web Host. The Research Agent runs as
a hosted Harness, while project execution remains in AlphaLab's existing backend
and task queue. Conexus can run on the same computer or on a privately reachable
server. Installing AlphaLab does not install Conexus.

See the [Agent contract](../../docs/04_CONEXUS_AGENT.md) and
[AlphaLab deployment guide](../../docs/05_DEPLOYMENT.md).

## Connection and compatibility

```text
Browser -> AlphaLab backend -> Conexus Web Host -> Model provider
                                  |
                                  +-> AlphaLab tool API -> Projects, data, backtests
```

The browser communicates through AlphaLab. Publication credentials stay on the
backend. Conexus persists research reports in its workspace; AlphaLab persists
conversation transcripts separately.

A branch named `local` is not sufficient evidence of compatibility. The checkout
must build a **Web Host**, persist hosted releases, and provide the Harness
manifest, run, event, cancellation, and workspace APIs used by AlphaLab. A desktop
Canvas alone does not provide those HTTP endpoints. Record and pin the tested
Conexus commit alongside the AlphaLab release.

The current AlphaLab client expects an enterprise manifest with
`defaultExposureId` and an Agent exposure containing `nodeType: "agent"` and the
`api` surface. Conexus revisions that require `nodeDefinition` instead have a
different contract. They need a coordinated Canvas/release/client migration and
acceptance test. Renaming a field in stored JSON is not a supported migration:
the executable release, checksums, runtime capabilities, and credentials must
also remain consistent.

## Configuration and identities

| Setting | Owner and purpose |
| --- | --- |
| `CONEXUS_WEB_ORIGIN` | AlphaLab backend: reachable Conexus HTTP origin |
| `CONEXUS_PUBLICATION_SLUG` | AlphaLab backend: exact hosted Harness slug |
| `CONEXUS_PUBLICATION_WORKSPACE_TOKEN` | AlphaLab backend: enterprise service credential for that publication |
| `ALPHALAB_INSTANCE_ID` | AlphaLab backend and tool registration: expected workstation identity |
| `ALPHALAB_TOOL_API_ORIGIN` | Tool registration: private AlphaLab API address reachable from the Conexus runtime |
| `CONEXUS_WEB_TOKEN` | Conexus process: persistent administrator token; never substitute it for the publication credential |
| `CONEXUS_PROJECT_ROOT` | Conexus process and registration: persistent project, Canvas, and hosted releases |
| `CONEXUS_SECRET_ROOT` | Conexus process: persistent credentials outside the tracked project |

Service identity authenticates access to a publication. Model-provider credentials
or publisher funding authorize model calls. Configure both. Account-funded
authorization can be scoped to a particular slug; do not assume another
publication's authorization applies.

For a same-host AlphaLab process, the connection settings can use:

```dotenv
CONEXUS_WEB_ORIGIN="http://127.0.0.1:3000"
CONEXUS_PUBLICATION_SLUG="alphalab-research-agent"
CONEXUS_PUBLICATION_WORKSPACE_TOKEN="REPLACE_WITH_PUBLICATION_SERVICE_CREDENTIAL"
```

Load these values using the service's environment mechanism. A container's
`127.0.0.1` refers to that container, so use the actual private network address
when the processes do not share a network namespace.

## Starting a local Web Host

Build the Conexus checkout using its own version-specific instructions. The
AlphaLab PowerShell launcher recognizes these existing build layouts:

| Layout | Server entry | Frontend |
| --- | --- | --- |
| Workspace | `apps/web/server-dist/apps/web/server/entrypoints/web-host.js` | `apps/web/dist` |
| Legacy | `backend/dist/web-host.js` | `dist/web` |

It requires Node.js 22.12 or later and an explicitly configured administrator
token. It does not silently generate an unrecoverable token. Provide an
untracked environment file with JSON-quoted strings, or export process variables;
process variables take precedence.

```powershell
.\scripts\start_conexus_web.ps1 -ConexusRoot C:\src\Conexus -ProjectRoot C:\data\conexus -EnvFile C:\config\conexus.env -Check
.\scripts\start_conexus_web.ps1 -ConexusRoot C:\src\Conexus -ProjectRoot C:\data\conexus -EnvFile C:\config\conexus.env
```

The directories must already exist. `-Check` validates files, Node, and the
administrator token format. It does **not** start the server or verify HTTP,
publication access, or model execution. The launcher binds to loopback. Add
`-TrustedRuntime` only for a trusted, single-tenant workspace requiring local
tool execution; follow that Conexus version's capability and model configuration.

On macOS/Linux, use the matching checkout's start command with the same
persistent project, secret, administrator, and model settings.

## Registration and hosting

`scripts/register_conexus_research_harness.mjs` stages the AlphaLab bundle and
Canvas. It supports:

- `CONEXUS_CANVAS_PATH` for an independent Canvas file.
- `ALPHALAB_STAGED_BUNDLE_PATH` for a bundle directory inside the workspace.
- `ALPHALAB_TOOL_API_ORIGIN` for the bound tool origin.
- `ALPHALAB_INSTANCE_ID` for the expected workstation identity.

Bound tools check `/api/agent/identity` before executing commands. A shared host's
global origin cannot override this binding. Verify it after copying configuration
so the browser and Agent access the same instance.

Use the compatible Conexus administrator **Host** operation (`harness:host`) to
create a hosted revision with the intended slug, enterprise identity, and
publisher billing. Hosting locally is separate from publishing to an account
registry. Older Canvas bundles may need migration before hosting on a newer
Conexus revision.

The legacy `scripts/host_conexus_research_harness.mjs` helper relies on internal
modules that newer Conexus releases no longer expose. `node
scripts/host_conexus_research_harness.mjs --check` checks for those module files
without hosting anything. If incompatible, use the supported administrator
workflow. Do not bypass activation and credential handling by importing a newly
relocated storage class.

## Private tool access

Same-host processes can use loopback. Across hosts, use a restricted private
network or SSH forwarding with environment-specific addresses and credentials.

`scripts/serve_conexus_private_api.py` forwards to the existing authenticated
AlphaLab backend. It accepts `--port` and `--backend-port`, reads JSON-quoted
values from `--env-file`, and injects workstation authentication locally. Browser
passwords do not enter the tool bundle. Do not launch a second job-execution
backend against the same database to provide this access.

The forwarder must be reachable only by the trusted Conexus runtime. Restart it
after upgrading its code, then recheck instance identity.

## Acceptance and troubleshooting

Validate each boundary independently:

1. Read `/api/conexus/status` and `/api/conexus/manifest` through AlphaLab.
   Status checks the authenticated manifest and the supported Agent exposure.
2. Read `/api/conexus/workspace` using the intended service identity.
3. Confirm tool identity, project records, and frozen results match the browser.
4. Complete one read-only Agent request, including model execution, events,
   terminal Run state, and delivered output.
5. Restart the services and confirm reports and conversations remain readable.

| Status error | Meaning and next check |
| --- | --- |
| `workspace_not_configured` | AlphaLab has no publication service credential. Configure it in the backend process. |
| `connection_failed` | AlphaLab cannot reach the origin. Check server startup, bind address, port, and network namespace. |
| `service_authentication_failed` | The server rejected access. Check credential scope and the selected slug. |
| `publication_unavailable` / HTTP 404 | The server cannot load the publication. Check both its slug and stored release compatibility; files can exist but fail validation. |
| `publication_incompatible` | The release or manifest contract is unsupported by the server or AlphaLab client. Check the pinned versions and supported migration procedure. |
| `upstream_error` | Inspect the Conexus service logs for the returned HTTP status. |

A successful manifest does not prove model funding or execution. A failed Run
such as `public_runtime_unavailable` requires inspection of the specific runtime
and model authorization error. An isolated AlphaLab deployment with no Conexus
configuration is expected to show the optional Agent as unavailable.

Back up AlphaLab databases and the Conexus project/secret stores separately.
Validate the target instance and one complete Run before switching the origin.
