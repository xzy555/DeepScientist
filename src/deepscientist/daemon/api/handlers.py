from __future__ import annotations

import base64
import html
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import parse_qs, unquote

from ...acp import OptionalACPBridge, build_session_descriptor, build_session_update, get_acp_bridge_status
from ...bash_exec.service import DEFAULT_TERMINAL_SESSION_ID
from ...gitops import commit_detail, compare_refs, diff_file_between_refs, diff_file_for_commit, export_git_graph, list_branch_canvas, log_ref_history
from ...shared import generate_id, read_text, resolve_within, sha256_text, utc_now
from ...runners import RunRequest


class ApiHandlers:
    def __init__(self, app: "DaemonApp") -> None:
        self.app = app

    def root(self) -> tuple[int, dict, str]:
        dist_root = self._ui_dist_root()
        if dist_root is None:
            return 200, self._html_headers(), self._ui_build_required_page()
        payload = dist_root.joinpath("index.html").read_text(encoding="utf-8")
        payload = self._inject_ui_runtime(payload)
        return 200, self._html_headers(), payload

    def spa_root(self, spa_path: str) -> tuple[int, dict, str]:
        return self.root()

    def ui_asset(self, ui_path: str) -> tuple[int, dict, bytes]:
        dist_root = self._ui_dist_root()
        if dist_root is None:
            return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"UI bundle is not built."
        path = resolve_within(dist_root, ui_path)
        if not path.exists() or not path.is_file():
            return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"Not Found"
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return 200, self._asset_headers(mime_type), path.read_bytes()

    @staticmethod
    def _html_headers() -> dict[str, str]:
        return {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store, max-age=0, must-revalidate",
        }

    @staticmethod
    def _asset_headers(mime_type: str) -> dict[str, str]:
        return {
            "Content-Type": mime_type,
            "Cache-Control": "no-store, max-age=0, must-revalidate",
        }

    def _ui_dist_root(self) -> Path | None:
        dist_root = self.app.repo_root / "src" / "ui" / "dist"
        if dist_root.exists() and dist_root.joinpath("index.html").exists():
            return dist_root
        return None

    def _inject_ui_runtime(self, payload: str) -> str:
        runtime_payload = {
            "surface": "quest",
            "supports": {
                "productApis": False,
                "socketIo": False,
                "notifications": False,
                "broadcasts": False,
                "points": False,
                "arxiv": False,
                "cliFrontend": False,
            },
        }
        bootstrap = (
            "<script>"
            f"window.__DEEPSCIENTIST_RUNTIME__ = {json.dumps(runtime_payload, ensure_ascii=False)};"
            "</script>"
        )
        if "</head>" in payload:
            return payload.replace("</head>", f"{bootstrap}</head>", 1)
        if "<body>" in payload:
            return payload.replace("<body>", f"<body>{bootstrap}", 1)
        return f"{bootstrap}{payload}"

    def _ui_build_required_page(self) -> str:
        repo_root = html.escape(str(self.app.repo_root))
        return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>DeepScientist UI build required</title>
    <link rel="stylesheet" href="/assets/fonts/ds-fonts.css" />
    <style>
      :root {{
        color-scheme: light dark;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background:
          radial-gradient(120% 80% at 14% 0%, rgba(215, 198, 174, 0.32), transparent 58%),
          radial-gradient(80% 70% at 88% 12%, rgba(159, 177, 194, 0.28), transparent 56%),
          linear-gradient(180deg, rgba(250, 247, 241, 0.98), rgba(244, 239, 233, 0.98));
        color: #1f2937;
      }}
      .panel {{
        width: min(720px, calc(100vw - 32px));
        border: 1px solid rgba(15, 23, 42, 0.08);
        border-radius: 28px;
        background: rgba(255, 255, 255, 0.78);
        backdrop-filter: blur(18px);
        box-shadow: 0 24px 80px -52px rgba(18, 24, 32, 0.35);
        padding: 28px;
      }}
      h1 {{ margin: 0 0 12px; font-size: 1.6rem; }}
      p {{ margin: 0 0 16px; line-height: 1.7; }}
      pre {{
        margin: 0;
        padding: 14px 16px;
        border-radius: 18px;
        background: rgba(15, 23, 42, 0.05);
        overflow: auto;
      }}
    </style>
  </head>
  <body>
    <main class="panel">
      <h1>DeepScientist UI bundle is not built yet</h1>
      <p>The daemon is running, but the local Copilot-style web workspace requires the React bundle in <code>src/ui/dist</code>.</p>
      <p>From <code>{repo_root}</code>, run:</p>
      <pre>npm --prefix src/ui install
npm --prefix src/ui run build</pre>
      <p>For live frontend development, you can also run <code>npm --prefix src/ui run dev</code> in a separate terminal.</p>
    </main>
  </body>
