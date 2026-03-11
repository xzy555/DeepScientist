from __future__ import annotations

import json
from pathlib import Path

from deepscientist.config import ConfigManager
from deepscientist.cli import _local_ui_url, init_command, pause_command
from deepscientist.home import ensure_home_layout, repo_root
from deepscientist.quest import QuestService
from deepscientist.skills import SkillInstaller


def test_init_creates_required_files(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    manager = ConfigManager(temp_home)
    created = manager.ensure_files()
    assert created
    assert (temp_home / "config" / "config.yaml").exists()
    assert (temp_home / "config" / "runners.yaml").exists()
    assert (temp_home / "config" / "connectors.yaml").exists()
    config = manager.load_named("config")
    assert config["ui"]["host"] == "0.0.0.0"
    assert config["ui"]["port"] == 20999
    assert config["ui"]["default_mode"] == "both"
    assert config["ui"]["auto_open_browser"] is True


def test_new_creates_standalone_git_repo(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    snapshot = service.create("test quest")
    quest_root = Path(snapshot["quest_root"])
    assert (quest_root / ".git").exists()
    assert (quest_root / ".gitignore").exists()
    assert (quest_root / "quest.yaml").exists()
    assert (quest_root / "tmp").exists()
    assert (quest_root / ".codex" / "skills").exists()
    assert (quest_root / ".claude" / "agents").exists()
    assert (quest_root / ".claude" / "agents" / "deepscientist-decision.md").exists()
    assert (quest_root / ".codex" / "skills" / "deepscientist-finalize" / "SKILL.md").exists()
    assert snapshot["runner"] == "codex"
    assert "paths" in snapshot
    assert snapshot["summary"]["status_line"] == "Quest created. Waiting for baseline setup or reuse."


def test_init_command_syncs_global_skills(temp_home: Path, monkeypatch) -> None:
    ensure_home_layout(temp_home)

    calls: list[str] = []

    def _record_sync(self):  # type: ignore[no-untyped-def]
        calls.append("sync_global")
        return {"codex": [], "claude": [], "notes": []}

    monkeypatch.setattr(SkillInstaller, "sync_global", _record_sync)
    exit_code = init_command(temp_home)
    assert exit_code in {0, 1}
    assert calls == ["sync_global"]


def test_pause_command_prefers_daemon_control_when_available(temp_home: Path, monkeypatch, capsys) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "ok": True,
                    "action": "pause",
                    "snapshot": {
                        "quest_id": "q-demo",
                        "status": "paused",
                    },
                }
            ).encode("utf-8")

    monkeypatch.setattr("deepscientist.cli.urlopen", lambda request, timeout=3: _FakeResponse())

    exit_code = pause_command(temp_home, "q-demo")

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "paused"' in captured.out


def test_local_ui_url_keeps_default_host_visible() -> None:
    assert _local_ui_url("0.0.0.0", 20999) == "http://0.0.0.0:20999"
    assert _local_ui_url("", 20999) == "http://0.0.0.0:20999"
