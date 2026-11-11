"""Acceptance checks for the reference strangle after completing the lab."""

import json
from math import isclose
from pathlib import Path

from alphalab.tools import ToolRegistry

from .options_homework.models import ReportRequest
from .options_homework.tools import register_lab_tools
from .run_report import collect


ROOT = Path(__file__).resolve().parent


def main():
    request = ReportRequest.model_validate_json((ROOT / "strangle.json").read_text(encoding="utf-8"))
    expected = json.loads((ROOT / "reference_checks.json").read_text(encoding="utf-8"))
    registry = ToolRegistry()
    register_lab_tools(registry)
    actual = collect(request, registry)

    assert isclose(actual["expiry"]["entry_credit_cny"], expected["entry_credit_cny"], abs_tol=0.01)
    expiry = {row["terminal_spot"]: row["pnl_cny"] for row in actual["expiry"]["rows"]}
    for spot in expected["break_evens"]:
        assert isclose(expiry[spot], 0, abs_tol=0.01), f"Expiry break-even at {spot}"
    for key, value in expected["net_greeks"].items():
        assert isclose(actual["greeks"]["net"][key], value, abs_tol=0.01), key

    grid = {(row["spot_shock"], row["iv_shift"]): row for row in actual["stress"]["rows"]}
    assert len(grid) == len(request.spot_shocks) * len(request.iv_shifts)
    assert isclose(grid[0, 0]["full_pnl_cny"], 0, abs_tol=0.01), "Zero shock"
    assert isclose(grid[-0.2, 0]["full_pnl_cny"], expected["drop_20_model_to_model_cny"], abs_tol=0.02)

    resized = request.model_dump(mode="json")
    for leg in resized["legs"]:
        leg["quantity"] = -2
    smaller = collect(ReportRequest.model_validate(resized), registry)
    smaller_grid = {(row["spot_shock"], row["iv_shift"]): row for row in smaller["stress"]["rows"]}
    for shock, row in grid.items():
        assert isclose(smaller_grid[shock]["full_pnl_cny"], row["full_pnl_cny"] / 5, abs_tol=0.02), shock
    print("Registered finance tools passed the reference strangle checks.")


if __name__ == "__main__":
    main()