</html>
"""

    def health(self) -> dict:
        return {
            "status": "ok",
            "home": str(self.app.home.resolve()),
            "daemon_id": self.app.daemon_id,
            "managed_by": self.app.daemon_managed_by,
            "pid": os.getpid(),
            "sessions": self.app.sessions.snapshot(),
        }

    def cli_health(self) -> dict:
        online_channels = [
            channel.status()
            for channel in self.app.channels.values()
            if channel.status().get("enabled") is not False
        ]
        return {
            "status": "ok",
            "timestamp": utc_now(),
            "checks": {
                "local": {
                    "status": "online",
                    "online_count": len(online_channels),
                    "active_connections": len(self.app.sessions.snapshot()),
                    "local_connections": len(self.app.sessions.snapshot()),
                }
            },
        }

    def admin_shutdown(self, body: dict) -> dict:
        source = str(body.get("source") or "local-admin").strip() or "local-admin"
        expected_daemon_id = str(body.get("daemon_id") or "").strip()
        if expected_daemon_id and expected_daemon_id != self.app.daemon_id:
            return {
                "ok": False,
                "message": "Daemon identity mismatch.",
                "daemon_id": self.app.daemon_id,
            }
        return self.app.request_shutdown(source=source)

    def acp_status(self) -> dict:
        return get_acp_bridge_status().as_dict()

    def connectors(self) -> list[dict]:
        return [channel.status() for channel in self.app.channels.values()]

    def bridge_webhook(self, connector: str, *, method: str, path: str, raw_body: bytes, headers: dict[str, str], body: dict) -> tuple[int, dict, bytes | str] | dict:
        return self.app.handle_bridge_webhook(
            connector,
            method=method,
            path=path,
            raw_body=raw_body,
            headers=headers,
            body=body,
        )

    def qq_bindings(self) -> list[dict]:
        return self.app.list_qq_bindings()

    def connector_bindings(self, connector: str) -> list[dict]:
        return self.app.list_connector_bindings(connector)

    def qq_inbound(self, body: dict) -> dict:
        return self.app.handle_qq_inbound(body)

    def connector_inbound(self, connector: str, body: dict) -> dict:
        return self.app.handle_connector_inbound(connector, body)

    def quests(self) -> list[dict]:
        return self.app.quest_service.list_quests()

    def quest_create(self, body: dict) -> dict:
        goal = body.get("goal", "").strip()
        title = body.get("title", "").strip() or None
        quest_id = body.get("quest_id", "").strip() or None
        source = body.get("source", "").strip() or "web"
        if not goal:
            return {"ok": False, "message": "Quest goal is required."}
        snapshot = self.app.create_quest(goal=goal, title=title, quest_id=quest_id, source=source)
        return {"ok": True, "snapshot": snapshot}

    def quest(self, quest_id: str) -> dict:
        return self.app.quest_service.snapshot(quest_id)

    def quest_settings(self, quest_id: str, body: dict) -> dict | tuple[int, dict]:
        updates = {
            "title": body.get("title") if "title" in body else None,
            "active_anchor": body.get("active_anchor") if "active_anchor" in body else None,
            "default_runner": body.get("default_runner") if "default_runner" in body else None,
        }
        if all(value is None for value in updates.values()):
            return {
                "ok": True,
                "snapshot": self.app.quest_service.snapshot(quest_id),
            }
        try:
            snapshot = self.app.quest_service.update_settings(quest_id, **updates)
        except FileNotFoundError:
            return 404, {"ok": False, "message": f"Unknown quest `{quest_id}`."}
        except ValueError as exc:
            return 400, {"ok": False, "message": str(exc)}
        return {
            "ok": True,
            "snapshot": snapshot,
        }

    def quest_session(self, quest_id: str) -> dict:
        snapshot = self.app.quest_service.snapshot(quest_id)
        return {
            "ok": True,
            "quest_id": quest_id,
            "snapshot": snapshot,
            "acp_session": build_session_descriptor(snapshot),
        }

    def quest_events(self, quest_id: str, path: str) -> dict:
        query = self.parse_query(path)
        after = int((query.get("after") or ["0"])[0] or "0")
        limit = int((query.get("limit") or ["200"])[0] or "200")
        tail_raw = ((query.get("tail") or ["0"])[0] or "0").strip().lower()
        tail = tail_raw in {"1", "true", "yes", "on"}
        format_name = ((query.get("format") or ["both"])[0] or "both").lower()
        session_id = ((query.get("session_id") or [f"quest:{quest_id}"])[0] or f"quest:{quest_id}")
        payload = self.app.quest_service.events(quest_id, after=after, limit=limit, tail=tail)
        if format_name in {"acp", "both"}:
            payload["acp_updates"] = [
                build_session_update(
                    event,
                    quest_id=quest_id,
                    cursor=event["cursor"],
                    session_id=session_id,
                )
                for event in payload["events"]
            ]
        if format_name in {"acp_sdk", "both"}:
            bridge = OptionalACPBridge()
            payload["acp_sdk_available"] = bridge.is_available()
            payload["acp_sdk_notifications"] = [
                notification
                for event in payload["events"]
                for notification in [bridge.build_sdk_notification(session_id=session_id, event=event)]
                if notification is not None
            ]
        if format_name == "acp":
            payload["events"] = []
            payload.pop("acp_sdk_notifications", None)
        elif format_name == "acp_sdk":
            payload["events"] = []
            payload.pop("acp_updates", None)
        payload["format"] = format_name
        payload["session_id"] = session_id
        return payload

    def quest_artifacts(self, quest_id: str) -> dict:
        return self.app.quest_service.artifacts(quest_id)

    def history(self, quest_id: str) -> list[dict]:
        return self.app.quest_service.history(quest_id)

    def bash_sessions(self, quest_id: str, path: str) -> list[dict]:
        query = self.parse_query(path)
        status = ((query.get("status") or [""])[0] or "").strip() or None
        chat_session_id = ((query.get("chat_session_id") or [""])[0] or "").strip() or None
        limit_raw = ((query.get("limit") or ["200"])[0] or "200").strip()
        try:
            limit = max(1, min(int(limit_raw), 500))
        except ValueError:
            limit = 200
        agent_ids = [
            item.strip()
            for item in (((query.get("agent_ids") or [""])[0] or "").split(","))
            if item.strip()
        ]
        agent_instance_ids = [
            item.strip()
            for item in (((query.get("agent_instance_ids") or [""])[0] or "").split(","))
            if item.strip()
        ]
        quest_root = self.app.quest_service._quest_root(quest_id)
        return self.app.bash_exec_service.list_sessions(
            quest_root,
            status=status,
            agent_ids=agent_ids or None,
            agent_instance_ids=agent_instance_ids or None,
            chat_session_id=chat_session_id,
            limit=limit,
        )

    def bash_session(self, quest_id: str, bash_id: str) -> dict | tuple[int, dict]:
        quest_root = self.app.quest_service._quest_root(quest_id)
        try:
            return self.app.bash_exec_service.get_session(quest_root, bash_id)
        except FileNotFoundError:
            return 404, {"ok": False, "message": f"Unknown bash session `{bash_id}`."}

    def bash_logs(self, quest_id: str, bash_id: str, path: str) -> tuple[int, dict, bytes]:
        query = self.parse_query(path)
        limit_raw = ((query.get("limit") or ["200"])[0] or "200").strip()
        before_seq_raw = ((query.get("before_seq") or [""])[0] or "").strip()
        order = ((query.get("order") or ["asc"])[0] or "asc").strip().lower()
        try:
            limit = max(1, min(int(limit_raw), 1000))
        except ValueError:
            limit = 200
        before_seq = int(before_seq_raw) if before_seq_raw.isdigit() else None
        quest_root = self.app.quest_service._quest_root(quest_id)
        try:
            entries, meta = self.app.bash_exec_service.read_log_entries(
                quest_root,
                bash_id,
                limit=limit,
                before_seq=before_seq,
                order=order,
            )
        except FileNotFoundError:
            payload = json.dumps({"ok": False, "message": f"Unknown bash session `{bash_id}`."}, ensure_ascii=False).encode("utf-8")
            return 404, {"Content-Type": "application/json; charset=utf-8"}, payload
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Bash-Log-Tail-Limit": str(meta.get("tail_limit") or ""),
            "X-Bash-Log-Tail-Start-Seq": str(meta.get("tail_start_seq") or ""),
            "X-Bash-Log-Latest-Seq": str(meta.get("latest_seq") or ""),
        }
        payload = json.dumps(entries, ensure_ascii=False).encode("utf-8")
        return 200, headers, payload

    def bash_stop(self, quest_id: str, bash_id: str, body: dict) -> dict | tuple[int, dict]:
        quest_root = self.app.quest_service._quest_root(quest_id)
        try:
            session = self.app.bash_exec_service.request_stop(
                quest_root,
                bash_id,
                reason=str(body.get("reason") or "").strip() or None,
                user_id="web-react",
            )
        except FileNotFoundError:
            return 404, {"success": False, "status": "not_found"}
        return {
            "success": True,
            "status": session.get("status"),
            "session": session,
        }

    def terminal_session_ensure(self, quest_id: str, body: dict | None = None) -> dict:
        payload = body or {}
        quest_root = self.app.quest_service._quest_root(quest_id)
        workspace_root = self.app.quest_service.active_workspace_root(quest_root)
        requested_bash_id = str(payload.get("bash_id") or "").strip() or None
        requested_label = str(payload.get("label") or "").strip() or None
        requested_cwd = str(payload.get("cwd") or "").strip() or None
        create_new_raw = payload.get("create_new")
        create_new = bool(create_new_raw) and str(create_new_raw).strip().lower() not in {"0", "false", "no", "off"}

        bash_id = requested_bash_id
        if create_new:
            # `terminal-main` is reserved for the default terminal session.
            if not bash_id or bash_id == DEFAULT_TERMINAL_SESSION_ID:
                bash_id = generate_id("terminal")
            # Guard against collisions (e.g., fast repeated clicks).
            for _ in range(8):
                if not self.app.bash_exec_service.meta_path(quest_root, bash_id).exists():
                    break
                bash_id = generate_id("terminal")
        bash_id = bash_id or DEFAULT_TERMINAL_SESSION_ID

        cwd_path = workspace_root
        if requested_cwd:
            candidate = Path(requested_cwd).expanduser()
            if candidate.is_absolute():
                cwd_path = candidate
            else:
                cwd_path = (workspace_root / candidate)

        session = self.app.bash_exec_service.ensure_terminal_session(
            quest_root,
            quest_id=quest_id,
            bash_id=bash_id,
            label=requested_label,
            cwd=cwd_path,
            source=str(payload.get("source") or "web-react").strip() or "web-react",
            conversation_id=str(payload.get("conversation_id") or "").strip() or None,
            user_id=str(payload.get("user_id") or "").strip() or None,
        )
        return {
            "ok": True,
            "session": session,
        }

    def terminal_input(self, quest_id: str, session_id: str, body: dict) -> dict | tuple[int, dict]:
        quest_root = self.app.quest_service._quest_root(quest_id)
        try:
            result = self.app.bash_exec_service.append_terminal_input(
                quest_root,
                session_id,
                data=str(body.get("data") or ""),
                source=str(body.get("source") or "web-react").strip() or "web-react",
                user_id=str(body.get("user_id") or "").strip() or None,
                conversation_id=str(body.get("conversation_id") or "").strip() or None,
            )
        except FileNotFoundError:
            return 404, {"ok": False, "message": f"Unknown terminal session `{session_id}`."}
        except ValueError as exc:
            return 400, {"ok": False, "message": str(exc)}
        return result

    def terminal_restore(self, quest_id: str, session_id: str, path: str) -> dict | tuple[int, dict]:
        query = self.parse_query(path)
        commands_raw = ((query.get("commands") or ["10"])[0] or "10").strip()
        output_raw = ((query.get("output") or ["80"])[0] or "80").strip()
        try:
            command_limit = max(1, min(int(commands_raw), 50))
        except ValueError:
            command_limit = 10
        try:
            output_limit = max(1, min(int(output_raw), 400))
        except ValueError:
            output_limit = 80
        quest_root = self.app.quest_service._quest_root(quest_id)
        try:
            return self.app.bash_exec_service.terminal_restore_payload(
                quest_root,
                session_id,
                command_limit=command_limit,
                output_limit=output_limit,
            )
        except FileNotFoundError:
            return 404, {"ok": False, "message": f"Unknown terminal session `{session_id}`."}

    def terminal_history(self, quest_id: str, path: str) -> dict:
        query = self.parse_query(path)
        limit_raw = ((query.get("limit") or ["20"])[0] or "20").strip()
        try:
            limit = max(1, min(int(limit_raw), 100))
        except ValueError:
            limit = 20
        quest_root = self.app.quest_service._quest_root(quest_id)
        sessions = self.app.bash_exec_service.list_sessions(
            quest_root,
            limit=limit,
        )
        terminal_sessions = [item for item in sessions if str(item.get("kind") or "") == "terminal"]
        exec_sessions = [item for item in sessions if str(item.get("kind") or "exec") != "terminal"]
        default_session_id = terminal_sessions[0]["bash_id"] if terminal_sessions else None
        return {
            "ok": True,
            "default_session_id": default_session_id,
            "terminal_sessions": terminal_sessions,
            "exec_sessions": exec_sessions[:limit],
        }

    def quest_control(self, quest_id: str, body: dict) -> dict:
        action = str(body.get("action") or "").strip().lower()
        source = str(body.get("source") or "local-ui").strip() or "local-ui"
        if action not in {"pause", "stop", "resume"}:
            return {"ok": False, "message": "Quest control action must be `pause`, `stop` or `resume`."}
        return self.app.control_quest(quest_id, action=action, source=source)

    def workflow(self, quest_id: str) -> dict:
        return self.app.quest_service.workflow(quest_id)

    def node_traces(self, quest_id: str, path: str) -> dict:
        query = self.parse_query(path)
        selection_type = ((query.get("selection_type") or [""])[0] or "").strip() or None
        return self.app.quest_service.node_traces(quest_id, selection_type=selection_type)

    def node_trace(self, quest_id: str, node_ref: str, path: str) -> dict:
        query = self.parse_query(path)
        selection_type = ((query.get("selection_type") or [""])[0] or "").strip() or None
        return self.app.quest_service.node_trace(
            quest_id,
            unquote(node_ref),
            selection_type=selection_type,
        )

    def graph(self, quest_id: str) -> dict:
        quest_root = self.app.quest_service._quest_root(quest_id)
        return export_git_graph(quest_root, quest_root / "artifacts" / "graphs")

    def metrics_timeline(self, quest_id: str) -> dict:
        return self.app.quest_service.metrics_timeline(quest_id)

    def git_branches(self, quest_id: str) -> dict:
        quest_root = self.app.quest_service._quest_root(quest_id)
        return list_branch_canvas(quest_root, quest_id=quest_id)

    def git_log(self, quest_id: str, path: str) -> dict:
        query = self.parse_query(path)
        ref = ((query.get("ref") or [""])[0] or "").strip()
        base = ((query.get("base") or [""])[0] or "").strip() or None
        limit_raw = ((query.get("limit") or ["30"])[0] or "30").strip()
        if not ref:
            return {"ok": False, "message": "`ref` is required."}
        try:
            limit = max(1, min(int(limit_raw), 100))
        except ValueError:
            limit = 30
        quest_root = self.app.quest_service._quest_root(quest_id)
        return log_ref_history(quest_root, ref=ref, base=base, limit=limit)

    def git_compare(self, quest_id: str, path: str) -> dict:
        query = self.parse_query(path)
        base = ((query.get("base") or [""])[0] or "").strip()
        head = ((query.get("head") or [""])[0] or "").strip()
        if not base or not head:
            return {"ok": False, "message": "`base` and `head` are required."}
        quest_root = self.app.quest_service._quest_root(quest_id)
        return compare_refs(quest_root, base=base, head=head)

    def git_commit(self, quest_id: str, path: str) -> dict:
        query = self.parse_query(path)
        sha = ((query.get("sha") or [""])[0] or "").strip()
        if not sha:
            return {"ok": False, "message": "`sha` is required."}
        quest_root = self.app.quest_service._quest_root(quest_id)
        return commit_detail(quest_root, sha=sha)

    def git_diff_file(self, quest_id: str, path: str) -> dict:
        query = self.parse_query(path)
        base = ((query.get("base") or [""])[0] or "").strip()
        head = ((query.get("head") or [""])[0] or "").strip()
        file_path = ((query.get("path") or [""])[0] or "").strip()
        if not base or not head or not file_path:
            return {"ok": False, "message": "`base`, `head`, and `path` are required."}
        quest_root = self.app.quest_service._quest_root(quest_id)
        return diff_file_between_refs(quest_root, base=base, head=head, path=file_path)

    def git_commit_file(self, quest_id: str, path: str) -> dict:
        query = self.parse_query(path)
        sha = ((query.get("sha") or [""])[0] or "").strip()
        file_path = ((query.get("path") or [""])[0] or "").strip()
        if not sha or not file_path:
            return {"ok": False, "message": "`sha` and `path` are required."}
        quest_root = self.app.quest_service._quest_root(quest_id)
        return diff_file_for_commit(quest_root, sha=sha, path=file_path)

    def graph_asset(self, quest_id: str, kind: str) -> tuple[int, dict, bytes]:
        graph = self.graph(quest_id)
        key = f"{kind}_path"
        raw_path = graph.get(key)
        if not raw_path:
            return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"Not Found"

        quest_root = self.app.quest_service._quest_root(quest_id)
        raw_file = Path(str(raw_path))
        if raw_file.is_absolute():
            try:
                relative = raw_file.relative_to(quest_root)
            except ValueError:
                return 400, {"Content-Type": "text/plain; charset=utf-8"}, b"Invalid graph path"
            path = resolve_within(quest_root, str(relative))
        else:
            path = resolve_within(quest_root, str(raw_file))

        if not path.exists() or not path.is_file():
            return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"Not Found"

        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return 200, {"Content-Type": mime_type}, path.read_bytes()

    def runs(self, quest_id: str) -> list[dict]:
        return self.app.quest_service.snapshot(quest_id).get("recent_runs", [])

    def quest_memory(self, quest_id: str) -> list[dict]:
        return self.app.memory_service.list_cards(
            scope="quest",
            quest_root=self.app.quest_service._quest_root(quest_id),
        )

    def documents(self, quest_id: str) -> list[dict]:
        return self.app.quest_service.list_documents(quest_id)

    def explorer(self, quest_id: str, path: str) -> dict:
        query = self.parse_query(path)
        revision = ((query.get("revision") or [""])[0] or "").strip() or None
        mode = ((query.get("mode") or [""])[0] or "").strip() or None
        return self.app.quest_service.explorer(quest_id, revision=revision, mode=mode)

    def quest_search(self, quest_id: str, path: str) -> dict:
        query = self.parse_query(path)
        term = ((query.get("q") or [""])[0] or "").strip()
        try:
            limit = int(((query.get("limit") or ["50"])[0] or "50").strip())
        except ValueError:
            limit = 50
        return self.app.quest_service.search_files(quest_id, term=term, limit=limit)

    def document_asset(self, quest_id: str, path: str) -> tuple[int, dict, bytes]:
        query = self.parse_query(path)
        document_id = (query.get("document_id") or [""])[0].strip()
        if not document_id:
            return 400, {"Content-Type": "text/plain; charset=utf-8"}, b"`document_id` is required."
        if document_id.startswith("git::"):
            quest_root = self.app.quest_service._quest_root(quest_id)
            revision, relative = self.app.quest_service._parse_git_document_id(document_id)
            if not self.app.quest_service._git_revision_exists(quest_root, revision):
                return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"Not Found"
            file_path = Path(relative)
            mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            content = self.app.quest_service._read_git_bytes(quest_root, revision, relative)
            return 200, self._asset_headers(mime_type), content
        path, _writable, _scope, _source_kind = self.app.quest_service._resolve_document(
            self.app.quest_service._quest_root(quest_id),
            document_id,
        )
        if not path.exists() or not path.is_file():
            return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"Not Found"
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return 200, self._asset_headers(mime_type), path.read_bytes()

    def document_open(self, quest_id: str, body: dict) -> dict:
        return self.app.quest_service.open_document(quest_id, body["document_id"])

    def document_asset_upload(self, quest_id: str, body: dict) -> dict:
        document_id = str(body.get("document_id") or "").strip()
        file_name = str(body.get("file_name") or "").strip()
        mime_type = str(body.get("mime_type") or "").strip()
        content_base64 = str(body.get("content_base64") or "").strip()
        kind = str(body.get("kind") or "image").strip() or "image"
        if not document_id:
            return {"ok": False, "message": "`document_id` is required."}
        if not file_name:
            return {"ok": False, "message": "`file_name` is required."}
        if not content_base64:
            return {"ok": False, "message": "`content_base64` is required."}
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, TypeError):
            return {"ok": False, "message": "Invalid `content_base64` payload."}
        return self.app.quest_service.save_document_asset(
            quest_id,
            document_id,
            file_name=file_name,
            mime_type=mime_type or None,
            content=content,
            kind=kind,
        )

    def document_save(self, quest_id: str, document_id: str, body: dict) -> dict:
        return self.app.quest_service.save_document(
            quest_id,
            document_id,
            body["content"],
            previous_revision=body.get("revision"),
        )

    def chat(self, quest_id: str, body: dict) -> dict:
        text = body.get("text", "").strip()
        if not text:
            return {"ok": False, "message": "Empty message."}
        source = body.get("source", "api")
        self.app.sessions.bind(quest_id, source)
        payload = self.app.submit_user_message(
            quest_id,
            text=text,
            source=source,
            reply_to_interaction_id=body.get("reply_to_interaction_id"),
            client_message_id=body.get("client_message_id"),
        )
        return {
            "ok": True,
            "ack": f"Received for {quest_id}. Stored and queued for execution.",
            **payload,
        }

    def command(self, quest_id: str, body: dict) -> dict:
        raw_command = body.get("command", "").strip()
        if not raw_command:
            return {"ok": False, "message": "Empty command."}

        source = body.get("source", "api")
        self.app.sessions.bind(quest_id, source)
        normalized_command = raw_command if raw_command.startswith("/") else f"/{raw_command}"
        command_name, args = self.app._parse_prefixed_command(normalized_command, "/")
        if not command_name:
            return {"ok": False, "message": "Empty command."}

        def append_command_reply(kind: str, message: str, **extra: object) -> dict:
            reply = self.app.quest_service.append_message(
                quest_id,
                role="assistant",
                content=message,
                source="command",
            )
            return {
                "ok": True,
                "type": kind,
                "message": message,
                "message_record": reply,
                **extra,
            }

        if command_name == "status":
            snapshot = self.quest(quest_id)
            return append_command_reply("status", self.app._format_status(snapshot), snapshot=snapshot)

        if command_name == "summary":
            return append_command_reply("summary", self.app._format_summary(quest_id))

        if command_name == "metrics":
            run_id = args[0] if args else None
            return append_command_reply("metrics", self.app._format_metrics(quest_id, run_id=run_id), run_id=run_id)

        if command_name == "graph":
            graph = self.graph(quest_id)
            return append_command_reply("graph", self.app._format_graph_reply(quest_id, graph), graph=graph)

        if command_name == "terminal":
            quest_root = self.app.quest_service._quest_root(quest_id)
            workspace_root = self.app.quest_service.active_workspace_root(quest_root)
            session = self.app.bash_exec_service.ensure_terminal_session(
                quest_root,
                quest_id=quest_id,
                cwd=workspace_root,
                source=source,
                conversation_id=source,
                user_id="user:web-react",
            )
            if args and args[0] == "-R":
                restored = self.app.bash_exec_service.terminal_restore_payload(
                    quest_root,
                    str(session.get("bash_id") or ""),
                    command_limit=10,
                    output_limit=40,
                )
                commands = [str(item.get("command") or "").strip() for item in restored.get("latest_commands") or []]
                tail = restored.get("tail") or []
                tail_preview = [
                    str(item.get("line") or "").strip()
                    for item in tail[-4:]
                    if str(item.get("line") or "").strip()
                ]
                lines = [
                    f"Terminal `{restored.get('session_id')}`",
                    f"status: {restored.get('status') or 'unknown'}",
                    f"cwd: {restored.get('cwd') or session.get('cwd') or quest_root}",
                ]
                if commands:
                    lines.append("latest commands:")
                    lines.extend(f"- {item}" for item in reversed(commands[-10:]))
                if tail_preview:
                    lines.append("recent output:")
                    lines.extend(f"- {item}" for item in tail_preview)
                return append_command_reply(
                    "terminal_restore",
                    "\n".join(lines),
                    session=restored.get("session"),
                    restore=restored,
                )
            if args:
                command_text = " ".join(args).rstrip()
                result = self.app.bash_exec_service.append_terminal_input(
                    quest_root,
                    str(session.get("bash_id") or ""),
                    data=f"{command_text}\n",
                    source=source,
                    user_id="user:web-react",
                    conversation_id=source,
                )
                cwd_value = str(result.get("session", {}).get("cwd") or session.get("cwd") or quest_root)
                return append_command_reply(
                    "terminal",
                    f"Sent to terminal `{session.get('bash_id')}` in `{cwd_value}`:\n{command_text}",
                    session=result.get("session"),
                    terminal=result,
                )
            return append_command_reply(
                "terminal",
                f"Terminal `{session.get('bash_id')}` ready.\nstatus: {session.get('status')}\ncwd: {session.get('cwd')}",
                session=session,
            )

        if command_name == "approve":
            if not args:
                return {
                    "ok": False,
                    "message": "Please provide a decision id, for example `/approve decision-001 Proceed`.",
                }
            decision_id = args[0]
            reason = " ".join(args[1:]).strip() or "Approved from local UI."
            approval = self.app.artifact_service.record(
                self.app.quest_service._quest_root(quest_id),
                {
                    "kind": "approval",
                    "decision_id": decision_id,
                    "reason": reason,
                    "source": {
                        "kind": "user",
                        "surface": source,
                    },
                    "raw_text": raw_command,
                },
            )
            return append_command_reply(
                "approve",
                f"Approved {decision_id}. {reason}",
                approval=approval,
                decision_id=decision_id,
            )

        if command_name == "note":
            note = " ".join(args).strip()
            if not note:
                return {"ok": False, "message": "Note text is required, for example `/note revisit baseline`."}
            payload = self.app.submit_user_message(quest_id, text=note, source="command")
            return {"ok": True, "type": "ack", "message": "Note stored and queued.", **payload}

        if command_name in {"pause", "stop", "resume"}:
            target_quest = args[0].strip() if args else quest_id
            if not target_quest:
                return {"ok": False, "message": f"Usage: `/{command_name} <quest_id>`."}
            control = self.app.control_quest(target_quest, action=command_name, source=source)
            fallback_verb = {"pause": "paused", "stop": "stopped", "resume": "resumed"}[command_name]
            message = str(control.get("message") or f"Quest {target_quest} {fallback_verb}.")
            if target_quest == quest_id:
                return append_command_reply(command_name, message, snapshot=control.get("snapshot"), interrupted=control.get("interrupted"))
            reply = self.app.quest_service.append_message(
                target_quest,
                role="assistant",
                content=message,
                source="command",
            )
            return {
                "ok": True,
                "type": command_name,
                "message": message,
                "message_record": reply,
                "snapshot": control.get("snapshot"),
                "interrupted": control.get("interrupted"),
                "target_quest_id": target_quest,
            }

        return {
            "ok": True,
            "type": "ack",
            "message": f"Command `{raw_command}` is accepted by the skeleton but not fully implemented yet.",
        }

    def run_create(self, quest_id: str, body: dict) -> dict:
        quest_root = self.app.quest_service._quest_root(quest_id)
        config = self.app.config_manager.load_named("config")
        runners = self.app.config_manager.load_named("runners")
        snapshot = self.app.quest_service.snapshot(quest_id)
        runner_name = str(body.get("runner") or snapshot.get("runner") or config.get("default_runner", "codex")).strip().lower()
        runner_cfg = runners.get(runner_name, {})
        if runner_cfg.get("enabled") is False:
            return {
                "ok": False,
                "message": f"Runner `{runner_name}` is disabled in `runners.yaml`.",
            }
        try:
            runner = self.app.get_runner(runner_name)
        except KeyError as exc:
            return {
                "ok": False,
                "message": str(exc),
            }
        request = RunRequest(
            quest_id=quest_id,
            quest_root=quest_root,
            worktree_root=self.app.quest_service.active_workspace_root(quest_root),
            run_id=body.get("run_id") or generate_id("run"),
            skill_id=body.get("skill_id", "decision"),
            message=body.get("message", "").strip(),
            model=body.get("model") or runner_cfg.get("model", "gpt-5.4"),
            approval_policy=runner_cfg.get("approval_policy", "on-request"),
            sandbox_mode=runner_cfg.get("sandbox_mode", "workspace-write"),
        )
        result = runner.run(request)
        if result.output_text:
            self.app.quest_service.append_message(
                quest_id,
                role="assistant",
                content=result.output_text,
                source=runner_name,
                run_id=result.run_id,
                skill_id=request.skill_id,
            )
        return {
            "ok": result.ok,
            "runner": runner_name,
            "run_id": result.run_id,
            "model": result.model,
            "exit_code": result.exit_code,
            "history_root": str(result.history_root),
            "run_root": str(result.run_root),
            "output_text": result.output_text,
            "stderr_text": result.stderr_text,
        }

    def memory(self, query: dict[str, list[str]]) -> list[dict]:
        term = (query.get("q") or [""])[0]
        if not term:
            return self.app.memory_service.list_cards(scope="global")
        return self.app.memory_service.search(term, scope="global")

    def docs(self) -> list[dict]:
        docs_root = self.app.repo_root / "docs"
        if not docs_root.exists():
            return []

        entries: list[dict] = []
        for path in sorted(docs_root.rglob("*.md")):
            if not path.is_file():
                continue
            relative = path.relative_to(docs_root).as_posix()
            title = self._markdown_title(path)
            entries.append(
                {
                    "document_id": relative,
                    "title": title,
                    "kind": "markdown",
                    "writable": False,
                    "path": str(path),
                    "source_scope": "system_docs",
                }
            )
        return entries

    def docs_open(self, body: dict) -> dict:
        docs_root = self.app.repo_root / "docs"
        document_id = str(body.get("document_id") or "").strip()
        if not document_id:
            raise ValueError("`document_id` is required.")

        path = resolve_within(docs_root, document_id)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Unknown docs file `{document_id}`.")

        content = read_text(path)
        return {
            "document_id": document_id,
            "title": self._markdown_title(path),
            "path": str(path),
            "kind": "markdown",
            "scope": "system_docs",
            "writable": False,
            "encoding": "utf-8",
            "source_scope": "system_docs",
            "content": content,
            "revision": f"sha256:{sha256_text(content)}",
            "updated_at": utc_now(),
            "meta": {
                "tags": list(path.relative_to(docs_root).parts[:-1]),
                "source_kind": "repo_docs",
                "renderer_hint": "markdown",
            },
        }

    def config_files(self) -> list[dict]:
        return [
            {
                "name": item.name,
                "path": str(item.path),
                "required": item.required,
                "exists": item.exists,
            }
            for item in self.app.config_manager.list_files()
        ]

    def config_show(self, name: str) -> dict:
        content = self.app.config_manager.load_named_text(name, create_optional=True)
        path = self.app.config_manager.path_for(name)
        meta: dict[str, object] = {
            "tags": [name],
            "source_kind": "config_file",
            "renderer_hint": "code",
            "help_markdown": self.app.config_manager.help_markdown(name),
            "system_testable": name in {"config", "runners", "connectors"},
            "structured_config": self.app.config_manager.load_named_normalized(name, create_optional=True),
        }
        return {
            "document_id": name,
            "title": path.name,
            "path": str(path),
            "kind": "code",
            "scope": "config",
            "writable": True,
            "encoding": "utf-8",
            "source_scope": "config",
            "content": content,
            "revision": f"sha256:{sha256_text(content)}",
            "updated_at": utc_now(),
            "meta": meta,
        }

    def config_save(self, name: str, body: dict) -> dict:
        if isinstance(body.get("structured"), dict):
            result = self.app.config_manager.save_named_payload(name, body["structured"])
        else:
            result = self.app.config_manager.save_named_text(name, body.get("content", ""))
        if result.get("ok") and name == "connectors":
            result["runtime_reload"] = self.app.reload_connectors_config()
        return result

    def config_validate(self, body: dict | None = None) -> dict:
        if body and "name" in body and isinstance(body.get("structured"), dict):
            return self.app.config_manager.validate_named_payload(body["name"], body["structured"])
        if body and "name" in body and "content" in body:
            return self.app.config_manager.validate_named_text(body["name"], body["content"])
        return self.app.config_manager.validate_all()

    def config_test(self, body: dict | None = None) -> dict:
        if not body or "name" not in body:
            return {"ok": False, "summary": "Config test requires `name` and `content`."}
        if isinstance(body.get("structured"), dict):
            return self.app.config_manager.test_named_payload(
                body["name"],
                body["structured"],
                live=bool(body.get("live", True)),
                delivery_targets=body.get("delivery_targets"),
            )
        if "content" not in body:
            return {"ok": False, "summary": "Config test requires `name` and `content`."}
        return self.app.config_manager.test_named_text(body["name"], body["content"], live=bool(body.get("live", True)))

    def asset(self, asset_path: str) -> tuple[int, dict, bytes]:
        asset_root = self.app.repo_root / "assets"
        path = resolve_within(asset_root, asset_path)
        if not path.exists() or not path.is_file():
            return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"Not Found"
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return 200, {"Content-Type": mime_type}, path.read_bytes()

    @staticmethod
    def parse_query(path: str) -> dict[str, list[str]]:
        if "?" not in path:
            return {}
        return parse_qs(path.split("?", 1)[1], keep_blank_values=True)


    @staticmethod
    def parse_body(raw: bytes) -> dict:
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    @staticmethod
    def _markdown_title(path: Path) -> str:
        content = read_text(path)
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip() or path.stem.replace("_", " ")
        return path.stem.replace("_", " ").replace("-", " ")

    @staticmethod
    def error(message: str, code: int = 400) -> tuple[int, dict]:
        return code, {"ok": False, "message": message}
