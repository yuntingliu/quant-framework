---
name: options-risk-report-audit
description: Audit the AlphaLab Lab 3 Question 2 position-size comparison against tool inputs, signed cash flows, Greek units and stress scenarios.
---

# Audit the Lab 3 position comparison

Read the Lab 3 [assignment](../../assignment.md) and [report template](../../report_template.md). Read the report directory and locate the independent test in the lab source. In `report.json`, inspect `ten_pairs` and `two_pairs`, each with `inputs` and `results`. Use them to check the claims in `report.md` and its figures.

1. Confirm identical case inputs except for signed quantities. Check both short legs create opening credit. Recalculate an expiry point on each side from the signed intrinsic payoffs, the entry premiums, and each multiplier. Check the reported break-evens against the payoff data.
2. Reconcile each net Greek to the leg totals and check units. Check the local stress estimate using a price change in CNY and vega in CNY per IV percentage point. Multiply the decimal IV shift by 100 before applying vega. Confirm that zero shock produces zero model-to-model P&L and that the two-pair P&L equals one fifth of the ten-pair P&L at matched shocks.
3. Find the largest absolute error between full repricing and the local estimate. Check the 20% spot decline with unchanged IV and the worst grid case against the CNY 10,000 budget. Keep expiry P&L distinct from instantaneous mark changes. A finite scenario grid cannot establish maximum loss.
4. Check that the plots and Markdown refer to the same JSON and that the independent test fails under a deliberate sign or multiplier mutation. Record each finding with the evidence and a concrete correction. If the student has not run a check, label it unverified. Leave the calculation implementation and the final position recommendation to the student.
