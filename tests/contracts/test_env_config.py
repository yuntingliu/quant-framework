from __future__ import annotations

import os
from pathlib import Path

from alphalab.utils.env import load_env_files
from alphalab.utils.paths import REPO_ROOT


def test_env_loader_preserves_shell_values(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        'RQ_USER=file-user\nRQ_PASSWORD="file-password"\nRQ_HOST=example.invalid:1234\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RQ_USER", "shell-user")
    monkeypatch.delenv("RQ_PASSWORD", raising=False)
    monkeypatch.delenv("RQ_HOST", raising=False)

    assert load_env_files([env_file]) == [env_file]
    assert os.environ["RQ_USER"] == "shell-user"
    assert os.environ["RQ_PASSWORD"] == "file-password"
    assert os.environ["RQ_HOST"] == "example.invalid:1234"


def test_tracked_env_template_contains_no_network_forwarding_fields() -> None:
    template = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "RQ_USER=\n" in template
    assert "RQ_PASSWORD=\n" in template
    assert "RQ_HOST=\n" in template
    assert "RQ_TUNNEL_" not in template
    assert "RQ_SSH_" not in template
    assert "RQ_TARGET_" not in template
    assert "RQ_LOCAL_" not in template
