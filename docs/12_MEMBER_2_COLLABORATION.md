# Member 2 collaboration finalization API

This document describes the AlphaLab workstation API that invokes the trusted
publication coordinator. It is distinct from the Conexus Local Host API in
`10_CONEXUS_FRAMEWORK.md`.

## Route

| Method and path | Authentication | Purpose |
|---|---|---|
| `POST /api/conexus/collaboration/tasks/{task_id}/finalize` | Existing workstation Basic authentication when enabled | Publish the latest approved candidate or reconcile an existing pending publication using an idempotency key. |

The request body accepts only:

```json
{
  "idempotency_key": "stable-delivery-identity"
}
```

The caller cannot supply review results, evidence references, report content,
workspace outputs, destination revisions or filesystem paths. The API loads
task state and frozen evidence from the canonical task stores, then invokes
the trusted finalization service.

## Task storage

Task storage is deterministic:

```text
${ALPHALAB_RUNTIME_DIR:-data/runtime}/
└── collaboration/
    └── tasks/
        └── {task_id}/
            ├── control/
            └── evidence/
```

The task ID is validated before path construction. The API opens existing
SQLite stores only; an unknown task does not create empty databases. Production
candidate preparation should use `create_task_evidence_store(task_id)` when no
explicit EvidenceStore is supplied. The collaboration Host uses the same
canonical roots when `storeRoot` and `evidenceRoot` are omitted.

Explicit store roots remain supported for tests and isolated fixtures. Such
stores are intentionally not discoverable by the production API route.

## Authentication and recovery

The route is covered by `HttpBasicAuthMiddleware` when workstation Basic
authentication is enabled. It is not within the `/api/conexus/runs/` run-token
bypass. For deployments reachable beyond the local machine, configure
`ALPHALAB_WEB_AUTH_ENABLED`, `ALPHALAB_WEB_USERNAME` and
`ALPHALAB_WEB_PASSWORD`.

A `200` response contains the committed durable receipt. A `202` response
indicates that the Host outcome is still pending; retry the same route with the
same idempotency key to reconcile. Never create a new idempotency key just
because a request timed out.
