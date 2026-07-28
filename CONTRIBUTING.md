# Contributing to AlphaLab

AlphaLab uses personal GitHub accounts, short-lived feature branches, and pull
requests. The shared Pop!_OS `dev` account is a deployment runtime, not a shared
Git author identity.

## Setup

```bash
git clone git@github.com:yuntingliu/quant-framework.git
cd quant-framework
python -m pip install -e ".[dev,dashboard,rq]"
npm --prefix dashboard/frontend ci
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
python -m ruff check alphalab dashboard/backend tests scripts
python scripts/check_repository_hygiene.py
npm --prefix dashboard/frontend run lint
npm --prefix dashboard/frontend run build:web
npm --prefix dashboard/frontend audit --omit=dev
```

Useful starting points are under `examples/`: a local strategy YAML, a fake
market adapter, and a FastAPI plus React widget vertical slice.
