# Portfolio Constraints Feature Development Plan

English | [简体中文](13_PORTFOLIO_CONSTRAINTS_WORK_PLAN.zh-CN.md)

## 1. Current State

The `optimize_portfolio(...)` function in `alphalab/sdk/v1/research.py` currently supports five portfolio optimization methods:

| Method | Purpose |
| --- | --- |
| `equal_weight` | Equal-weight allocation |
| `minimum_variance` | Minimum variance |
| `risk_parity` | Risk parity |
| `hrp` | Hierarchical risk parity |
| `max_sharpe` | Maximum Sharpe ratio |

The existing optimizer already supports **per-security weight caps, target gross allocation, cash, and turnover constraints**. It accounts for current holdings and positions to be exited outside the candidate universe, and records diagnostics including historical sample information, covariance repairs, risk contributions, and solver status.

Two main capability gaps remain:

| Gap | Specific issue |
| --- | --- |
| Incomplete portfolio constraints | No support yet for sector/group, benchmark-relative deviation, or risk-budget constraints |
| Incomplete execution validation | The core currently checks mainly the investment universe, per-security caps, and aggregate allocation; it lacks unified checks for the new constraints, explanations of failures, and pre-execution revalidation |

## 2. Objectives and Requirements

**Project objective: Extend the existing portfolio optimizer so users can specify a broader set of investment restrictions, obtain portfolio weights that satisfy them, and validate both optimization and execution outcomes.**

### 2.1 Three new constraint types

| Constraint | Purpose | Example |
| --- | --- | --- |
| Sector/group | Cap the combined portfolio weight of a sector or asset group | Technology sector weight ≤ 30% |
| Benchmark deviation | Control allocation differences relative to a benchmark index | Technology sector deviation from benchmark ≤ 5 percentage points |
| Risk budget | Limit an asset's or sector's contribution to overall portfolio risk | Technology sector risk contribution ≤ 40% |

### 2.2 Core requirements

1. **Unified rules:** Represent the three constraint types with a common data structure specifying limits, calculation conventions, priority, tolerance, and version.
2. **Reliable data:** Use sector classifications, benchmark weights, and historical returns known as of the decision time. Handle missing values according to predefined rules.
3. **Correct optimization:** Every successfully returned weight vector must satisfy all hard constraints. Failures must distinguish data errors, infeasible constraints, covariance problems, and solver failures.
4. **Pre-trade validation:** The core must check both the optimizer's target weights and the planned weights after execution adjustments; noncompliant plans must be blocked.
5. **Traceable results:** Retain data and constraint versions, realized values, limits, tolerances, slack, and solver diagnostics for independent verification.

Users must explicitly confirm all changes to constraints or optimization methods. Calculations must account for existing holdings, exiting assets, cash, and turnover.

## 3. Scope of Work

The end-to-end workflow is:

**Constraint configuration and data preparation → Portfolio optimization → Constraint result checks → Pre-trade revalidation → Recording and presentation of results.**

### 3.1 Establish unified interfaces

Define three public objects to be shared by the data layer, optimizer, execution core, and API:

| Object | Main fields |
| --- | --- |
| `PortfolioConstraintSet` | Constraint type, upper/lower bounds, tolerance, decision date, rule version |
| `PortfolioConstraintData` | Security-to-sector mapping, benchmark constituents and weights, data effective dates, data version |
| `ConstraintCheck` | Constraint ID, actual value, limit, tolerance, slack, pass/fail status, and failure reason |

Add optional `constraints` and `constraint_data` parameters to `optimize_portfolio(...)`, preserving compatibility with existing SDK calls.

### 3.2 Implement a shared constraint calculation mechanism

Add a common calculation module that consistently computes:

- **Group weights:** The total portfolio allocation to each sector or asset group.
- **Benchmark deviations:** Differences between portfolio weights and corresponding benchmark weights.
- **Risk contributions:** The share of portfolio risk attributable to a security or group, based on the covariance matrix.
- **Constraint checks:** Comparison of actual values with limits, including tolerance, slack, and pass/fail status.

The optimizer uses these calculations during solving; the core invokes the same checking functions against the actual weights before trading, ensuring consistent calculation conventions at both stages.

### 3.3 Extend all five optimization methods

Retain the existing optimization objectives while incorporating the new constraints:

