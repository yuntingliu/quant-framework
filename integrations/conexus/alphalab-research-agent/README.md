# AlphaLab Research Agent Bundle

This bundle publishes the conversational Agent for AlphaLab's current research
project contract. One project contains:

- `recipe.py` for visible RQData acquisition and resumable publication;
- one `factors/<factor_id>.py` file per registered factor;
- `strategies/<strategy_id>.py` for each independent strategy (universe, signal, portfolio, event risk, and execution);
- `validation.py` for performance, Alpha/Beta, and custom post-run research.

The Agent can create a project from the system default, inspect and edit those
canonical files, synchronize and validate runtime data, evaluate factor research
with next-open evidence, preview the saved strategy, run and compare pinned event
backtests, inspect their frozen named validation outputs, and persist the final
report. Strategy saves automatically record the internal immutable
source package; no separate revision operation is exposed. Strategies share the
project recipe, factors and validation. Batch backtests pin every selected
strategy and return independent jobs for comparison. `strategy.py` remains a
compatibility alias for the migrated `main` strategy.

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

The local Harness receives bounded dialogue from AlphaLab's shared
server-side conversation history plus a structured research checkpoint as
untrusted context. It reads the lightweight AlphaLab Agent snapshot only when
current live facts are needed. Terminal Runs identify structured output changes
by node ID; AlphaLab resolves their bounded payloads from the durable
workspace and accepts only nodes updated by that exact Run.
Quantitative reports remain native Document nodes in the same workspace.

See `docs/04_CONEXUS_AGENT.md` for the full contract.

`workspace-output.schema.json` describes AlphaLab's application payloads. It is
not attached to a Harness exposure: current Conexus Agent exposures accept a
request and store deliverables through native node operations.

The prompt includes the complete `workspaceResult` schema. Requested machine
fields must be delivered in its `columns` and `rows`, even when the report also
contains a Markdown table. Keep numeric tool precision in the data and round
only presentation text. Preparation rejects incomplete or inconsistent tables.

The included local host counts every proposed native tool call, including batch
members, against a 40-call run budget and reports the remaining allowance to the
model. Reserve at least five calls for final delivery. Tests may supply a smaller
explicit budget; an over-limit batch is rejected in full before dispatch.

The local host independently authorizes project changes, cancellation and local
Python execution. A model's confirmation flags do not grant permission. Before
dispatch, the existing Run interaction UI shows the exact proposed operation;
only an authenticated **Approve** response resumes that frozen operation. Denial,
cancellation and shutdown do not dispatch it, and approval cannot be replayed for
another operation or run. Read-only queries continue without approval. The host
accepts only the original published tools and blocks direct shell execution.
Natural-language permission in the chat is not itself a host authorization token,
so the local UI currently requires this concrete confirmation even when the chat
already requested execution. Approvals use Run polling and are not restored after
a host restart; interrupted runs retain the existing recovery behavior.

These runtime protections are wired into `integrations/conexus/local-host.mjs`.
Remote or custom Conexus hosts must provide equivalent host-side authorization
and accounting; importing the prompt or registering the canvas alone does not
install either protection. The benchmark can supply trusted authorization
decisions from its task policy, outside the model, without simulating UI latency.

Explicit bounded data-query rows are preserved up to the 50-row limit, with
truthful upstream and local truncation flags. Nested diagnostic arrays keep
their compact summaries and internal fields remain filtered. This allows
cross-dataset audits to inspect middle records without repeatedly fetching
smaller samples of an already bounded response.
