"""Explicit offline prepared-to-committed SQLite activation; no migration/restore."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alphalab.utils.configuration_backend import ConfigurationBackend  # noqa: E402
from alphalab.utils.configuration_store import ConfigurationStoreError, _raise_redacted  # noqa: E402
from apps.api.services.configuration_activation_service import activate_sqlite_candidate  # noqa: E402


class _Arguments(argparse.ArgumentParser):
    def error(self, _message):
        self._print_message("Check the explicit activation arguments.\n", sys.stderr)
        _raise_redacted(SystemExit(2))


def main():
    parser = _Arguments(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--installation-id", required=True)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--acknowledge-offline", action="store_true")
    parser.add_argument("--acknowledge-current-credentials", action="store_true")
    args = parser.parse_args()
    try:
        owner = ConfigurationBackend.json(args.root)
        owner.close()
        report = activate_sqlite_candidate(
            owner,
            node_executable=args.node,
            expected_installation_id=args.installation_id,
            expected_decision_id=args.decision_id,
            expected_revision=args.revision,
            acknowledge_offline=args.acknowledge_offline,
            acknowledge_current_credentials=args.acknowledge_current_credentials,
        )
    except ConfigurationStoreError as error:
        print(json.dumps({"error": error.code, "message": str(error)}))
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
