"""Packaging guard: the comparison evaluator must ship with the app package."""

from __future__ import annotations

from pathlib import Path

from setuptools import find_packages

_ROOT = Path(__file__).resolve().parents[2]


def test_comparison_service_is_a_discoverable_package():
    packages = set(find_packages(where=str(_ROOT), include=["alphalab*", "apps", "apps.api*"]))
    assert "apps.api.services.comparison" in packages
