"""Explicit configuration inspection / in-place journal recovery. No restore."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alphalab.utils.configuration_store import ConfigurationStoreError, _raise_redacted  # noqa: E402
from apps.api.services.configuration_recovery_service import (  # noqa: E402
    inspect_configuration_recovery,
    recover_configuration_journal,
)


class _Arguments(argparse.ArgumentParser):
    def error(self, _message):
        self._print_message("Check the explicit recovery arguments.\n", sys.stderr)
        _raise_redacted(SystemExit(2))


def main():
    parser = _Arguments(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "recover-journal"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--installation-id")
    parser.add_argument("--decision-id")
    parser.add_argument("--current-revision")
    parser.add_argument("--node")
    parser.add_argument("--uncertain-acknowledgement", action="store_true")
    parser.add_argument("--acknowledge-offline", action="store_true")
    parser.add_argument("--acknowledge-current-revision", action="store_true")
    args = parser.parse_args()
    try:
        if args.operation == "inspect":
            result = inspect_configuration_recovery(
                args.root,
                acknowledgement_uncertain=args.uncertain_acknowledgement,
                expected_installation_id=args.installation_id,
                expected_decision_id=args.decision_id,
                expected_current_revision=args.current_revision,
            )
        else:
            result = recover_configuration_journal(
                args.root,
                installation_id=args.installation_id,
                decision_id=args.decision_id,
                current_revision=args.current_revision,
                node_executable=args.node,
                acknowledge_offline=args.acknowledge_offline,
                acknowledge_current_revision=args.acknowledge_current_revision,
            )
    except ConfigurationStoreError as error:
        print(json.dumps({"error": error.code, "message": str(error)}))
        return 1
    except KeyboardInterrupt:
        print(
            json.dumps(
                {
                    "error": "INTERRUPTED",
                    "message": "Recovery interrupted. Inspect the installation before further action; do not retry automatically.",
                }
            )
        )
        return 130
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
