# AlphaLab Documentation

## Getting started

- [Overview and installation](../README.md): capabilities, prerequisites, and startup.
- [SDK guide](06_ALPHALAB_SDK_GUIDE.md): strategy, factor, data recipe, validation, and technical-evidence examples; also the source for the workstation documentation panel.
- [Data operations](04_DATA_OPERATIONS.md): RQ templates, synchronization, resumption, coverage checks, and financial definitions.
- [Conexus Agent](04_CONEXUS_AGENT.md): optional Agent tools, conversations, reports, and delivery contracts.

- [First research project](guides/first-research-project.md): a guided experiment and codebase walkthrough.

## Development reference

- [Architecture](01_ARCHITECTURE.md): module responsibilities, workbenches, and execution flow.
- [Strategy SDK contract](02_STRATEGY_SDK_V1_CONTRACT.md): events, schedules, inputs, outputs, and backtest rules.
- [Data SDK contract](05_DATA_SDK_V1_CONTRACT.md): custom providers and the DataEngine interface.
- [Development guide](03_DEVELOPMENT_GUIDE.md): code placement, frontend/backend contracts, and checks.
- [Testing guide](guides/testing.md): test groups and common commands.
- [Contributing](../CONTRIBUTING.md): branches and review workflow.
- [Multifactor research notes](guides/multifactor-research.md): conceptual factor evaluation and monthly portfolio construction; illustrative rules, not SDK defaults.

## Conexus framework reference

For working on the Agent framework itself rather than on AlphaLab's research
logic. These describe the pinned core snapshot and are validated by
`scripts/check_conexus_docs.py`.

- [Conexus framework architecture](10_CONEXUS_FRAMEWORK.md): module boundary; Agent, Harness, node, capability, and tool; HTTP surface, state on disk, trust boundary, and current constraints.
- [Conexus execution lifecycle](11_CONEXUS_EXECUTION_LIFECYCLE.md): one Run end to end, asynchronous work, cancellation, browser reconnection, and Host restart.
- [Extending and upgrading Conexus](12_CONEXUS_EXTENSION_GUIDE.md): tools, host capabilities, models, search, skills, Agents, compatibility checks, and moving the pinned core.
- [Conexus examples](../examples/conexus/README.md): runnable minimal and financial examples with model and tool test doubles, and the lifecycle validation suite.

## Deployment reference

- [Deployment branches and dev3](09_DEPLOYMENT_BRANCHES.md): exact deployed revision, legacy branch mapping, local architecture, private configuration and management.
- [Local deployment](07_LOCAL_DEPLOYMENT.md): bundled Conexus runtime and model-provider settings.
- [Deployment acceptance](08_LOCAL_DEPLOYMENT_VALIDATION.md): verification before promotion.

- [Deployment](05_DEPLOYMENT.md): building, configuration, persistence, updates, and operational checks.
- [Linux services](guides/linux-deployment.md): systemd example and release script.
- [Conexus integration](../examples/deployment/conexus-cloud/README.md): local compatibility, instance binding, service identity, and model authorization.

## Release notes

- [0.7.0](releases/0.7.0-local-agent.md): bundled local Conexus, provider settings, and integrated research safeguards.
- [0.6.2](releases/0.6.2-dev-liu.md): point-in-time financial corrections and quality-portfolio replication.
- [0.6.1](releases/0.6.1-dev-liu.md): technical evidence, pattern factors, and annual-return audits.
- [0.6.0](releases/0.6.0-integration.md): workstation and research-module integration.

Experimental findings apply only to the stated samples, rules, and cost assumptions. Use the current guides and interface contracts for current behavior.
