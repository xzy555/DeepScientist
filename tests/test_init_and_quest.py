from __future__ import annotations

import json
import shutil
from pathlib import Path

from deepscientist.config import ConfigManager
from deepscientist.cli import _local_ui_url, init_command, pause_command
from deepscientist.home import ensure_home_layout, repo_root
from deepscientist.mcp.context import McpContext
from deepscientist.quest import QuestService
from deepscientist.shared import ensure_dir, write_json, write_text
from deepscientist.skills import SkillInstaller


def test_init_creates_required_files(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    manager = ConfigManager(temp_home)
    created = manager.ensure_files()
    assert created
    assert (temp_home / "runtime" / "python").exists()
    assert (temp_home / "runtime" / "uv-cache").exists()
    assert not (temp_home / "runtime" / "venv").exists()
    assert (temp_home / "config" / "config.yaml").exists()
    assert (temp_home / "config" / "runners.yaml").exists()
    assert (temp_home / "config" / "connectors.yaml").exists()
    config = manager.load_named("config")
    runners = manager.load_named_normalized("runners")
    assert config["ui"]["host"] == "0.0.0.0"
    assert config["ui"]["port"] == 20999
    assert config["ui"]["default_mode"] == "web"
    assert config["ui"]["auto_open_browser"] is True
    assert config["bootstrap"]["codex_ready"] is False
    assert config["bootstrap"]["codex_last_checked_at"] is None
    assert config["bootstrap"]["locale_source"] == "default"
    assert config["bootstrap"]["locale_initialized_from_browser"] is False
    assert config["bootstrap"]["locale_initialized_at"] is None
    assert config["bootstrap"]["locale_initialized_browser_locale"] is None
    assert config["connectors"]["system_enabled"]["qq"] is True
    assert config["connectors"]["system_enabled"]["weixin"] is True
    assert config["connectors"]["system_enabled"]["telegram"] is True
    assert config["connectors"]["system_enabled"]["discord"] is False
    assert config["connectors"]["system_enabled"]["slack"] is False
    assert config["connectors"]["system_enabled"]["feishu"] is True
    assert config["connectors"]["system_enabled"]["whatsapp"] is True
    assert config["connectors"]["system_enabled"]["lingzhu"] is True
    assert runners["codex"]["profile"] == ""
    assert runners["codex"]["model"] == "gpt-5.4"
    assert runners["codex"]["model_reasoning_effort"] == "xhigh"
    assert runners["codex"]["retry_initial_backoff_sec"] == 10.0
    assert runners["codex"]["retry_backoff_multiplier"] == 6.0
    assert runners["codex"]["retry_max_backoff_sec"] == 1800.0


def test_legacy_codex_retry_profile_is_upgraded_when_loading_normalized_runners(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    manager = ConfigManager(temp_home)
    manager.ensure_files()
    runners_path = manager.path_for("runners")
    runners_path.write_text(
        "\n".join(
            [
                "codex:",
                "  enabled: true",
                "  binary: codex",
                "  retry_on_failure: true",
                "  retry_max_attempts: 5",
                "  retry_initial_backoff_sec: 1",
                "  retry_backoff_multiplier: 2",
                "  retry_max_backoff_sec: 8",
                "",
            ]
        ),
        encoding="utf-8",
    )

    runners = manager.load_named_normalized("runners")

    assert runners["codex"]["retry_initial_backoff_sec"] == 10.0
    assert runners["codex"]["retry_backoff_multiplier"] == 6.0
    assert runners["codex"]["retry_max_backoff_sec"] == 1800.0


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
    assert (quest_root / "userfiles").exists()
    assert (quest_root / ".codex" / "prompts" / "system.md").exists()
    assert (quest_root / ".codex" / "prompts" / "connectors" / "qq.md").exists()
    assert (quest_root / ".codex" / "skills").exists()
    assert (quest_root / ".claude" / "agents").exists()
    assert (quest_root / ".claude" / "agents" / "deepscientist-decision.md").exists()
    assert (quest_root / ".codex" / "skills" / "deepscientist-finalize" / "SKILL.md").exists()
    assert snapshot["quest_id"] == "001"
    assert snapshot["runner"] == "codex"
    assert "paths" in snapshot
    assert snapshot["summary"]["status_line"] == "Quest created. Waiting for baseline setup or reuse."


def test_auto_generated_quest_ids_are_sequential(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    service = QuestService(temp_home)

    first = service.create("first quest")
    second = service.create("second quest")
    third = service.create("third quest")

    assert [first["quest_id"], second["quest_id"], third["quest_id"]] == ["001", "002", "003"]


def test_events_slice_uses_placeholder_for_oversized_event_lines(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    service = QuestService(temp_home)
    snapshot = service.create("oversized event slice quest")
    quest_root = Path(snapshot["quest_root"])
    events_path = quest_root / ".ds" / "events.jsonl"
    huge_output = "x" * (9 * 1024 * 1024)
    oversized_event = {
        "event_id": "evt-huge",
        "type": "runner.tool_result",
        "quest_id": snapshot["quest_id"],
        "run_id": "run-huge",
        "tool_name": "bash_exec.bash_exec",
        "output": huge_output,
    }
    small_event = {
        "event_id": "evt-small",
        "type": "runner.agent_message",
        "quest_id": snapshot["quest_id"],
        "text": "small tail event",
    }
    events_path.write_text(
        json.dumps(oversized_event, ensure_ascii=False) + "\n" + json.dumps(small_event, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    payload = service.events(snapshot["quest_id"], tail=True, limit=2)

    assert payload["events"][0]["type"] == "runner.tool_result"
    assert payload["events"][0]["oversized_event"] is True
    assert payload["events"][0]["oversized_bytes"] > 8 * 1024 * 1024
    assert payload["events"][1]["type"] == "runner.agent_message"


def test_deleted_quest_ids_are_not_reused(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    service = QuestService(temp_home)

    first = service.create("first quest")
    second = service.create("second quest")
    third = service.create("third quest")
    shutil.rmtree(Path(second["quest_root"]))

    fourth = service.create("fourth quest")

    assert [first["quest_id"], second["quest_id"], third["quest_id"], fourth["quest_id"]] == ["001", "002", "003", "004"]


def test_auto_generated_quest_ids_initialize_from_existing_numeric_quests(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    for quest_id in ("001", "002", "010"):
        quest_root = ensure_dir(temp_home / "quests" / quest_id)
        write_text(quest_root / "quest.yaml", f'quest_id: "{quest_id}"\n')

    service = QuestService(temp_home)
    snapshot = service.create("after existing quests")

    assert snapshot["quest_id"] == "011"


def test_skill_installer_can_resync_existing_quests_after_version_change(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    installer = SkillInstaller(repo_root(), temp_home)
    service = QuestService(temp_home, skill_installer=installer)
    snapshot = service.create("sync existing quest skills")
    quest_root = Path(snapshot["quest_root"])
    stale_file = quest_root / ".codex" / "skills" / "deepscientist-scout" / "obsolete.txt"
    stale_prompt = quest_root / ".codex" / "prompts" / "obsolete.txt"
    stale_file.write_text("stale", encoding="utf-8")
    stale_prompt.write_text("stale", encoding="utf-8")

    result = installer.ensure_release_sync(
        installed_version="9.9.9",
        sync_global_enabled=False,
        sync_existing_quests_enabled=True,
        force=True,
    )

    assert result["updated"] is True
    assert result["existing_quests_synced"] is True
    assert result["existing_quests"]["count"] == 1
    assert not stale_file.exists()
    assert not stale_prompt.exists()
    assert (quest_root / ".codex" / "prompts" / "system.md").exists()
    assert (quest_root / ".codex" / "skills" / "deepscientist-scout" / "SKILL.md").exists()
    state_path = temp_home / "runtime" / "skill-sync-state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["installed_version"] == "9.9.9"


def test_explicit_custom_quest_id_still_works(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    service = QuestService(temp_home)

    snapshot = service.create("custom quest", quest_id="demo-quest")

    assert snapshot["quest_id"] == "demo-quest"


def test_explicit_numeric_quest_id_advances_next_auto_id(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    service = QuestService(temp_home)

    explicit = service.create("explicit numeric quest", quest_id="010")
    automatic = service.create("automatic after explicit numeric")

    assert explicit["quest_id"] == "010"
    assert automatic["quest_id"] == "011"


def test_preview_next_numeric_quest_id_matches_allocator_without_consuming_it(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    service = QuestService(temp_home)

    assert service.preview_next_numeric_quest_id() == "001"
    assert service.preview_next_numeric_quest_id() == "001"

    created = service.create("preview quest")

    assert created["quest_id"] == "001"
    assert service.preview_next_numeric_quest_id() == "002"

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


def test_mcp_context_prefers_deepscientist_home_env(monkeypatch, tmp_path: Path) -> None:
    primary_home = tmp_path / "primary-home"
    fallback_home = tmp_path / "fallback-home"
    monkeypatch.setenv("DEEPSCIENTIST_HOME", str(primary_home))
    monkeypatch.setenv("DS_HOME", str(fallback_home))

    context = McpContext.from_env()

    assert context.home == primary_home


def test_repo_root_prefers_explicit_env(monkeypatch, tmp_path: Path) -> None:
    install_root = tmp_path / "install-root"
    ensure_dir(install_root / "src" / "deepscientist")
    ensure_dir(install_root / "src" / "skills")
    write_text(install_root / "pyproject.toml", "[project]\nname='deepscientist'\n")
    monkeypatch.setenv("DEEPSCIENTIST_REPO_ROOT", str(install_root))

    assert repo_root() == install_root.resolve()