| Method | Implementation |
| --- | --- |
| Equal weight | Generate an equal-weight target, then perform constrained projection |
| Minimum variance | Add all three constraint types to SLSQP |
| Risk parity | Add all three constraint types to SLSQP |
| HRP | Generate an HRP target, then perform constrained projection |
| Maximum Sharpe | Preserve the explicit expected-return requirement and add all three constraint types to SLSQP |

Constrained projection should preserve the original target weights as closely as possible and record the actual projected results. Risk budgets entail nonlinear calculations, so solver stability and each method's supported scope must be tested.

### 3.4 Complete the validation and diagnostics pipeline

Add two checkpoints to the core:

- **Target-weight check:** Verify all hard constraints when optimizer output is received.
- **Planned-weight check:** Recheck planned weights after liquidity, cash, and transaction-cost adjustments.

Write check results to execution records and pass them through the API to the risk service and strategy workbench. Users should be able to see which constraints passed, where failures occurred, and suggested adjustments.

---

## 4. Division of Work Among Three Members

Assign clear ownership across three modules: **Member A owns constraints and data; Member B owns calculations and optimization; Member C owns execution validation and system integration.**

### Member A: Constraint definitions and data interfaces

**Objective: Provide a unified constraint input so the optimizer receives complete, valid, and traceable rules and market data.**

| Task | Specific work | Main files |
| --- | --- | --- |
| A1. Define public objects | Implement the three public objects and fields for sector, benchmark-deviation, and risk-budget constraints; specify calculation conventions, tolerances, versions, and error formats | New `sdk/v1/constraints.py` |
| A2. Implement data validation | Validate security identifiers, duplicates, missing sector classifications, benchmark weights, dates, versions, and required fields; define missing-data policies | `constraints.py` |
| A3. Integrate constraint data | Reuse existing `industry/sector_code`; add adapters that retrieve sector classifications and benchmark constituents/weights as of the decision date and produce standardized data | `dataio/rq_frames.py`, `dataio/` |
| A4. Finish interfaces and tests | Export public types; provide a shared three-asset test fixture; cover valid inputs, missing values, future-dated data, and invalid versions | `sdk/v1/__init__.py`, `test_portfolio_constraint_data.py` |

**Deliverables:** `PortfolioConstraintSet`, `PortfolioConstraintData`, `ConstraintCheck`, data adapters, and input-validation tests.

**Definition of done:** Given a constraint configuration and decision date, generate standardized data ready for optimization and verification; return explicit errors for invalid inputs.

### Member B: Optimization algorithms and solver

**Objective: Integrate all three constraint types into the five existing optimization methods, produce compliant weights, and explain solver outcomes.**

| Task | Specific work | Main files |
| --- | --- | --- |
| B1. Build the constraint calculation module | Implement group weights, benchmark deviations, risk contributions, and `evaluate_constraints(...)`; produce standardized `ConstraintCheck` results | New `sdk/v1/constraint_math.py` |
| B2. Extend the optimization entry point | Add the two optional parameters using Member A's public objects while preserving backward compatibility | `sdk/v1/research.py` |
| B3. Integrate all five methods | Extend `_solve_weights` and `_project_weights` with sector, benchmark-deviation, and risk-budget constraints; preserve existing cash and turnover handling | `research.py` |
| B4. Improve solver diagnostics | Check each constraint after solving; extend `PortfolioOptimizationError` to record infeasible constraints, covariance issues, solver status, and adjustment suggestions | `research.py` |
| B5. Write algorithm tests | Cover five methods, jointly feasible/infeasible combinations of three constraint types, zero turnover, cash, exiting positions, solver failures, and regression tests for the old interface | `test_sdk_research.py` |

**Implementation sequence:** Build the shared constraint calculation functions first; then integrate group, benchmark-deviation, and risk-budget constraints; finally test their combinations across all five methods and assess solver stability.

**Deliverables:** `constraint_math.py`, an enhanced `optimize_portfolio(...)`, structured solver diagnostics, and algorithm tests.

**Definition of done:** All successfully returned weights satisfy declared hard constraints; when no valid portfolio can be produced, the system returns a clear error type, relevant constraint information, and solver diagnostics.

### Member C: Core validation and system integration

**Objective: Integrate the new constraints into strategy execution, including pre-trade revalidation, violation blocking, and result presentation.**

