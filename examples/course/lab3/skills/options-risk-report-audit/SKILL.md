---
name: options-risk-report-audit
description: Audit the generated AlphaLab Lab 3 Question 1 option risk reports against tool inputs, signed cash flows, Greek units, and stress scenarios. Use when reviewing the ten-pair and two-pair position report bundles.
---

# Audit the Lab 3 option risk reports

Read the Lab 3 [assignment](../../assignment.md) and [report template](../../report_template.md). Ask for both generated report directories and the independent test result. Use the JSON inputs and rows to check the claims in each Markdown report and figure.

1. Confirm identical case inputs except for signed quantities. Check both short legs create opening credit. Recalculate an expiry point on each side from the signed intrinsic payoffs, the entry premiums, and each multiplier. Check the reported break-evens against the payoff data.
2. Reconcile each net Greek to the leg totals and check units. Check the local stress estimate using a price change in CNY and an IV change in percentage points. Confirm that zero shock produces zero model-to-model P&L and that the two-pair P&L equals one fifth of the ten-pair P&L at matched shocks.
3. Find the largest absolute error between full repricing and the local estimate. Check the 20% spot decline with unchanged IV and the worst grid case against the CNY 10,000 budget. Keep expiry P&L distinct from instantaneous mark changes. A finite scenario grid cannot establish maximum loss.
4. Check that the plots and Markdown refer to the same JSON and that the submitted independent test fails under a deliberate sign or multiplier mutation. Record each finding with the evidence and a concrete correction. If the student has not run a check, label it unverified. Leave the calculation implementation and the final position recommendation to the student.
