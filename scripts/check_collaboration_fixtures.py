"""Verify the frozen development corpus, or inspect the exact safe Agent input."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphalab.collaboration import (  # noqa: E402
    check_development_oracle,
    load_agent_input,
    validate_run_record,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "tests/fixtures/collaboration/dev")
    parser.add_argument("--show-agent-input", metavar="FIXTURE_ID")
    parser.add_argument("--record", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.show_agent_input:
        result = load_agent_input(args.corpus, args.show_agent_input)
    else:
        result = check_development_oracle(args.corpus)
        for path in args.record:
            validate_run_record(json.loads(path.read_text(encoding="utf-8")))
        result["validated_records"] = len(args.record)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
