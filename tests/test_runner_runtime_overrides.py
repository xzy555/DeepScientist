from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from deepscientist.config import ConfigManager
from deepscientist.daemon.app import DaemonApp
from deepscientist.home import ensure_home_layout
from deepscientist.runners.runtime_overrides import apply_codex_runtime_overrides


def test_apply_codex_runtime_overrides_keeps_default_config_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSCIENTIST_CODEX_YOLO", raising=False)
    monkeypatch.delenv("DEEPSCIENTIST_CODEX_APPROVAL_POLICY", raising=False)
    monkeypatch.delenv("DEEPSCIENTIST_CODEX_SANDBOX_MODE", raising=False)

    config = apply_codex_runtime_overrides(
        {
            "approval_policy": "on-request",
            "sandbox_mode": "workspace-write",
        }
    )

    assert config["approval_policy"] == "on-request"
    assert config["sandbox_mode"] == "workspace-write"


def test_apply_codex_runtime_overrides_enables_yolo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSCIENTIST_CODEX_YOLO", "1")

    config = apply_codex_runtime_overrides(
        {
            "approval_policy": "on-request",
            "sandbox_mode": "workspace-write",
        }
    )

    assert config["approval_policy"] == "never"
    assert config["sandbox_mode"] == "danger-full-access"


def test_apply_codex_runtime_overrides_accepts_profile_and_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSCIENTIST_CODEX_PROFILE", "m27")
    monkeypatch.setenv("DEEPSCIENTIST_CODEX_MODEL", "inherit")

    config = apply_codex_runtime_overrides(
        {
            "profile": "",
            "model": "gpt-5.4",
        }
    )

    assert config["profile"] == "m27"
    assert config["model"] == "inherit"


def test_daemon_app_applies_yolo_env_to_runners_config(temp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    monkeypatch.setenv("DEEPSCIENTIST_CODEX_YOLO", "1")

    app = DaemonApp(temp_home)

    assert app.runners_config["codex"]["approval_policy"] == "never"
    assert app.runners_config["codex"]["sandbox_mode"] == "danger-full-access"


def test_config_manager_load_runners_config_backfills_normalized_defaults(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    manager = ConfigManager(temp_home)
    manager.ensure_files()
    manager.path_for("runners").write_text(
        "\n".join(
            [
                "codex:",
                "  enabled: true",
                "  binary: codex",
                "  model: gpt-5.4",
                "  approval_policy: never",
                "  sandbox_mode: workspace-write",
                "claude:",
                "  enabled: false",
                "  binary: claude",
                "  status: reserved_todo",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    runners = manager.load_runners_config()

    assert runners["codex"]["mcp_tool_timeout_sec"] == 180000
    assert runners["codex"]["config_dir"] == "~/.codex"
    assert runners["codex"]["retry_on_failure"] is True


def test_probe_codex_runner_applies_yolo_env_overrides(
    temp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure_home_layout(temp_home)
    manager = ConfigManager(temp_home)
    manager.ensure_files()
    monkeypatch.setenv("DEEPSCIENTIST_CODEX_YOLO", "1")
    monkeypatch.setattr("deepscientist.config.service.resolve_runner_binary", lambda binary, runner_name=None: "/tmp/fake-codex")
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):  # noqa: ANN001
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout='{"item":{"text":"HELLO"}}\n', stderr="")

    monkeypatch.setattr("deepscientist.config.service.subprocess.run", fake_run)

    result = manager._probe_codex_runner({"binary": "codex", "model": "gpt-5.4"})

    assert result["ok"] is True
    assert result["details"]["approval_policy"] == "never"
    assert result["details"]["sandbox_mode"] == "danger-full-access"
    assert 'approval_policy="never"' in captured["command"]
    assert "--sandbox" in captured["command"]
    assert "danger-full-access" in captured["command"]
