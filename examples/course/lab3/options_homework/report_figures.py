"""Render Question 1 figures from the registered tools' results."""

from math import isclose
from pathlib import Path

from .models import ReportRequest


def write_figures(request: ReportRequest, results: dict, directory: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.ticker import PercentFormatter, StrMethodFormatter

    payoff = sorted(results["expiry"]["rows"], key=lambda row: row["terminal_spot"])
    fig, ax = plt.subplots(figsize=(7.5, 3.8), layout="constrained")
    try:
        ax.plot([row["terminal_spot"] for row in payoff],
                [row["pnl_cny"] for row in payoff], "o-", color="#185b85", markersize=4,
                label="Expiry P&L")
        ax.axhline(0, color="#666666", linewidth=0.8)
        for strike in sorted({leg.strike for leg in request.legs}):
            ax.axvline(strike, color="#9a9a9a", linestyle=":", linewidth=0.8)
            ax.annotate(f"K={strike:g}", (strike, 0.97), xycoords=("data", "axes fraction"),
                        xytext=(3, 0), textcoords="offset points", va="top", fontsize=8)
        zeros = [row["terminal_spot"] for row in payoff if isclose(row["pnl_cny"], 0, abs_tol=0.005)]
        if zeros:
            ax.scatter(zeros, [0] * len(zeros), color="#b54e35", s=24, zorder=4,
                       label="Zero P&L on supplied grid")
        ax.set(xlabel="ETF price at expiry (CNY per unit)", ylabel="Position P&L (CNY)",
               title="Expiry payoff including entry credit")
        ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
        ax.grid(axis="y", alpha=0.22)
        ax.legend(frameon=False)
        fig.savefig(directory / "expiry_payoff.png", dpi=160)
    finally:
        plt.close(fig)

    rows = results["stress"]["rows"]
    shifts = sorted({row["iv_shift"] for row in rows})
    columns = min(3, len(shifts))
    nrows = (len(shifts) + columns - 1) // columns
    fig, axes = plt.subplots(nrows, columns, squeeze=False, sharex=True, sharey=True,
                             figsize=(3.7 * columns, 3.15 * nrows), layout="constrained")
    try:
        for index, shift in enumerate(shifts):
            ax = axes.flat[index]
            panel = sorted((row for row in rows if row["iv_shift"] == shift),
                           key=lambda row: row["spot_shock"])
            spots = [row["spot_shock"] for row in panel]
            ax.plot(spots, [row["full_pnl_cny"] for row in panel], "o-", color="#185b85",
                    markersize=4, label="Full reprice")
            ax.plot(spots, [row["local_pnl_cny"] for row in panel], "s--", color="#b54e35",
                    markersize=3, label="Local estimate")
            ax.axhline(-request.scenario_budget_cny, color="#666666", linestyle=":", linewidth=1,
                       label="Loss budget")
            ax.set_title(f"IV shift {100 * shift:+g} points")
            ax.xaxis.set_major_formatter(PercentFormatter(1))
            ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
            ax.grid(axis="y", alpha=0.22)
        for ax in list(axes.flat)[len(shifts):]:
            ax.set_visible(False)
        axes[0, 0].legend(frameon=False, fontsize=8)
        fig.supxlabel("Instantaneous ETF price shock")
        fig.supylabel("Change in position value (CNY)")
        fig.savefig(directory / "stress_comparison.png", dpi=160)
    finally:
        plt.close(fig)
