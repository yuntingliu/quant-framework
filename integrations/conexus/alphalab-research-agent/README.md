# AlphaLab Research Agent Bundle

This bundle publishes the conversational Agent for AlphaLab's current research
project contract. One project contains:

- `recipe.py` for visible RQData acquisition and resumable publication;
- one `factors/<factor_id>.py` file per registered factor;
- `strategy.py` for universe, signal, portfolio, event risk, and execution;
- `validation.py` for performance, Alpha/Beta, and custom post-run research.

The Agent can create a project from the system default, inspect and edit those
canonical files, synchronize and validate runtime data, evaluate factor research
with next-open evidence, preview the saved strategy, run and compare pinned event
backtests, inspect their frozen named validation outputs, and persist the final
report. Strategy saves automatically record the internal immutable
source package; no separate revision operation is exposed.

Only two AlphaLab Tool nodes are exposed: `alphalab_project_files` and
`alphalab_project_run`. Their compact `command + args` contract is documented
by `skills/alphalab-sdk-v1/SKILL.md`, which registration injects into the Agent
prompt before publication. The skill is a bundle resource, not a workspace
project file or canvas node, so SDK guidance does not couple research projects
to Conexus node persistence.

Source writes and trusted-local Python execution retain explicit top-level
current-user confirmation boundaries. Full project-file edits still go through
the canonical AlphaLab validation and revision APIs. The bundle exposes no
shell, arbitrary server path, application source-tree mutation, deployment,
legacy pipeline, Python Lab, paper execution, or broker tool.

The enterprise publication receives bounded dialogue from AlphaLab's shared
server-side conversation history plus a structured research checkpoint as
untrusted context. It reads the lightweight AlphaLab Agent snapshot only when
current live facts are needed. Terminal Runs identify structured output changes
by node ID; AlphaLab resolves their bounded payloads from the durable
publication workspace and accepts only nodes updated by that exact Run.
Quantitative reports remain native Document nodes in the same workspace.

See the [Agent contract](../../../docs/04_CONEXUS_AGENT.md) for the full contract,
the [deployment runbook](../../../deploy/conexus-cloud/README.md) for instance
binding and model funding, and [deployment status](../../../docs/05_DEPLOYMENT.md)
for current dev3 readiness. A successful tool query does not establish that a
new publication can start a complete model-backed Agent run.
