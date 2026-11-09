"""Test-owned backend lifetime and private Node handoff, synthetic data only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from configuration_runtime_bridge import documents  # noqa: E402

from alphalab.utils.configuration_backend import ConfigurationBackend  # noqa: E402
from alphalab.utils.configuration_store import ConfigurationStore  # noqa: E402
from apps.api.services.configuration_binding_service import ModelSearchBindings  # noqa: E402


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    root, mode = Path(sys.argv[1]), sys.argv[2]
    native = documents()
    if mode == "sqlite":
        store = ConfigurationStore.initialize(
            root,
            documents=native,
            selections={
                key: {"kind": "profile", "id": value["active_id"]} for key, value in native.items()
            },
        )
        owner = ConfigurationBackend.sqlite(
            root, installation_id=store.read_snapshot().installation_id
        )
    else:
        owner = ConfigurationBackend.json(root)
    with owner, owner.borrow():
        binding = ModelSearchBindings(owner)
        if mode == "json":
            binding.models.save_profile(native["models"]["profiles"][0], activate=True)
            binding.search.save_profile(
                native["search"]["profiles"][0] | {"model_profile_id": "model"}, activate=True
            )
        emit({"contract": owner.node_contract()})
        for line in sys.stdin:
            request = json.loads(line)
            service = getattr(binding, request.get("service", "models"))
            if request["op"] == "edit":
                clear = {"clear_api_key": True} if request.get("clear") else {}
                value = service.save_profile(request["profile"], activate=True, **clear)
            elif request["op"] == "select":
                value = service.select(request["selection"])
            elif request["op"] == "verify":
                owner.verify_agent_identity(request["identity"])
                value = {"agreed": True}
            else:
                value = service.public_settings()
            emit(value)


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    try:
        main()
    except BaseException:
        emit({"fixture_error": True})
        raise SystemExit(1) from None
