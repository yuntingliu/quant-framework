# Local deployment acceptance

Use this procedure before promoting a local deployment branch to `main`.
Installation commands are in [Local deployment](07_LOCAL_DEPLOYMENT.md).
Store machine-specific logs, credentials, incident details, and acceptance
records in ignored artifacts rather than the project documentation.

## Build and automated checks

Verify the exact candidate commit, Python environment, and Node version. Build
the browser and pinned Conexus runtime from the lockfiles. Verify the upstream
export checksums and package boundaries before running:

```bash
python -m pytest tests -q
python scripts/check_facade_imports.py
python scripts/check_repository_hygiene.py
python -m ruff check alphalab apps/api tests scripts
python scripts/check_conexus_runtime.py
npm --prefix apps/desktop test
npm --prefix apps/desktop run lint
npm --prefix apps/desktop run build
git diff --check
```

The Conexus runner executes the unchanged compiled upstream tests with a
referenced event-loop timer. Hosted cancellation tests use unreferenced timers
and require an active server lifetime; a bare test process can otherwise cancel
them before their assertions settle.

## Isolated deployment test

Use new runtime and cache directories, unused loopback ports, and a fresh browser
session. Do not share a production job database between two backend processes.

| Boundary | Acceptance evidence |
| --- | --- |
| Startup | One launcher owns the Python service and one local Conexus child |
| HTTP | Workstation assets, health, authenticated manifest, and workspace respond |
| Configuration | Provider profiles save; reads expose key presence without returning keys |
| Model | A short request reaches the configured endpoint and model |
| Agent tools | A complete Run invokes the real Python tool boundary |
| Delivery | Events reach a terminal state and the expected output appears |
| Persistence | A restart retains reports and conversation/Run history |
| Shutdown | Normal and forced launcher exits release the child and directory lock |
| Instance identity | Agent tools and browser access the same research instance |
| Data | Coverage is checked for the requested instruments, dates, and fields |

A deterministic loopback model proves protocol integration and allows repeatable
tool, failure, and persistence tests. It does not prove that an external provider
accepts a real key, that a chosen model has adequate tool-calling behavior, or
that a research strategy produces useful results.

Perform a live provider test separately using credentials entered in the local
settings UI. Record the provider, model, result, and time without recording the
key or private prompt content.

## Promotion and rollback

Promote the tested integration commit rather than replacing `main` with an
unreviewed branch tip. Retain the current branch's data-quality fixes, runtime
isolation, authentication, and source/result compatibility when merging local
deployment changes.

Before an upgrade, stop the service and back up its state and configuration.
After switching the source/build, repeat HTTP, provider, tool, and persistence
checks. Keep the previous release and its matching state backup until acceptance
is complete. Schema-changing upgrades require a matching backup when rolling
back; changing only the Git commit does not undo a database migration.
