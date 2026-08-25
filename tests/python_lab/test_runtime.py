from __future__ import annotations

from types import SimpleNamespace
import subprocess

import pytest

from alphalab.python_lab import PythonLabConfig, PythonLabRuntime, validate_lab_source


def test_source_requires_one_synchronous_run_context_entrypoint():
    valid = validate_lab_source("def run(context):\n    return {'value': context['value']}\n")
    assert valid["entrypoint"] == "run"
    assert len(valid["sha256"]) == 64

    with pytest.raises(ValueError, match=r"run\(context\)"):
        validate_lab_source("def execute(context):\n    return context\n")
    with pytest.raises(ValueError, match="synchronous"):
        validate_lab_source("async def run(context):\n    return context\n")


def test_runtime_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ALPHALAB_PYTHON_LAB_RUNTIME", raising=False)
    config = PythonLabConfig.from_env()
    assert config.kind == "disabled"
    assert config.capabilities()["available"] is False
    with pytest.raises(PermissionError, match="disabled"):
        PythonLabRuntime(config).execute(
            "def run(context):\n    return context\n",
            {},
        )


def test_trusted_local_requires_separate_confirmation_and_returns_json():
    runtime = PythonLabRuntime(PythonLabConfig(kind="trusted_local", timeout_seconds=5))
    source = (
        "def run(context):\n    print('captured')\n    return {'value': context['value'] + 1}\n"
    )
    with pytest.raises(PermissionError, match="confirm_trusted_local"):
        runtime.execute(source, {"value": 2})

    result = runtime.execute(source, {"value": 2}, confirm_trusted_local=True)
    assert result.output == {"value": 3}
    assert result.stdout.strip() == "captured"


def test_docker_command_has_no_network_mounts_or_privileges(monkeypatch):
    monkeypatch.setattr("alphalab.python_lab.runtime.shutil.which", lambda _: "docker")
    monkeypatch.setattr(
        "alphalab.python_lab.runtime.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="[]", stderr=""),
    )
    command = PythonLabRuntime(
        PythonLabConfig(kind="docker", docker_image="alphalab-python-lab:test")
    )._command()
    for flag in (
        "--pull=never",
        "--stop-timeout=1",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--user=65534:65534",
        "--entrypoint=python",
        "--ulimit=nofile=256:256",
    ):
        assert flag in command
    assert not any(item.startswith("--volume") or item == "-v" for item in command)
    assert any(item.startswith("--name=alphalab-python-lab-") for item in command)


def test_docker_timeout_removes_the_exact_generated_container(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, 1)
        if command[1:3] == ["rm", "-f"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("alphalab.python_lab.runtime.shutil.which", lambda _: "docker")
    monkeypatch.setattr("alphalab.python_lab.runtime.subprocess.run", fake_run)
    runtime = PythonLabRuntime(
        PythonLabConfig(kind="docker", docker_image="alphalab-python-lab:test", timeout_seconds=1)
    )
    with pytest.raises(TimeoutError, match="exceeded"):
        runtime.execute("def run(context):\n    return {}\n", {})

    run_command = next(command for command in calls if command[1] == "run")
    name = next(item.removeprefix("--name=") for item in run_command if item.startswith("--name="))
    assert ["docker", "rm", "-f", name] in calls
