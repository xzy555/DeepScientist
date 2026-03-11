from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse

from ..artifact import ArtifactService
from ..bash_exec import BashExecService
from ..bridges import get_connector_bridge, register_builtin_connector_bridges
from ..channels import QQRelayChannel, get_channel_factory, list_channel_names, register_builtin_channels
from ..channels.discord_gateway import DiscordGatewayService
from ..channels.feishu_long_connection import FeishuLongConnectionService
from ..channels.qq_gateway import QQGatewayService
from ..channels.slack_socket import SlackSocketModeService
from ..channels.telegram_polling import TelegramPollingService
from ..channels.whatsapp_local_session import WhatsAppLocalSessionService
from ..cloud import CloudLinkService
from ..config import ConfigManager
from ..home import repo_root
from ..memory import MemoryService
from ..prompts import PromptBuilder
from ..prompts.builder import STANDARD_SKILLS
from ..quest import QuestService
from ..runners import CodexRunner, RunRequest, get_runner_factory, register_builtin_runners
from ..runtime_logs import JsonlLogger
from ..shared import append_jsonl, generate_id, read_json, read_jsonl, read_text, utc_now, which
from ..skills import SkillInstaller
from ..team import SingleTeamService
from .api import ApiHandlers, match_route
from .sessions import SessionStore


