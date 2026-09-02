# AlphaLab Research Agent Bundle

This bundle publishes the conversational Agent for AlphaLab's current research
project contract. One project contains:

- `recipe.py` for visible RQData acquisition and resumable publication;
- one `factors/<factor_id>.py` file per registered factor;
- `strategy.py` for universe, signal, portfolio, event risk, and execution;
- `validation.py` for performance, Alpha/Beta, and custom post-run research.

The Agent can create a project from the system default, inspect and edit those
canonical files, synchronize and validate runtime data, evaluate factors,
preview the saved strategy, run and compare pinned event backtests, and persist
the final report. Strategy saves automatically record the internal immutable
source package; no separate revision operation is exposed.

Source writes and trusted-local Python execution retain explicit current-user
confirmation boundaries. The bundle exposes no shell, application source-tree
mutation, deployment, legacy pipeline, Python Lab, paper execution, or broker
tool.

The enterprise publication receives bounded dialogue from AlphaLab's shared
server-side conversation history plus a structured research checkpoint as
untrusted context. It reads the lightweight AlphaLab Agent snapshot only when
current live facts are needed. Structured workspace results return as
sanitized `workspaceOutputs`; durable quantitative reports remain native
Document nodes in the publication workspace.

See `docs/04_CONEXUS_AGENT.md` for the full contract.
