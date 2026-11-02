"""Private saved data-source profiles for workstation-local RQData access."""

from __future__ import annotations

from typing import Any, Mapping

from alphalab.dataio import rq_profiles


def public_settings() -> dict[str, Any]:
    return rq_profiles.public_settings()


def save_profile(
    profile: Mapping[str, object],
    *,
    activate: bool,
    clear_password: bool = False,
) -> dict[str, Any]:
    return rq_profiles.save_profile(profile, activate=activate, clear_password=clear_password)


def resolve_test_profile(profile: Mapping[str, object]) -> dict[str, str]:
    return rq_profiles.resolve_connection_profile(profile).to_config()
