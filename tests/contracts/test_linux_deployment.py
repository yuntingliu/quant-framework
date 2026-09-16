from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="Linux release script")
@pytest.mark.parametrize("previous_release", [False, True])
def test_failed_release_restores_only_an_existing_previous_link(tmp_path, previous_release):
    commit = "a" * 40
    source = tmp_path / "source"
    (source / ".git").mkdir(parents=True)
    release_root = tmp_path / "releases"
    release = release_root / commit
    (release / ".venv/bin").mkdir(parents=True)
    probe = release / ".venv/bin/python"
    probe.write_text("#!/bin/sh\nexit 1\n")
    probe.chmod(0o700)
    current = tmp_path / "apps/current"
    old = release_root / ("b" * 40)
    if previous_release:
        old.mkdir()
        current.parent.mkdir()
        current.symlink_to(old, target_is_directory=True)
    env_file = tmp_path / "service.env"
    env_file.write_text("ALPHALAB_WEB_AUTH_ENABLED=0\n")
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("git", "python3", "npm", "systemctl", "tar"):
        command = commands / name
        body = f"case \"$*\" in *rev-parse*) echo {commit} ;; esac\n" if name == "git" else "exit 0\n"
        command.write_text("#!/bin/sh\n" + body)
        command.chmod(0o700)
    env = dict(os.environ, PATH=f"{commands}:{os.environ['PATH']}")
    for key, value in {
        "SOURCE_DIR": source, "RELEASES_DIR": release_root, "CURRENT_LINK": current,
        "RUNTIME_DIR": tmp_path / "runtime", "ENV_FILE": env_file,
        "STATE_DIR": tmp_path / "state", "BACKUP_DIR": tmp_path / "backups",
    }.items():
        env[f"ALPHALAB_{key}"] = str(value)
    env["ALPHALAB_SERVICE_NAME"] = "test-only.service"
    script = Path(__file__).resolve().parents[2] / "scripts/deploy_linux.sh"
    result = subprocess.run(["bash", str(script), commit], env=env, text=True,
                            capture_output=True, timeout=20)
    assert result.returncode == 1, result.stdout + result.stderr
    assert release.is_dir()
    if previous_release:
        assert current.resolve(strict=True) == old
        assert "previous release restored" in result.stderr
    else:
        assert not current.is_symlink()
        assert not current.exists()
        assert "new service stopped" in result.stderr
