"""Render one Question 2 report from the two matched strangle runs."""

import json
from pathlib import Path

from .models import ReportRequest
from .report_figures import write_figures


def two_pair_case(ten: ReportRequest) -> ReportRequest:
    if len(ten.legs) != 2 or {leg.kind for leg in ten.legs} != {"P", "C"} or any(
        leg.quantity != -10 for leg in ten.legs
    ):
        raise ValueError("Size comparison requires the original short-ten-put and short-ten-call case")
    payload = ten.model_dump(mode="json")
    for leg in payload["legs"]:
        leg["quantity"] = -2
    return ReportRequest.model_validate(payload)


def _scenario_rows(results: dict) -> dict:
    rows = results["stress"]["rows"]
    indexed = {(row["spot_shock"], row["iv_shift"]): row for row in rows}
    if not rows or len(indexed) != len(rows):
        raise ValueError("Both stress grids must contain unique scenarios")
    return indexed


def write_comparison_report(ten, ten_results, two, two_results, interpretation, out: Path) -> None:
    if two != two_pair_case(ten):
        raise ValueError("The two-pair case must differ only in the two signed quantities")
    cases = [("ten_pairs", ten, ten_results), ("two_pairs", two, two_results)]
    big = _scenario_rows(ten_results)
    small = _scenario_rows(two_results)
    if big.keys() != small.keys():
        raise ValueError("The two stress grids must cover the same scenarios")

    lines = [
        "# Short-strangle position-size comparison", "",
        f"{ten.underlying} at **{ten.spot:g} CNY per ETF unit** on {ten.as_of}. "
        f"Expiry {ten.expiry}. Continuous rate {ten.rate:.4%} and yield {ten.dividend_yield:.4%}.", "",
        "| Leg | Strike | Units/contract | Premium per unit (CNY) | IV | Ten-pair qty | Two-pair qty |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for large_leg, small_leg in zip(ten.legs, two.legs, strict=True):
        lines.append(f"| {large_leg.kind} ({large_leg.contract}) | {large_leg.strike:g} | "
                     f"{large_leg.multiplier:,} | {large_leg.entry_price:.4f} | {large_leg.iv:.2%} | "
                     f"{large_leg.quantity:+d} | {small_leg.quantity:+d} |")
    lines.extend(["", "## Expiry payoff and portfolio Greeks", "",
                  "| Size | Entry credit (CNY) | Net delta (ETF units) | Net gamma (ETF units/CNY) | "
                  "Net vega (CNY/IV point) | Net theta (CNY/day) |",
                  "|---|---:|---:|---:|---:|---:|"])
    for label, results in [("Ten pairs", ten_results), ("Two pairs", two_results)]:
        net = results["greeks"]["net"]
        lines.append(f"| {label} | {results['expiry']['entry_credit_cny']:,.2f} | {net['delta']:+,.1f} | "
                     f"{net['gamma']:+,.1f} | {net['vega_1pct']:+,.2f} | {net['theta_day']:+,.2f} |")
    lines.extend(["", "![Ten-pair expiry payoff](ten_pairs/expiry_payoff.png)", "",
                  "![Two-pair expiry payoff](two_pairs/expiry_payoff.png)", "",
                  "## Instantaneous stress", "",
                  "P&L is the change from the original model value in CNY. The same IV shift applies "
                  "to both legs while time, rate and yield stay fixed.", "",
                  "| Spot shock | IV shift (points) | Ten full | Ten local | Two full | Two local |",
                  "|---:|---:|---:|---:|---:|---:|"])
    for shock, iv in sorted(big):
        row, resized = big[shock, iv], small[shock, iv]
        lines.append(f"| {shock:+.0%} | {100 * iv:+g} | {row['full_pnl_cny']:+,.2f} | "
                     f"{row['local_pnl_cny']:+,.2f} | {resized['full_pnl_cny']:+,.2f} | "
                     f"{resized['local_pnl_cny']:+,.2f} |")
    lines.extend(["", "![Ten-pair stress comparison](ten_pairs/stress_comparison.png)", "",
                  "![Two-pair stress comparison](two_pairs/stress_comparison.png)", "",
                  f"## CNY {ten.scenario_budget_cny:,.0f} loss budget", "",
                  "| Size | Scenario | Spot shock | IV shift (points) | Full P&L (CNY) | Budget |",
                  "|---|---|---:|---:|---:|---|"])
    for label, grid in [("Ten pairs", big), ("Two pairs", small)]:
        worst = min(grid, key=lambda key: grid[key]["full_pnl_cny"])
        checks = [("Worst on grid", worst)]
        if (-0.2, 0) in grid:
            checks.insert(0, ("Price-only decline", (-0.2, 0)))
        for scenario, key in checks:
            pnl = grid[key]["full_pnl_cny"]
            verdict = "Within budget" if -pnl <= ten.scenario_budget_cny else "Over budget"
            lines.append(f"| {label} | {scenario} | {key[0]:+.0%} | {100 * key[1]:+g} | {pnl:+,.2f} | {verdict} |")

    lines.extend(["", "## Interpretation and decision", "", interpretation.strip(), ""])
    payload = {key: {"inputs": request.model_dump(mode="json"), "results": results}
               for key, request, results in cases}
    raw = json.dumps(payload, indent=2, allow_nan=False)
    for key, request, results in cases:
        directory = out / key
        directory.mkdir(parents=True, exist_ok=True)
        write_figures(request, results, directory)
    (out / "report.json").write_text(raw + "\n", encoding="utf-8")
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
