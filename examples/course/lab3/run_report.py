"""Discover and invoke the lab tools, then write Markdown and JSON together."""

import argparse
import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from alphalab.tools import ToolRegistry

from .options_homework.models import ReportRequest
from .options_homework.tools import register_lab_tools


DEFAULT_CASE = Path(__file__).with_name("strangle.json")


TOOLS = {
    "case": "options_lab.case_inputs",
    "expiry": "options_lab.expiry",
    "greeks": "options_lab.greeks",
    "stress": "options_lab.stress",
}


def http_json(url, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def collect(request, registry, base_url=None, *, inputs_only=False):
    payload = request.model_dump(mode="json")
    endpoint = f"{base_url.rstrip('/')}/api/agent/data-tools" if base_url else None
    names = TOOLS if not inputs_only else {"case": TOOLS["case"]}
    available = http_json(endpoint)["tools"] if endpoint else registry.describe()
    registered = {item["name"] for item in available}
    missing = set(names.values()) - registered
    if missing:
        raise ValueError("Register these tools before generating the report: " + ", ".join(sorted(missing)))
    if endpoint:
        return {
            key: http_json(f"{endpoint}/{quote(name)}/invoke", {"input": payload})["result"]
            for key, name in names.items()
        }
    return {key: registry.invoke(name, payload) for key, name in names.items()}


def render_markdown(request, results, interpretation):
    case = results["case"]
    lines = [
        f"# Option risk report for {case['underlying']}", "",
        f"Valuation date **{case['as_of']}**. Expiry **{case['expiry']}**. Spot **{request.spot:.4f} CNY**.", "",
        "| Contract | Side and quantity | Strike | ETF units per contract | Entry CNY per unit | IV |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for leg in request.legs:
        lines.append(f"| {leg.contract} ({leg.kind}) | {leg.quantity:+d} | {leg.strike:.2f} | "
                     f"{leg.multiplier:,} | {leg.entry_price:.4f} | {leg.iv:.2%} |")
    lines += ["", "## Expiry P&L", "",
              f"Entry credit **{results['expiry']['entry_credit_cny']:,.2f} CNY**. "
              "Expiry P&L uses the entry cash flow and signed exercise payoffs before costs.", "",
              "| ETF at expiry (CNY) | P&L (CNY) |", "|---:|---:|"]
    for row in results["expiry"]["rows"]:
        lines.append(f"| {row['terminal_spot']:.4f} | {row['pnl_cny']:+,.2f} |")
    lines += ["", "## Portfolio sensitivities", "",
              "| Position | Delta (ETF units) | Gamma (ETF units/CNY) | Vega (CNY/vol point) | Theta (CNY/day) |",
              "|---|---:|---:|---:|---:|"]
    greeks = results["greeks"]
    for label, values in [(row["contract"], row) for row in greeks["legs"]] + [("Net", greeks["net"])]:
        numbers = [f"{values[k]:+,.1f}" for k in ("delta", "gamma", "vega_1pct", "theta_day")]
        lines.append("| " + " | ".join([label, *numbers]) + " |")
    lines += ["", "## Instantaneous stress", "",
              f"Each strike's IV shifts by the specified amount. Time to expiry stays fixed. "
              f"Annual rate {request.rate:.4%}, continuous yield {request.dividend_yield:.4%}. "
              "Model-to-model P&L before transaction costs.", "",
              "| ETF shock | IV shift (vol points) | Full reprice (CNY) | Local estimate (CNY) |",
              "|---:|---:|---:|---:|"]
    rows = results["stress"]["rows"]
    for row in rows:
        lines.append(f"| {row['spot_shock']:+.0%} | {100*row['iv_shift']:+.1f} | "
                     f"{row['full_pnl_cny']:+,.2f} | {row['local_pnl_cny']:+,.2f} |")
    worst = min(rows, key=lambda row: row["full_pnl_cny"])
    lines += ["", f"Largest modeled loss on this grid **{max(0, -worst['full_pnl_cny']):,.2f} CNY**. "
              f"Scenario budget **{request.scenario_budget_cny:,.2f} CNY**.", "",
              "## Interpretation and decision", "", interpretation.strip(), ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path, nargs="?", default=DEFAULT_CASE)
    parser.add_argument("--api", help="URL of your local AlphaLab API, for example http://127.0.0.1:8765")
    parser.add_argument("--inputs-only", action="store_true", help="Run the completed starter tool")
    parser.add_argument("--out", type=Path, default=Path("runs/strangle"))
    parser.add_argument("--interpretation", type=Path, help="Your written interpretation in Markdown")
    args = parser.parse_args()
    request = ReportRequest.model_validate_json(args.case.read_text(encoding="utf-8"))
    registry = ToolRegistry()
    register_lab_tools(registry)
    try:
        results = collect(request, registry, args.api, inputs_only=args.inputs_only)
    except ValueError as exc:
        parser.error(str(exc))
    if args.inputs_only:
        print(json.dumps(results, indent=2))
        return
    if args.interpretation is None:
        parser.error("Provide --interpretation with your financial interpretation")
    markdown = render_markdown(request, results, args.interpretation.read_text(encoding="utf-8"))
    payload = {"inputs": request.model_dump(mode="json"), "results": results}
    raw = json.dumps(payload, indent=2, allow_nan=False)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(raw + "\n", encoding="utf-8")
    (args.out / "report.md").write_text(markdown, encoding="utf-8")
    print(args.out.resolve())


if __name__ == "__main__":
    main()