class DaemonApp:
    def __init__(self, home: Path) -> None:
        self.home = home.resolve()
        self.daemon_id = str(os.environ.get("DS_DAEMON_ID") or "").strip() or generate_id("daemon")
        self.daemon_managed_by = str(os.environ.get("DS_DAEMON_MANAGED_BY") or "manual").strip() or "manual"
        self.repo_root = repo_root()
        self.config_manager = ConfigManager(home)
        self.runners_config = self.config_manager.load_named("runners")
        self.connectors_config = self.config_manager.load_named("connectors")
        self.skill_installer = SkillInstaller(self.repo_root, home)
        self.quest_service = QuestService(home, skill_installer=self.skill_installer)
        self.memory_service = MemoryService(home)
        self.artifact_service = ArtifactService(home)
        self.bash_exec_service = BashExecService(home)
        self.team_service = SingleTeamService(home)
        self.cloud_service = CloudLinkService(home)
        config = self.config_manager.load_named("config")
        self.logger = JsonlLogger(home / "logs", level=config.get("logging", {}).get("level", "info"))
        self.reconciled_quests = self.quest_service.reconcile_runtime_state()
        for item in self.reconciled_quests:
            self.logger.log(
                "warning",
                "quest.runtime_reconciled",
                quest_id=item.get("quest_id"),
                previous_status=item.get("previous_status"),
                abandoned_run_id=item.get("abandoned_run_id"),
                status=item.get("status"),
            )
        self.prompt_builder = PromptBuilder(self.repo_root, home)
        self.codex_runner = CodexRunner(
            home=home,
            repo_root=self.repo_root,
            binary=self.runners_config.get("codex", {}).get("binary", "codex"),
            logger=self.logger,
            prompt_builder=self.prompt_builder,
            artifact_service=self.artifact_service,
        )
        register_builtin_runners(codex_runner=self.codex_runner)
        register_builtin_connector_bridges()
        register_builtin_channels(home=home, connectors_config=self.connectors_config)
        self.runners = {
            "codex": self._create_runner("codex"),
        }
        self.channels = {name: self._create_channel(name) for name in list_channel_names()}
        self.sessions = SessionStore()
        self._turn_lock = threading.Lock()
        self._turn_state: dict[str, dict[str, object]] = {}
        self._server: ThreadingHTTPServer | None = None
        self._shutdown_requested = threading.Event()
        self._qq_gateway: QQGatewayService | None = None
        self._telegram_polling: TelegramPollingService | None = None
        self._slack_socket: SlackSocketModeService | None = None
        self._discord_gateway: DiscordGatewayService | None = None
        self._feishu_long_connection: FeishuLongConnectionService | None = None
        self._whatsapp_local_session: WhatsAppLocalSessionService | None = None
        self.handlers = ApiHandlers(self)

    def get_runner(self, name: str):
        normalized = str(name or "").strip().lower()
        try:
            return self.runners[normalized]
        except KeyError as exc:
            available = ", ".join(sorted(self.runners)) or "none"
            raise KeyError(f"Unknown runner `{normalized}`. Available runners: {available}.") from exc

    def _create_runner(self, name: str):
        factory = get_runner_factory(name)
        return factory(home=self.home, app=self, config=self.runners_config.get(name, {}))

    def _create_channel(self, name: str):
        factory = get_channel_factory(name)
        return factory(home=self.home, app=self, config=self.connectors_config.get(name, {}))

    def reload_connectors_config(self, *, restart_background: bool = True) -> dict[str, object]:
        self.connectors_config = self.config_manager.load_named("connectors")
        register_builtin_channels(home=self.home, connectors_config=self.connectors_config)
        for name, channel in self.channels.items():
            config = self.connectors_config.get(name, {})
            if hasattr(channel, "config") and isinstance(getattr(channel, "config"), dict):
                channel.config.clear()
                if isinstance(config, dict):
                    channel.config.update(config)
        if restart_background and self._server is not None:
            self._stop_background_connectors()
            self._start_background_connectors()
        return {
            "ok": True,
            "connectors": sorted(
                name for name, config in self.connectors_config.items() if not str(name).startswith("_") and isinstance(config, dict)
            ),
        }

    def _preferred_locale(self) -> str:
        config = self.config_manager.load_named("config")
        return str(config.get("default_locale") or "zh-CN").lower()

    def _polite_copy(self, *, zh: str, en: str) -> str:
        return zh if self._preferred_locale().startswith("zh") else en

    def submit_user_message(
        self,
        quest_id: str,
        *,
        text: str,
        source: str,
        reply_to_interaction_id: str | None = None,
        client_message_id: str | None = None,
    ) -> dict:
        previous_snapshot = self.quest_service.snapshot(quest_id)
        previous_status = str(previous_snapshot.get("runtime_status") or previous_snapshot.get("status") or "").strip()
        message = self.quest_service.append_message(
            quest_id,
            role="user",
            content=text,
            source=source,
            reply_to_interaction_id=reply_to_interaction_id,
            client_message_id=client_message_id,
        )
        snapshot = self.quest_service.snapshot(quest_id)
        runtime_status = str(snapshot.get("runtime_status") or snapshot.get("status") or "").strip()
        auto_resumed = previous_status in {"stopped", "paused"} and runtime_status not in {"stopped", "paused"}
        if auto_resumed:
            self._append_control_event(
                quest_id,
                action="resume",
                source=f"auto:{source}",
                status=runtime_status or "active",
                interrupted=False,
                summary=f"Quest {quest_id} automatically resumed after a new user message.",
                automated=True,
            )
        with self._turn_lock:
            turn_state = dict(self._turn_state.get(quest_id) or {})
        has_live_turn = bool(turn_state.get("running")) or bool(snapshot.get("active_run_id"))
        if runtime_status == "running" and has_live_turn:
            scheduled = {
                "scheduled": True,
                "started": False,
                "queued": True,
                "reason": "queued_for_artifact_interact",
            }
        else:
            scheduled = self.schedule_turn(quest_id, reason="user_message")
        return {
            "message": message,
            "auto_resumed": auto_resumed,
            "previous_status": previous_status or None,
            **scheduled,
        }

    def create_quest(
        self,
        *,
        goal: str,
        title: str | None = None,
        quest_id: str | None = None,
        source: str = "local",
        announce_connector_binding: bool = True,
        exclude_conversation_id: str | None = None,
    ) -> dict:
        snapshot = self.quest_service.create(goal=goal, title=title, quest_id=quest_id)
        self._auto_bind_connectors_to_latest_quest(
            snapshot["quest_id"],
            source=source,
            announce=announce_connector_binding,
            exclude_conversation_id=exclude_conversation_id,
        )
        return snapshot

    def schedule_turn(self, quest_id: str, *, reason: str = "user_message") -> dict:
        with self._turn_lock:
            state = self._turn_state.setdefault(quest_id, {"running": False, "pending": False})
            state["pending"] = True
            state["stop_requested"] = False
            state["reason"] = reason
            if state.get("running"):
                return {
                    "scheduled": True,
                    "started": False,
                    "queued": True,
                    "reason": reason,
                }
            state["running"] = True
            worker = threading.Thread(
                target=self._drain_turns,
                args=(quest_id,),
                daemon=True,
                name=f"deepscientist-turn-{quest_id}",
            )
            state["worker"] = worker
        worker.start()
        return {
            "scheduled": True,
            "started": True,
            "queued": False,
            "reason": reason,
        }

    def control_quest(self, quest_id: str, *, action: str, source: str = "local") -> dict:
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "pause":
            return self.pause_quest(quest_id, source=source)
        if normalized_action == "stop":
            return self.stop_quest(quest_id, source=source)
        if normalized_action == "resume":
            return self.resume_quest(quest_id, source=source)
        raise ValueError(f"Unsupported quest control action `{action}`.")

    def pause_quest(self, quest_id: str, *, source: str = "local") -> dict:
        return self._interrupt_quest(quest_id, action="pause", status="paused", source=source)

    def stop_quest(self, quest_id: str, *, source: str = "local") -> dict:
        return self._interrupt_quest(quest_id, action="stop", status="stopped", source=source)

    def _interrupt_quest(self, quest_id: str, *, action: str, status: str, source: str) -> dict:
        previous_snapshot = self.quest_service.snapshot(quest_id)
        runner_name = self._runner_name_for(previous_snapshot)
        interrupted = False
        cancelled_pending = {
            "batch_id": None,
            "cancelled_count": 0,
            "cancelled": [],
        }
        try:
            runner = self.get_runner(runner_name)
        except KeyError:
            runner = None
        if runner is not None and hasattr(runner, "interrupt"):
            interrupted = bool(getattr(runner, "interrupt")(quest_id))
        with self._turn_lock:
            state = self._turn_state.setdefault(quest_id, {"running": False, "pending": False})
            state["pending"] = False
            state["stop_requested"] = True
        if action == "stop":
            cancel_reason = "cancelled_by_daemon_shutdown" if source == "local-admin" else "cancelled_by_stop"
            cancelled_pending = self.quest_service.cancel_pending_user_messages(
                quest_id,
                reason=cancel_reason,
                action=action,
                source=source,
            )
        stop_reason = "daemon_shutdown" if action == "stop" and source == "local-admin" else f"user_{action}"
        snapshot = self.quest_service.mark_turn_finished(quest_id, status=status, stop_reason=stop_reason)
        verb = "paused" if action == "pause" else "stopped"
        summary = f"Quest {quest_id} {verb}."
        if interrupted:
            summary = f"Quest {quest_id} {verb} and the active runner was interrupted."
        cancelled_count = int(cancelled_pending.get("cancelled_count") or 0)
        if cancelled_count > 0:
            summary = f"{summary} Cancelled {cancelled_count} queued user message(s)."
        event = self._append_control_event(
            quest_id,
            action=action,
            source=source,
            status=str(snapshot.get("status") or status),
            interrupted=interrupted,
            cancelled_pending_user_message_count=cancelled_count,
            summary=summary,
        )
        notice = self._announce_control_state(
            quest_id,
            action=action,
            source=source,
            snapshot=snapshot,
            interrupted=interrupted,
            cancelled_pending_user_message_count=cancelled_count,
            previous_snapshot=previous_snapshot,
        )
        return {
            "ok": True,
            "quest_id": quest_id,
            "action": action,
            "interrupted": interrupted,
            "cancelled_pending_user_message_count": cancelled_count,
            "snapshot": snapshot,
            "message": summary,
            "event": event,
            "notice": notice,
        }

    def resume_quest(self, quest_id: str, *, source: str = "local") -> dict:
        previous_snapshot = self.quest_service.snapshot(quest_id)
        with self._turn_lock:
            state = self._turn_state.setdefault(quest_id, {"running": False, "pending": False})
            state["stop_requested"] = False
        snapshot = self.quest_service.snapshot(quest_id)
        next_status = "running" if snapshot.get("status") == "running" else "active"
        snapshot = self.quest_service.set_status(quest_id, next_status)
        summary = f"Quest {quest_id} resumed."
        event = self._append_control_event(
            quest_id,
            action="resume",
            source=source,
            status=str(snapshot.get("status") or next_status),
            interrupted=False,
            summary=summary,
        )
        notice = self._announce_control_state(
            quest_id,
            action="resume",
            source=source,
            snapshot=snapshot,
            interrupted=False,
            cancelled_pending_user_message_count=0,
            previous_snapshot=previous_snapshot,
        )
        return {
            "ok": True,
            "quest_id": quest_id,
            "action": "resume",
            "interrupted": False,
            "snapshot": snapshot,
            "message": summary,
            "event": event,
            "notice": notice,
        }

    def _append_control_event(
        self,
        quest_id: str,
        *,
        action: str,
        source: str,
        status: str,
        interrupted: bool,
        summary: str,
        cancelled_pending_user_message_count: int = 0,
        automated: bool = False,
    ) -> dict:
        payload = {
            "event_id": generate_id("evt"),
            "type": "quest.control",
            "quest_id": quest_id,
            "action": action,
            "source": source,
            "status": status,
            "interrupted": interrupted,
            "cancelled_pending_user_message_count": max(0, int(cancelled_pending_user_message_count or 0)),
            "summary": summary,
            "automated": automated,
            "created_at": utc_now(),
        }
        append_jsonl(self.home / "quests" / quest_id / ".ds" / "events.jsonl", payload)
        return payload

    def _announce_control_state(
        self,
        quest_id: str,
        *,
        action: str,
        source: str,
        snapshot: dict,
        interrupted: bool,
        cancelled_pending_user_message_count: int,
        previous_snapshot: dict | None = None,
    ) -> dict:
        quest_root = self.quest_service._quest_root(quest_id)
        message = self._control_notice_message(
            quest_id,
            action=action,
            snapshot=snapshot,
            interrupted=interrupted,
            cancelled_pending_user_message_count=cancelled_pending_user_message_count,
            previous_snapshot=previous_snapshot,
        )
        history_record = self.quest_service.append_message(
            quest_id,
            role="assistant",
            content=message,
            source="system-control",
        )
        connectors = self.artifact_service._connectors_config()
        targets = self.artifact_service._select_delivery_targets(
            self.artifact_service._bound_conversations(quest_root),
            connectors=connectors,
        )
        attachments = [
            {
                "kind": "quest_control",
                "action": action,
                "status": snapshot.get("status"),
                "source": source,
                "branch": snapshot.get("branch"),
                "workspace_root": snapshot.get("current_workspace_root") or snapshot.get("quest_root"),
                "interrupted": interrupted,
                "cancelled_pending_user_message_count": int(cancelled_pending_user_message_count or 0),
                "previous_status": (
                    (previous_snapshot or {}).get("runtime_status")
                    or (previous_snapshot or {}).get("status")
                ),
                "stop_reason": snapshot.get("stop_reason"),
            }
        ]
        delivery_targets: list[str] = []
        importance = "warning" if action in {"pause", "stop"} else "info"
        for target in targets:
            channel_name = self.artifact_service._normalize_channel_name(target)
            payload = {
                "quest_root": str(quest_root),
                "quest_id": quest_id,
                "conversation_id": target,
                "kind": "progress",
                "message": message,
                "response_phase": "control",
                "importance": importance,
                "attachments": attachments,
            }
            if self.artifact_service._send_to_channel(channel_name, payload, connectors=connectors):
                delivery_targets.append(target)
        return {
            "message": message,
            "history_record": history_record,
            "delivery_targets": delivery_targets,
        }

    def _control_notice_message(
        self,
        quest_id: str,
        *,
        action: str,
        snapshot: dict,
        interrupted: bool,
        cancelled_pending_user_message_count: int,
        previous_snapshot: dict | None = None,
    ) -> str:
        branch = str(snapshot.get("branch") or "unknown").strip() or "unknown"
        workspace_root = str(snapshot.get("current_workspace_root") or snapshot.get("quest_root") or "").strip()
        if action == "resume":
            lines = [
                self._polite_copy(
                    zh="DeepScientist 已恢复运行。",
                    en="DeepScientist has resumed.",
                ),
                self._polite_copy(
                    zh="当前 Git 分支与 worktree 已保留，系统会沿用现有研究上下文继续。",
                    en="The current Git branch and worktree were kept intact, and the quest will continue from the existing research context.",
                ),
            ]
        elif action == "pause":
            lines = [
                self._polite_copy(
                    zh="DeepScientist 已从运行状态转为暂停状态。",
                    en="DeepScientist has moved from running to paused.",
                ),
                self._polite_copy(
                    zh="当前 Git 分支与 worktree 已保留，发送新消息或使用 /resume 即可继续。",
                    en="The current Git branch and worktree were kept intact. Send a new message or use /resume to continue.",
                ),
            ]
        else:
            lines = [
                self._polite_copy(
                    zh="DeepScientist 已从运行状态转为停止状态。",
                    en="DeepScientist has moved from running to stopped.",
                ),
                self._polite_copy(
                    zh="当前 Git 分支与 worktree 已保留，发送新消息或使用 /resume 即可继续。",
                    en="The current Git branch and worktree were kept intact. Send a new message or use /resume to continue.",
                ),
            ]
        if interrupted:
            lines.append(
                self._polite_copy(
                    zh="当前活跃 runner 已被中断。",
                    en="The active runner was interrupted.",
                )
            )
        cancelled_count = max(0, int(cancelled_pending_user_message_count or 0))
        if cancelled_count > 0:
            lines.append(
                self._polite_copy(
                    zh=f"已取消 {cancelled_count} 条排队中的用户消息。",
                    en=f"Cancelled {cancelled_count} queued user message(s).",
                )
            )
        previous_status = str(
            (previous_snapshot or {}).get("runtime_status")
            or (previous_snapshot or {}).get("status")
            or ""
        ).strip()
        if previous_status and action == "resume":
            lines.append(
                self._polite_copy(
                    zh=f"此前状态：`{previous_status}`。",
                    en=f"Previous status: `{previous_status}`.",
                )
            )
        lines.extend(
            [
                f"- Quest: `{quest_id}`",
                f"- Branch: `{branch}`",
                f"- Workspace: `{workspace_root or snapshot.get('quest_root')}`",
            ]
        )
        return "\n".join(lines)

    def _drain_turns(self, quest_id: str) -> None:
        while True:
            with self._turn_lock:
                state = self._turn_state.setdefault(quest_id, {"running": True, "pending": False})
                if not state.get("pending"):
                    state["running"] = False
                    state.pop("worker", None)
                    return
                state["pending"] = False
            self._run_quest_turn(quest_id)

    def _run_quest_turn(self, quest_id: str) -> None:
        with self._turn_lock:
            state = dict(self._turn_state.get(quest_id) or {})
        if state.get("stop_requested"):
            return
        snapshot = self.quest_service.snapshot(quest_id)
        if str(snapshot.get("status") or snapshot.get("runtime_status") or "").strip() in {"stopped", "paused"} and not snapshot.get("active_run_id"):
            return
        latest_user_message = self._latest_user_message(quest_id)
        if latest_user_message is None:
            return

        runner_name = self._runner_name_for(snapshot)
        runner_cfg = self.runners_config.get(runner_name, {})
        skill_id = self._turn_skill_for(snapshot, latest_user_message)
        run_id = generate_id("run")
        model = str(runner_cfg.get("model", "gpt-5.4"))

        if runner_cfg.get("enabled") is False:
            self._record_turn_error(
                quest_id=quest_id,
                runner_name=runner_name,
                run_id=run_id,
                skill_id=skill_id,
                model=model,
                summary=f"Runner `{runner_name}` is disabled in `runners.yaml`.",
            )
            return

        try:
            runner = self.get_runner(runner_name)
        except KeyError as exc:
            self._record_turn_error(
                quest_id=quest_id,
                runner_name=runner_name,
                run_id=run_id,
                skill_id=skill_id,
                model=model,
                summary=str(exc),
            )
            return

        binary_issue = self._runner_binary_issue(runner_name, runner)
        if binary_issue is not None:
            self._record_turn_error(
                quest_id=quest_id,
                runner_name=runner_name,
                run_id=run_id,
                skill_id=skill_id,
                model=model,
                summary=binary_issue,
            )
            return

        request = RunRequest(
            quest_id=quest_id,
            quest_root=Path(snapshot["quest_root"]),
            worktree_root=Path(str(snapshot["current_workspace_root"])) if snapshot.get("current_workspace_root") else None,
            run_id=run_id,
            skill_id=skill_id,
            message=str(latest_user_message.get("content") or "").strip(),
            model=model,
            approval_policy=str(runner_cfg.get("approval_policy", "on-request")),
            sandbox_mode=str(runner_cfg.get("sandbox_mode", "workspace-write")),
        )

        with self._turn_lock:
            if bool((self._turn_state.get(quest_id) or {}).get("stop_requested")):
                return
        self.quest_service.claim_pending_user_message_for_turn(
            quest_id,
            message_id=str(latest_user_message.get("id") or "").strip() or None,
            run_id=run_id,
        )
        self.quest_service.mark_turn_started(quest_id, run_id=run_id, status="running")
        try:
            result = runner.run(request)
        except Exception as exc:  # pragma: no cover - exercised via integration behavior
            self._record_turn_error(
                quest_id=quest_id,
                runner_name=runner_name,
                run_id=run_id,
                skill_id=skill_id,
                model=model,
                summary=f"Runner `{runner_name}` failed: {exc}",
            )
            return

        if result.output_text:
            self.quest_service.append_message(
                quest_id,
                role="assistant",
                content=result.output_text,
                source=runner_name,
                run_id=result.run_id,
                skill_id=skill_id,
            )
            self._relay_quest_message_to_bound_connectors(
                quest_id,
                message=result.output_text,
                kind="assistant",
                response_phase="final",
                importance="normal",
                attachments=[
                    {
                        "kind": "runner_result",
                        "run_id": result.run_id,
                        "skill_id": skill_id,
                        "runner": runner_name,
                        "model": result.model,
                        "exit_code": result.exit_code,
                        "history_root": str(result.history_root),
                        "run_root": str(result.run_root),
                    }
                ],
            )
        self._normalize_status_after_turn(quest_id)

    def _runner_name_for(self, snapshot: dict) -> str:
        configured = self.config_manager.load_named("config")
        return str(snapshot.get("runner") or configured.get("default_runner", "codex")).strip().lower()

    @staticmethod
    def _turn_skill_for(snapshot: dict, latest_user_message: dict) -> str:
        reply_target = str(latest_user_message.get("reply_to_interaction_id") or "").strip()
        if reply_target:
            for item in (snapshot.get("active_interactions") or []):
                candidate_ids = {
                    str(item.get("interaction_id") or "").strip(),
                    str(item.get("artifact_id") or "").strip(),
                }
                if reply_target in candidate_ids and (
                    str(item.get("status") or "") == "waiting"
                    or str(item.get("reply_mode") or "") == "blocking"
                    or str(item.get("kind") or "") == "decision_request"
                ):
                    return "decision"
            for item in (snapshot.get("recent_reply_threads") or []):
                candidate_ids = {
                    str(item.get("interaction_id") or "").strip(),
                    str(item.get("artifact_id") or "").strip(),
                }
                if reply_target in candidate_ids and (
                    str(item.get("reply_mode") or "") == "blocking"
                    or str(item.get("kind") or "") == "decision_request"
                ):
                    return "decision"
        active_anchor = str(snapshot.get("active_anchor") or "").strip()
        return active_anchor if active_anchor in STANDARD_SKILLS else "decision"

    def _latest_user_message(self, quest_id: str) -> dict | None:
        for item in reversed(self.quest_service.history(quest_id, limit=200)):
            if str(item.get("role") or "") == "user":
                return item
        return None

    @staticmethod
    def _runner_binary_issue(runner_name: str, runner: object) -> str | None:
        binary = getattr(runner, "binary", None)
        if not isinstance(binary, str) or not binary.strip():
            return None
        candidate = binary.strip()
        if os.sep in candidate or candidate.startswith("."):
            return None if Path(candidate).expanduser().exists() else f"Runner `{runner_name}` binary was not found at `{candidate}`."
        return None if which(candidate) else f"Runner `{runner_name}` binary `{candidate}` is not on PATH."

    def _record_turn_error(
        self,
        *,
        quest_id: str,
        runner_name: str,
        run_id: str,
        skill_id: str,
        model: str,
        summary: str,
    ) -> None:
        append_jsonl(
            self.home / "quests" / quest_id / ".ds" / "events.jsonl",
            {
                "event_id": generate_id("evt"),
                "type": "runner.turn_error",
                "quest_id": quest_id,
                "run_id": run_id,
                "source": runner_name,
                "skill_id": skill_id,
                "model": model,
                "summary": summary,
                "created_at": utc_now(),
            },
        )
        self.logger.log(
            "error",
            "runner.turn_error",
            quest_id=quest_id,
            run_id=run_id,
            runner=runner_name,
            skill_id=skill_id,
            model=model,
            summary=summary,
        )
        self._relay_quest_message_to_bound_connectors(
            quest_id,
            message=summary,
            kind="error",
            response_phase="final",
            importance="warning",
            attachments=[
                {
                    "kind": "runner_error",
                    "run_id": run_id,
                    "skill_id": skill_id,
                    "runner": runner_name,
                    "model": model,
                }
            ],
        )
        self._normalize_status_after_turn(quest_id)

    def _normalize_status_after_turn(self, quest_id: str) -> None:
        with self._turn_lock:
            if bool((self._turn_state.get(quest_id) or {}).get("stop_requested")):
                return
        snapshot = self.quest_service.snapshot(quest_id)
        current_status = str(snapshot.get("status") or snapshot.get("display_status") or "active").strip() or "active"
        normalized_status = "active" if current_status == "running" else current_status
        snapshot = self.quest_service.mark_turn_finished(quest_id, status=normalized_status)
        status = str(snapshot.get("status") or "")
        if status in {"stopped", "paused"}:
            return
        if status == "waiting_for_user":
            if snapshot.get("waiting_interaction_id"):
                return
            if int(snapshot.get("pending_user_message_count") or 0) > 0:
                self.schedule_turn(quest_id, reason="queued_user_messages")
            return
        if int(snapshot.get("pending_user_message_count") or 0) > 0:
            self.schedule_turn(quest_id, reason="queued_user_messages")

    def _relay_quest_message_to_bound_connectors(
        self,
        quest_id: str,
        *,
        message: str,
        kind: str = "assistant",
        response_phase: str | None = "final",
        importance: str | None = "normal",
        attachments: list[dict[str, object]] | None = None,
    ) -> list[str]:
        text = str(message or "").strip()
        if not text:
            return []
        quest_root = self.quest_service._quest_root(quest_id)
        connectors = self.artifact_service._connectors_config()
        targets = self.artifact_service._select_delivery_targets(
            self.artifact_service._bound_conversations(quest_root),
            connectors=connectors,
        )
        delivered: list[str] = []
        for target in targets:
            channel_name = self.artifact_service._normalize_channel_name(target)
            payload = {
                "quest_root": str(quest_root),
                "quest_id": quest_id,
                "conversation_id": target,
                "kind": kind,
                "message": text,
                "response_phase": response_phase,
                "importance": importance,
                "attachments": attachments or [],
            }
            if self.artifact_service._send_to_channel(channel_name, payload, connectors=connectors):
                delivered.append(target)
        return delivered

    def request_shutdown(self, *, source: str = "local-admin") -> dict:
        if self._shutdown_requested.is_set():
            return {
                "ok": True,
                "message": "DeepScientist daemon shutdown is already in progress.",
                "source": source,
            }
        interrupted_quests: list[str] = []
        for snapshot in self.quest_service.list_quests():
            quest_id = str(snapshot.get("quest_id") or "").strip()
            if not quest_id:
                continue
            if snapshot.get("status") != "running" and not snapshot.get("active_run_id"):
                continue
            result = self.stop_quest(quest_id, source=source)
            if result.get("ok"):
                interrupted_quests.append(quest_id)
        self._shutdown_requested.set()
        self._stop_background_connectors()
        self.logger.log(
            "info",
            "daemon.shutdown_requested",
            source=source,
            interrupted_quests=interrupted_quests,
        )

        server = self._server

        def _shutdown_server() -> None:
            time.sleep(0.05)
            if server is not None:
                try:
                    server.shutdown()
                except Exception:
                    return

        threading.Thread(
            target=_shutdown_server,
            daemon=True,
            name="deepscientist-daemon-shutdown",
        ).start()
        return {
            "ok": True,
            "message": "DeepScientist daemon shutdown requested.",
            "source": source,
            "interrupted_quests": interrupted_quests,
        }

    def handle_qq_inbound(self, body: dict) -> dict:
        return self.handle_connector_inbound("qq", body)

    def list_qq_bindings(self) -> list[dict]:
        return self._qq_channel().list_bindings()

    def list_connector_bindings(self, connector_name: str) -> list[dict]:
        channel = self._channel_with_bindings(connector_name)
        return channel.list_bindings()

    def handle_connector_inbound(self, connector_name: str, body: dict) -> dict:
        channel = self._channel_with_bindings(connector_name)
        ingested = channel.ingest(body)
        if not ingested.get("accepted", False):
            return {
                "ok": True,
                "accepted": False,
                "reason": ingested.get("normalized", {}).get("reason"),
                "normalized": ingested.get("normalized"),
            }
        normalized = ingested["normalized"]
        if connector_name == "qq":
            qq_binding = self._maybe_bind_qq_main_chat(normalized)
            if qq_binding is not None:
                normalized = {
                    **normalized,
                    "_qq_main_chat_binding": qq_binding,
                }
        reply = self._route_connector_message(connector_name, normalized)
        return {
            "ok": True,
            "accepted": True,
            "normalized": normalized,
            "reply": reply,
        }

    def handle_bridge_webhook(
        self,
        connector_name: str,
        *,
        method: str,
        path: str,
        raw_body: bytes,
        headers: dict[str, str],
        body: dict,
    ) -> tuple[int, dict, bytes | str] | dict:
        bridge = get_connector_bridge(connector_name)
        if bridge is None:
            return 404, {"Content-Type": "application/json; charset=utf-8"}, json.dumps({"ok": False, "message": f"Unknown bridge `{connector_name}`."}, ensure_ascii=False)
        query = self.handlers.parse_query(path)
        result = bridge.parse_webhook(
            method=method,
            headers=headers,
            query=query,
            raw_body=raw_body,
            body=body,
            config=self.connectors_config.get(connector_name, {}),
        )
        if result.response_body is not None and not result.events:
            headers_out = {"Content-Type": "application/json; charset=utf-8", **result.response_headers}
            body_out = result.response_body
            if isinstance(body_out, bytes):
                return result.status_code, headers_out, body_out
            if isinstance(body_out, str):
                return result.status_code, headers_out, body_out
            return result.status_code, headers_out, json.dumps(body_out, ensure_ascii=False)

        responses: list[dict] = []
        accepted = False
        for event in result.events:
            routed = self.handle_connector_inbound(connector_name, event)
            responses.append(routed)
            accepted = accepted or bool(routed.get("accepted"))
        return {
            "ok": result.ok,
            "accepted": accepted,
            "connector": connector_name,
            "event_count": len(result.events),
            "message": result.message,
            "responses": responses,
        }

    def _route_connector_message(self, connector_name: str, message: dict) -> dict:
        channel = self._channel_with_bindings(connector_name)
        connector_label = self._connector_label(connector_name)
        conversation_id = str(message.get("conversation_id") or "")
        text = str(message.get("text") or "").strip()
        command_prefix = channel.command_prefix()
        quest_id = channel.resolve_bound_quest(conversation_id)

        if text.startswith(command_prefix):
            command_name, args = self._parse_prefixed_command(text, command_prefix)
            if command_name == "help":
                return channel.send(
                    {
                        "conversation_id": conversation_id,
                        "kind": "help",
                        "message": self._connector_home_help(connector_name, message=message),
                    }
                )
            if command_name in {"projects", "quests", "list"}:
                return channel.send(
                    {
                        "conversation_id": conversation_id,
                        "kind": "projects",
                        "message": self._format_projects_list(),
                    }
                )
            if command_name == "new":
                if not args:
                    return channel.send(
                        {
                            "conversation_id": conversation_id,
                            "kind": "ack",
                            "message": self._polite_copy(
                                zh="老师您好，请先提供研究目标，例如 `/new 复现一个图神经网络基线`。",
                                en="Please provide a quest goal first, for example `/new reproduce a graph baseline`.",
                            ),
                        }
                    )
                created = self.create_quest(
                    goal=" ".join(args).strip(),
                    source=f"{connector_name}:connector",
                    announce_connector_binding=True,
                    exclude_conversation_id=conversation_id,
                )
                channel.bind_conversation(conversation_id, created["quest_id"])
                self.sessions.bind(created["quest_id"], conversation_id)
                self.quest_service.bind_source(created["quest_id"], conversation_id)
                return channel.send(
                    {
                        "conversation_id": conversation_id,
                        "quest_id": created["quest_id"],
                        "kind": "ack",
                        "message": self._with_qq_main_chat_notice(
                            message,
                            self._polite_copy(
                                zh=f"老师，已创建新的 quest `{created['quest_id']}`，当前 {connector_label} 会话已自动绑定到这个最新 quest。",
                                en=f"Created a new quest `{created['quest_id']}`. This {connector_label} conversation is now bound to the latest quest.",
                            ),
                        ),
                    }
                )
            if command_name == "use":
                if not args:
                    return channel.send(
                        {
                            "conversation_id": conversation_id,
                            "kind": "ack",
                            "message": self._polite_copy(
                                zh="老师您好，请先提供 quest id，例如 `/use q-001`。",
                                en="Please provide a quest id first, for example `/use q-001`.",
                            ),
                        }
                    )
                target_quest = args[0]
                if target_quest in {"latest", "newest"}:
                    quests = self.quest_service.list_quests()
                    target_quest = str(quests[0]["quest_id"]) if quests else ""
                if not (self.home / "quests" / target_quest / "quest.yaml").exists():
                    available = ", ".join(item["quest_id"] for item in self.quest_service.list_quests()[:6]) or "none"
                    return channel.send(
                        {
                            "conversation_id": conversation_id,
                            "kind": "ack",
                            "message": self._polite_copy(
                                zh=f"老师，目前没有找到 quest `{target_quest}`。可用 quest 包括：{available}。",
                                en=f"I could not find quest `{target_quest}`. Available quests: {available}.",
                            ),
                        }
                    )
                channel.bind_conversation(conversation_id, target_quest)
                self.sessions.bind(target_quest, conversation_id)
                self.quest_service.bind_source(target_quest, conversation_id)
                return channel.send(
                    {
                        "conversation_id": conversation_id,
                        "quest_id": target_quest,
                        "kind": "ack",
                        "message": self._polite_copy(
                            zh=f"老师，已将当前 {connector_label} 会话绑定到 {target_quest}，我会继续推进并同步计划。",
                            en=f"Received. I’ve bound this {connector_label} conversation to {target_quest} and will keep the plan moving.",
                        ),
                    }
                )

            if quest_id is None:
                return channel.send(
                    {
                        "conversation_id": conversation_id,
                        "kind": "ack",
                        "message": self._connector_home_help(connector_name, message=message),
                    }
                )

            self.sessions.bind(quest_id, conversation_id)
            self.quest_service.bind_source(quest_id, conversation_id)
            if command_name == "status":
                snapshot = self.quest_service.snapshot(quest_id)
                return channel.send(
                    {
                        "conversation_id": conversation_id,
                        "quest_id": quest_id,
                        "kind": "status",
                        "message": self._format_status(snapshot),
                    }
                )
            if command_name == "summary":
                return channel.send(
                    {
                        "conversation_id": conversation_id,
                        "quest_id": quest_id,
                        "kind": "summary",
                        "message": self._format_summary(quest_id),
                    }
                )
            if command_name == "metrics":
                run_id = args[0] if args else None
                return channel.send(
                    {
                        "conversation_id": conversation_id,
                        "quest_id": quest_id,
                        "kind": "metrics",
                        "message": self._format_metrics(quest_id, run_id=run_id),
                    }
                )
            if command_name == "graph":
                graph = self.artifact_service.render_git_graph(self.home / "quests" / quest_id)
                attachments = [
                    {"kind": "path", "path": graph["graph"].get("png_path") or graph["graph"].get("svg_path")},
                    {"kind": "path", "path": graph["graph"].get("json_path")},
                ]
                return channel.send(
                    {
                        "conversation_id": conversation_id,
                        "quest_id": quest_id,
                        "kind": "graph",
                        "message": self._format_graph_reply(quest_id, graph["graph"]),
                        "attachments": [item for item in attachments if item.get("path")],
                    }
                )
            if command_name == "terminal":
                quest_root = self.home / "quests" / quest_id
                workspace_root = self.quest_service.active_workspace_root(quest_root)
                session = self.bash_exec_service.ensure_terminal_session(
                    quest_root,
                    quest_id=quest_id,
                    cwd=workspace_root,
                    source=f"{connector_name}:connector",
                    conversation_id=conversation_id,
                    user_id=f"user:{connector_name}",
                )
                if args and args[0] == "-R":
                    restored = self.bash_exec_service.terminal_restore_payload(
                        quest_root,
                        str(session.get("bash_id") or ""),
                        command_limit=10,
                        output_limit=40,
                    )
                    commands = [
                        str(item.get("command") or "").strip()
                        for item in restored.get("latest_commands") or []
                        if str(item.get("command") or "").strip()
                    ]
                    tail_preview = [
                        str(item.get("line") or "").strip()
                        for item in (restored.get("tail") or [])[-4:]
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
                    return channel.send(
                        {
                            "conversation_id": conversation_id,
                            "quest_id": quest_id,
                            "kind": "terminal_restore",
                            "message": "\n".join(lines),
                        }
                    )
                if args:
                    command_text = " ".join(args).rstrip()
                    result = self.bash_exec_service.append_terminal_input(
                        quest_root,
                        str(session.get("bash_id") or ""),
                        data=f"{command_text}\n",
                        source=f"{connector_name}:connector",
                        user_id=f"user:{connector_name}",
                        conversation_id=conversation_id,
                    )
                    cwd_value = str(result.get("session", {}).get("cwd") or session.get("cwd") or quest_root)
                    return channel.send(
                        {
                            "conversation_id": conversation_id,
                            "quest_id": quest_id,
                            "kind": "terminal",
                            "message": self._polite_copy(
                                zh=f"已发送到终端 `{session.get('bash_id')}`，当前路径：`{cwd_value}`。\n命令：`{command_text}`",
                                en=f"Sent to terminal `{session.get('bash_id')}` at `{cwd_value}`.\nCommand: `{command_text}`",
                            ),
                        }
                    )
                return channel.send(
                    {
                        "conversation_id": conversation_id,
                        "quest_id": quest_id,
                        "kind": "terminal",
                        "message": self._polite_copy(
                            zh=f"终端 `{session.get('bash_id')}` 已就绪。\n状态：{session.get('status')}\n当前路径：`{session.get('cwd')}`",
                            en=f"Terminal `{session.get('bash_id')}` is ready.\nstatus: {session.get('status')}\ncwd: `{session.get('cwd')}`",
                        ),
                    }
                )
            if command_name == "approve":
                if not args:
                    return channel.send(
                        {
                            "conversation_id": conversation_id,
                            "quest_id": quest_id,
                            "kind": "ack",
                            "message": self._polite_copy(
                                zh="老师您好，请先提供决策 id，例如 `/approve decision-001`。",
                                en="Please provide a decision id, for example `/approve decision-001`.",
                            ),
                        }
                    )
                decision_id = args[0]
                reason = " ".join(args[1:]).strip() or self._polite_copy(
                    zh=f"由 {connector_label} 侧确认通过。",
                    en=f"Approved from {connector_label}.",
                )
                result = self.artifact_service.record(
                    self.home / "quests" / quest_id,
                    {
                        "kind": "approval",
                        "decision_id": decision_id,
                        "reason": reason,
                        "source": {
                            "kind": "user",
                            "conversation_id": conversation_id,
                            "user_id": message.get("sender_id"),
                        },
                        "raw_text": text,
                    },
                )
                return channel.send(
                    {
                        "conversation_id": conversation_id,
                        "quest_id": quest_id,
                        "kind": "approval_result",
                        "message": self._polite_copy(
                            zh=f"Approval recorded for `{decision_id}`. 老师，已记录通过决定。原因：{reason}",
                            en=f"Approval recorded for `{decision_id}`. Reason: {reason}",
                        ),
                        "attachments": [{"kind": "path", "path": result.get("path")}],
                    }
                )

        if quest_id is None:
            return channel.send(
                {
                    "conversation_id": conversation_id,
                    "kind": "ack",
                    "message": self._connector_home_help(connector_name, message=message),
                }
            )

        self.sessions.bind(quest_id, conversation_id)
        self.submit_user_message(
            quest_id,
            text=text,
            source=conversation_id,
        )
        return channel.send(
            {
                "conversation_id": conversation_id,
                "quest_id": quest_id,
                "kind": "ack",
                "message": self._with_qq_main_chat_notice(
                    message,
                    self._polite_copy(
                        zh=f"老师，已收到您的消息，当前会话已绑定到 {quest_id}。我会继续推进，并通过后续消息同步进展。",
                        en=f"Received. This conversation is bound to {quest_id}; I’ll continue the work and keep you updated with the next progress checkpoint.",
                    ),
                ),
            }
        )

    def _qq_channel(self) -> QQRelayChannel:
        return self.channels["qq"]  # type: ignore[return-value]

    def _auto_bind_connectors_to_latest_quest(
        self,
        quest_id: str,
        *,
        source: str,
        announce: bool,
        exclude_conversation_id: str | None = None,
    ) -> list[str]:
        bound_sources: list[str] = []
        for connector_name, channel in self.channels.items():
            if connector_name == "local":
                continue
            connector_config = self.connectors_config.get(connector_name, {})
            if not isinstance(connector_config, dict):
                continue
            if not connector_config.get("enabled", False):
                continue
            if not connector_config.get("auto_bind_dm_to_active_quest", False):
                continue
            conversation_id = self._latest_connector_conversation_id(connector_name)
            if not conversation_id or conversation_id == exclude_conversation_id:
                continue
            channel = self._channel_with_bindings(connector_name)
            channel.bind_conversation(conversation_id, quest_id)
            self.sessions.bind(quest_id, conversation_id)
            self.quest_service.bind_source(quest_id, conversation_id)
            bound_sources.append(conversation_id)
            if announce:
                channel.send(
                    {
                        "conversation_id": conversation_id,
                        "quest_id": quest_id,
                        "kind": "ack",
                        "message": self._polite_copy(
                            zh=f"老师，系统刚刚创建了新的 quest `{quest_id}`，当前 {self._connector_label(connector_name)} 会话已自动切换并绑定到这个最新 quest。",
                            en=f"A new quest `{quest_id}` was created. This {self._connector_label(connector_name)} conversation has been automatically rebound to the latest quest.",
                        ),
                    }
                )
        return bound_sources

    def _latest_connector_conversation_id(self, connector_name: str) -> str:
        if connector_name == "qq":
            qq_config = self.connectors_config.get("qq", {})
            if isinstance(qq_config, dict):
                main_chat_id = str(qq_config.get("main_chat_id") or "").strip()
                if main_chat_id:
                    return f"qq:direct:{main_chat_id}"
        state_path = self.home / "logs" / "connectors" / connector_name / "state.json"
        state = read_json(state_path, {})
        if not isinstance(state, dict):
            return ""
        return str(state.get("last_conversation_id") or "").strip()

    def _connector_home_help(self, connector_name: str, *, message: dict) -> str:
        quests = self.quest_service.list_quests()
        latest = str(quests[0]["quest_id"]) if quests else "none"
        body = self._polite_copy(
            zh=(
                "当前这个 connector 会话还没有绑定 quest。\n"
                "在创建或绑定 quest 之前，仅支持以下关键字：\n"
                "- `/help`：查看帮助\n"
                f"- `/projects` 或 `/list`：查看最近的 quest（当前最新：`{latest}`）\n"
                "- `/use <quest_id>`：绑定指定 quest\n"
                "- `/use latest`：直接绑定当前最新 quest\n"
                "- `/new <goal>`：新建一个 quest 并自动绑定当前会话\n"
                "如果直接输入普通文本，我会先返回这份帮助，而不会把文本投递给 agent。"
            ),
            en=(
                "This connector conversation is not bound to any quest yet.\n"
                "Before a quest is created or selected, only these commands are available:\n"
                "- `/help`: show help\n"
                f"- `/projects` or `/list`: list recent quests (latest: `{latest}`)\n"
                "- `/use <quest_id>`: bind a specific quest\n"
                "- `/use latest`: bind the latest quest\n"
                "- `/new <goal>`: create a new quest and bind this conversation\n"
                "If you send plain text now, I will return this help message instead of forwarding the text to the agent."
            ),
        )
        return self._with_qq_main_chat_notice(message, body)

    def _format_projects_list(self) -> str:
        quests = self.quest_service.list_quests()
        if not quests:
            return self._polite_copy(
                zh="当前还没有 quest。可以发送 `/new <goal>` 来创建一个新的 quest。",
                en="There is no quest yet. Use `/new <goal>` to create one.",
            )
        lines = [
            self._polite_copy(
                zh="最近的 quest 如下：",
                en="Recent quests:",
            )
        ]
        for item in quests[:6]:
            lines.append(
                f"- `{item['quest_id']}` · {item.get('title') or item['quest_id']} · {item.get('status') or 'active'}"
            )
        lines.append(
            self._polite_copy(
                zh="发送 `/use <quest_id>`、`/use latest`、`/projects` 或 `/list` 可以继续。",
                en="Use `/use <quest_id>`, `/use latest`, `/projects`, or `/list` to continue.",
            )
        )
        return "\n".join(lines)

    def _channel_with_bindings(self, name: str):
        channel = self.channels.get(name)
        if channel is None:
            raise KeyError(f"Unknown connector `{name}`.")
        for attr in ("ingest", "bind_conversation", "resolve_bound_quest", "list_bindings", "command_prefix"):
            if not hasattr(channel, attr):
                raise TypeError(f"Connector `{name}` does not implement `{attr}`.")
        return channel

    @staticmethod
    def _connector_label(name: str) -> str:
        normalized = (name or "").strip().lower()
        if normalized == "qq":
            return "QQ"
        if normalized == "feishu":
            return "Feishu"
        if normalized == "whatsapp":
            return "WhatsApp"
        return normalized.title() or "Connector"

    @staticmethod
    def _parse_prefixed_command(text: str, prefix: str) -> tuple[str, list[str]]:
        stripped = text[len(prefix):].strip()
        if not stripped:
            return "", []
        parts = stripped.split()
        return parts[0].lower(), parts[1:]

    def _maybe_bind_qq_main_chat(self, message: dict) -> dict | None:
        chat_type = str(message.get("chat_type") or "").strip().lower()
        if chat_type != "direct":
            return None
        chat_id = str(message.get("chat_id") or message.get("direct_id") or "").strip()
        if not chat_id:
            conversation_id = str(message.get("conversation_id") or "").strip()
            parts = conversation_id.split(":", 2)
            if len(parts) == 3 and parts[0] == "qq" and parts[1] == "direct":
                chat_id = parts[2]
        if not chat_id:
            return None
        result = self.config_manager.bind_qq_main_chat(chat_id=chat_id)
        if not result.get("ok"):
            self.logger.log(
                "warning",
                "connector.qq_main_chat_bind_failed",
                chat_id=chat_id,
                errors=result.get("errors") or [],
            )
            return None
        if not result.get("saved"):
            return None
        qq_config = self.connectors_config.get("qq")
        if isinstance(qq_config, dict):
            qq_config["main_chat_id"] = chat_id
        channel = self.channels.get("qq")
        if channel is not None and hasattr(channel, "config") and isinstance(channel.config, dict):
            channel.config["main_chat_id"] = chat_id
        gateway = self._qq_gateway
        if gateway is not None and isinstance(gateway.config, dict):
            gateway.config["main_chat_id"] = chat_id
        return {
            "chat_id": chat_id,
            "saved_at": result.get("saved_at"),
        }

    def _with_qq_main_chat_notice(self, message: dict, base: str) -> str:
        binding = message.get("_qq_main_chat_binding")
        if not isinstance(binding, dict):
            return base
        chat_id = str(binding.get("chat_id") or "").strip()
        if not chat_id:
            return base
        notice = self._polite_copy(
            zh=f"已自动检测并保存当前 QQ openid：`{chat_id}`。您现在可以在 settings 页面看到这个绑定结果。",
            en=f"I automatically detected and saved this QQ openid: `{chat_id}`. You can now see the binding in settings.",
        )
        return f"{notice}\n\n{base}"

    def _start_background_connectors(self) -> None:
        qq_config = self.connectors_config.get("qq", {})
        if isinstance(qq_config, dict) and self._qq_gateway is None:
            gateway = QQGatewayService(
                home=self.home,
                config=qq_config,
                on_event=lambda event: self.handle_connector_inbound("qq", event),
                log=lambda level, message: self.logger.log(level, "connector.qq_gateway", message=message),
            )
            if gateway.start():
                self._qq_gateway = gateway
        telegram_config = self.connectors_config.get("telegram", {})
        if isinstance(telegram_config, dict) and self._telegram_polling is None:
            polling = TelegramPollingService(
                home=self.home,
                config=telegram_config,
                on_event=lambda event: self.handle_connector_inbound("telegram", event),
                log=lambda level, message: self.logger.log(level, "connector.telegram_polling", message=message),
            )
            if polling.start():
                self._telegram_polling = polling
        slack_config = self.connectors_config.get("slack", {})
        if isinstance(slack_config, dict) and self._slack_socket is None:
            slack = SlackSocketModeService(
                home=self.home,
                config=slack_config,
                on_event=lambda event: self.handle_connector_inbound("slack", event),
                log=lambda level, message: self.logger.log(level, "connector.slack_socket", message=message),
            )
            if slack.start():
                self._slack_socket = slack
        discord_config = self.connectors_config.get("discord", {})
        if isinstance(discord_config, dict) and self._discord_gateway is None:
            discord = DiscordGatewayService(
                home=self.home,
                config=discord_config,
                on_event=lambda event: self.handle_connector_inbound("discord", event),
                log=lambda level, message: self.logger.log(level, "connector.discord_gateway", message=message),
            )
            if discord.start():
                self._discord_gateway = discord
        feishu_config = self.connectors_config.get("feishu", {})
        if isinstance(feishu_config, dict) and self._feishu_long_connection is None:
            feishu = FeishuLongConnectionService(
                home=self.home,
                config=feishu_config,
                on_event=lambda event: self.handle_connector_inbound("feishu", event),
                log=lambda level, message: self.logger.log(level, "connector.feishu_long_connection", message=message),
            )
            if feishu.start():
                self._feishu_long_connection = feishu
        whatsapp_config = self.connectors_config.get("whatsapp", {})
        if isinstance(whatsapp_config, dict) and self._whatsapp_local_session is None:
            whatsapp = WhatsAppLocalSessionService(
                home=self.home,
                config=whatsapp_config,
                on_event=lambda event: self.handle_connector_inbound("whatsapp", event),
                log=lambda level, message: self.logger.log(level, "connector.whatsapp_local_session", message=message),
            )
            if whatsapp.start():
                self._whatsapp_local_session = whatsapp

    def _stop_background_connectors(self) -> None:
        gateway = self._qq_gateway
        self._qq_gateway = None
        if gateway is not None:
            gateway.stop()
        polling = self._telegram_polling
        self._telegram_polling = None
        if polling is not None:
            polling.stop()
        slack = self._slack_socket
        self._slack_socket = None
        if slack is not None:
            slack.stop()
        discord = self._discord_gateway
        self._discord_gateway = None
        if discord is not None:
            discord.stop()
        feishu = self._feishu_long_connection
        self._feishu_long_connection = None
        if feishu is not None:
            feishu.stop()
        whatsapp = self._whatsapp_local_session
        self._whatsapp_local_session = None
        if whatsapp is not None:
            whatsapp.stop()

    @staticmethod
    def _format_status(snapshot: dict) -> str:
        latest_metric = (snapshot.get("summary") or {}).get("latest_metric")
        pending = snapshot.get("pending_decisions") or []
        counts = snapshot.get("counts") or {}
        bound_conversations = snapshot.get("bound_conversations") or []
        lines = [
            f"Quest {snapshot.get('quest_id')}",
            "Quest Status",
            f"- quest_id: {snapshot.get('quest_id')}",
            f"- title: {snapshot.get('title')}",
            f"- status: {snapshot.get('status')}",
            f"- anchor: {snapshot.get('active_anchor')}",
            f"- runner: {snapshot.get('runner')}",
            f"- branch: {snapshot.get('branch')}",
            f"- active_run_id: {snapshot.get('active_run_id') or 'none'}",
            f"- quest_root: {snapshot.get('quest_root')}",
            f"- updated_at: {snapshot.get('updated_at')}",
            f"- history_count: {snapshot.get('history_count')}",
            f"- artifact_count: {snapshot.get('artifact_count')}",
            f"- memory_cards: {counts.get('memory_cards', 0)}",
            f"- pending_decisions: {len(pending)}",
            f"- bound_conversations: {', '.join(str(item) for item in bound_conversations) if bound_conversations else 'none'}",
        ]
        status_line = ((snapshot.get("summary") or {}).get("status_line") or "").strip()
        if status_line:
            lines.append(f"- summary: {status_line}")
        if isinstance(latest_metric, dict) and latest_metric.get("key"):
            lines.append(f"- latest_metric: {latest_metric.get('key')} = {latest_metric.get('value')}")
        if pending:
            lines.append(f"- pending_decision_ids: {', '.join(str(item) for item in pending[:8])}")
        return "\n".join(lines)

    def _format_summary(self, quest_id: str) -> str:
        summary = read_text(self.home / "quests" / quest_id / "SUMMARY.md").strip()
        if not summary:
            return f"No summary has been written yet for {quest_id}."
        return summary[:1800]

    def _format_metrics(self, quest_id: str, *, run_id: str | None = None) -> str:
        quest_root = self.home / "quests" / quest_id
        run_paths = sorted((quest_root / "artifacts" / "runs").glob("*.json"))
        if not run_paths:
            snapshot = self.quest_service.snapshot(quest_id)
            latest_metric = (snapshot.get("summary") or {}).get("latest_metric")
            if isinstance(latest_metric, dict) and latest_metric.get("key"):
                return f"{latest_metric.get('key')} = {latest_metric.get('value')}"
            return "No metrics are available yet."
        matches = []
        for path in run_paths:
            payload = read_json(path, {})
            if run_id and payload.get("run_id") != run_id:
                continue
            matches.append(payload)
        if not matches:
            return f"No run metrics found for `{run_id}`."
        latest = matches[-1]
        metrics = latest.get("metrics") or latest.get("metrics_summary") or {}
        if isinstance(metrics, dict) and metrics:
            compact = ", ".join(f"{key}={value}" for key, value in list(metrics.items())[:6])
            return f"{latest.get('run_id') or latest.get('artifact_id')}: {compact}"
        summary = str(latest.get("summary") or "No structured metrics were recorded.")
        return f"{latest.get('run_id') or latest.get('artifact_id')}: {summary}"

    @staticmethod
    def _format_graph_reply(quest_id: str, graph: dict) -> str:
        branch = graph.get("branch")
        head = graph.get("head")
        lines = graph.get("lines") or []
        return (
            f"Git graph refreshed for {quest_id}.\n"
            f"Branch: {branch}\n"
            f"Head: {head}\n"
            f"Recent commits: {min(len(lines), 8)} shown in the attached graph."
        )

    def _wants_event_stream(self, path: str, headers: dict[str, str]) -> bool:
        query = self.handlers.parse_query(path)
        stream_value = ((query.get("stream") or [""])[0] or "").strip().lower()
        accept = str(headers.get("Accept") or headers.get("accept") or "").lower()
        return stream_value in {"1", "true", "yes", "stream"} or "text/event-stream" in accept

    @staticmethod
    def _write_sse_event(
        handler: BaseHTTPRequestHandler,
        *,
        event: str,
        data: dict,
        event_id: str | None = None,
    ) -> None:
        if event_id:
            handler.wfile.write(f"id: {event_id}\n".encode("utf-8"))
        handler.wfile.write(f"event: {event}\n".encode("utf-8"))
        rendered = json.dumps(data, ensure_ascii=False)
        for line in rendered.splitlines() or ["{}"]:
            handler.wfile.write(f"data: {line}\n".encode("utf-8"))
        handler.wfile.write(b"\n")
        handler.wfile.flush()

    def stream_quest_events(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        quest_id: str,
        path: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        query = self.handlers.parse_query(path)
        after = int((query.get("after") or ["0"])[0] or "0")
        limit = max(1, min(int((query.get("limit") or ["200"])[0] or "200"), 200))
        format_name = ((query.get("format") or ["acp"])[0] or "acp").lower()
        session_id = ((query.get("session_id") or [f"quest:{quest_id}"])[0] or f"quest:{quest_id}")
        last_event_id = str((headers or {}).get("Last-Event-ID") or (headers or {}).get("last-event-id") or "").strip()
        current_cursor = max(after, int(last_event_id)) if last_event_id.isdigit() else after
        heartbeat_at = time.monotonic()

        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache, no-transform")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()
        handler.wfile.write(b"retry: 1000\n\n")
        handler.wfile.flush()

        try:
            while True:
                stream_path = f"/api/quests/{quest_id}/events?{urlencode({'after': current_cursor, 'limit': limit, 'format': format_name, 'session_id': session_id})}"
                payload = self.handlers.quest_events(quest_id, path=stream_path)
                updates = payload.get("acp_updates") or []
                if updates:
                    for update in updates:
                        update_cursor = str(((update.get("params") or {}).get("update") or {}).get("cursor") or "")
                        self._write_sse_event(
                            handler,
                            event="acp_update",
                            data=update,
                            event_id=update_cursor or None,
                        )
                    current_cursor = int(payload.get("cursor") or current_cursor)
                    self._write_sse_event(
                        handler,
                        event="cursor",
                        data={"cursor": current_cursor, "quest_id": quest_id},
                    )
                    heartbeat_at = time.monotonic()
                else:
                    now = time.monotonic()
                    if now - heartbeat_at >= 10:
                        handler.wfile.write(b": keep-alive\n\n")
                        handler.wfile.flush()
                        heartbeat_at = now
                time.sleep(0.35)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return

    def stream_bash_sessions(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        quest_id: str,
        path: str,
    ) -> None:
        quest_root = self.quest_service._quest_root(quest_id)
        query = self.handlers.parse_query(path)
        status = ((query.get("status") or [""])[0] or "").strip() or None
        limit_raw = ((query.get("limit") or ["200"])[0] or "200").strip()
        chat_session_id = ((query.get("chat_session_id") or [""])[0] or "").strip() or None
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

        def list_payload() -> list[dict[str, object]]:
            return self.bash_exec_service.list_sessions(
                quest_root,
                status=status,
                agent_ids=agent_ids or None,
                agent_instance_ids=agent_instance_ids or None,
                chat_session_id=chat_session_id,
                limit=limit,
            )

        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache, no-transform")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()
        handler.wfile.write(b"retry: 1000\n\n")
        handler.wfile.flush()

        previous_snapshot: dict[str, dict[str, object]] = {}
        heartbeat_at = time.monotonic()
        try:
            while True:
                sessions = list_payload()
                current_snapshot = {
                    str(item.get("bash_id") or ""): item
                    for item in sessions
                    if item.get("bash_id")
                }
                if not previous_snapshot:
                    self._write_sse_event(
                        handler,
                        event="snapshot",
                        data={"sessions": sessions},
                    )
                    previous_snapshot = current_snapshot
                    heartbeat_at = time.monotonic()
                else:
                    changed = [
                        session
                        for bash_id, session in current_snapshot.items()
                        if previous_snapshot.get(bash_id) != session
                    ]
                    removed = set(previous_snapshot) - set(current_snapshot)
                    for session in changed:
                        self._write_sse_event(
                            handler,
                            event="session",
                            data={"session": session},
                        )
                    for bash_id in removed:
                        self._write_sse_event(
                            handler,
                            event="session",
                            data={"session": {"bash_id": bash_id, "status": "terminated"}},
                        )
                    if changed or removed:
                        previous_snapshot = current_snapshot
                        heartbeat_at = time.monotonic()
                    elif time.monotonic() - heartbeat_at >= 10:
                        handler.wfile.write(b": keep-alive\n\n")
                        handler.wfile.flush()
                        heartbeat_at = time.monotonic()
                time.sleep(0.4)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return

    def stream_bash_logs(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        quest_id: str,
        bash_id: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        quest_root = self.quest_service._quest_root(quest_id)
        last_event_raw = str((headers or {}).get("Last-Event-ID") or (headers or {}).get("last-event-id") or "").strip()
        last_event_id = int(last_event_raw) if last_event_raw.isdigit() else None

        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache, no-transform")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()
        handler.wfile.write(b"retry: 1000\n\n")
        handler.wfile.flush()

        previous_progress = None
        previous_status = None
        previous_exit_code = None
        cursor = last_event_id or 0
        snapshot_sent = False
        heartbeat_at = time.monotonic()
        try:
            while True:
                session = self.bash_exec_service.get_session(quest_root, bash_id)
                entries, meta = self.bash_exec_service.read_log_entries(
                    quest_root,
                    bash_id,
                    limit=200,
                    before_seq=None,
                    order="asc",
                )
                latest_seq = int(meta.get("latest_seq") or 0)
                if not snapshot_sent and last_event_id is None:
                    self._write_sse_event(
                        handler,
                        event="snapshot",
                        event_id=str(latest_seq) if latest_seq else None,
                        data={
                            "bash_id": bash_id,
                            "tail_limit": meta.get("tail_limit"),
                            "tail_start_seq": meta.get("tail_start_seq"),
                            "latest_seq": meta.get("latest_seq"),
                            "lines": [
                                {
                                    "seq": entry.get("seq"),
                                    "stream": entry.get("stream"),
                                    "line": entry.get("line"),
                                    "timestamp": entry.get("timestamp"),
                                }
                                for entry in entries
                            ],
                            "progress": session.get("last_progress"),
                        },
                    )
                    snapshot_sent = True
                    cursor = latest_seq
                    previous_progress = session.get("last_progress")
                    previous_status = session.get("status")
                    previous_exit_code = session.get("exit_code")
                    heartbeat_at = time.monotonic()
                    if str(session.get("status") or "") in {"completed", "failed", "terminated"}:
                        self._write_sse_event(
                            handler,
                            event="done",
                            data={
                                "bash_id": bash_id,
                                "status": session.get("status"),
                                "exit_code": session.get("exit_code"),
                                "finished_at": session.get("finished_at"),
                            },
                        )
                        return
                else:
                    fresh_entries = [
                        entry
                        for entry in read_jsonl(self.bash_exec_service.log_path(quest_root, bash_id))
                        if int(entry.get("seq") or 0) > cursor
                    ]
                    if fresh_entries:
                        cursor = int(fresh_entries[-1].get("seq") or cursor)
                        self._write_sse_event(
                            handler,
                            event="log_batch",
                            event_id=str(cursor),
                            data={
                                "bash_id": bash_id,
                                "from_seq": fresh_entries[0].get("seq"),
                                "to_seq": fresh_entries[-1].get("seq"),
                                "lines": [
                                    {
                                        "seq": entry.get("seq"),
                                        "stream": entry.get("stream"),
                                        "line": entry.get("line"),
                                        "timestamp": entry.get("timestamp"),
                                    }
                                    for entry in fresh_entries
                                ],
                            },
                        )
                        heartbeat_at = time.monotonic()
                    if session.get("last_progress") != previous_progress and session.get("last_progress") is not None:
                        previous_progress = session.get("last_progress")
                        self._write_sse_event(
                            handler,
                            event="progress",
                            data={
                                "bash_id": bash_id,
                                **dict(session.get("last_progress") or {}),
                            },
                        )
                        heartbeat_at = time.monotonic()
                    if (
                        session.get("status") != previous_status
                        or session.get("exit_code") != previous_exit_code
                    ) and str(session.get("status") or "") in {"completed", "failed", "terminated"}:
                        previous_status = session.get("status")
                        previous_exit_code = session.get("exit_code")
                        self._write_sse_event(
                            handler,
                            event="done",
                            data={
                                "bash_id": bash_id,
                                "status": session.get("status"),
                                "exit_code": session.get("exit_code"),
                                "finished_at": session.get("finished_at"),
                            },
                        )
                        heartbeat_at = time.monotonic()
                        return
                    if time.monotonic() - heartbeat_at >= 10:
                        handler.wfile.write(b": keep-alive\n\n")
                        handler.wfile.flush()
                        heartbeat_at = time.monotonic()
                time.sleep(0.35)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return

    def serve(self, host: str, port: int) -> None:
        app = self

        class RequestHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                self._dispatch("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._dispatch("POST")

            def do_PUT(self) -> None:  # noqa: N802
                self._dispatch("PUT")

            def do_PATCH(self) -> None:  # noqa: N802
                self._dispatch("PATCH")

            def _dispatch(self, method: str) -> None:
                parsed = urlparse(self.path)
                route_name, params = match_route(method, parsed.path)
                if route_name is None:
                    self._write_json(404, {"ok": False, "message": "Not Found"})
                    return
                if route_name == "quest_events" and app._wants_event_stream(self.path, dict(self.headers.items())):
                    try:
                        app.stream_quest_events(self, **params, path=self.path, headers=dict(self.headers.items()))
                    except Exception as exc:
                        self._write_json(500, {"ok": False, "message": str(exc)})
                    return
                if route_name == "bash_sessions_stream":
                    try:
                        app.stream_bash_sessions(self, **params, path=self.path)
                    except Exception as exc:
                        self._write_json(500, {"ok": False, "message": str(exc)})
                    return
                if route_name == "bash_log_stream":
                    try:
                        app.stream_bash_logs(self, **params, headers=dict(self.headers.items()))
                    except Exception as exc:
                        self._write_json(500, {"ok": False, "message": str(exc)})
                    return
                if route_name == "terminal_stream":
                    try:
                        app.stream_bash_logs(self, quest_id=params["quest_id"], bash_id=params["session_id"], headers=dict(self.headers.items()))
                    except Exception as exc:
                        self._write_json(500, {"ok": False, "message": str(exc)})
                    return

                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length) if content_length else b""
                body = {}
                if raw_body and self.headers.get("Content-Type", "").startswith("application/json"):
                    body = app.handlers.parse_body(raw_body)

                try:
                    result = getattr(app.handlers, route_name)
                    if route_name == "asset":
                        status, headers, content = result(**params)
                        self.send_response(status)
                        for key, value in headers.items():
                            self.send_header(key, value)
                        self.end_headers()
                        self.wfile.write(content)
                        return
                    if route_name in {
                        "quest_events",
                        "bash_sessions",
                        "bash_logs",
                        "git_log",
                        "git_compare",
                        "git_commit",
                        "git_diff_file",
                        "git_commit_file",
                        "explorer",
                        "quest_search",
                        "node_traces",
                        "node_trace",
                        "document_asset",
                        "terminal_restore",
                        "terminal_history",
                    }:
                        payload = result(**params, path=self.path)
                    elif method == "GET":
                        payload = result(**params) if params else result()
                    elif route_name == "bridge_webhook":
                        payload = result(
                            **params,
                            method=method,
                            path=self.path,
                            raw_body=raw_body,
                            headers=dict(self.headers.items()),
                            body=body,
                        )
                    elif route_name in {"document_open", "document_asset_upload", "chat", "command", "quest_control", "config_save", "quest_create", "run_create", "qq_inbound", "connector_inbound", "docs_open", "admin_shutdown", "bash_stop", "quest_settings", "terminal_session_ensure", "terminal_input"}:
                        payload = result(**params, body=body)
                    elif route_name == "config_validate":
                        payload = result(body)
                    elif route_name == "config_test":
                        payload = result(body)
                    elif method in {"PUT", "PATCH"}:
                        payload = result(**params, body=body)
                    elif route_name == "memory":
                        payload = result(app.handlers.parse_query(self.path))
                    else:
                        payload = result(**params) if params else result()
                except Exception as exc:
                    self._write_json(500, {"ok": False, "message": str(exc)})
                    return

                if isinstance(payload, tuple) and len(payload) == 2:
                    status, body = payload
                    self._write_json(status, body)
                    return
                if isinstance(payload, tuple) and len(payload) == 3:
                    status, headers, content = payload
                    self.send_response(status)
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.end_headers()
                    if isinstance(content, str):
                        self.wfile.write(content.encode("utf-8"))
                    else:
                        self.wfile.write(content)
                    return
                self._write_json(200, payload)

            def _write_json(self, code: int, payload: dict | list) -> None:
                encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = ThreadingHTTPServer((host, port), RequestHandler)
        server.daemon_threads = True
        self._server = server
        self._shutdown_requested.clear()
        self._start_background_connectors()
        print(f"DeepScientist daemon listening on http://{host}:{port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._stop_background_connectors()
            self._server = None
            server.server_close()