| Task | Specific work | Main files |
| --- | --- | --- |
| C1. Extend the decision object | Add constraint and data-version evidence to `PortfolioDecision`; update serialization, deserialization, and byte-size limits | `sdk/v1/model.py` |
| C2. Validate target weights | Implement `verify_portfolio_constraints(...)`, recheck incoming weights using Member B's calculation functions, and integrate with `_validate_decision` | New `strategy/portfolio_constraints.py`, `strategy/sdk_runtime.py` |
| C3. Revalidate planned weights | Check planned weights after liquidity, cash, and transaction-cost adjustments; block booking when violations occur | `strategy/engine.py` |
| C4. Integrate diagnostics | Record `ConstraintCheck` in execution results; expose it through the API to the risk service and strategy workbench, consistently showing limits, actual values, and error reasons | `apps/api/services/`, related frontend |
| C5. Complete integration tests | Cover target-weight violations, post-adjustment violations, cash and exiting positions, booking prevention, and API/UI data consistency | `test_sdk_v1.py`, API/frontend tests |

**Deliverables:** Core constraint verifier, two-stage pre-trade checks, violation blocking, and API/UI integration tests.

**Definition of done:** Target weights and execution plans are checked against the applicable constraints; violating plans are blocked and check results reach the user interface intact.

### Key interface handoffs

| Handoff | Supplied artifacts | Recipient |
| --- | --- | --- |
| A → B | Standardized constraint rules, sector and benchmark data | Optimizer |
| A → C | The same constraint rules and data, plus version evidence | Core validation |
| B → C | Target weights, solver diagnostics, shared constraint calculation functions | Core and execution flow |
| C → API/UI | Constraint check results, execution status, and failure reasons | Risk service and strategy workbench |

Each feature has one implementation owner. Member B's shared math functions serve both post-optimization checks and Member C's pre-trade revalidation; each check uses the actual weights from its respective business stage.

---

## 5. Development Sequence and Acceptance Criteria

### 5.1 Development plan

**Phase 1: Agree on unified interfaces**

All three members use one shared fixture containing three stocks, sector classifications, benchmark weights, current holdings, and cash. Together they agree on data fields, calculation conventions, tolerances, and diagnostic formats; Member A finalizes the public interfaces.

**Phase 2: Develop in parallel**

- A: Complete constraint objects, input validation, and data adapters.
- B: Use the shared fixture to develop math calculations and optimization solving.
- C: Use the shared fixture to extend the decision object and develop core validation and the integration framework.

**Phase 3: Merge and integrate**

Merge in A → B → C order. Member B integrates the finalized constraint objects, and Member C integrates the shared math functions and optimization results; then run end-to-end tests.

**Phase 4: Joint acceptance**

Member A validates data accuracy and versioning; Member B validates optimization results and solver diagnostics; Member C validates execution blocking and system presentation. All three members confirm that the complete acceptance test suite passes.

### 5.2 Final acceptance criteria

| Item | Acceptance condition | Owner |
| --- | --- | --- |
| Constraints and data | Three types are configurable; inputs are valid, point-in-time correct, and version-traceable | A |
| Portfolio optimization | Five methods solve according to their supported rules; successful weights satisfy all hard constraints | B |
| Failure diagnostics | Data errors, infeasible constraints, covariance problems, and solver failures are distinguishable | A / B |
| Pre-trade validation | Both target and planned weights are checked; violating plans are blocked | C |
| System integration | Diagnostics agree across the SDK, event engine, API, and UI | C |
| Regression testing | Existing optimizers, cash and turnover accounting, and event-driven workflows continue to work | Respective module owners |

### 5.3 Development baseline and dependencies

Use dev3 commit `d0ce50c` and PR #5 as the development baseline, continuing with the existing SDK and event-driven backtesting path.

- **Issue #14:** Data-quality checks, point-in-time (PIT) auditing, and targeted gap fixes.
- **Issue #15:** Reproducibility management for data snapshots, code, and environment versions.

Member A is responsible for integrating the constraint data interfaces with the deliverables from these two existing issues. Before the dependencies are complete, development may proceed using adapters that retain date and version information. Formal delivery requires integration of the dependencies.

Maintain the shared `docs/02_STRATEGY_SDK_V1_CONTRACT.md` document by module: A documents constraint objects and data conventions; B documents optimization interfaces and solver errors; C documents execution validation and result propagation.

**Final outcome: A complete workflow for sector, benchmark-deviation, and risk-budget constraints, from data input and portfolio optimization through result verification, pre-trade risk controls, and diagnostic presentation.**