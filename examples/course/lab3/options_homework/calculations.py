"""Implement these three functions in the homework. See the lab handout for units."""

from .models import ReportRequest


def expiry_pnl(request: ReportRequest) -> dict:
    """Return entry_credit_cny and rows with terminal_spot and pnl_cny."""
    raise NotImplementedError("Implement signed expiry P&L including entry cash flow")


def portfolio_greeks(request: ReportRequest) -> dict:
    """Return legs and net. Each has delta, gamma, vega_1pct and theta_day."""
    raise NotImplementedError("Implement position-level Greeks and portfolio totals")


def stress_grid(request: ReportRequest) -> dict:
    """Return scenario rows with full_pnl_cny and local_pnl_cny."""
    raise NotImplementedError("Implement model-to-model stress and the Greek approximation")
