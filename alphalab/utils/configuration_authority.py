"""Private Node admission helper. No activation, provider calls or credentials IPC.

The child owns real OS leases, not a caller's asserted identity. Idle ownership
is distinct from active-work admission. Only existing JSON / explicitly inactive
SQLite operation is admitted; the bounded stdin protocol never carries secrets.
"""

from __future__ import annotations

import os
import sys
from contextlib import ExitStack, nullcontext

from alphalab.utils.configuration_decision import check_backend_authority
from alphalab.utils.configuration_maintenance import (
    agent_access,
    checked_root,
    execution_access,
    json_configuration_access,
)
from alphalab.utils.configuration_store import ConfigurationStoreError


def main():
    try:
        if len(sys.argv) != 3 or sys.argv[2] not in {"json", "inactive_sqlite", "sqlite"}:
            raise ValueError
        root, backend = checked_root(sys.argv[1]), sys.argv[2]
        secret = None
        if backend == "sqlite":
            wire = sys.stdin.buffer.readline(80)
            if len(wire) != 70 or not wire.startswith(b"auth ") or not wire.endswith(b"\n"):
                raise ValueError
            secret = wire[5:-1].decode("ascii")

        def verify_owner():
            if backend == "sqlite":
                from alphalab.utils.configuration_decision import validate_committed_authority
                from alphalab.utils.configuration_owner import verify_agent_owner

                identity = validate_committed_authority(root)
                verify_agent_owner(root, identity, secret, child_pid=os.getppid())

        with ExitStack() as lifetime, ExitStack() as work:
            # Initial admission cannot race an offline transition. Idle ownership
            # subsequently retains its own OS lease, not the active execution gate.
            with execution_access(root):
                lifetime.enter_context(agent_access(root))
                lifetime.enter_context(
                    json_configuration_access(root) if backend == "json" else nullcontext()
                )
                check_backend_authority(root, backend=backend)
                verify_owner()
            sys.stdout.buffer.write(b"leased\n")
            sys.stdout.buffer.flush()
            users = 0
            # A bounded, credential-free protocol. Each admitted callback keeps a
            # credit until its actual completion, including after outward abort.
            while command := sys.stdin.buffer.readline(16):
                if command == b"enter\n":
                    if not users:
                        try:
                            work.enter_context(execution_access(root))
                        except ConfigurationStoreError as error:
                            if error.code != "STORE_BUSY":
                                raise
                            sys.stdout.buffer.write(b"denied\n")
                            sys.stdout.buffer.flush()
                            continue
                    check_backend_authority(root, backend=backend)
                    verify_owner()
                    users += 1
                    acknowledgement = b"admitted\n"
                elif command == b"leave\n" and users:
                    users -= 1
                    if not users:
                        work.close()
                    acknowledgement = b"released\n"
                else:
                    raise ValueError
                sys.stdout.buffer.write(acknowledgement)
                sys.stdout.buffer.flush()
        return 0
    except BaseException:
        # Never print an exception, path, private document or child traceback.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
