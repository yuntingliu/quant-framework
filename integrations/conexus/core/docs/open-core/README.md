# Conexus Core — local source candidate

This source distribution contains the Agent/Harness execution engine and a
single-user local HTTP service. It includes no Canvas editor, desktop application,
account service, billing service, enterprise deployment scripts or Git history.

Publication is pending the owner's license selection. The packages retain
`private: true`; this directory is a buildable review candidate, not a published
open-source release.

## Build and test

Use Node.js 22.18+ or 24 and npm:

```sh
npm ci
npm run build
npm test
```

The export is independent of the private development repository. All local
workspace dependencies are included. `npm run check:boundaries` checks the
package boundary; builds and tests also resolve imports against this standalone
tree. Dependencies must be installed once before running without a network.

## Run a local Harness

The service accepts a compiled Harness Release JSON file. Applications can
compile their graph using `compileHarnessWorkspace` from `@conexus/local-host`.
They can also call `createLocalHost`, `createLocalRuntime` and
`createModelCompletion` to embed the service without a second Agent engine.

Set these environment variables before `npm start`:

| Variable | Purpose |
| --- | --- |
| `CONEXUS_RELEASE_FILE` | Path to the compiled Release JSON |
| `CONEXUS_LOCAL_ROOT` | Durable state directory; default `.conexus/local` |
| `CONEXUS_LOCAL_SLUG` | Harness path component; default `local-agent` |
| `CONEXUS_LOCAL_PORT` | Loopback HTTP port; default `8787` |
| `CONEXUS_MODEL_BASE_URL` | OpenAI-compatible endpoint base, ending in `/v1` |
| `CONEXUS_MODEL_ID` | Exact model identifier served by that endpoint |
| `CONEXUS_MODEL_API_KEY` | Provider key; optional for a loopback model endpoint |

The service binds to `127.0.0.1`. Its `host-token` file authenticates the workspace;
each run receives a separate access token. Tokens stay in the local state directory.
There is no Conexus cloud login or model gateway. A local model endpoint allows
Agent execution without a remote model service. Tools can still access networks
when the supplied Harness requires them.

The API provides a descriptor, workspace snapshot, create/get/cancel Run,
user-input responses and SSE events. Workspace changes commit after a completed
or blocked run; reports survive restarts and graph updates. Interrupted runs are
marked cancelled on restart and are never automatically replayed. The local Host
serializes runs sharing one workspace. Back up the whole state directory while
the service is stopped.

This is trusted local execution under the current OS account. It is not a sandbox
for untrusted Harness code. The default adapter runs Node/Python/shell Tool nodes;
applications can supply additional capabilities explicitly. Canvas rendering and
commercial control-plane extensions are outside this distribution.

See [Architecture](docs/open-core/ARCHITECTURE.md) for the dependency boundary.
