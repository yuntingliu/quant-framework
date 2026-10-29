# Risk policies, paper-trading enforcement, and holdings risk

This document describes the paper-trading risk layer added for issue #20. It
covers the versioned rulebook, the single evaluation every order passes through,
the audit trail, and the holdings risk analysis. Everything here is paper-only:
no broker connection, no live orders, and no trading authority for an Agent.

## Rulebooks are versions, never edits

A policy is one immutable version of an account's rulebook
(`alphalab/execution/risk_policy.py`). Changing a limit appends version *n+1*
with a reason and an author; the earlier version stays readable, so an evaluation
recorded months ago can still name the rules it was judged by.
`ResultStore.save_risk_policy` refuses a write that would change an existing
version's content, and the only accepted no-op is re-inserting identical content.

Two tables hold this:

| Table | Holds |
| --- | --- |
| `risk_policies` | One row per `(policy_id, version)`: the limits, the pause rules, the reason, the author, the timestamp |
| `risk_policy_activation` | Which policy version one account, and optionally one strategy, follows now |

A strategy-scoped activation overrides the account policy without replacing it:
`ResultStore.active_risk_policy(account_id, strategy_id)` looks for the strategy
row first, then the account row.

### Limits

| Field | Meaning | Baseline |
| --- | --- | --- |
| `max_order_value` | Largest notional of a single order | 100,000 |
| `max_order_weight` | Largest single order as share of equity (`1.0` = non-binding) | 1.0 |
| `max_position_weight` | Largest resulting weight of one position | 1.0 |
| `max_gross_exposure` | Largest resulting sum of position weights | 1.0 |
| `min_cash_weight` | Cash that must remain after the orders | 0.0 |
| `max_turnover` | Largest turnover of one pass, as share of equity | 2.0 |
| `max_participation` | Largest order as share of average daily volume (`null` disables) | 0.10 |
| `volume_lookback_days` | Trading days behind the volume average | 20 |

The baseline is deliberately non-binding for the limits that did not exist
before, so deploying the policy does not silently refuse portfolios that used to
work. Tightening a limit is a recorded act with a reason.

### Pause and resume

`PauseRules` decides when trading stops and what has to happen before it resumes:
a daily loss limit (default 3%), a drawdown limit (default 15%), an optional
automatic resume level (default 10%), an optional waiting period, and a
`requires_manual_resume` flag (default true, so only an explicit request clears
the pause). The breach is blocking even before the pause is stored, and the
resume decision is evaluated from the rules and the stored pause start — never
from a flag the caller supplies.

The account row carries the state: `status`, `paused_at`, `pause_reason`, and
`resume_reason` / `resumed_at` after an explicit resume.

## One evaluation behind every door

`alphalab/execution/risk_guard.py` exposes `evaluate(policy, snapshot, orders)`.
The manual order door, the rebalance preview and the rebalance execution all call
it; there is no second implementation to drift.

The evaluator applies the orders to a copy of the snapshot and reports:

* **blocking** findings — hard rules, including a paused account, a loss breach,
  insufficient cash, a stale price, a sell larger than the position, a broken
  board lot, or any configured limit being exceeded;
* **warning** findings — allowed, such as liquidity that cannot be evaluated
  because no volume history exists;
* **info** findings — research-quality notes such as concentration or the
  projected account.

Missing information is never treated as zero. A symbol without volume history
produces a visible `liquidity_unevaluated` warning, and a symbol without a price
produces a blocking finding instead of a guessed value.

### Snapshots and the stale-approval rule

Every evaluation freezes the account it judged (`risk_snapshots`) and writes one
audit row (`risk_evaluations`) naming the door, the strategy, the signal, the
policy version, the snapshot, and the verdict. Orders reference the evaluation
that authorised them (`orders.risk_evaluation_id`).

`preview_id` is a hash of the orders, the snapshot and the policy version — and
deliberately not of the door or the clock. `/api/paper/rebalance/execute`
requires that id, recomputes the evaluation against the current account, and
refuses with `409` when the two differ, naming what changed (prices, cash, or the
policy version). An approval therefore cannot be replayed after the account has
moved.

The store applies orders in a single transaction with `BEGIN IMMEDIATE` and rolls
back on any error, so a refused check or a mid-flight failure cannot leave the
account half-modified.

## Holdings risk

`alphalab/analytics/holdings_risk.py` analyses the positions the account really
holds, from daily closes:

* **contributions** — each position's Euler share of portfolio volatility
  (the contributions sum to the portfolio volatility, the shares to 1.0);
* **concentration** — largest position, top-3 share, HHI, and effective number
  of positions;
* **historical scenarios** — the worst non-overlapping windows for these exact
  weights, replayed on the supplied history;
* **parameter shocks** — every price down 10%, volatility × 1.5, and every
  pairwise correlation shifted by +0.2;
* **assumptions and gaps** — frequency, window, source, and an explicit
  `unevaluated` block for positions without enough history.

The analysis is a pure function of frozen inputs (`frozen_inputs` →
`input_hash` → `analyse`). The service stores the inputs next to the result in
`risk_analyses`, keyed by that hash, so a repeated request replays the stored
result instead of recomputing a different number. Risk metrics describe past
co-movement of those holdings; they are not a forecast and not a guarantee.

## API

| Method and path | Purpose |
| --- | --- |
| `POST /api/paper/orders` | Manual order; enforces the same blocking rules and returns `409` with the findings when refused |
| `POST /api/paper/rebalance/preview` | Builds and judges the proposal; returns checks, findings, policy, snapshot and evaluation ids |
| `POST /api/paper/rebalance/execute` | Requires `preview_id`; refuses a stale preview (`409`) and a blocked proposal (`409`) |
| `GET /api/paper/risk/policy` | The policy in force plus the full version history |
| `POST /api/paper/risk/policy` | Publishes the next version (reason required) and activates it |
| `GET /api/paper/risk/evaluations` | The audit trail, newest first |
| `POST /api/paper/risk/analysis` | Holdings risk for the current account, replayed from frozen inputs |
| `POST /api/paper/risk/resume` | Clears a pause explicitly, recording why |

The Risk Console (`apps/desktop/src/widgets/execution/RiskConsole.tsx`) shows the
active rulebook, separates blocking checks from warnings and research notes, and
exposes the holdings analysis.

## Tests

| File | Covers |
| --- | --- |
| `tests/execution/test_risk_policy.py` | Validation, versioning, immutability, activation precedence |
| `tests/execution/test_risk_guard.py` | Thresholds, cash, non-finite values, missing prices, existing positions, pause and resume, fingerprints, severity |
| `tests/execution/test_holdings_risk.py` | Contributions, concentration, scenarios, shocks, unevaluated gaps, reproducibility |
| `tests/execution/test_paper_risk_service.py` | The five acceptance criteria end to end at the service layer |
| `tests/api/test_paper_risk_api.py` | The same guarantees through HTTP, including a direct call that bypasses the UI |

Run them with `python -m pytest tests/execution tests/api/test_paper_risk_api.py -q`.

## Boundaries

The layer governs research and paper accounts only. It does not connect a broker,
submit a live order, or give an Agent trading authority, and it keeps the existing
single SDK/event-based backtesting path rather than introducing a second one.
Backtest results that were already frozen are never rewritten when a policy
changes: they record the rulebook they ran under.
