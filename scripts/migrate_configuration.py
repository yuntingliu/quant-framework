"""Explicit offline configuration migration; does not activate SQLite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alphalab.utils.configuration_store import (  # noqa: E402
    ConfigurationStoreError,
    _raise_redacted,
)
from apps.api.services.configuration_migration_service import (  # noqa: E402
    migrate_legacy_configuration,
    verify_legacy_migration,
)


class _Arguments(argparse.ArgumentParser):
    def error(self, _message):
        # argparse's default errors echo rejected values and unknown arguments.
        self._print_message("Check the explicit migration arguments.\n", sys.stderr)
        _raise_redacted(SystemExit(2))


def main():
    parser = _Arguments(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--node", required=True, help="Explicit supported Node 22 executable")
    parser.add_argument(
        "--acknowledge-offline",
        action="store_true",
        help="All old/unmanaged writers, editors and deployment tools are stopped",
    )
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--none",
        action="append",
        choices=("models", "search", "rqdata"),
        default=[],
        help="Explicitly disable a service with null legacy selection",
    )
    args = parser.parse_args()
    operation = verify_legacy_migration if args.verify_only else migrate_legacy_configuration
    try:
        report = operation(
            args.root,
            node_executable=args.node,
            acknowledge_offline=args.acknowledge_offline,
            unselected_intents={key: "none" for key in args.none},
        )
    except ConfigurationStoreError as error:
        print(json.dumps({"error": error.code, "message": str(error)}))
        return 1
    print(json.dumps(report.as_dict()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
