# Contributing to AlphaLab

AlphaLab contributions use individual Git author identities, short-lived
feature branches, and pull requests.

## Setup

```bash
git clone git@github.com:yuntingliu/quant-framework.git
cd quant-framework
python -m pip install -e ".[dev,dashboard,rq]"
npm --prefix apps/desktop ci
alphalab dev doctor
```

RQ is optional for development. Keep `RQ_USER`, `RQ_PASSWORD`, and `RQ_HOST` in
an ignored `.env`; never add credentials or downloaded runtime data to Git.

## Workflow

1. Create a branch from current `main`, such as `feature/strategy-import`.
2. Keep the change inside the owning module described in
   `docs/01_ARCHITECTURE.md`.
3. Add focused tests and run the quality gate below.
4. Push the branch and open a pull request. Do not edit server release
   directories or push directly to protected `main`.

Prefer facade imports from `alphalab` in external scripts and tests. New data
vendors implement provider protocols and register explicitly; vendor behavior
does not belong in framework core. Built-in strategy templates remain generic
and immutable.

## Quality Gate

```bash
python -m pytest tests -q
python scripts/check_facade_imports.py
python -m ruff check alphalab apps/api tests scripts
python scripts/check_repository_hygiene.py
npm --prefix apps/desktop run lint
npm --prefix apps/desktop run build:web
npm --prefix apps/desktop audit --omit=dev
```

Use the current SDK v1 default project and maintained factor templates for
strategy development; see `docs/06_ALPHALAB_SDK_GUIDE.md`. The examples include
a fake market adapter and a FastAPI plus React widget vertical slice.

## Documentation

Write maintained project documentation and examples in English, including the
canonical SDK guide served by the workstation. Preserve stable API identifiers
and source-topic markers when translating or restructuring a guide.

Keep project documentation portable. Describe supported behavior, interfaces,
configuration variables, and reproducible examples. Use relative paths,
environment variables, or explicit placeholders for deployment-specific values.
Host inventories, personal domains and paths, tunnel routes, account setup
status, incident logs, and one-off deployment acceptance records belong outside
the tracked documentation. See [the documentation index](docs/README.md) for
the maintained guides.
