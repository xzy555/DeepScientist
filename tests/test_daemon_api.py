from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from deepscientist.config import ConfigManager
from deepscientist.daemon.api.router import match_route
from deepscientist.daemon.app import DaemonApp
from deepscientist.home import ensure_home_layout
from deepscientist.mcp.context import McpContext
from deepscientist.runners import RunResult
from deepscientist.shared import append_jsonl, ensure_dir, generate_id, read_yaml, utc_now, write_yaml


def _get_json(url: str):
    with urlopen(url) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def test_daemon_serves_health_and_ui(temp_home: Path, project_root: Path, pythonpath_env) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    new_process = subprocess.run(
        [
            "python3",
            "-m",
            "deepscientist.cli",
            "--home",
            str(temp_home),
            "new",
            "daemon api quest",
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    quest_id = json.loads(new_process.stdout)["quest_id"]
    quest_root = temp_home / "quests" / quest_id
    (quest_root / "docs").mkdir(parents=True, exist_ok=True)
    (quest_root / "figures").mkdir(parents=True, exist_ok=True)
    (quest_root / "docs" / "appendix.pdf").write_bytes(b"%PDF-1.4\n%quest-pdf\n")
    (quest_root / "figures" / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\nquest-plot")

    server = subprocess.Popen(
        [
            "python3",
            "-m",
            "deepscientist.cli",
            "--home",
            str(temp_home),
            "daemon",
            "--host",
            "127.0.0.1",
            "--port",
            "20901",
        ],
        cwd=project_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        time.sleep(2)
        health = _get_json("http://127.0.0.1:20901/api/health")
        assert health["status"] == "ok"
        quest = _get_json(f"http://127.0.0.1:20901/api/quests/{quest_id}")
        assert quest["quest_id"] == quest_id
        workflow = _get_json(f"http://127.0.0.1:20901/api/quests/{quest_id}/workflow")
        assert workflow["quest_id"] == quest_id
        assert "entries" in workflow
        node_traces = _get_json(f"http://127.0.0.1:20901/api/quests/{quest_id}/node-traces")
        assert node_traces["quest_id"] == quest_id
        assert "items" in node_traces
        explorer = _get_json(f"http://127.0.0.1:20901/api/quests/{quest_id}/explorer")
        assert explorer["view"]["mode"] == "live"
        search = _get_json(
            f"http://127.0.0.1:20901/api/quests/{quest_id}/search?q={quote('daemon api quest')}&limit=10"
        )
        assert search["quest_id"] == quest_id
        assert search["items"]
        assert any(item["path"] == "brief.md" for item in search["items"])
        graph = _get_json(f"http://127.0.0.1:20901/api/quests/{quest_id}/graph")
        assert "lines" in graph
        branches = _get_json(f"http://127.0.0.1:20901/api/quests/{quest_id}/git/branches")
        assert branches["default_ref"] == "main"
        explorer_snapshot = _get_json(f"http://127.0.0.1:20901/api/quests/{quest_id}/explorer?revision=HEAD&mode=commit")
        assert explorer_snapshot["view"]["mode"] == "commit"
        assert explorer_snapshot["view"]["revision"] == "HEAD"
        assert explorer_snapshot["view"]["read_only"] is True
        log_payload = _get_json(f"http://127.0.0.1:20901/api/quests/{quest_id}/git/log?ref=main&limit=5")
        assert log_payload["ok"] is True
        assert log_payload["commits"]
        latest_sha = log_payload["commits"][0]["sha"]
        compare = _get_json(f"http://127.0.0.1:20901/api/quests/{quest_id}/git/compare?base=main&head=main")
        assert compare["ok"] is True
        commit_payload = _get_json(f"http://127.0.0.1:20901/api/quests/{quest_id}/git/commit?sha={latest_sha}")
        assert commit_payload["ok"] is True
        assert commit_payload["sha"] == latest_sha
        docs_index = _get_json("http://127.0.0.1:20901/api/docs")
        assert docs_index
        docs_open_request = Request(
            "http://127.0.0.1:20901/api/docs/open",
            data=json.dumps({"document_id": docs_index[0]["document_id"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(docs_open_request) as response:  # noqa: S310
            docs_open = json.loads(response.read().decode("utf-8"))
        assert docs_open["source_scope"] == "system_docs"
        assert docs_open["content"]
        diff = _get_json(
            f"http://127.0.0.1:20901/api/quests/{quest_id}/git/diff-file?base=main&head=main&path={quote('plan.md')}"
        )
        assert diff["ok"] is True
        assert diff["path"] == "plan.md"
        commit_diff = _get_json(
            f"http://127.0.0.1:20901/api/quests/{quest_id}/git/commit-file?sha={latest_sha}&path={quote('plan.md')}"
        )
        assert commit_diff["ok"] is True
        assert commit_diff["path"] == "plan.md"
        with urlopen(  # noqa: S310
            Request(
                f"http://127.0.0.1:20901/api/quests/{quest_id}/documents/open",
                data=json.dumps({"document_id": "path::docs/appendix.pdf"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        ) as response:
            pdf_doc = json.loads(response.read().decode("utf-8"))
        assert pdf_doc["meta"]["renderer_hint"] == "pdf"
        with urlopen(f"http://127.0.0.1:20901{pdf_doc['asset_url']}") as response:  # noqa: S310
            assert response.headers["Content-Type"] == "application/pdf"
            assert response.read().startswith(b"%PDF-1.4")
        with urlopen(  # noqa: S310
            Request(
                f"http://127.0.0.1:20901/api/quests/{quest_id}/documents/open",
                data=json.dumps({"document_id": "path::figures/plot.png"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        ) as response:
            image_doc = json.loads(response.read().decode("utf-8"))
        assert image_doc["meta"]["renderer_hint"] == "image"
        with urlopen(f"http://127.0.0.1:20901{image_doc['asset_url']}") as response:  # noqa: S310
            assert response.headers["Content-Type"] == "image/png"
            assert response.read().startswith(b"\x89PNG")
        upload_asset_request = Request(
            f"http://127.0.0.1:20901/api/quests/{quest_id}/documents/assets",
            data=json.dumps(
                {
                    "document_id": "brief.md",
                    "file_name": "diagram.png",
                    "mime_type": "image/png",
                    "kind": "image",
                    "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\ndaemon-upload").decode("ascii"),
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(upload_asset_request) as response:  # noqa: S310
            uploaded_asset = json.loads(response.read().decode("utf-8"))
        assert uploaded_asset["ok"] is True
        assert uploaded_asset["relative_path"].startswith("brief.assets/")
        with urlopen(f"http://127.0.0.1:20901{uploaded_asset['asset_url']}") as response:  # noqa: S310
            assert response.headers["Content-Type"] == "image/png"
            assert response.read().startswith(b"\x89PNG")
        with urlopen(f"http://127.0.0.1:20901/api/quests/{quest_id}/graph/svg") as response:  # noqa: S310
            svg = response.read().decode("utf-8")
        assert "<svg" in svg
        root_request = Request("http://127.0.0.1:20901/")
        with urlopen(root_request) as response:  # noqa: S310
            html = response.read().decode("utf-8")
        assert "DeepScientist" in html
        assert "Copilot" in html
        assert "/assets/fonts/ds-fonts.css" in html
        with urlopen(f"http://127.0.0.1:20901/projects/{quest_id}") as response:  # noqa: S310
            project_html = response.read().decode("utf-8")
        assert "DeepScientist" in project_html
        assert "Copilot" in project_html

        with urlopen("http://127.0.0.1:20901/assets/fonts/ds-fonts.css") as response:  # noqa: S310
            stylesheet = response.read().decode("utf-8")
            assert response.headers["Content-Type"] == "text/css"
        assert "font-family: 'DS-Project';" in stylesheet
        assert "NotoSerifSC-Regular-C94HN_ZN.ttf" in stylesheet

        with urlopen("http://127.0.0.1:20901/assets/fonts/Satoshi-Medium-ByP-Zb-9.woff2") as response:  # noqa: S310
            font_payload = response.read(16)
            assert response.headers["Content-Type"] == "font/woff2"
        assert font_payload
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_ui_root_shows_build_instructions_when_bundle_missing(
    temp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    monkeypatch.setattr(app.handlers, "_ui_dist_root", lambda: None)

    status, headers, html = app.handlers.root()

    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "DeepScientist UI bundle is not built yet" in html
    assert "npm --prefix src/ui run build" in html


def test_health_reports_daemon_identity(monkeypatch: pytest.MonkeyPatch, temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    monkeypatch.setenv("DS_DAEMON_ID", "daemon-test-001")
    monkeypatch.setenv("DS_DAEMON_MANAGED_BY", "ds-launcher")
    app = DaemonApp(temp_home)

    payload = app.handlers.health()

    assert payload["status"] == "ok"
    assert payload["home"] == str(temp_home.resolve())
    assert payload["daemon_id"] == "daemon-test-001"
    assert payload["managed_by"] == "ds-launcher"
    assert isinstance(payload["pid"], int)


def test_admin_shutdown_rejects_daemon_identity_mismatch(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)

    payload = app.handlers.admin_shutdown({"source": "pytest", "daemon_id": "wrong-daemon-id"})

    assert payload["ok"] is False
    assert "identity mismatch" in str(payload["message"]).lower()
    assert app._shutdown_requested.is_set() is False


def test_router_matches_spa_project_paths() -> None:
    route_name, params = match_route("GET", "/projects/q-123456")

    assert route_name == "spa_root"
    assert params["spa_path"] == "projects/q-123456"


def test_router_matches_quest_search_path() -> None:
    route_name, params = match_route("GET", "/api/quests/q-123456/search")

    assert route_name == "quest_search"
    assert params["quest_id"] == "q-123456"


def test_quest_create_handler_auto_binds_recent_connector_to_newest_quest(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    manager = ConfigManager(temp_home)
    manager.ensure_files()
    connectors = manager.load_named("connectors")
    connectors["whatsapp"]["enabled"] = True
    connectors["whatsapp"]["auto_bind_dm_to_active_quest"] = True
    write_yaml(manager.path_for("connectors"), connectors)
    app = DaemonApp(temp_home)

    first = app.handle_connector_inbound(
        "whatsapp",
        {
            "chat_type": "direct",
            "sender_id": "+15550001111",
            "sender_name": "Researcher",
            "text": "Please summarize the latest result.",
        },
    )
    assert first["accepted"] is True
    assert "/use" in first["reply"]["payload"]["text"]
    assert app.list_connector_bindings("whatsapp") == []

    payload = app.handlers.quest_create({"goal": "daemon api connector bind quest", "source": "web"})

    assert payload["ok"] is True
    quest_id = payload["snapshot"]["quest_id"]
    bindings = app.list_connector_bindings("whatsapp")
    assert any(item["conversation_id"] == "whatsapp:direct:+15550001111" and item["quest_id"] == quest_id for item in bindings)


def test_quest_settings_handler_updates_quest_yaml(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("settings quest")
    quest_id = quest["quest_id"]

    payload = app.handlers.quest_settings(
        quest_id,
        {
            "title": "Updated Quest Title",
            "active_anchor": "experiment",
            "default_runner": "codex",
        },
    )

    assert isinstance(payload, dict)
    assert payload["ok"] is True
    assert payload["snapshot"]["title"] == "Updated Quest Title"
    assert payload["snapshot"]["active_anchor"] == "experiment"
    assert payload["snapshot"]["runner"] == "codex"

    quest_yaml = read_yaml(temp_home / "quests" / quest_id / "quest.yaml", {})
    assert quest_yaml["title"] == "Updated Quest Title"
    assert quest_yaml["active_anchor"] == "experiment"
    assert quest_yaml["default_runner"] == "codex"


def test_quest_settings_handler_rejects_invalid_anchor(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("invalid anchor quest")

    status_code, payload = app.handlers.quest_settings(
        quest["quest_id"],
        {
            "active_anchor": "not-a-skill",
        },
    )

    assert status_code == 400
    assert payload["ok"] is False
    assert "active anchor" in str(payload["message"]).lower()


def test_bash_exec_handlers_expose_sessions_logs_and_stop(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("bash api quest")
    quest_id = quest["quest_id"]
    quest_root = Path(quest["quest_root"])
    context = McpContext(
        home=temp_home,
        quest_id=quest_id,
        quest_root=quest_root,
        run_id="run-bash-api-001",
        active_anchor="experiment",
        conversation_id=f"quest:{quest_id}",
        agent_role="pi",
        worker_id="worker-main",
        worktree_root=None,
        team_mode="single",
    )

    session = app.bash_exec_service.start_session(
        context,
        command="printf 'alpha\\n'; sleep 5; printf 'omega\\n'",
        mode="detach",
    )
    bash_id = session["bash_id"]
    time.sleep(0.8)

    sessions = app.handlers.bash_sessions(quest_id, f"/api/quests/{quest_id}/bash/sessions")
    assert any(item["bash_id"] == bash_id for item in sessions)

    detail = app.handlers.bash_session(quest_id, bash_id)
    assert isinstance(detail, dict)
    assert detail["bash_id"] == bash_id

    status, headers, payload = app.handlers.bash_logs(
        quest_id,
        bash_id,
        f"/api/quests/{quest_id}/bash/sessions/{bash_id}/logs?limit=20",
    )
    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    entries = json.loads(payload.decode("utf-8"))
    assert any("alpha" in str(entry.get("line") or "") for entry in entries)

    stop_payload = app.handlers.bash_stop(quest_id, bash_id, {"reason": "pytest-stop"})
    assert isinstance(stop_payload, dict)
    assert stop_payload["success"] is True

    final = app.bash_exec_service.wait_for_session(quest_root, bash_id, timeout_seconds=10)
    assert final["status"] == "terminated"


def test_terminal_handlers_ensure_input_restore_and_stop(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("terminal api quest")
    quest_id = quest["quest_id"]
    quest_root = Path(quest["quest_root"])

    ensured = app.handlers.terminal_session_ensure(
        quest_id,
        {
            "source": "pytest",
            "conversation_id": f"quest:{quest_id}:pytest",
            "user_id": "user:pytest",
        },
    )
    assert ensured["ok"] is True
    session = ensured["session"]
    assert session["bash_id"] == "terminal-main"
    assert session["kind"] == "terminal"
    assert session["status"] == "running"

    accepted = app.handlers.terminal_input(
        quest_id,
        "terminal-main",
        {
            "data": "pwd\n",
            "source": "pytest",
            "conversation_id": f"quest:{quest_id}:pytest",
            "user_id": "user:pytest",
        },
    )
    assert isinstance(accepted, dict)
    assert accepted["ok"] is True
    assert accepted["completed_commands"][-1]["command"] == "pwd"

    deadline = time.time() + 6
    saw_pwd = False
    while time.time() < deadline:
        entries, _meta = app.bash_exec_service.read_log_entries(quest_root, "terminal-main", limit=200, order="asc")
        if any(str(entry.get("line") or "").strip() == str(quest_root) for entry in entries):
            saw_pwd = True
            break
        time.sleep(0.2)
    assert saw_pwd is True

    restored = app.handlers.terminal_restore(
        quest_id,
        "terminal-main",
        f"/api/quests/{quest_id}/terminal/sessions/terminal-main/restore?commands=10&output=40",
    )
    assert isinstance(restored, dict)
    assert restored["ok"] is True
    assert restored["session_id"] == "terminal-main"
    assert restored["cwd"] == str(quest_root)
    assert any(item["command"] == "pwd" for item in restored["latest_commands"])

    history_payload = app.handlers.terminal_history(quest_id, f"/api/quests/{quest_id}/terminal/history?limit=20")
    assert history_payload["ok"] is True
    assert history_payload["default_session_id"] == "terminal-main"
    assert any(item["bash_id"] == "terminal-main" for item in history_payload["terminal_sessions"])

    stop_payload = app.handlers.bash_stop(quest_id, "terminal-main", {"reason": "pytest-stop"})
    assert isinstance(stop_payload, dict)
    assert stop_payload["success"] is True

    final = app.bash_exec_service.wait_for_session(quest_root, "terminal-main", timeout_seconds=10)
    assert final["status"] == "terminated"


def test_terminal_handlers_create_new_terminal_session(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("multi terminal api quest")
    quest_id = quest["quest_id"]
    quest_root = Path(quest["quest_root"])

    ensured_main = app.handlers.terminal_session_ensure(
        quest_id,
        {
            "source": "pytest",
            "conversation_id": f"quest:{quest_id}:pytest",
            "user_id": "user:pytest",
        },
    )
    assert ensured_main["ok"] is True
    assert ensured_main["session"]["bash_id"] == "terminal-main"

    created = app.handlers.terminal_session_ensure(
        quest_id,
        {
            "source": "pytest",
            "conversation_id": f"quest:{quest_id}:pytest",
            "user_id": "user:pytest",
            "create_new": True,
            "label": "Scratch",
        },
    )
    assert created["ok"] is True
    created_session = created["session"]
    created_id = str(created_session.get("bash_id") or "")
    assert created_id
    assert created_id != "terminal-main"
    assert created_session.get("kind") == "terminal"
    assert created_session.get("label") == "Scratch"

    accepted = app.handlers.terminal_input(
        quest_id,
        created_id,
        {
            "data": "echo hello\n",
            "source": "pytest",
            "conversation_id": f"quest:{quest_id}:pytest",
            "user_id": "user:pytest",
        },
    )
    assert isinstance(accepted, dict)
    assert accepted["ok"] is True

    deadline = time.time() + 6
    saw_hello = False
    while time.time() < deadline:
        entries, _meta = app.bash_exec_service.read_log_entries(quest_root, created_id, limit=200, order="asc")
        if any("hello" in str(entry.get("line") or "") for entry in entries):
            saw_hello = True
            break
        time.sleep(0.2)
    assert saw_hello is True

    history_payload = app.handlers.terminal_history(quest_id, f"/api/quests/{quest_id}/terminal/history?limit=50")
    assert history_payload["ok"] is True
    assert any(item["bash_id"] == created_id for item in history_payload["terminal_sessions"])

    stop_created = app.handlers.bash_stop(quest_id, created_id, {"reason": "pytest-stop"})
    assert isinstance(stop_created, dict)
    assert stop_created["success"] is True
    stop_main = app.handlers.bash_stop(quest_id, "terminal-main", {"reason": "pytest-stop"})
    assert isinstance(stop_main, dict)
    assert stop_main["success"] is True

    final_created = app.bash_exec_service.wait_for_session(quest_root, created_id, timeout_seconds=10)
    assert final_created["status"] == "terminated"
    final_main = app.bash_exec_service.wait_for_session(quest_root, "terminal-main", timeout_seconds=10)
    assert final_main["status"] == "terminated"


def test_chat_endpoint_schedules_background_runner(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("background run quest")
    quest_id = quest["quest_id"]
    quest_root = Path(quest["quest_root"])

    class FakeRunner:
        binary = ""

        def run(self, request):
            history_root = ensure_dir(request.quest_root / ".ds" / "codex_history" / request.run_id)
            run_root = ensure_dir(request.quest_root / ".ds" / "runs" / request.run_id)
            append_jsonl(
                request.quest_root / ".ds" / "events.jsonl",
                {
                    "event_id": generate_id("evt"),
                    "type": "runner.delta",
                    "quest_id": request.quest_id,
                    "run_id": request.run_id,
                    "source": "codex",
                    "skill_id": request.skill_id,
                    "text": "Drafting response…",
                    "created_at": utc_now(),
                },
            )
            append_jsonl(
                request.quest_root / ".ds" / "events.jsonl",
                {
                    "event_id": generate_id("evt"),
                    "type": "runner.turn_finish",
                    "quest_id": request.quest_id,
                    "run_id": request.run_id,
                    "source": "codex",
                    "skill_id": request.skill_id,
                    "model": request.model,
                    "exit_code": 0,
                    "summary": "Auto reply from background runner.",
                    "created_at": utc_now(),
                },
            )
            return RunResult(
                ok=True,
                run_id=request.run_id,
                model=request.model,
                output_text="Auto reply from background runner.",
                exit_code=0,
                history_root=history_root,
                run_root=run_root,
                stderr_text="",
            )

    app.runners["codex"] = FakeRunner()

    payload = app.handlers.chat(quest_id, {"text": "Please continue.", "source": "tui-ink"})

    assert payload["ok"] is True
    assert payload["scheduled"] is True
    assert payload["started"] is True

    deadline = time.time() + 3
    while time.time() < deadline:
        history = app.quest_service.history(quest_id)
        if any(item.get("role") == "assistant" and item.get("content") == "Auto reply from background runner." for item in history):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("assistant reply was not appended after chat submission")

    events = app.handlers.quest_events(
        quest_id,
        path=f"/api/quests/{quest_id}/events?format=acp&session_id=session:test",
    )
    updates = [item["params"]["update"] for item in events["acp_updates"]]

    assert any(
        update["kind"] == "message" and (update.get("message") or {}).get("content") == "Please continue."
        for update in updates
    )
    assert any(
        update["kind"] == "message" and (update.get("message") or {}).get("content") == "Auto reply from background runner."
        for update in updates
    )
    assert any(
        update["kind"] == "event" and (update.get("data") or {}).get("label") == "run_finished"
        for update in updates
    )
    deadline = time.time() + 3
    while time.time() < deadline:
        if app.quest_service.snapshot(quest_id)["status"] == "active":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("quest status did not settle back to active after the background turn")
    assert quest_root.exists()


def test_chat_endpoint_relays_assistant_reply_to_bound_connector(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    manager = ConfigManager(temp_home)
    manager.ensure_files()
    connectors = manager.load_named("connectors")
    connectors["qq"]["enabled"] = True
    write_yaml(manager.path_for("connectors"), connectors)

    app = DaemonApp(temp_home)
    quest = app.quest_service.create("background relay quest")
    quest_id = quest["quest_id"]
    app.quest_service.bind_source(quest_id, "qq:direct:UserABC123")

    class FakeRunner:
        binary = ""

        def run(self, request):
            history_root = ensure_dir(request.quest_root / ".ds" / "codex_history" / request.run_id)
            run_root = ensure_dir(request.quest_root / ".ds" / "runs" / request.run_id)
            return RunResult(
                ok=True,
                run_id=request.run_id,
                model=request.model,
                output_text="Assistant relay payload.",
                exit_code=0,
                history_root=history_root,
                run_root=run_root,
                stderr_text="",
            )

    app.runners["codex"] = FakeRunner()

    sent: list[tuple[str, dict]] = []

    def fake_send_to_channel(channel_name, payload, *, connectors=None):  # noqa: ANN001
        sent.append((channel_name, dict(payload)))
        return True

    app.artifact_service._send_to_channel = fake_send_to_channel  # type: ignore[method-assign]

    payload = app.handlers.chat(quest_id, {"text": "Please continue.", "source": "tui-ink"})
    assert payload["ok"] is True

    deadline = time.time() + 3
    while time.time() < deadline:
        qq_payloads = [item for item in sent if item[0] == "qq"]
        if qq_payloads:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("assistant reply was not relayed to the bound QQ connector")

    channel_name, outbound = qq_payloads[-1]
    assert channel_name == "qq"
    assert outbound["conversation_id"] == "qq:direct:UserABC123"
    assert outbound["kind"] == "assistant"
    assert outbound["message"] == "Assistant relay payload."


def test_chat_endpoint_persists_client_message_delivery_state(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("client message id quest")
    quest_id = quest["quest_id"]

    payload = app.handlers.chat(
        quest_id,
        {
            "text": "Track this message.",
            "source": "web-react",
            "client_message_id": "client-msg-001",
        },
    )

    assert payload["ok"] is True
    assert payload["message"]["client_message_id"] == "client-msg-001"
    assert payload["message"]["delivery_state"] == "sent"

    history = app.quest_service.history(quest_id)
    record = next(item for item in history if item.get("role") == "user" and item.get("content") == "Track this message.")
    assert record["client_message_id"] == "client-msg-001"
    assert record["delivery_state"] == "sent"

    events = app.quest_service.events(quest_id)["events"]
    event = next(item for item in events if item.get("type") == "conversation.message" and item.get("content") == "Track this message.")
    assert event["client_message_id"] == "client-msg-001"
    assert event["delivery_state"] == "sent"


def test_chat_endpoint_passes_user_text_into_codex_prompt(temp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("prompt delivery quest")
    quest_id = quest["quest_id"]
    quest_root = Path(quest["quest_root"])

    fake_bin_root = temp_home / "bin"
    fake_bin_root.mkdir(parents=True, exist_ok=True)
    capture_path = temp_home / "captured-prompt.md"
    fake_codex = fake_bin_root / "codex"
    fake_codex.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json, os, sys",
                "prompt = sys.stdin.read()",
                "with open(os.environ['FAKE_CODEX_CAPTURE'], 'w', encoding='utf-8') as handle:",
                "    handle.write(prompt)",
                "print(json.dumps({'item': {'text': 'captured prompt ok'}}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin_root}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_CODEX_CAPTURE", str(capture_path))
    app.codex_runner.binary = "codex"
    app.runners["codex"] = app.codex_runner

    user_text = "Please confirm this user text reaches the Codex prompt."
    payload = app.handlers.chat(quest_id, {"text": user_text, "source": "tui-ink"})

    assert payload["ok"] is True

    deadline = time.time() + 5
    while time.time() < deadline:
        if capture_path.exists():
            history = app.quest_service.history(quest_id)
            if any(item.get("role") == "assistant" and item.get("content") == "captured prompt ok" for item in history):
                break
        time.sleep(0.05)
    else:
        raise AssertionError("codex prompt was not captured from the background chat run")

    captured_prompt = capture_path.read_text(encoding="utf-8")
    assert "## Current User Message" in captured_prompt
    assert user_text in captured_prompt

    prompt_files = sorted((quest_root / ".ds" / "runs").glob("*/prompt.md"))
    assert prompt_files
    prompt_text = prompt_files[-1].read_text(encoding="utf-8")
    assert user_text in prompt_text


def test_quest_control_resume_marks_quest_active(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("resume quest")
    quest_id = quest["quest_id"]

    app.quest_service.set_status(quest_id, "stopped")

    payload = app.handlers.quest_control(quest_id, {"action": "resume", "source": "tui-ink"})

    assert payload["ok"] is True
    assert payload["action"] == "resume"
    assert payload["snapshot"]["status"] == "active"
    history = app.quest_service.history(quest_id)
    assert any(
        item.get("role") == "assistant"
        and "DeepScientist" in str(item.get("content") or "")
        and ("恢复运行" in str(item.get("content") or "") or "resumed" in str(item.get("content") or "").lower())
        for item in history
    )
    outbox_path = temp_home / "logs" / "connectors" / "local" / "outbox.jsonl"
    outbox = [json.loads(line) for line in outbox_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(
        "DeepScientist" in str(item.get("message") or "")
        and ("恢复运行" in str(item.get("message") or "") or "resumed" in str(item.get("message") or "").lower())
        for item in outbox
    )


def test_quest_control_pause_marks_quest_paused_and_interrupts_runner(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("pause quest")
    quest_id = quest["quest_id"]

    class InterruptibleRunner:
        binary = ""

        def __init__(self) -> None:
            self.started = threading.Event()
            self.interrupted = threading.Event()

        def run(self, request):
            self.started.set()
            while not self.interrupted.is_set():
                time.sleep(0.05)
            history_root = ensure_dir(request.quest_root / ".ds" / "codex_history" / request.run_id)
            run_root = ensure_dir(request.quest_root / ".ds" / "runs" / request.run_id)
            return RunResult(
                ok=False,
                run_id=request.run_id,
                model=request.model,
                output_text="Interrupted.",
                exit_code=130,
                history_root=history_root,
                run_root=run_root,
                stderr_text="paused by user",
            )

        def interrupt(self, quest_id: str) -> bool:
            self.interrupted.set()
            return True

    runner = InterruptibleRunner()
    app.runners["codex"] = runner

    payload = app.handlers.chat(quest_id, {"text": "Pause this long task.", "source": "tui-ink"})
    assert payload["ok"] is True
    assert runner.started.wait(timeout=2)
    running_snapshot = app.quest_service.snapshot(quest_id)
    assert running_snapshot["status"] == "running"
    assert running_snapshot["active_run_id"]

    pause_payload = app.handlers.quest_control(quest_id, {"action": "pause", "source": "tui-ink"})

    assert pause_payload["ok"] is True
    assert pause_payload["action"] == "pause"
    assert pause_payload["interrupted"] is True
    assert pause_payload["snapshot"]["status"] == "paused"

    deadline = time.time() + 3
    while time.time() < deadline:
        paused_snapshot = app.quest_service.snapshot(quest_id)
        if paused_snapshot["status"] == "paused" and paused_snapshot["active_run_id"] is None:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("paused quest did not clear active_run_id after the runner exited")


def test_admin_shutdown_endpoint_requests_daemon_shutdown(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)

    payload = app.handlers.admin_shutdown({"source": "pytest"})

    assert payload["ok"] is True
    assert "shutdown requested" in payload["message"].lower()
    assert app._shutdown_requested.is_set() is True


def test_admin_shutdown_interrupts_running_quest_and_clears_active_run(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("shutdown interrupt quest")
    quest_id = quest["quest_id"]

    class InterruptOnlyRunner:
        binary = ""

        def __init__(self) -> None:
            self.interrupt_calls: list[str] = []

        def interrupt(self, target_quest_id: str) -> bool:
            self.interrupt_calls.append(target_quest_id)
            return True

    runner = InterruptOnlyRunner()
    app.runners["codex"] = runner
    app.quest_service.mark_turn_started(quest_id, run_id="run-stale-001", status="running")

    payload = app.handlers.admin_shutdown({"source": "pytest"})

    assert payload["ok"] is True
    assert quest_id in payload["interrupted_quests"]
    assert runner.interrupt_calls == [quest_id]

    snapshot = app.quest_service.snapshot(quest_id)
    assert snapshot["status"] == "stopped"
    assert snapshot["active_run_id"] is None


def test_daemon_startup_reconciles_stale_running_quest(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    first_app = DaemonApp(temp_home)
    quest = first_app.quest_service.create("reconcile stale runtime quest")
    quest_id = quest["quest_id"]
    quest_root = Path(quest["quest_root"])

    quest_yaml = read_yaml(quest_root / "quest.yaml", {})
    quest_yaml["status"] = "running"
    quest_yaml["active_run_id"] = "run-crashed-001"
    write_yaml(quest_root / "quest.yaml", quest_yaml)

    app = DaemonApp(temp_home)

    snapshot = app.quest_service.snapshot(quest_id)
    assert snapshot["status"] == "stopped"
    assert snapshot["active_run_id"] is None
    assert any(item["quest_id"] == quest_id for item in app.reconciled_quests)

    events = app.quest_service.events(quest_id)["events"]
    assert any(
        item.get("type") == "quest.runtime_reconciled"
        and item.get("abandoned_run_id") == "run-crashed-001"
        for item in events
    )


def test_chat_reply_auto_links_interaction_and_resumes_with_decision_skill(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("artifact reply quest")
    quest_id = quest["quest_id"]

    class InteractiveRunner:
        binary = ""

        def __init__(self, artifact_service) -> None:
            self.artifact_service = artifact_service
            self.requests: list[dict[str, str]] = []

        def run(self, request):
            self.requests.append(
                {
                    "run_id": request.run_id,
                    "message": request.message,
                    "skill_id": request.skill_id,
                }
            )
            history_root = ensure_dir(request.quest_root / ".ds" / "codex_history" / request.run_id)
            run_root = ensure_dir(request.quest_root / ".ds" / "runs" / request.run_id)
            if request.message == "Ask me A or B.":
                self.artifact_service.interact(
                    request.quest_root,
                    kind="decision_request",
                    message="先做A还是先做B？",
                    deliver_to_bound_conversations=False,
                    include_recent_inbound_messages=False,
                    options=[
                        {"id": "a", "label": "A"},
                        {"id": "b", "label": "B"},
                    ],
                    allow_free_text=False,
                )
                return RunResult(
                    ok=True,
                    run_id=request.run_id,
                    model=request.model,
                    output_text="Question sent.",
                    exit_code=0,
                    history_root=history_root,
                    run_root=run_root,
                    stderr_text="",
                )
            return RunResult(
                ok=True,
                run_id=request.run_id,
                model=request.model,
                output_text=f"收到选择：{request.message}",
                exit_code=0,
                history_root=history_root,
                run_root=run_root,
                stderr_text="",
            )

    runner = InteractiveRunner(app.artifact_service)
    app.runners["codex"] = runner

    initial_payload = app.handlers.chat(quest_id, {"text": "Ask me A or B.", "source": "tui-ink"})
    assert initial_payload["ok"] is True

    interaction_id = None
    deadline = time.time() + 3
    while time.time() < deadline:
        snapshot = app.quest_service.snapshot(quest_id)
        interactions = snapshot.get("active_interactions") or []
        if snapshot["status"] == "waiting_for_user" and interactions:
            interaction_id = interactions[-1]["interaction_id"]
            break
        time.sleep(0.05)
    else:
        raise AssertionError("decision_request was not persisted as a waiting interaction")

    reply_payload = app.handlers.chat(quest_id, {"text": "我选A。", "source": "tui-ink"})
    assert reply_payload["ok"] is True
    assert reply_payload["message"]["reply_to_interaction_id"] == interaction_id

    deadline = time.time() + 3
    while time.time() < deadline:
        history = app.quest_service.history(quest_id)
        if any(item.get("role") == "assistant" and item.get("content") == "收到选择：我选A。" for item in history):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("assistant follow-up was not appended after the interaction reply")

    history = app.quest_service.history(quest_id)
    reply_record = next(item for item in history if item.get("role") == "user" and item.get("content") == "我选A。")
    assert reply_record["reply_to_interaction_id"] == interaction_id
    assert runner.requests[-1]["skill_id"] == "decision"

    deadline = time.time() + 3
    while time.time() < deadline:
        snapshot = app.quest_service.snapshot(quest_id)
        if snapshot["status"] == "active" and snapshot["active_run_id"] is None:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("quest did not settle back to active after handling the interaction reply")

    events = app.quest_service.events(quest_id)["events"]
    assert any(
        item.get("type") == "interaction.reply_received"
        and item.get("reply_to_interaction_id") == interaction_id
        for item in events
    )


def test_chat_reply_auto_links_latest_threaded_progress_without_forcing_decision_skill(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("threaded progress chat quest")
    quest_id = quest["quest_id"]

    class ThreadedRunner:
        binary = ""

        def __init__(self, artifact_service) -> None:
            self.artifact_service = artifact_service
            self.requests: list[dict[str, str]] = []

        def run(self, request):
            self.requests.append(
                {
                    "run_id": request.run_id,
                    "message": request.message,
                    "skill_id": request.skill_id,
                }
            )
            history_root = ensure_dir(request.quest_root / ".ds" / "codex_history" / request.run_id)
            run_root = ensure_dir(request.quest_root / ".ds" / "runs" / request.run_id)
            if request.message == "Start the audit.":
                self.artifact_service.interact(
                    request.quest_root,
                    kind="progress",
                    message="老师，我已经开始审计仓库结构，下一步会确认依赖入口。",
                    deliver_to_bound_conversations=False,
                    include_recent_inbound_messages=False,
                )
                return RunResult(
                    ok=True,
                    run_id=request.run_id,
                    model=request.model,
                    output_text="Audit started.",
                    exit_code=0,
                    history_root=history_root,
                    run_root=run_root,
                    stderr_text="",
                )
            return RunResult(
                ok=True,
                run_id=request.run_id,
                model=request.model,
                output_text=f"继续执行：{request.message}",
                exit_code=0,
                history_root=history_root,
                run_root=run_root,
                stderr_text="",
            )

    runner = ThreadedRunner(app.artifact_service)
    app.runners["codex"] = runner

    initial_payload = app.handlers.chat(quest_id, {"text": "Start the audit.", "source": "tui-ink"})
    assert initial_payload["ok"] is True

    threaded_interaction_id = None
    deadline = time.time() + 3
    while time.time() < deadline:
        snapshot = app.quest_service.snapshot(quest_id)
        threaded_interaction_id = snapshot.get("default_reply_interaction_id")
        if threaded_interaction_id:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("threaded progress interaction was not persisted")

    reply_payload = app.handlers.chat(quest_id, {"text": "继续，先确认 requirements 和入口脚本。", "source": "web-react"})
    assert reply_payload["ok"] is True
    assert reply_payload["message"]["reply_to_interaction_id"] == threaded_interaction_id

    deadline = time.time() + 3
    while time.time() < deadline:
        history = app.quest_service.history(quest_id)
        if any(item.get("role") == "assistant" and item.get("content") == "继续执行：继续，先确认 requirements 和入口脚本。" for item in history):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("assistant follow-up was not appended after threaded progress reply")

    assert runner.requests[-1]["skill_id"] != "decision"


def test_running_turn_consumes_queued_user_messages_via_artifact_without_duplicate_follow_up_turn(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("running mailbox quest")
    quest_id = quest["quest_id"]

    class MailboxRunner:
        binary = ""

        def __init__(self, artifact_service) -> None:
            self.artifact_service = artifact_service
            self.requests: list[str] = []
            self.started = threading.Event()
            self.mailbox_ready = threading.Event()
            self.mailbox_payloads: list[dict] = []

        def run(self, request):
            self.requests.append(request.message)
            history_root = ensure_dir(request.quest_root / ".ds" / "codex_history" / request.run_id)
            run_root = ensure_dir(request.quest_root / ".ds" / "runs" / request.run_id)
            if request.message == "Start long task.":
                self.started.set()
                assert self.mailbox_ready.wait(timeout=3)
                mailbox = self.artifact_service.interact(
                    request.quest_root,
                    kind="progress",
                    message="老师，我先检查运行中的邮箱。",
                    deliver_to_bound_conversations=False,
                    include_recent_inbound_messages=True,
                )
                self.mailbox_payloads.append(mailbox)
                return RunResult(
                    ok=True,
                    run_id=request.run_id,
                    model=request.model,
                    output_text=mailbox["agent_instruction"],
                    exit_code=0,
                    history_root=history_root,
                    run_root=run_root,
                    stderr_text="",
                )
            return RunResult(
                ok=True,
                run_id=request.run_id,
                model=request.model,
                output_text=f"unexpected extra turn: {request.message}",
                exit_code=0,
                history_root=history_root,
                run_root=run_root,
                stderr_text="",
            )

    runner = MailboxRunner(app.artifact_service)
    app.runners["codex"] = runner

    initial_payload = app.handlers.chat(quest_id, {"text": "Start long task.", "source": "tui-ink"})
    assert initial_payload["ok"] is True
    assert runner.started.wait(timeout=3)

    queued_payload = app.handlers.chat(
        quest_id,
        {"text": "Please also inspect config.", "source": "web-react"},
    )
    assert queued_payload["ok"] is True
    assert queued_payload["started"] is False
    assert queued_payload["queued"] is True

    runner.mailbox_ready.set()

    deadline = time.time() + 3
    while time.time() < deadline:
        history = app.quest_service.history(quest_id)
        if any(
            item.get("role") == "assistant"
            and "这是最新用户的要求" in str(item.get("content") or "")
            for item in history
        ):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("mailbox delivery was not surfaced in the assistant output")

    assert runner.requests == ["Start long task."]
    assert len(runner.mailbox_payloads) == 1
    assert [item["text"] for item in runner.mailbox_payloads[0]["recent_inbound_messages"]] == [
        "Please also inspect config."
    ]
    snapshot = app.quest_service.snapshot(quest_id)
    assert snapshot["pending_user_message_count"] == 0


def test_status_formatter_includes_detailed_runtime_state(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("status detail quest")

    rendered = app._format_status(app.quest_service.snapshot(quest["quest_id"]))

    assert "Quest Status" in rendered
    assert "- quest_id:" in rendered
    assert "- quest_root:" in rendered
    assert "- runner:" in rendered
    assert "- history_count:" in rendered


def test_stop_then_user_text_continues_same_quest_context(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("stop and continue quest")
    quest_id = quest["quest_id"]
    quest_root = Path(quest["quest_root"])

    class InterruptibleRunner:
        binary = ""

        def __init__(self) -> None:
            self.started = threading.Event()
            self.interrupted = threading.Event()
            self.seen_messages: list[str] = []

        def run(self, request):
            self.seen_messages.append(request.message)
            self.started.set()
            if request.message == "First long task.":
                while not self.interrupted.is_set():
                    time.sleep(0.05)
                history_root = ensure_dir(request.quest_root / ".ds" / "codex_history" / request.run_id)
                run_root = ensure_dir(request.quest_root / ".ds" / "runs" / request.run_id)
                return RunResult(
                    ok=False,
                    run_id=request.run_id,
                    model=request.model,
                    output_text="Interrupted.",
                    exit_code=130,
                    history_root=history_root,
                    run_root=run_root,
                    stderr_text="stopped by user",
                )
            history_root = ensure_dir(request.quest_root / ".ds" / "codex_history" / request.run_id)
            run_root = ensure_dir(request.quest_root / ".ds" / "runs" / request.run_id)
            return RunResult(
                ok=True,
                run_id=request.run_id,
                model=request.model,
                output_text=f"Echo: {request.message}",
                exit_code=0,
                history_root=history_root,
                run_root=run_root,
                stderr_text="",
            )

        def interrupt(self, quest_id: str) -> bool:
            self.interrupted.set()
            return True

    runner = InterruptibleRunner()
    app.runners["codex"] = runner

    first_payload = app.handlers.chat(quest_id, {"text": "First long task.", "source": "tui-ink"})

    assert first_payload["ok"] is True
    assert runner.started.wait(timeout=2)

    stop_payload = app.handlers.quest_control(quest_id, {"action": "stop", "source": "tui-ink"})

    assert stop_payload["ok"] is True
    assert stop_payload["action"] == "stop"
    assert stop_payload["interrupted"] is True
    assert stop_payload["snapshot"]["status"] == "stopped"
    assert stop_payload["snapshot"]["pending_user_message_count"] == 0
    assert "DeepScientist" in str(stop_payload["notice"]["message"])
    assert "停止状态" in str(stop_payload["notice"]["message"])
    queue_after_stop = json.loads((quest_root / ".ds" / "user_message_queue.json").read_text(encoding="utf-8"))
    assert queue_after_stop["pending"] == []
    completed_status_by_id = {
        str(item.get("message_id") or ""): str(item.get("status") or "")
        for item in queue_after_stop["completed"]
    }
    assert completed_status_by_id[first_payload["message"]["id"]] == "accepted_by_run"

    follow_up = "Continue from the same quest context."
    next_payload = app.handlers.chat(quest_id, {"text": follow_up, "source": "tui-ink"})

    assert next_payload["ok"] is True
    assert next_payload["auto_resumed"] is True
    assert next_payload["previous_status"] == "stopped"

    deadline = time.time() + 3
    while time.time() < deadline:
        if follow_up in runner.seen_messages:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("follow-up user text was not delivered to the same quest runner")

    history = app.quest_service.history(quest_id)
    assert any(item.get("role") == "user" and item.get("content") == follow_up for item in history)
    assert any(
        item.get("role") == "assistant"
        and "DeepScientist" in str(item.get("content") or "")
        and "停止状态" in str(item.get("content") or "")
        for item in history
    )
    outbox_path = temp_home / "logs" / "connectors" / "local" / "outbox.jsonl"
    outbox = [json.loads(line) for line in outbox_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(
        "DeepScientist" in str(item.get("message") or "")
        and "停止状态" in str(item.get("message") or "")
        for item in outbox
    )


def test_stop_cancels_queued_mailbox_messages_and_preserves_audit(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    app = DaemonApp(temp_home)
    quest = app.quest_service.create("stop queue cleanup quest")
    quest_id = quest["quest_id"]
    quest_root = Path(quest["quest_root"])

    class InterruptibleRunner:
        binary = ""

        def __init__(self) -> None:
            self.started = threading.Event()
            self.interrupted = threading.Event()
            self.seen_messages: list[str] = []

        def run(self, request):
            self.seen_messages.append(request.message)
            self.started.set()
            history_root = ensure_dir(request.quest_root / ".ds" / "codex_history" / request.run_id)
            run_root = ensure_dir(request.quest_root / ".ds" / "runs" / request.run_id)
            if request.message == "Run the primary task.":
                while not self.interrupted.is_set():
                    time.sleep(0.05)
                return RunResult(
                    ok=False,
                    run_id=request.run_id,
                    model=request.model,
                    output_text="Interrupted.",
                    exit_code=130,
                    history_root=history_root,
                    run_root=run_root,
                    stderr_text="stopped by user",
                )
            return RunResult(
                ok=True,
                run_id=request.run_id,
                model=request.model,
                output_text=f"Echo: {request.message}",
                exit_code=0,
                history_root=history_root,
                run_root=run_root,
                stderr_text="",
            )

        def interrupt(self, target_quest_id: str) -> bool:
            self.interrupted.set()
            return True

    runner = InterruptibleRunner()
    app.runners["codex"] = runner

    initial_payload = app.handlers.chat(quest_id, {"text": "Run the primary task.", "source": "tui-ink"})
    assert initial_payload["ok"] is True
    assert runner.started.wait(timeout=2)

    queued_payload = app.handlers.chat(
        quest_id,
        {"text": "Please also inspect config.", "source": "web-react"},
    )
    assert queued_payload["ok"] is True
    assert queued_payload["queued"] is True

    stop_payload = app.handlers.quest_control(quest_id, {"action": "stop", "source": "tui-ink"})
    assert stop_payload["ok"] is True
    assert stop_payload["snapshot"]["status"] == "stopped"
    assert stop_payload["snapshot"]["pending_user_message_count"] == 0
    assert stop_payload["cancelled_pending_user_message_count"] == 1

    queue_after_stop = json.loads((quest_root / ".ds" / "user_message_queue.json").read_text(encoding="utf-8"))
    assert queue_after_stop["pending"] == []
    completed_status_by_id = {
        str(item.get("message_id") or ""): str(item.get("status") or "")
        for item in queue_after_stop["completed"]
    }
    assert completed_status_by_id[initial_payload["message"]["id"]] == "accepted_by_run"
    assert completed_status_by_id[queued_payload["message"]["id"]] == "cancelled_by_stop"

    follow_up = app.handlers.chat(quest_id, {"text": "Continue cleanly.", "source": "tui-ink"})
    assert follow_up["ok"] is True

    deadline = time.time() + 3
    while time.time() < deadline:
        if "Continue cleanly." in runner.seen_messages:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("fresh post-stop turn was not started")

    assert runner.seen_messages == ["Run the primary task.", "Continue cleanly."]
