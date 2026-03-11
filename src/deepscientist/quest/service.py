from __future__ import annotations

import hashlib
import subprocess
import json
import mimetypes
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from ..artifact.metrics import build_metrics_timeline, normalize_metrics_summary
from ..connector_runtime import conversation_identity_key, normalize_conversation_id
from ..gitops import current_branch, export_git_graph, head_commit, init_repo
from ..home import repo_root
from ..shared import append_jsonl, ensure_dir, generate_id, read_json, read_jsonl, read_text, read_yaml, resolve_within, run_command, sha256_text, slugify, utc_now, write_json, write_text, write_yaml
from ..skills import SkillInstaller
from .layout import (
    QUEST_DIRECTORIES,
    gitignore,
    initial_brief,
    initial_plan,
    initial_quest_yaml,
    initial_status,
    initial_summary,
)
from .node_traces import QuestNodeTraceManager

_UNSET = object()


class QuestService:
    def __init__(self, home: Path, skill_installer: SkillInstaller | None = None) -> None:
        self.home = home
        self.quests_root = home / "quests"
        self.skill_installer = skill_installer

    def _quest_root(self, quest_id: str) -> Path:
        return self.quests_root / quest_id

    @staticmethod
    def _research_state_path(quest_root: Path) -> Path:
        return quest_root / ".ds" / "research_state.json"

    def _default_research_state(self, quest_root: Path) -> dict[str, Any]:
        return {
            "version": 1,
            "active_idea_id": None,
            "research_head_branch": None,
            "research_head_worktree_root": None,
            "current_workspace_branch": None,
            "current_workspace_root": None,
            "active_idea_md_path": None,
            "active_analysis_campaign_id": None,
            "analysis_parent_branch": None,
            "analysis_parent_worktree_root": None,
            "next_pending_slice_id": None,
            "workspace_mode": "quest",
            "last_flow_type": None,
            "updated_at": utc_now(),
        }

    def read_research_state(self, quest_root: Path) -> dict[str, Any]:
        self._initialize_runtime_files(quest_root)
        payload = read_json(self._research_state_path(quest_root), self._default_research_state(quest_root))
        if not isinstance(payload, dict):
            payload = self._default_research_state(quest_root)
        merged = {**self._default_research_state(quest_root), **payload}
        worktree_root = str(merged.get("research_head_worktree_root") or "").strip()
        if worktree_root and not Path(worktree_root).exists():
            merged["research_head_worktree_root"] = None
        current_root = str(merged.get("current_workspace_root") or "").strip()
        if current_root and not Path(current_root).exists():
            merged["current_workspace_root"] = None
        parent_root = str(merged.get("analysis_parent_worktree_root") or "").strip()
        if parent_root and not Path(parent_root).exists():
            merged["analysis_parent_worktree_root"] = None
        return merged

    def write_research_state(self, quest_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = {**self._default_research_state(quest_root), **payload, "updated_at": utc_now()}
        write_json(self._research_state_path(quest_root), normalized)
        return normalized

    def update_research_state(self, quest_root: Path, **updates: Any) -> dict[str, Any]:
        current = self.read_research_state(quest_root)
        for key, value in updates.items():
            if value is _UNSET:
                continue
            current[key] = str(value) if isinstance(value, Path) else value
        return self.write_research_state(quest_root, current)

    def workspace_roots(self, quest_root: Path) -> list[Path]:
        roots: list[Path] = [quest_root]
        state = self.read_research_state(quest_root)
        preferred_raw = str(state.get("research_head_worktree_root") or "").strip()
        if preferred_raw:
            preferred = Path(preferred_raw)
            if preferred.exists():
                roots.append(preferred)
        worktrees_root = quest_root / ".ds" / "worktrees"
        if worktrees_root.exists():
            for path in sorted(worktrees_root.iterdir()):
                if path.is_dir():
                    roots.append(path)
        deduped: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root.resolve())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(root)
        return deduped

    def active_workspace_root(self, quest_root: Path) -> Path:
        state = self.read_research_state(quest_root)
        current_raw = str(state.get("current_workspace_root") or "").strip()
        if current_raw:
            current = Path(current_raw)
            if current.exists():
                return current
        preferred_raw = str(state.get("research_head_worktree_root") or "").strip()
        if preferred_raw:
            preferred = Path(preferred_raw)
            if preferred.exists():
                return preferred
        return quest_root

    def _artifact_roots(self, quest_root: Path) -> list[Path]:
        return [root for root in self.workspace_roots(quest_root) if (root / "artifacts").exists()]

    def _collect_artifacts(self, quest_root: Path) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for root in self._artifact_roots(quest_root):
            artifacts_root = root / "artifacts"
            if not artifacts_root.exists():
                continue
            for folder in sorted(artifacts_root.iterdir()):
                if not folder.is_dir():
                    continue
                for path in sorted(folder.glob("*.json")):
                    resolved_key = str(path.resolve())
                    if resolved_key in seen_paths:
                        continue
                    seen_paths.add(resolved_key)
                    item = read_json(path, {})
                    artifacts.append(
                        {
                            "kind": folder.name,
                            "path": str(path),
                            "payload": item,
                            "workspace_root": str(root),
                        }
                    )
        artifacts.sort(
            key=lambda item: str(
                ((item.get("payload") or {}).get("updated_at"))
                or ((item.get("payload") or {}).get("created_at"))
                or item.get("path")
                or ""
            )
        )
        return artifacts

    @staticmethod
    def _active_baseline_attachment(quest_root: Path, workspace_root: Path) -> dict[str, Any] | None:
        attachments: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for root in (workspace_root, quest_root):
            attachment_root = root / "baselines" / "imported"
            if not attachment_root.exists():
                continue
            for path in sorted(attachment_root.glob("*/attachment.yaml")):
                key = str(path.resolve())
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                payload = read_yaml(path, {})
                if isinstance(payload, dict) and payload:
                    attachments.append(payload)
        if not attachments:
            return None
        return max(
            attachments,
            key=lambda item: (
                str(item.get("attached_at") or ""),
                str(item.get("source_baseline_id") or ""),
            ),
        )

    @staticmethod
    def _latest_metric_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
        metrics_summary = normalize_metrics_summary(payload.get("metrics_summary"))
        if not metrics_summary:
            return None
        progress_eval = payload.get("progress_eval") if isinstance(payload.get("progress_eval"), dict) else {}
        comparisons = payload.get("baseline_comparisons") if isinstance(payload.get("baseline_comparisons"), dict) else {}
        primary_metric_id = (
            str(progress_eval.get("primary_metric_id") or comparisons.get("primary_metric_id") or "").strip()
            or next(iter(metrics_summary.keys()))
        )
        result = {
            "key": primary_metric_id,
            "value": metrics_summary.get(primary_metric_id),
        }
        if progress_eval.get("delta_vs_baseline") is not None:
            result["delta_vs_baseline"] = progress_eval.get("delta_vs_baseline")
        return result

    def _normalize_quest_id(self, quest_id: str | None) -> str:
        raw = str(quest_id or "").strip().lower()
        if not raw:
          return generate_id("q")
        slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("._-")
        if not slug:
            return generate_id("q")
        return slug[:80]

    def create(
        self,
        goal: str,
        quest_id: str | None = None,
        runner: str = "codex",
        title: str | None = None,
    ) -> dict:
        quest_id = self._normalize_quest_id(quest_id)
        quest_root = self._quest_root(quest_id)
        if quest_root.exists():
            raise FileExistsError(f"Quest already exists: {quest_id}")
        ensure_dir(quest_root)
        for relative in QUEST_DIRECTORIES:
            ensure_dir(quest_root / relative)
        write_yaml(quest_root / "quest.yaml", initial_quest_yaml(quest_id, goal, quest_root, runner, title=title))
        write_text(quest_root / "brief.md", initial_brief(goal))
        write_text(quest_root / "plan.md", initial_plan())
        write_text(quest_root / "status.md", initial_status())
        write_text(quest_root / "SUMMARY.md", initial_summary())
        write_text(quest_root / ".gitignore", gitignore())
        init_repo(quest_root)
        if self.skill_installer is not None:
            self.skill_installer.sync_quest(quest_root)
        from ..gitops import checkpoint_repo

        checkpoint_repo(quest_root, f"quest: initialize {quest_id}", allow_empty=False)
        export_git_graph(quest_root, ensure_dir(quest_root / "artifacts" / "graphs"))
        self._initialize_runtime_files(quest_root)
        return self.snapshot(quest_id)

    def list_quests(self) -> list[dict]:
        items: list[dict] = []
        if not self.quests_root.exists():
            return items
        for quest_yaml in sorted(self.quests_root.glob("*/quest.yaml")):
            quest_id = quest_yaml.parent.name
            items.append(self.snapshot(quest_id))
        return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)

    def snapshot(self, quest_id: str) -> dict:
        quest_root = self._quest_root(quest_id)
        workspace_root = self.active_workspace_root(quest_root)
        research_state = self.read_research_state(quest_root)
        quest_yaml = read_yaml(quest_root / "quest.yaml", {})
        graph_dir = quest_root / "artifacts" / "graphs"
        graph_svg = graph_dir / "git-graph.svg"
        history = read_jsonl(quest_root / ".ds" / "conversations" / "main.jsonl")
        artifacts = []
        recent_runs = []
        memory_cards = list((workspace_root / "memory").glob("**/*.md"))
        pending_decisions = []
        active_interactions = []
        candidate_pending_decisions = []
        approved_decision_ids: set[str] = set()
        latest_metric = None
        active_baseline_id = None
        active_baseline_variant_id = None
        interaction_state = self._read_interaction_state(quest_root)
        open_requests = [
            dict(item)
            for item in (interaction_state.get("open_requests") or [])
            if str(item.get("status") or "") in {"waiting", "answered"}
        ]
        active_interactions = open_requests[-5:]
        recent_reply_threads = [dict(item) for item in (interaction_state.get("recent_threads") or [])][-5:]
        waiting_interaction_id = self._latest_waiting_interaction_id(open_requests)
        latest_thread_interaction_id = str(interaction_state.get("latest_thread_interaction_id") or "").strip() or None
        default_reply_interaction_id = str(interaction_state.get("default_reply_interaction_id") or "").strip() or None
        if not default_reply_interaction_id:
            default_reply_interaction_id = self._default_reply_interaction_id(
                open_requests=open_requests,
                recent_threads=recent_reply_threads,
            )
        answered_interaction_ids = {
            str(item.get("artifact_id") or item.get("interaction_id") or "")
            for item in active_interactions
            if str(item.get("status") or "") == "answered"
        }
        pending_decisions.extend(
            [
                str(item.get("artifact_id") or item.get("interaction_id") or "")
                for item in active_interactions
                if str(item.get("status") or "") == "waiting"
                and (item.get("artifact_id") or item.get("interaction_id"))
            ]
        )
        artifacts = self._collect_artifacts(quest_root)
        for artifact_item in artifacts:
            folder_name = str(artifact_item.get("kind") or "")
            path = Path(str(artifact_item.get("path") or "artifact.json"))
            item = artifact_item.get("payload") or {}
            if folder_name == "approvals":
                decision_id = str(item.get("decision_id") or "").strip()
                if decision_id:
                    approved_decision_ids.add(decision_id)
            if folder_name == "decisions":
                is_pending_user = (
                    str(item.get("verdict") or "") == "pending_user"
                    or str(item.get("action") or "") == "request_user_decision"
                    or str(item.get("interaction_phase") or "") == "request"
                )
                decision_id = str(item.get("id") or path.stem)
                if is_pending_user:
                    candidate_pending_decisions.append(decision_id)
            artifact_metric = self._latest_metric_from_payload(item)
            if artifact_metric is not None:
                latest_metric = artifact_metric
        for decision_id in candidate_pending_decisions:
            if decision_id in pending_decisions:
                continue
            if decision_id in answered_interaction_ids:
                continue
            if decision_id in approved_decision_ids:
                continue
            pending_decisions.append(decision_id)
        codex_history_root = quest_root / ".ds" / "codex_history"
        if codex_history_root.exists():
            for meta_path in sorted(codex_history_root.glob("*/meta.json")):
                run_data = read_json(meta_path, {})
                if run_data:
                    recent_runs.append(run_data)
                    if latest_metric is None and run_data.get("summary"):
                        latest_metric = {"key": "summary", "value": run_data.get("summary")}
        attachment = self._active_baseline_attachment(quest_root, workspace_root)
        if attachment:
            active_baseline_id = attachment.get("source_baseline_id")
            active_baseline_variant_id = attachment.get("source_variant_id")
        status_line = "Quest created."
        status_text = read_text(quest_root / "status.md").strip().splitlines()
        if status_text:
            for line in status_text:
                line = line.strip().lstrip("#").strip()
                if line and line.lower() not in {"status", "summary"}:
                    status_line = line
                    break
        runtime_state = self._read_runtime_state(quest_root)
        from ..bash_exec import BashExecService

        bash_service = BashExecService(self.home)
        bash_sessions = bash_service.list_sessions(quest_root, limit=500)
        bash_running = [item for item in bash_sessions if str(item.get("status") or "") in {"running", "terminating"}]
        latest_bash_session = bash_sessions[0] if bash_sessions else None
        paths = {
            "brief": str(workspace_root / "brief.md"),
            "plan": str(workspace_root / "plan.md"),
            "status": str(workspace_root / "status.md"),
            "summary": str(workspace_root / "SUMMARY.md"),
            "git_graph_svg": str(graph_svg) if graph_svg.exists() else None,
            "runtime_state": str(self._runtime_state_path(quest_root)),
            "research_state": str(self._research_state_path(quest_root)),
            "active_workspace_root": str(workspace_root),
            "user_message_queue": str(self._message_queue_path(quest_root)),
            "interaction_journal": str(self._interaction_journal_path(quest_root)),
            "bash_exec_root": str(quest_root / ".ds" / "bash_exec"),
        }
        counts = {
            "memory_cards": len(memory_cards),
            "artifacts": len(artifacts),
            "pending_decision_count": len(pending_decisions),
            "analysis_run_count": sum(
                1
                for item in recent_runs
                if str(item.get("run_id", "")).startswith("analysis")
                or item.get("run_kind") == "analysis-campaign"
            ),
            "pending_user_message_count": int(runtime_state.get("pending_user_message_count") or 0),
            "bash_session_count": len(bash_sessions),
            "bash_running_count": len(bash_running),
        }
        guidance = None
        try:
            from ..artifact.guidance import build_guidance_for_snapshot

            guidance = build_guidance_for_snapshot(
                {
                    "quest_id": quest_yaml.get("quest_id", quest_id),
                    "active_anchor": quest_yaml.get("active_anchor", "baseline"),
                    "pending_decisions": pending_decisions,
                    "waiting_interaction_id": waiting_interaction_id,
                    "recent_artifacts": artifacts[-5:],
                }
            )
        except Exception:
            guidance = None
        return {
            "quest_id": quest_yaml.get("quest_id", quest_id),
            "title": quest_yaml.get("title", quest_id),
            "quest_root": str(quest_root.resolve()),
            "status": runtime_state.get("display_status") or runtime_state.get("status") or quest_yaml.get("status", "idle"),
            "runtime_status": runtime_state.get("status") or quest_yaml.get("status", "idle"),
            "display_status": runtime_state.get("display_status") or runtime_state.get("status") or quest_yaml.get("status", "idle"),
            "active_anchor": quest_yaml.get("active_anchor", "baseline"),
            "runner": quest_yaml.get("default_runner", "codex"),
            "active_workspace_root": str(workspace_root),
            "research_head_branch": research_state.get("research_head_branch"),
            "research_head_worktree_root": research_state.get("research_head_worktree_root"),
            "current_workspace_branch": research_state.get("current_workspace_branch"),
            "current_workspace_root": research_state.get("current_workspace_root"),
            "active_idea_id": research_state.get("active_idea_id"),
            "active_idea_md_path": research_state.get("active_idea_md_path"),
            "active_analysis_campaign_id": research_state.get("active_analysis_campaign_id"),
            "analysis_parent_branch": research_state.get("analysis_parent_branch"),
            "analysis_parent_worktree_root": research_state.get("analysis_parent_worktree_root"),
            "next_pending_slice_id": research_state.get("next_pending_slice_id"),
            "workspace_mode": research_state.get("workspace_mode") or "quest",
            "active_baseline_id": active_baseline_id,
            "active_baseline_variant_id": active_baseline_variant_id,
            "active_run_id": runtime_state.get("active_run_id") or quest_yaml.get("active_run_id"),
            "pending_decisions": pending_decisions,
            "active_interactions": active_interactions,
            "recent_reply_threads": recent_reply_threads,
            "waiting_interaction_id": waiting_interaction_id,
            "latest_thread_interaction_id": latest_thread_interaction_id,
            "default_reply_interaction_id": default_reply_interaction_id or latest_thread_interaction_id,
            "pending_user_message_count": int(runtime_state.get("pending_user_message_count") or 0),
            "stop_reason": runtime_state.get("stop_reason"),
            "active_interaction_id": runtime_state.get("active_interaction_id"),
            "last_transition_at": runtime_state.get("last_transition_at"),
            "last_artifact_interact_at": runtime_state.get("last_artifact_interact_at"),
            "last_delivered_batch_id": runtime_state.get("last_delivered_batch_id"),
            "last_delivered_at": runtime_state.get("last_delivered_at"),
            "bound_conversations": (read_json(quest_root / ".ds" / "bindings.json", {}).get("sources") or ["local:default"]),
            "created_at": quest_yaml.get("created_at"),
            "updated_at": quest_yaml.get("updated_at"),
            "branch": current_branch(workspace_root),
            "head": head_commit(workspace_root),
            "graph_svg_path": str(graph_svg) if graph_svg.exists() else None,
            "summary": {
                "status_line": status_line,
                "latest_metric": latest_metric,
                "latest_bash_session": latest_bash_session,
            },
            "paths": paths,
            "counts": counts,
            "team": {"mode": "single", "active_workers": []},
            "cloud": {"linked": False, "base_url": "https://deepscientist.cc"},
            "history_count": len(history),
            "artifact_count": len(artifacts),
            "recent_artifacts": artifacts[-5:],
            "recent_runs": recent_runs[-5:],
            "guidance": guidance,
        }

    def append_message(
        self,
        quest_id: str,
        role: str,
        content: str,
        source: str = "local",
        *,
        run_id: str | None = None,
        skill_id: str | None = None,
        reply_to_interaction_id: str | None = None,
        client_message_id: str | None = None,
    ) -> dict:
        quest_root = self._quest_root(quest_id)
        timestamp = utc_now()
        resolved_reply_to_interaction_id = str(reply_to_interaction_id or "").strip() or None
        record = {
            "id": generate_id("msg"),
            "role": role,
            "content": content,
            "source": source,
            "created_at": timestamp,
        }
        if run_id:
            record["run_id"] = run_id
        if skill_id:
            record["skill_id"] = skill_id
        if client_message_id:
            record["client_message_id"] = str(client_message_id)
        if role == "user":
            record["delivery_state"] = "sent"
        interaction_state_path = quest_root / ".ds" / "interaction_state.json"
        interaction_state = self._read_interaction_state(quest_root)
        open_requests: list[dict] = []
        waiting_indexes: list[int] = []
        target_index: int | None = None
        target_thread_index: int | None = None
        if role == "user":
            self.bind_source(quest_id, source)
            open_requests = list(interaction_state.get("open_requests") or [])
            recent_threads = [dict(item) for item in (interaction_state.get("recent_threads") or [])]
            waiting_indexes = [index for index, item in enumerate(open_requests) if str(item.get("status") or "") == "waiting"]
            if resolved_reply_to_interaction_id:
                for index in waiting_indexes:
                    item = open_requests[index]
                    if resolved_reply_to_interaction_id in self._interaction_candidate_ids(item):
                        target_index = index
                        break
                for index, item in enumerate(recent_threads):
                    if resolved_reply_to_interaction_id in self._interaction_candidate_ids(item):
                        target_thread_index = index
                        break
            else:
                default_reply_target = str(interaction_state.get("default_reply_interaction_id") or "").strip() or self._default_reply_interaction_id(
                    open_requests=open_requests,
                    recent_threads=recent_threads,
                )
                if default_reply_target:
                    resolved_reply_to_interaction_id = default_reply_target
                    for index in waiting_indexes:
                        if default_reply_target in self._interaction_candidate_ids(open_requests[index]):
                            target_index = index
                            break
                    for index, item in enumerate(recent_threads):
                        if default_reply_target in self._interaction_candidate_ids(item):
                            target_thread_index = index
                            break
        if resolved_reply_to_interaction_id:
            record["reply_to_interaction_id"] = resolved_reply_to_interaction_id
        append_jsonl(quest_root / ".ds" / "conversations" / "main.jsonl", record)
        if role == "user":
            recent_threads = [dict(item) for item in (interaction_state.get("recent_threads") or [])]
            if target_thread_index is None and resolved_reply_to_interaction_id:
                for index, item in enumerate(recent_threads):
                    if resolved_reply_to_interaction_id in self._interaction_candidate_ids(item):
                        target_thread_index = index
                        break
            if target_thread_index is not None:
                thread = dict(recent_threads[target_thread_index])
                thread["reply_count"] = int(thread.get("reply_count") or 0) + 1
                thread["last_reply_message_id"] = record["id"]
                thread["last_reply_preview"] = content[:240]
                thread["last_reply_at"] = timestamp
                thread["updated_at"] = timestamp
                if str(thread.get("reply_mode") or "") == "blocking":
                    thread["status"] = "answered"
                recent_threads[target_thread_index] = thread
            if target_index is not None:
                request = dict(open_requests[target_index])
                request["status"] = "answered"
                request["answered_at"] = timestamp
                request["reply_message_id"] = record["id"]
                request["reply_preview"] = content[:240]
                open_requests[target_index] = request
                interaction_state["open_requests"] = open_requests
                interaction_state["last_reply_message_id"] = record["id"]
                interaction_state["recent_threads"] = recent_threads[-30:]
                interaction_state["default_reply_interaction_id"] = self._default_reply_interaction_id(
                    open_requests=open_requests,
                    recent_threads=interaction_state["recent_threads"],
                )
                if resolved_reply_to_interaction_id:
                    interaction_state["latest_thread_interaction_id"] = resolved_reply_to_interaction_id
                write_json(interaction_state_path, interaction_state)
                append_jsonl(
                    quest_root / ".ds" / "events.jsonl",
                    {
                        "type": "interaction.reply_received",
                        "quest_id": quest_id,
                        "interaction_id": resolved_reply_to_interaction_id,
                        "message_id": record["id"],
                        "reply_to_interaction_id": resolved_reply_to_interaction_id,
                        "source": source,
                        "content": content,
                        "created_at": timestamp,
                    },
                )
            elif waiting_indexes or target_thread_index is not None:
                interaction_state["last_reply_message_id"] = record["id"]
                interaction_state["recent_threads"] = recent_threads[-30:]
                interaction_state["default_reply_interaction_id"] = self._default_reply_interaction_id(
                    open_requests=open_requests,
                    recent_threads=interaction_state["recent_threads"],
                )
                if resolved_reply_to_interaction_id:
                    interaction_state["latest_thread_interaction_id"] = resolved_reply_to_interaction_id
                write_json(interaction_state_path, interaction_state)
            if resolved_reply_to_interaction_id and target_index is None and target_thread_index is not None:
                append_jsonl(
                    quest_root / ".ds" / "events.jsonl",
                    {
                        "type": "interaction.reply_received",
                        "quest_id": quest_id,
                        "interaction_id": resolved_reply_to_interaction_id,
                        "message_id": record["id"],
                        "reply_to_interaction_id": resolved_reply_to_interaction_id,
                        "source": source,
                        "content": content,
                        "created_at": timestamp,
                    },
                )
        append_jsonl(
            quest_root / ".ds" / "events.jsonl",
            {
                "type": "conversation.message",
                "quest_id": quest_id,
                "message_id": record["id"],
                "role": role,
                "source": source,
                "content": content,
                "run_id": run_id,
                "skill_id": skill_id,
                "reply_to_interaction_id": resolved_reply_to_interaction_id,
                "client_message_id": record.get("client_message_id"),
                "delivery_state": record.get("delivery_state"),
                "created_at": timestamp,
            },
        )
        if role == "user":
            self._enqueue_user_message(quest_root, record)
            quest_data = read_yaml(quest_root / "quest.yaml", {})
            status = str(quest_data.get("status") or "")
            next_status = status
            if status == "waiting_for_user":
                interaction_state = read_json(quest_root / ".ds" / "interaction_state.json", {"open_requests": []})
                still_waiting = any(str(item.get("status") or "") == "waiting" for item in (interaction_state.get("open_requests") or []))
                if not still_waiting:
                    next_status = "running"
            elif status in {"stopped", "paused"}:
                next_status = "active"
            if next_status != status:
                self.update_runtime_state(
                    quest_root=quest_root,
                    status=next_status,
                    stop_reason=None,
                )
            else:
                self.update_runtime_state(
                    quest_root=quest_root,
                    pending_user_message_count=len((self._read_message_queue(quest_root).get("pending") or [])),
                )
        else:
            quest_data = read_yaml(quest_root / "quest.yaml", {})
            quest_data["updated_at"] = timestamp
            write_yaml(quest_root / "quest.yaml", quest_data)
        return record

    def mark_turn_started(self, quest_id: str, *, run_id: str, status: str = "running") -> dict:
        quest_root = self._quest_root(quest_id)
        self.update_runtime_state(
            quest_root=quest_root,
            status=status,
            active_run_id=run_id,
            stop_reason=None,
        )
        return self.snapshot(quest_id)

    def mark_turn_finished(
        self,
        quest_id: str,
        *,
        status: str | None = None,
        stop_reason: str | None | object = _UNSET,
    ) -> dict:
        quest_root = self._quest_root(quest_id)
        payload: dict[str, Any] = {
            "quest_root": quest_root,
            "active_run_id": None,
        }
        if status is not None:
            payload["status"] = status
        if stop_reason is not _UNSET:
            payload["stop_reason"] = stop_reason
        self.update_runtime_state(**payload)
        return self.snapshot(quest_id)

    def bind_source(self, quest_id: str, source: str) -> dict:
        quest_root = self._quest_root(quest_id)
        bindings_path = quest_root / ".ds" / "bindings.json"
        bindings = read_json(bindings_path, {"sources": []})
        normalized_source = self._normalize_binding_source(source)
        normalized_key = conversation_identity_key(normalized_source)
        changed = False
        replaced = False
        sources: list[str] = []
        for item in list(bindings.get("sources") or []):
            existing = self._normalize_binding_source(str(item))
            if conversation_identity_key(existing) == normalized_key:
                if not replaced:
                    sources.append(normalized_source)
                    replaced = True
                    if existing != normalized_source:
                        changed = True
                else:
                    changed = True
                continue
            sources.append(existing)
            if existing != item:
                changed = True
        if not replaced:
            sources.append(normalized_source)
            changed = True
        if changed:
            bindings["sources"] = sources
            write_json(bindings_path, bindings)
        return bindings

    def set_status(self, quest_id: str, status: str) -> dict:
        quest_root = self._quest_root(quest_id)
        self.update_runtime_state(
            quest_root=quest_root,
            status=status,
            stop_reason=None if status not in {"stopped", "paused"} else _UNSET,
        )
        return self.snapshot(quest_id)

    def update_settings(
        self,
        quest_id: str,
        *,
        title: str | None = None,
        active_anchor: str | None = None,
        default_runner: str | None = None,
    ) -> dict:
        quest_root = self._quest_root(quest_id)
        quest_yaml_path = quest_root / "quest.yaml"
        if not quest_yaml_path.exists():
            raise FileNotFoundError(f"Unknown quest `{quest_id}`.")

        quest_data = read_yaml(quest_yaml_path, {})
        changed = False

        if title is not None:
            normalized_title = str(title).strip()
            if not normalized_title:
                raise ValueError("Quest title cannot be empty.")
            if quest_data.get("title") != normalized_title:
                quest_data["title"] = normalized_title
                changed = True

        if active_anchor is not None:
            normalized_anchor = str(active_anchor).strip()
            if not normalized_anchor:
                raise ValueError("`active_anchor` cannot be empty.")
            from ..prompts.builder import STANDARD_SKILLS

            if normalized_anchor not in STANDARD_SKILLS:
                allowed = ", ".join(STANDARD_SKILLS)
                raise ValueError(f"Unsupported active anchor `{normalized_anchor}`. Allowed values: {allowed}.")
            if quest_data.get("active_anchor") != normalized_anchor:
                quest_data["active_anchor"] = normalized_anchor
                changed = True

        if default_runner is not None:
            normalized_runner = str(default_runner).strip().lower()
            if not normalized_runner:
                raise ValueError("`default_runner` cannot be empty.")
            from ..runners import list_runner_names

            available_runners = set(list_runner_names()) or {"codex"}
            if normalized_runner not in available_runners:
                allowed = ", ".join(sorted(available_runners))
                raise ValueError(f"Unsupported runner `{normalized_runner}`. Available runners: {allowed}.")
            if quest_data.get("default_runner") != normalized_runner:
                quest_data["default_runner"] = normalized_runner
                changed = True

        if changed:
            quest_data["updated_at"] = utc_now()
            write_yaml(quest_yaml_path, quest_data)

        return self.snapshot(quest_id)

    def reconcile_runtime_state(self) -> list[dict[str, Any]]:
        reconciled: list[dict[str, Any]] = []
        if not self.quests_root.exists():
            return reconciled
        for quest_yaml_path in sorted(self.quests_root.glob("*/quest.yaml")):
            quest_root = quest_yaml_path.parent
            quest_data = read_yaml(quest_yaml_path, {})
            runtime_state = self._read_runtime_state(quest_root)
            status = str(runtime_state.get("status") or quest_data.get("status") or "").strip()
            active_run_id = str(runtime_state.get("active_run_id") or quest_data.get("active_run_id") or "").strip()
            if not active_run_id and status != "running":
                continue
            previous_status = status or "running"
            self.update_runtime_state(
                quest_root=quest_root,
                status="stopped",
                active_run_id=None,
                stop_reason="crash_recovered",
            )
            summary = (
                f"Recovered quest from stale runtime state; previous status `{previous_status}`"
                + (f", abandoned run `{active_run_id}`." if active_run_id else ".")
            )
            append_jsonl(
                quest_root / ".ds" / "events.jsonl",
                {
                    "event_id": generate_id("evt"),
                    "type": "quest.runtime_reconciled",
                    "quest_id": quest_root.name,
                    "previous_status": previous_status,
                    "abandoned_run_id": active_run_id or None,
                    "status": "stopped",
                    "summary": summary,
                    "created_at": utc_now(),
                },
            )
            reconciled.append(
                {
                    "quest_id": quest_root.name,
                    "previous_status": previous_status,
                    "abandoned_run_id": active_run_id or None,
                    "status": "stopped",
                }
            )
        return reconciled

    def history(self, quest_id: str, limit: int = 100) -> list[dict]:
        return read_jsonl(self._quest_root(quest_id) / ".ds" / "conversations" / "main.jsonl")[-limit:]

    def workflow(self, quest_id: str) -> dict:
        snapshot = self.snapshot(quest_id)
        documents = self.list_documents(quest_id)
        entries: list[dict] = []
        changed_files: list[dict] = []
        seen_files: set[str] = set()

        def add_file(path: str | None, *, source: str, document_id: str | None = None, writable: bool | None = None) -> None:
            if not path:
                return
            normalized = str(path)
            if normalized in seen_files:
                return
            seen_files.add(normalized)
            changed_files.append(
                {
                    "path": normalized,
                    "source": source,
                    "document_id": document_id,
                    "writable": writable,
                }
            )

        for document in documents:
            if document.get("document_id") in {"brief.md", "plan.md", "status.md", "SUMMARY.md"}:
                add_file(
                    document.get("path"),
                    source="document",
                    document_id=document.get("document_id"),
                    writable=document.get("writable"),
                )

        recent_runs = snapshot.get("recent_runs") or []
        for run in recent_runs:
            run_id = str(run.get("run_id") or "run")
            entries.append(
                {
                    "id": f"run:{run_id}",
                    "kind": "run",
                    "run_id": run_id,
                    "skill_id": run.get("skill_id"),
                    "title": run_id,
                    "summary": run.get("summary") or "Run completed.",
                    "status": "completed" if run.get("exit_code", 0) == 0 else "failed",
                    "created_at": run.get("completed_at") or run.get("created_at") or run.get("updated_at"),
                    "paths": [item for item in [run.get("history_root"), run.get("run_root"), run.get("output_path")] if item],
                }
            )
            for path in (run.get("history_root"), run.get("run_root"), run.get("output_path")):
                add_file(path, source="run")
            history_root = run.get("history_root")
            if history_root:
                entries.extend(
                    _parse_codex_history(
                        Path(str(history_root)),
                        quest_id=quest_id,
                        run_id=run_id,
                        skill_id=run.get("skill_id"),
                    )
                )

        for artifact in snapshot.get("recent_artifacts") or []:
            payload = artifact.get("payload") or {}
            artifact_path = artifact.get("path")
            entries.append(
                {
                    "id": f"artifact:{payload.get('artifact_id') or artifact_path}",
                    "kind": "artifact",
                    "title": str(payload.get("artifact_id") or artifact.get("kind") or "artifact"),
                    "summary": payload.get("summary") or payload.get("message") or payload.get("reason") or "Artifact updated.",
                    "status": payload.get("status"),
                    "reason": payload.get("reason"),
                    "created_at": payload.get("updated_at"),
                    "paths": list((payload.get("paths") or {}).values()) + ([str(artifact_path)] if artifact_path else []),
                }
            )
            add_file(str(artifact_path) if artifact_path else None, source="artifact")
            for path in (payload.get("paths") or {}).values():
                add_file(str(path), source="artifact_path")

        entries.sort(key=lambda item: str(item.get("created_at") or item.get("id") or ""))
        return {
            "quest_id": quest_id,
            "quest_root": snapshot.get("quest_root"),
            "entries": entries[-80:],
            "changed_files": changed_files[-30:],
        }

    def events(self, quest_id: str, *, after: int = 0, limit: int = 200, tail: bool = False) -> dict:
        records = read_jsonl(self._quest_root(quest_id) / ".ds" / "events.jsonl")
        normalized_limit = max(limit, 0)
        if tail and normalized_limit > 0:
            start = max(len(records) - normalized_limit, 0)
        else:
            start = max(after, 0)
        sliced = records[start : start + normalized_limit]
        enriched = []
        for index, item in enumerate(sliced, start=start + 1):
            enriched.append(
                {
                    "cursor": index,
                    "event_id": item.get("event_id") or f"evt-{quest_id}-{index}",
                    **item,
                }
            )
        next_cursor = len(records) if tail else start + len(sliced)
        return {
            "quest_id": quest_id,
            "cursor": next_cursor,
            "has_more": start > 0 if tail else next_cursor < len(records),
            "events": enriched,
        }

    def artifacts(self, quest_id: str) -> dict:
        quest_root = self._quest_root(quest_id)
        return {
            "quest_id": quest_id,
            "items": self._collect_artifacts(quest_root),
        }

    def node_traces(self, quest_id: str, *, selection_type: str | None = None) -> dict:
        quest_root = self._quest_root(quest_id)
        workflow = self.workflow(quest_id)
        snapshot = self.snapshot(quest_id)
        payload = QuestNodeTraceManager(quest_root).materialize(
            quest_id=quest_id,
            workflow=workflow,
            snapshot=snapshot,
        )
        items = list(payload.get("items") or [])
        if selection_type:
            normalized = selection_type.strip()
            items = [item for item in items if str(item.get("selection_type") or "") == normalized]
        return {
            "quest_id": quest_id,
            "generated_at": payload.get("generated_at"),
            "materialized_path": str(QuestNodeTraceManager(quest_root).materialized_path),
            "items": items,
        }

    def node_trace(self, quest_id: str, selection_ref: str, *, selection_type: str | None = None) -> dict:
        payload = self.node_traces(quest_id, selection_type=selection_type)
        normalized_ref = str(selection_ref or "").strip()
        normalized_type = str(selection_type or "").strip()
        for item in payload.get("items") or []:
            item_ref = str(item.get("selection_ref") or "").strip()
            item_type = str(item.get("selection_type") or "").strip()
            if item_ref != normalized_ref:
                continue
            if normalized_type and item_type != normalized_type:
                continue
            return {
                "quest_id": quest_id,
                "generated_at": payload.get("generated_at"),
                "materialized_path": payload.get("materialized_path"),
                "trace": item,
            }
        raise FileNotFoundError(f"Unknown node trace `{selection_ref}`.")

    def metrics_timeline(self, quest_id: str) -> dict:
        quest_root = self._quest_root(quest_id)
        workspace_root = self.active_workspace_root(quest_root)
        attachment = self._active_baseline_attachment(quest_root, workspace_root)
        baseline_entry = dict(attachment.get("entry") or {}) if isinstance(attachment, dict) else None
        selected_variant_id = (
            str(attachment.get("source_variant_id") or "").strip() or None if isinstance(attachment, dict) else None
        )
        run_records = [
            item.get("payload") or {}
            for item in self._collect_artifacts(quest_root)
            if str((item.get("payload") or {}).get("kind") or "") == "run"
            and str((item.get("payload") or {}).get("run_kind") or "") == "main_experiment"
        ]
        return build_metrics_timeline(
            quest_id=quest_id,
            run_records=[item for item in run_records if isinstance(item, dict)],
            baseline_entry=baseline_entry,
            selected_variant_id=selected_variant_id,
        )

    def list_documents(self, quest_id: str) -> list[dict]:
        quest_root = self._quest_root(quest_id)
        workspace_root = self.active_workspace_root(quest_root)
        documents = []
        for relative in ("brief.md", "plan.md", "status.md", "SUMMARY.md"):
            path = workspace_root / relative
            documents.append(
                {
                    "document_id": relative,
                    "title": relative,
                    "path": str(path),
                    "kind": "markdown",
                    "writable": True,
                    "source_scope": "quest",
                }
            )
        for path in sorted((workspace_root / "memory").glob("**/*.md")):
            relative = path.relative_to(workspace_root / "memory").as_posix()
            documents.append(
                {
                    "document_id": f"memory::{relative}",
                    "title": path.name,
                    "path": str(path),
                    "kind": "markdown",
                    "writable": True,
                    "source_scope": "quest_memory",
                }
            )
        skills_root = repo_root() / "src" / "skills"
        for skill_md in sorted(skills_root.glob("*/SKILL.md")):
            if skill_md.parent.name.startswith("."):
                continue
            relative = skill_md.relative_to(skills_root).as_posix()
            documents.append(
                {
                    "document_id": f"skill::{relative}",
                    "title": relative,
                    "path": str(skill_md),
                    "kind": "markdown",
                    "writable": False,
                    "source_scope": "skill",
                }
            )
        return documents

    def explorer(self, quest_id: str, revision: str | None = None, mode: str | None = None) -> dict:
        if revision:
            return self._revision_explorer(quest_id, revision=revision, mode=mode or "ref")

        quest_root = self._quest_root(quest_id)
        workspace_root = self.active_workspace_root(quest_root)
        changed_paths = {
            item["path"]: item
            for item in self.workflow(quest_id).get("changed_files", [])
            if item.get("path")
        }
        git_status = self._git_status_map(workspace_root)

        root_nodes = self._tree_children(
            workspace_root,
            workspace_root,
            git_status=git_status,
            changed_paths=changed_paths,
        )
        sections = self._group_explorer_sections(root_nodes)

        return {
            "quest_id": quest_id,
            "quest_root": str(workspace_root.resolve()),
            "view": {
                "mode": "live",
                "revision": None,
                "label": "Latest",
                "read_only": False,
            },
            "sections": sections,
        }

    def search_files(self, quest_id: str, term: str, limit: int = 50) -> dict[str, Any]:
        query = term.strip()
        normalized_query = query.casefold()
        workspace_root = self.active_workspace_root(self._quest_root(quest_id))
        resolved_limit = max(1, min(limit, 200))
        if not normalized_query:
            return {
                "quest_id": quest_id,
                "query": query,
                "items": [],
                "limit": resolved_limit,
                "truncated": False,
                "files_scanned": 0,
            }

        items: list[dict[str, Any]] = []
        files_scanned = 0
        truncated = False
        max_file_size = 1_000_000

        for path in sorted(workspace_root.rglob("*")):
            try:
                if not path.is_file() or self._skip_explorer_path(workspace_root, path):
                    continue
            except OSError:
                continue

            renderer_hint, mime_type = self._renderer_hint_for(path)
            if not self._is_text_document(path, mime_type, renderer_hint):
                continue

            try:
                size_bytes = path.stat().st_size
            except OSError:
                continue
            if size_bytes > max_file_size:
                continue

            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
            except OSError:
                continue

            files_scanned += 1
            relative = path.relative_to(workspace_root).as_posix()
            scope, writable = self._classify_path_scope(workspace_root, path)

            for line_index, line in enumerate(content.splitlines(), start=1):
                haystack = line.casefold()
                if normalized_query not in haystack:
                    continue
                match_spans: list[dict[str, int]] = []
                start = 0
                while True:
                    found = haystack.find(normalized_query, start)
                    if found < 0:
                        break
                    match_spans.append({"start": found, "end": found + len(query)})
                    start = found + max(1, len(query))

                snippet = line.strip() or line
                items.append(
                    {
                        "id": f"{relative}:{line_index}",
                        "document_id": f"path::{relative}",
                        "title": path.name,
                        "path": relative,
                        "scope": scope,
                        "writable": writable,
                        "line_number": line_index,
                        "line_text": line,
                        "snippet": snippet[:320],
                        "match_spans": match_spans,
                        "open_kind": renderer_hint,
                        "mime_type": mime_type,
                    }
                )
                if len(items) >= resolved_limit:
                    truncated = True
                    break
            if truncated:
                break

        return {
            "quest_id": quest_id,
            "query": query,
            "items": items,
            "limit": resolved_limit,
            "truncated": truncated,
            "files_scanned": files_scanned,
        }

    def open_document(self, quest_id: str, document_id: str) -> dict:
        quest_root = self._quest_root(quest_id)
        workspace_root = self.active_workspace_root(quest_root)
        if document_id.startswith("git::"):
            revision, relative = self._parse_git_document_id(document_id)
            if not self._git_revision_exists(quest_root, revision):
                raise FileNotFoundError(f"Unknown git revision `{revision}`.")
            renderer_hint, mime_type = self._renderer_hint_for(Path(relative))
            is_text = self._is_text_document(Path(relative), mime_type, renderer_hint)
            content = self._read_git_text(quest_root, revision, relative) if is_text else ""
            blob_id = self._git_blob_id(quest_root, revision, relative)
            size_bytes = self._git_blob_size(quest_root, revision, relative)
            return {
                "document_id": document_id,
                "quest_id": quest_id,
                "title": Path(relative).name,
                "path": relative,
                "kind": "markdown" if renderer_hint == "markdown" else renderer_hint,
                "scope": "git_snapshot",
                "writable": False,
                "encoding": "utf-8" if is_text else None,
                "source_scope": "git_snapshot",
                "content": content,
                "revision": f"git:{revision}:{blob_id or sha256_text(content)}",
                "updated_at": utc_now(),
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "asset_url": f"/api/quests/{quest_id}/documents/asset?document_id={quote(document_id, safe='')}",
                "meta": {
                    "tags": [Path(relative).stem],
                    "source_kind": "git_snapshot",
                    "renderer_hint": renderer_hint,
                    "git_revision": revision,
                    "git_path": relative,
                },
            }

        path, writable, scope, source_kind = self._resolve_document(workspace_root, document_id)
        renderer_hint, mime_type = self._renderer_hint_for(path)
        is_text = self._is_text_document(path, mime_type, renderer_hint)
        content = read_text(path) if is_text else ""
        revision = f"sha256:{sha256_text(content)}" if is_text else f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        return {
            "document_id": document_id,
            "quest_id": quest_id,
            "title": path.name if "::" in document_id else document_id,
            "path": str(path),
            "kind": "markdown" if renderer_hint == "markdown" else renderer_hint,
            "scope": scope,
            "writable": writable,
            "encoding": "utf-8" if is_text else None,
            "source_scope": source_kind,
            "content": content,
            "revision": revision,
            "updated_at": utc_now(),
            "mime_type": mime_type,
            "size_bytes": path.stat().st_size,
            "asset_url": f"/api/quests/{quest_id}/documents/asset?document_id={quote(document_id, safe='')}",
            "meta": {
                "tags": [path.stem],
                "source_kind": source_kind,
                "renderer_hint": renderer_hint,
            },
        }

    def save_document(self, quest_id: str, document_id: str, content: str, previous_revision: str | None = None) -> dict:
        current = self.open_document(quest_id, document_id)
        if not current.get("writable", False):
            return {
                "ok": False,
                "conflict": False,
                "message": "Document is read-only.",
                "document_id": document_id,
                "saved_at": utc_now(),
                "updated_payload": current,
            }
        current_revision = current["revision"]
        if previous_revision and previous_revision != current_revision:
            return {
                "ok": False,
                "conflict": True,
                "message": "Document changed since it was opened.",
                "current_revision": current_revision,
                "document_id": document_id,
                "saved_at": utc_now(),
                "updated_payload": current,
            }
        path = Path(current["path"])
        write_text(path, content)
        new_revision = f"sha256:{sha256_text(content)}"
        return {
            "ok": True,
            "document_id": document_id,
            "quest_id": quest_id,
            "conflict": False,
            "path": str(path),
            "saved_at": utc_now(),
            "revision": new_revision,
            "updated_payload": self.open_document(quest_id, document_id),
        }

    @staticmethod
    def _document_relative_path(document_id: str) -> tuple[str | None, str | None]:
        if document_id.startswith("git::"):
            _prefix, revision, relative = (document_id.split("::", 2) + ["", "", ""])[:3]
            return relative.lstrip("/") or None, revision or None
        if document_id.startswith("path::"):
            return document_id.split("::", 1)[1].lstrip("/") or None, None
        if document_id.startswith("memory::"):
            relative = document_id.split("::", 1)[1].lstrip("/")
            return f"memory/{relative}" if relative else None, None
        if document_id.startswith("skill::"):
            return None, None
        if "/" in document_id or document_id.startswith("."):
            return None, None
        return document_id, None

    @staticmethod
    def _markdown_asset_directory(relative_path: str) -> PurePosixPath:
        base_path = PurePosixPath(relative_path)
        return base_path.parent / f"{base_path.stem}.assets"

    @staticmethod
    def _relative_path_from_base(base_file: str, target_path: str) -> str:
        base_dir_parts = PurePosixPath(base_file).parent.parts
        target_parts = PurePosixPath(target_path).parts
        common = 0
        max_common = min(len(base_dir_parts), len(target_parts))
        while common < max_common and base_dir_parts[common] == target_parts[common]:
            common += 1
        up_parts = [".."] * (len(base_dir_parts) - common)
        down_parts = list(target_parts[common:])
        joined = "/".join([*up_parts, *down_parts]).strip("/")
        return joined or PurePosixPath(target_path).name

    def save_document_asset(
        self,
        quest_id: str,
        document_id: str,
        *,
        file_name: str,
        mime_type: str | None,
        content: bytes,
        kind: str = "image",
    ) -> dict:
        quest_root = self._quest_root(quest_id)
        workspace_root = self.active_workspace_root(quest_root)
        current = self.open_document(quest_id, document_id)
        if not current.get("writable", False):
            return {
                "ok": False,
                "message": "Document is read-only.",
                "document_id": document_id,
            }
        base_relative, revision = self._document_relative_path(document_id)
        if revision:
            return {
                "ok": False,
                "message": "Cannot upload assets into a git snapshot document.",
                "document_id": document_id,
            }
        if not base_relative:
            return {
                "ok": False,
                "message": "Document path is required for asset uploads.",
                "document_id": document_id,
            }
        base_path = Path(str(current.get("path") or ""))
        suffix = base_path.suffix.lower()
        if suffix not in {".md", ".markdown", ".mdx"}:
            return {
                "ok": False,
                "message": "Assets can only be attached to markdown documents.",
                "document_id": document_id,
            }
        original_name = Path(file_name).name
        original_suffix = Path(original_name).suffix.lower()
        guessed_suffix = mimetypes.guess_extension(mime_type or "") or ""
        asset_suffix = original_suffix or guessed_suffix or ".bin"
        if asset_suffix == ".jpe":
            asset_suffix = ".jpg"
        safe_stem = slugify(Path(original_name).stem or kind, default=kind)
        asset_name = f"{safe_stem}-{generate_id('asset').split('-', 1)[1]}{asset_suffix}"
        asset_relative_dir = self._markdown_asset_directory(base_relative)
        asset_relative = (asset_relative_dir / asset_name).as_posix()
        asset_path = resolve_within(workspace_root, asset_relative)
        ensure_dir(asset_path.parent)
        asset_path.write_bytes(content)
        asset_document_id = f"path::{asset_relative}"
        relative_markdown_path = self._relative_path_from_base(base_relative, asset_relative)
        return {
            "ok": True,
            "quest_id": quest_id,
            "document_id": document_id,
            "asset_document_id": asset_document_id,
            "asset_path": str(asset_path),
            "relative_path": relative_markdown_path,
            "asset_url": f"/api/quests/{quest_id}/documents/asset?document_id={quote(asset_document_id, safe='')}",
            "mime_type": mimetypes.guess_type(asset_path.name)[0] or mime_type or "application/octet-stream",
            "kind": kind,
            "saved_at": utc_now(),
        }

    def _revision_explorer(self, quest_id: str, *, revision: str, mode: str) -> dict:
        quest_root = self._quest_root(quest_id)
        if not self._git_revision_exists(quest_root, revision):
            raise FileNotFoundError(f"Unknown git revision `{revision}`.")

        snapshot_paths = self._git_snapshot_paths(quest_root, revision)
        snapshot_tree = self._build_snapshot_tree(snapshot_paths)
        root_nodes = self._snapshot_children(snapshot_tree, revision=revision, prefix="")
        sections = self._group_explorer_sections(root_nodes)

        return {
            "quest_id": quest_id,
            "quest_root": str(quest_root.resolve()),
            "view": {
                "mode": mode,
                "revision": revision,
                "label": revision,
                "read_only": True,
            },
            "sections": sections,
        }

    @staticmethod
    def _group_explorer_sections(nodes: list[dict]) -> list[dict]:
        section_titles = {
            "core": "Core",
            "memory": "Memory",
            "research": "Research",
            "artifacts": "Artifacts",
            "runtime": "Runtime",
            "runner_history": "Runner History",
            "quest": "Quest",
        }
        order = ["core", "memory", "research", "artifacts", "quest", "runtime", "runner_history"]
        grouped: dict[str, list[dict]] = {key: [] for key in order}
        extra_order: list[str] = []

        for node in nodes:
            section_id = str(node.get("scope") or "quest")
            if section_id not in grouped:
                grouped[section_id] = []
                extra_order.append(section_id)
            grouped[section_id].append(node)

        sections: list[dict] = []
        for section_id in [*order, *extra_order]:
            bucket = [item for item in grouped.get(section_id, []) if item is not None]
            if not bucket:
                continue
            sections.append(
                {
                    "id": section_id,
                    "title": section_titles.get(section_id, section_id.replace("_", " ").title()),
                    "nodes": bucket,
                }
            )
        return sections

    @staticmethod
    def _normalize_binding_source(source: str) -> str:
        return normalize_conversation_id(source)

    @staticmethod
    def _interaction_candidate_ids(item: dict[str, Any]) -> set[str]:
        return {
            str(item.get("interaction_id") or "").strip(),
            str(item.get("artifact_id") or "").strip(),
        } - {""}

    @staticmethod
    def _default_reply_interaction_id(
        *,
        open_requests: list[dict[str, Any]],
        recent_threads: list[dict[str, Any]],
    ) -> str | None:
        for item in reversed(open_requests):
            if str(item.get("status") or "") != "waiting":
                continue
            interaction_id = str(item.get("interaction_id") or item.get("artifact_id") or "").strip()
            if interaction_id:
                return interaction_id
        for item in reversed(recent_threads):
            if str(item.get("reply_mode") or "") not in {"threaded", "blocking"}:
                continue
            if str(item.get("status") or "") in {"closed", "superseded"}:
                continue
            interaction_id = str(item.get("interaction_id") or item.get("artifact_id") or "").strip()
            if interaction_id:
                return interaction_id
        return None

    @staticmethod
    def _latest_waiting_interaction_id(open_requests: list[dict[str, Any]]) -> str | None:
        for item in reversed(open_requests):
            if str(item.get("status") or "") != "waiting":
                continue
            interaction_id = str(item.get("interaction_id") or item.get("artifact_id") or "").strip()
            if interaction_id:
                return interaction_id
        return None

    @staticmethod
    def _read_interaction_state(quest_root: Path) -> dict[str, Any]:
        state = read_json(quest_root / ".ds" / "interaction_state.json", {})
        state.setdefault("open_requests", [])
        state.setdefault("recent_threads", [])
        return state

    @staticmethod
    def _runtime_state_path(quest_root: Path) -> Path:
        return quest_root / ".ds" / "runtime_state.json"

    @staticmethod
    def _agent_status_path(quest_root: Path) -> Path:
        return quest_root / ".ds" / "agent_status.json"

    @staticmethod
    def _message_queue_path(quest_root: Path) -> Path:
        return quest_root / ".ds" / "user_message_queue.json"

    @staticmethod
    def _interaction_journal_path(quest_root: Path) -> Path:
        return quest_root / ".ds" / "interaction_journal.jsonl"

    @staticmethod
    def _default_message_queue() -> dict[str, Any]:
        return {
            "version": 1,
            "pending": [],
            "completed": [],
        }

    def _default_runtime_state(self, quest_root: Path) -> dict[str, Any]:
        quest_yaml = read_yaml(quest_root / "quest.yaml", {})
        queue_payload = read_json(self._message_queue_path(quest_root), self._default_message_queue())
        pending_count = len((queue_payload or {}).get("pending") or [])
        timestamp = quest_yaml.get("updated_at") or quest_yaml.get("created_at") or utc_now()
        status = str(quest_yaml.get("status") or "idle")
        return {
            "quest_id": str(quest_yaml.get("quest_id") or quest_root.name),
            "status": status,
            "display_status": status,
            "active_run_id": quest_yaml.get("active_run_id"),
            "active_interaction_id": None,
            "stop_reason": None,
            "last_transition_at": timestamp,
            "last_artifact_interact_at": None,
            "pending_user_message_count": pending_count,
            "last_delivered_batch_id": None,
            "last_delivered_at": None,
        }

    def _default_agent_status(self, quest_root: Path) -> dict[str, Any]:
        quest_yaml = read_yaml(quest_root / "quest.yaml", {})
        timestamp = quest_yaml.get("updated_at") or quest_yaml.get("created_at") or utc_now()
        return {
            "version": 1,
            "quest_id": str(quest_yaml.get("quest_id") or quest_root.name),
            "state": "idle",
            "comment": "",
            "current_focus": "",
            "next_action": "",
            "plan_items": [],
            "related_paths": [],
            "updated_at": timestamp,
        }

    def _initialize_runtime_files(self, quest_root: Path) -> None:
        queue_path = self._message_queue_path(quest_root)
        if not queue_path.exists():
            write_json(queue_path, self._default_message_queue())
        runtime_path = self._runtime_state_path(quest_root)
        if not runtime_path.exists():
            write_json(runtime_path, self._default_runtime_state(quest_root))
        research_state_path = self._research_state_path(quest_root)
        if not research_state_path.exists():
            write_json(research_state_path, self._default_research_state(quest_root))
        agent_status_path = self._agent_status_path(quest_root)
        if not agent_status_path.exists():
            write_json(agent_status_path, self._default_agent_status(quest_root))

    def _read_message_queue(self, quest_root: Path) -> dict[str, Any]:
        payload = read_json(self._message_queue_path(quest_root), self._default_message_queue())
        if not isinstance(payload, dict):
            payload = self._default_message_queue()
        payload.setdefault("version", 1)
        payload.setdefault("pending", [])
        payload.setdefault("completed", [])
        return payload

    def _write_message_queue(self, quest_root: Path, payload: dict[str, Any]) -> None:
        write_json(self._message_queue_path(quest_root), payload)

    def _read_runtime_state(self, quest_root: Path) -> dict[str, Any]:
        self._initialize_runtime_files(quest_root)
        payload = read_json(self._runtime_state_path(quest_root), self._default_runtime_state(quest_root))
        if not isinstance(payload, dict):
            payload = self._default_runtime_state(quest_root)
        defaults = self._default_runtime_state(quest_root)
        merged = {**defaults, **payload}
        merged["pending_user_message_count"] = int(merged.get("pending_user_message_count") or 0)
        return merged

    def _write_runtime_state(self, quest_root: Path, payload: dict[str, Any]) -> None:
        write_json(self._runtime_state_path(quest_root), payload)

    def update_runtime_state(
        self,
        *,
        quest_root: Path,
        status: str | object = _UNSET,
        active_run_id: str | None | object = _UNSET,
        stop_reason: str | None | object = _UNSET,
        active_interaction_id: str | None | object = _UNSET,
        last_transition_at: str | None | object = _UNSET,
        last_artifact_interact_at: str | None | object = _UNSET,
        pending_user_message_count: int | object = _UNSET,
        last_delivered_batch_id: str | None | object = _UNSET,
        last_delivered_at: str | None | object = _UNSET,
        display_status: str | None | object = _UNSET,
    ) -> dict[str, Any]:
        state = self._read_runtime_state(quest_root)
        now = utc_now()
        status_changed = False
        run_changed = False

        if status is not _UNSET:
            normalized_status = str(status or state.get("status") or "idle")
            state["status"] = normalized_status
            status_changed = True
            if display_status is _UNSET:
                state["display_status"] = normalized_status
        if display_status is not _UNSET:
            state["display_status"] = str(display_status or state.get("status") or "idle")
        if active_run_id is not _UNSET:
            state["active_run_id"] = str(active_run_id).strip() if active_run_id else None
            run_changed = True
        if stop_reason is not _UNSET:
            state["stop_reason"] = str(stop_reason).strip() if stop_reason else None
        elif status is not _UNSET and str(state.get("status") or "") not in {"stopped", "paused", "error"}:
            state["stop_reason"] = None
        if active_interaction_id is not _UNSET:
            state["active_interaction_id"] = str(active_interaction_id).strip() if active_interaction_id else None
        if last_artifact_interact_at is not _UNSET:
            state["last_artifact_interact_at"] = last_artifact_interact_at
        if pending_user_message_count is not _UNSET:
            state["pending_user_message_count"] = max(0, int(pending_user_message_count))
        if last_delivered_batch_id is not _UNSET:
            state["last_delivered_batch_id"] = str(last_delivered_batch_id).strip() if last_delivered_batch_id else None
        if last_delivered_at is not _UNSET:
            state["last_delivered_at"] = last_delivered_at
        if last_transition_at is not _UNSET:
            state["last_transition_at"] = last_transition_at
        elif status_changed or run_changed:
            state["last_transition_at"] = now

        self._write_runtime_state(quest_root, state)

        if status_changed or run_changed:
            quest_data = read_yaml(quest_root / "quest.yaml", {})
            if status is not _UNSET:
                quest_data["status"] = state["status"]
            if active_run_id is not _UNSET:
                if state.get("active_run_id"):
                    quest_data["active_run_id"] = state["active_run_id"]
                else:
                    quest_data.pop("active_run_id", None)
            quest_data["updated_at"] = now
            write_yaml(quest_root / "quest.yaml", quest_data)
        return state

    def _enqueue_user_message(self, quest_root: Path, record: dict[str, Any]) -> dict[str, Any]:
        queue_payload = self._read_message_queue(quest_root)
        source = str(record.get("source") or "local")
        queue_record = {
            "message_id": record.get("id"),
            "source": source,
            "conversation_id": self._normalize_binding_source(source),
            "content": record.get("content") or "",
            "created_at": record.get("created_at"),
            "reply_to_interaction_id": record.get("reply_to_interaction_id"),
            "status": "queued",
        }
        queue_payload["pending"] = [*list(queue_payload.get("pending") or []), queue_record]
        self._write_message_queue(quest_root, queue_payload)
        self.update_runtime_state(
            quest_root=quest_root,
            pending_user_message_count=len(queue_payload["pending"]),
        )
        append_jsonl(
            self._interaction_journal_path(quest_root),
            {
                "event_id": generate_id("evt"),
                "type": "user_inbound",
                "quest_id": quest_root.name,
                **queue_record,
            },
        )
        return queue_record

    def claim_pending_user_message_for_turn(
        self,
        quest_id: str,
        *,
        message_id: str | None,
        run_id: str,
    ) -> dict[str, Any] | None:
        normalized_message_id = str(message_id or "").strip()
        if not normalized_message_id:
            return None
        quest_root = self._quest_root(quest_id)
        queue_payload = self._read_message_queue(quest_root)
        pending = [dict(item) for item in (queue_payload.get("pending") or [])]
        target_index: int | None = None
        for index in range(len(pending) - 1, -1, -1):
            if str(pending[index].get("message_id") or "").strip() == normalized_message_id:
                target_index = index
                break
        if target_index is None:
            self.update_runtime_state(
                quest_root=quest_root,
                pending_user_message_count=len(pending),
            )
            return None

        now = utc_now()
        claimed = {
            **pending.pop(target_index),
            "status": "accepted_by_run",
            "claimed_by_run_id": run_id,
            "claimed_at": now,
        }
        queue_payload["pending"] = pending
        queue_payload["completed"] = [*list(queue_payload.get("completed") or []), claimed][-200:]
        self._write_message_queue(quest_root, queue_payload)
        self.update_runtime_state(
            quest_root=quest_root,
            pending_user_message_count=len(pending),
        )
        append_jsonl(
            self._interaction_journal_path(quest_root),
            {
                "event_id": generate_id("evt"),
                "type": "user_claimed_for_turn",
                "quest_id": quest_id,
                "message_id": normalized_message_id,
                "run_id": run_id,
                "created_at": now,
            },
        )
        return claimed

    def cancel_pending_user_messages(
        self,
        quest_id: str,
        *,
        reason: str,
        action: str,
        source: str,
    ) -> dict[str, Any]:
        quest_root = self._quest_root(quest_id)
        queue_payload = self._read_message_queue(quest_root)
        pending = [dict(item) for item in (queue_payload.get("pending") or [])]
        if not pending:
            self.update_runtime_state(
                quest_root=quest_root,
                pending_user_message_count=0,
            )
            return {
                "batch_id": None,
                "cancelled_count": 0,
                "cancelled": [],
            }

        now = utc_now()
        batch_id = generate_id("cancel")
        cancelled = [
            {
                **item,
                "status": reason,
                "cancelled_at": now,
                "cancelled_by_action": action,
                "cancelled_by_source": source,
            }
            for item in pending
        ]
        queue_payload["pending"] = []
        queue_payload["completed"] = [*list(queue_payload.get("completed") or []), *cancelled][-200:]
        self._write_message_queue(quest_root, queue_payload)
        append_jsonl(
            self._interaction_journal_path(quest_root),
            {
                "event_id": generate_id("evt"),
                "type": "user_queue_cancelled",
                "quest_id": quest_id,
                "batch_id": batch_id,
                "reason": reason,
                "action": action,
                "source": source,
                "message_ids": [item.get("message_id") for item in cancelled],
                "created_at": now,
            },
        )
        self.update_runtime_state(
            quest_root=quest_root,
            pending_user_message_count=0,
        )
        return {
            "batch_id": batch_id,
            "cancelled_count": len(cancelled),
            "cancelled": cancelled,
        }

    def record_artifact_interaction(
        self,
        quest_root: Path,
        *,
        interaction_id: str | None,
        artifact_id: str | None,
        kind: str,
        message: str,
        response_phase: str | None = None,
        reply_mode: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        timestamp = created_at or utc_now()
        payload = {
            "event_id": generate_id("evt"),
            "type": "artifact_outbound",
            "quest_id": quest_root.name,
            "interaction_id": interaction_id,
            "artifact_id": artifact_id,
            "kind": kind,
            "message": message,
            "response_phase": response_phase,
            "reply_mode": reply_mode,
            "created_at": timestamp,
        }
        append_jsonl(self._interaction_journal_path(quest_root), payload)
        self.update_runtime_state(
            quest_root=quest_root,
            active_interaction_id=interaction_id or artifact_id,
            last_artifact_interact_at=timestamp,
            pending_user_message_count=len((self._read_message_queue(quest_root).get("pending") or [])),
        )
        return payload

    def latest_artifact_interaction_records(self, quest_root: Path, limit: int = 10) -> list[dict[str, Any]]:
        items = [
            item
            for item in read_jsonl(self._interaction_journal_path(quest_root))
            if str(item.get("type") or "") in {"user_inbound", "artifact_outbound"}
        ]
        return items[-max(limit, 0):]

    def consume_pending_user_messages(
        self,
        quest_root: Path,
        *,
        interaction_id: str | None,
        limit: int = 10,
    ) -> dict[str, Any]:
        queue_payload = self._read_message_queue(quest_root)
        pending = [dict(item) for item in (queue_payload.get("pending") or [])]
        recent_records = self.latest_artifact_interaction_records(quest_root, limit=max(limit, 10))
        delivered_messages: list[dict[str, Any]] = []
        delivery_batch = None
        now = utc_now()

        if pending:
            batch_id = generate_id("delivery")
            for item in pending:
                delivered = {
                    **item,
                    "status": "completed",
                    "delivered_batch_id": batch_id,
                    "delivered_at": now,
                    "delivered_to_interaction_id": interaction_id,
                }
                delivered_messages.append(delivered)
            queue_payload["pending"] = []
            queue_payload["completed"] = [*list(queue_payload.get("completed") or []), *delivered_messages][-200:]
            self._write_message_queue(quest_root, queue_payload)
            append_jsonl(
                self._interaction_journal_path(quest_root),
                {
                    "event_id": generate_id("evt"),
                    "type": "user_delivery",
                    "quest_id": quest_root.name,
                    "batch_id": batch_id,
                    "interaction_id": interaction_id,
                    "message_ids": [item.get("message_id") for item in delivered_messages],
                    "created_at": now,
                },
            )
            self.update_runtime_state(
                quest_root=quest_root,
                pending_user_message_count=0,
                last_delivered_batch_id=batch_id,
                last_delivered_at=now,
            )
            delivery_batch = {
                "batch_id": batch_id,
                "message_ids": [item.get("message_id") for item in delivered_messages],
            }
        else:
            self.update_runtime_state(
                quest_root=quest_root,
                pending_user_message_count=0,
            )

        recent_inbound_messages = [
            {
                "message_id": item.get("message_id"),
                "source": str(item.get("conversation_id") or item.get("source") or "local").split(":", 1)[0],
                "conversation_id": item.get("conversation_id") or self._normalize_binding_source(str(item.get("source") or "local")),
                "sender": "user",
                "created_at": item.get("created_at"),
                "text": item.get("content") or "",
                "content": item.get("content") or "",
                "reply_to_interaction_id": item.get("reply_to_interaction_id"),
            }
            for item in delivered_messages
        ]
        if delivered_messages:
            lines = [
                "这是最新用户的要求（按时间顺序拼接）。在确保完成用户之前要求的前提下，完成当前的任务：",
                "",
            ]
            for index, item in enumerate(delivered_messages, start=1):
                source = str(item.get("conversation_id") or item.get("source") or "local")
                lines.append(f"{index}. [{source}] {item.get('content') or ''}")
            agent_instruction = "\n".join(lines).strip()
        else:
            lines = [
                "当前用户并没有发送任何消息，请按照用户的要求继续进行任务。",
                "",
                "以下是最近 10 次与 artifact 交互相关的记录：",
            ]
            if recent_records:
                for index, item in enumerate(recent_records[-10:], start=1):
                    kind = str(item.get("type") or "")
                    created_at = str(item.get("created_at") or "")
                    if kind == "artifact_outbound":
                        lines.append(
                            f"{index}. [artifact][{item.get('kind') or 'progress'}][{created_at}] {item.get('message') or ''}"
                        )
                    else:
                        lines.append(
                            f"{index}. [user][{item.get('conversation_id') or item.get('source') or 'local'}][{created_at}] {item.get('content') or ''}"
                        )
            else:
                lines.append("1. 暂无历史交互记录。")
            agent_instruction = "\n".join(lines).strip()

        return {
            "delivery_batch": delivery_batch,
            "recent_inbound_messages": recent_inbound_messages,
            "recent_interaction_records": recent_records[-10:],
            "agent_instruction": agent_instruction,
            "queued_message_count_before_delivery": len(pending),
            "queued_message_count_after_delivery": len(queue_payload.get("pending") or []),
        }

    @staticmethod
    def _resolve_document(quest_root: Path, document_id: str) -> tuple[Path, bool, str, str]:
        if document_id.startswith("memory::"):
            relative = document_id.split("::", 1)[1]
            if relative.startswith("."):
                raise ValueError("Document ID must stay within quest memory.")
            root = (quest_root / "memory").resolve()
            path = (root / relative).resolve()
            if path != root and root not in path.parents:
                raise ValueError("Document ID escapes quest memory.")
            return path, True, "quest_memory", "quest_memory"
        if document_id.startswith("skill::"):
            relative = document_id.split("::", 1)[1]
            root = (repo_root() / "src" / "skills").resolve()
            path = (root / relative).resolve()
            if path != root and root not in path.parents:
                raise ValueError("Document ID escapes skills root.")
            return path, False, "skill", "skill"
        if document_id.startswith("path::"):
            relative = document_id.split("::", 1)[1]
            path = resolve_within(quest_root, relative)
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"Unknown quest file `{relative}`.")
            scope, writable = QuestService._classify_path_scope(quest_root, path)
            return path, writable, scope, scope
        if "/" in document_id or document_id.startswith("."):
            raise ValueError("Document ID must be a simple curated file name.")
        return quest_root / document_id, True, "quest", "quest_file"

    def _collect_nodes(
        self,
        quest_root: Path,
        *,
        roots: list[str],
        git_status: dict[str, str],
        changed_paths: dict[str, dict],
    ) -> list[dict]:
        nodes: list[dict] = []
        for relative in roots:
            root = quest_root / relative
            if not root.exists():
                continue
            if root.is_file():
                node = self._file_node(quest_root, path=root, git_status=git_status, changed_paths=changed_paths)
                if node is not None:
                    nodes.append(node)
                continue
            nodes.extend(self._tree_children(quest_root, root, git_status=git_status, changed_paths=changed_paths))
        return nodes

    def _snapshot_children(
        self,
        tree: dict[str, dict | None],
        *,
        revision: str,
        prefix: str,
    ) -> list[dict]:
        prefix_parts = PurePosixPath(prefix).parts
        subtree = tree
        for part in prefix_parts:
            child = subtree.get(part)
            if not isinstance(child, dict):
                return []
            subtree = child
        return self._snapshot_tree_nodes(subtree, revision=revision, prefix=prefix)

    def _snapshot_tree_nodes(
        self,
        tree: dict[str, dict | None],
        *,
        revision: str,
        prefix: str,
    ) -> list[dict]:
        nodes: list[dict] = []
        for name, child in sorted(tree.items(), key=lambda item: (item[1] is None, item[0].lower())):
            relative = f"{prefix}/{name}" if prefix else name
            if child is None:
                nodes.append(self._snapshot_file_node(revision, relative))
                continue
            nodes.append(
                {
                    "id": f"git-dir::{revision}::{relative}",
                    "name": name,
                    "path": relative,
                    "kind": "directory",
                    "scope": self._classify_relative_scope(relative)[0],
                    "children": self._snapshot_tree_nodes(child, revision=revision, prefix=relative),
                    "git_status": None,
                    "recently_changed": False,
                    "updated_at": utc_now(),
                }
            )
        return nodes

    def _snapshot_file_node(self, revision: str, relative: str) -> dict:
        return {
            "id": f"git-file::{revision}::{relative}",
            "name": Path(relative).name,
            "path": relative,
            "kind": "file",
            "scope": self._classify_relative_scope(relative)[0],
            "writable": False,
            "document_id": f"git::{revision}::{relative}",
            "open_kind": self._open_kind_for(Path(relative)),
            "git_status": None,
            "recently_changed": False,
            "updated_at": utc_now(),
            "size": None,
        }

    @staticmethod
    def _build_snapshot_tree(paths: list[str]) -> dict[str, dict | None]:
        tree: dict[str, dict | None] = {}
        for relative in paths:
            parts = PurePosixPath(relative).parts
            if not parts:
                continue
            cursor = tree
            for part in parts[:-1]:
                next_cursor = cursor.setdefault(part, {})
                if not isinstance(next_cursor, dict):
                    next_cursor = {}
                    cursor[part] = next_cursor
                cursor = next_cursor
            cursor[parts[-1]] = None
        return tree

    def _git_snapshot_paths(self, quest_root: Path, revision: str) -> list[str]:
        result = run_command(
            ["git", "ls-tree", "-r", "--full-tree", "--name-only", revision],
            cwd=quest_root,
            check=False,
        )
        if result.returncode != 0:
            raise FileNotFoundError(f"Unable to inspect git revision `{revision}`.")
        return [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() and not self._skip_explorer_relative(line.strip())
        ]

    @staticmethod
    def _parse_git_document_id(document_id: str) -> tuple[str, str]:
        _prefix, revision, relative = (document_id.split("::", 2) + ["", "", ""])[:3]
        if not revision or not relative:
            raise ValueError("Git snapshot document id must include revision and path.")
        return revision, relative.lstrip("/")

    @staticmethod
    def _git_revision_exists(quest_root: Path, revision: str) -> bool:
        result = run_command(["git", "rev-parse", "--verify", revision], cwd=quest_root, check=False)
        return result.returncode == 0

    @staticmethod
    def _read_git_text(quest_root: Path, revision: str, relative: str) -> str:
        result = run_command(["git", "show", f"{revision}:{relative}"], cwd=quest_root, check=False)
        if result.returncode != 0:
            raise FileNotFoundError(f"File `{relative}` does not exist at `{revision}`.")
        return result.stdout

    @staticmethod
    def _read_git_bytes(quest_root: Path, revision: str, relative: str) -> bytes:
        result = subprocess.run(
            ["git", "show", f"{revision}:{relative}"],
            cwd=str(quest_root),
            check=False,
            text=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise FileNotFoundError(f"File `{relative}` does not exist at `{revision}`.")
        return bytes(result.stdout)

    @staticmethod
    def _git_blob_id(quest_root: Path, revision: str, relative: str) -> str | None:
        result = run_command(["git", "rev-parse", f"{revision}:{relative}"], cwd=quest_root, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    @staticmethod
    def _git_blob_size(quest_root: Path, revision: str, relative: str) -> int | None:
        object_id_result = run_command(["git", "rev-parse", f"{revision}:{relative}"], cwd=quest_root, check=False)
        object_id = object_id_result.stdout.strip() if object_id_result.returncode == 0 else ""
        if not object_id:
            return None
        size_result = run_command(["git", "cat-file", "-s", object_id], cwd=quest_root, check=False)
        if size_result.returncode != 0:
            return None
        try:
            return int(size_result.stdout.strip())
        except ValueError:
            return None

    def _tree_children(
        self,
        quest_root: Path,
        root: Path,
        *,
        git_status: dict[str, str],
        changed_paths: dict[str, dict],
    ) -> list[dict]:
        if not root.exists():
            return []
        try:
            entries = list(root.iterdir())
        except OSError:
            return []

        def _sort_key(item: Path) -> tuple[bool, str]:
            try:
                return item.is_file(), item.name.lower()
            except OSError:
                return True, item.name.lower()

        nodes: list[dict] = []
        for path in sorted(entries, key=_sort_key):
            try:
                if self._skip_explorer_path(quest_root, path):
                    continue
            except OSError:
                continue
            try:
                is_dir = path.is_dir()
            except OSError:
                continue
            if is_dir:
                children = self._tree_children(quest_root, path, git_status=git_status, changed_paths=changed_paths)
                nodes.append(
                    self._directory_node(
                        quest_root,
                        path=path,
                        children=children,
                        git_status=git_status,
                        changed_paths=changed_paths,
                    )
                )
            else:
                node = self._file_node(quest_root, path=path, git_status=git_status, changed_paths=changed_paths)
                if node is not None:
                    nodes.append(node)
        return nodes

    def _directory_node(
        self,
        quest_root: Path,
        *,
        path: Path,
        children: list[dict],
        git_status: dict[str, str],
        changed_paths: dict[str, dict],
    ) -> dict:
        try:
            relative = path.relative_to(quest_root).as_posix()
            scope = self._classify_path_scope(quest_root, path)[0]
        except (OSError, ValueError):
            relative = path.name
            scope = "quest"
        return {
            "id": f"dir::{relative}",
            "name": path.name,
            "path": relative,
            "kind": "directory",
            "scope": scope,
            "children": children,
            "git_status": git_status.get(relative),
            "recently_changed": relative in changed_paths,
            "updated_at": utc_now(),
        }

    def _file_node(
        self,
        quest_root: Path,
        *,
        path: Path,
        git_status: dict[str, str],
        changed_paths: dict[str, dict],
    ) -> dict | None:
        try:
            if not path.exists() or not path.is_file() or self._skip_explorer_path(quest_root, path):
                return None
            relative = path.relative_to(quest_root).as_posix()
            scope, writable = self._classify_path_scope(quest_root, path)
            size = path.stat().st_size
        except (OSError, ValueError):
            return None
        changed_meta = changed_paths.get(str(path)) or changed_paths.get(relative)
        open_kind = self._open_kind_for(path)
        return {
            "id": f"file::{relative}",
            "name": path.name,
            "path": relative,
            "kind": "file",
            "scope": scope,
            "writable": writable,
            "document_id": f"path::{relative}",
            "open_kind": open_kind,
            "git_status": git_status.get(relative),
            "recently_changed": changed_meta is not None,
            "updated_at": utc_now(),
            "size": size,
        }

    @staticmethod
    def _skip_explorer_path(quest_root: Path, path: Path) -> bool:
        relative = path.relative_to(quest_root).as_posix()
        return QuestService._skip_explorer_relative(relative)

    @staticmethod
    def _skip_explorer_relative(relative: str) -> bool:
        if relative.startswith(".git/") or relative == ".git":
            return True
        if relative.startswith(".ds/worktrees/"):
            return True
        parts = PurePosixPath(relative).parts
        return "__pycache__" in parts or ".pytest_cache" in parts

    @staticmethod
    def _classify_path_scope(quest_root: Path, path: Path) -> tuple[str, bool]:
        relative = path.relative_to(quest_root).as_posix()
        return QuestService._classify_relative_scope(relative)

    @staticmethod
    def _classify_relative_scope(relative: str) -> tuple[str, bool]:
        top = PurePosixPath(relative).parts[0] if PurePosixPath(relative).parts else relative
        if relative in {"brief.md", "plan.md", "status.md", "SUMMARY.md"}:
            return "core", True
        if top == "memory":
            return "memory", True
        if top in {"literature", "baselines", "experiments", "paper", "handoffs"}:
            return "research", True
        if top == "artifacts":
            return "artifacts", False
        if relative.startswith(".ds/codex_history/"):
            return "runner_history", False
        if relative.startswith(".ds/"):
            return "runtime", False
        return "quest", True

    @staticmethod
    def _open_kind_for(path: Path) -> str:
        return QuestService._renderer_hint_for(path)[0]

    @staticmethod
    def _renderer_hint_for(path: Path) -> tuple[str, str]:
        suffix = path.suffix.lower()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if suffix in {".md", ".markdown"}:
            return "markdown", mime_type or "text/markdown"
        if suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml", ".toml", ".sh", ".txt", ".log", ".ini", ".cfg"}:
            return "code", mime_type
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"} or mime_type.startswith("image/"):
            return "image", mime_type
        if suffix == ".pdf" or mime_type == "application/pdf":
            return "pdf", "application/pdf"
        if mime_type.startswith("text/"):
            return "text", mime_type
        return "binary", mime_type

    @staticmethod
    def _is_text_document(path: Path, mime_type: str, renderer_hint: str) -> bool:
        if renderer_hint in {"markdown", "code", "text"}:
            return True
        if mime_type.startswith("text/"):
            return True
        return path.suffix.lower() in {".jsonl", ".csv", ".tsv"}

    @staticmethod
    def _git_status_map(quest_root: Path) -> dict[str, str]:
        result = run_command(["git", "status", "--porcelain"], cwd=quest_root, check=False)
        mapping: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            status = line[:2].strip() or "??"
            relative = line[3:].strip()
            if " -> " in relative:
                relative = relative.split(" -> ", 1)[1].strip()
            if relative:
                mapping[relative] = status
        return mapping


def _compact_text(value: object, *, limit: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except TypeError:
            text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _extract_history_texts(event: dict) -> list[str]:
    texts: list[str] = []
    for key in ("text", "content", "message", "summary"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value.strip())
    item = event.get("item")
    if isinstance(item, dict):
        for key in ("text", "content", "message", "summary"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value.strip())
        content = item.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    value = block.get("text") or block.get("content")
                    if isinstance(value, str) and value.strip():
                        texts.append(value.strip())
    delta = event.get("delta")
    if isinstance(delta, dict):
        for key in ("text", "content", "arguments"):
            value = delta.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value.strip())
    return texts


def _dedupe_history_texts(values: list[object]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _extract_web_search_payload(item: dict) -> dict:
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    raw_queries = action.get("queries") if isinstance(action, dict) else None
    queries = _dedupe_history_texts(
        [
            *(raw_queries if isinstance(raw_queries, list) else []),
            action.get("query") if isinstance(action, dict) else "",
            item.get("query"),
        ]
    )
    query = ""
    if isinstance(item.get("query"), str) and item.get("query").strip():
        query = item.get("query").strip()
    elif queries:
        query = queries[0]
    payload: dict[str, Any] = {
        "query": query,
        "queries": queries,
        "action_type": action.get("type") if isinstance(action, dict) else None,
    }
    if isinstance(action, dict) and action:
        payload["action"] = action
    return payload


def _tool_call_id(event: dict, item: dict) -> str:
    for value in (
        item.get("call_id"),
        item.get("tool_call_id"),
        item.get("id"),
        event.get("call_id"),
        event.get("tool_call_id"),
        event.get("id"),
    ):
        if value:
            return str(value)
    return generate_id("tool")


def _tool_name(event: dict, item: dict) -> str:
    for value in (
        item.get("name"),
        item.get("function"),
        event.get("name"),
        event.get("function"),
    ):
        if isinstance(value, dict):
            nested = value.get("name")
            if nested:
                return str(nested)
        elif value:
            return str(value)
    return "tool"


def _tool_args(event: dict, item: dict) -> str:
    for value in (
        item.get("command"),
        item.get("query"),
        item.get("action"),
        item.get("arguments"),
        item.get("input"),
        event.get("arguments"),
        event.get("input"),
        event.get("query"),
        event.get("action"),
        event.get("delta"),
    ):
        text = _compact_text(value, limit=1200)
        if text:
            return text
    return ""


def _tool_output(event: dict, item: dict) -> str:
    for value in (
        item.get("aggregated_output"),
        item.get("changes"),
        item.get("output"),
        item.get("result"),
        item.get("content"),
        event.get("aggregated_output"),
        event.get("changes"),
        event.get("output"),
        event.get("result"),
        event.get("content"),
    ):
        text = _compact_text(value, limit=1200)
        if text:
            return text
    return ""


def _mcp_result_payload(item: dict) -> dict:
    result = item.get("result")
    if isinstance(result, dict):
        structured = result.get("structured_content") or result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        return result
    return {}


def _mcp_tool_metadata(*, quest_id: str, run_id: str, server: str, tool: str, item: dict) -> dict:
    metadata: dict[str, Any] = {
        "mcp_server": server,
        "mcp_tool": tool,
        "session_id": f"quest:{quest_id}",
        "agent_id": "pi",
        "agent_instance_id": run_id,
        "quest_id": quest_id,
    }
    arguments = item.get("arguments")
    if isinstance(arguments, dict):
        for key in ("command", "workdir", "mode", "timeout_seconds", "comment"):
            if key in arguments:
                metadata[key] = arguments.get(key)
    result_payload = _mcp_result_payload(item)
    if server == "bash_exec" and tool == "bash_exec":
        for key in (
            "bash_id",
            "status",
            "started_at",
            "finished_at",
            "exit_code",
            "stop_reason",
            "last_progress",
            "log_path",
        ):
            if key in result_payload:
                metadata[key] = result_payload.get(key)
    return metadata


def _parse_codex_history(history_root: Path, *, quest_id: str, run_id: str, skill_id: str | None) -> list[dict]:
    history_path = history_root / "events.jsonl"
    if not history_path.exists():
        return []

    entries: list[dict] = []
    known_tool_names: dict[str, str] = {}

    for raw in read_jsonl(history_path):
        timestamp = raw.get("timestamp")
        event = raw.get("event")
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = str(item.get("type") or event.get("item_type") or "")

        if item_type == "command_execution":
            tool_call_id = _tool_call_id(event, item)
            tool_name = "shell_command"
            known_tool_names[tool_call_id] = tool_name
            if event_type == "item.started" or str(item.get("status") or "") == "in_progress":
                entries.append(
                    {
                        "id": f"tool:{run_id}:{tool_call_id}:{len(entries)}",
                        "kind": "tool_call",
                        "run_id": run_id,
                        "skill_id": skill_id,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "title": tool_name,
                        "summary": "Command execution started.",
                        "status": "calling",
                        "created_at": timestamp,
                        "args": _tool_args(event, item),
                        "raw_event_type": event_type,
                    }
                )
            else:
                entries.append(
                    {
                        "id": f"tool-result:{run_id}:{tool_call_id}:{len(entries)}",
                        "kind": "tool_result",
                        "run_id": run_id,
                        "skill_id": skill_id,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "title": tool_name,
                        "summary": "Command execution completed.",
                        "status": str(item.get("status") or "completed"),
                        "created_at": timestamp,
                        "args": _tool_args(event, item),
                        "output": _tool_output(event, item),
                        "raw_event_type": event_type,
                    }
                )
            continue

        if item_type == "web_search":
            tool_call_id = _tool_call_id(event, item)
            tool_name = "web_search"
            search_payload = _extract_web_search_payload(item)
            known_tool_names[tool_call_id] = tool_name
            if event_type == "item.started":
                entries.append(
                    {
                        "id": f"tool:{run_id}:{tool_call_id}:{len(entries)}",
                        "kind": "tool_call",
                        "run_id": run_id,
                        "skill_id": skill_id,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "title": tool_name,
                        "summary": "Web search started.",
                        "status": "calling",
                        "created_at": timestamp,
                        "args": _compact_text(search_payload, limit=2400),
                        "metadata": {"search": search_payload},
                        "raw_event_type": event_type,
                    }
                )
            else:
                entries.append(
                    {
                        "id": f"tool-result:{run_id}:{tool_call_id}:{len(entries)}",
                        "kind": "tool_result",
                        "run_id": run_id,
                        "skill_id": skill_id,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "title": tool_name,
                        "summary": "Web search completed.",
                        "status": "completed",
                        "created_at": timestamp,
                        "args": _compact_text(search_payload, limit=2400),
                        "output": _compact_text(search_payload, limit=2400),
                        "metadata": {"search": search_payload},
                        "raw_event_type": event_type,
                    }
                )
            continue

        if item_type == "file_change":
            tool_call_id = _tool_call_id(event, item)
            tool_name = "file_change"
            known_tool_names[tool_call_id] = tool_name
            entries.append(
                {
                    "id": f"tool-result:{run_id}:{tool_call_id}:{len(entries)}",
                    "kind": "tool_result",
                    "run_id": run_id,
                    "skill_id": skill_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "title": tool_name,
                    "summary": "File change recorded.",
                    "status": str(item.get("status") or "completed"),
                    "created_at": timestamp,
                    "output": _tool_output(event, item),
                    "raw_event_type": event_type,
                }
            )
            continue

        if item_type == "mcp_tool_call":
            tool_call_id = _tool_call_id(event, item)
            server = str(item.get("server") or "").strip()
            tool = str(item.get("tool") or "").strip()
            tool_name = f"{server}.{tool}" if server and tool else tool or server or "mcp_tool"
            metadata = _mcp_tool_metadata(
                quest_id=quest_id,
                run_id=run_id,
                server=server,
                tool=tool,
                item=item,
            )
            known_tool_names[tool_call_id] = tool_name
            if event_type == "item.started" or str(item.get("status") or "") == "in_progress":
                entries.append(
                    {
                        "id": f"tool:{run_id}:{tool_call_id}:{len(entries)}",
                        "kind": "tool_call",
                        "run_id": run_id,
                        "skill_id": skill_id,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "title": tool_name,
                        "summary": "MCP tool invocation started.",
                        "status": "calling",
                        "created_at": timestamp,
                        "args": _tool_args(event, item),
                        "mcp_server": server,
                        "mcp_tool": tool,
                        "metadata": metadata,
                        "raw_event_type": event_type,
                    }
                )
            else:
                entries.append(
                    {
                        "id": f"tool-result:{run_id}:{tool_call_id}:{len(entries)}",
                        "kind": "tool_result",
                        "run_id": run_id,
                        "skill_id": skill_id,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "title": tool_name,
                        "summary": "MCP tool invocation completed.",
                        "status": str(item.get("status") or "completed"),
                        "created_at": timestamp,
                        "args": _tool_args(event, item),
                        "output": _tool_output(event, item),
                        "mcp_server": server,
                        "mcp_tool": tool,
                        "metadata": metadata,
                        "raw_event_type": event_type,
                    }
                )
            continue

        if item_type in {"function_call", "custom_tool_call", "tool_call"} or "function_call" in event_type or "tool_call" in event_type:
            tool_call_id = _tool_call_id(event, item)
            tool_name = _tool_name(event, item)
            known_tool_names[tool_call_id] = tool_name
            entries.append(
                {
                    "id": f"tool:{run_id}:{tool_call_id}:{len(entries)}",
                    "kind": "tool_call",
                    "run_id": run_id,
                    "skill_id": skill_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "title": tool_name,
                    "summary": "Tool invocation started.",
                    "status": "calling" if "delta" in event_type or "added" in event_type else "completed",
                    "created_at": timestamp,
                    "args": _tool_args(event, item),
                    "raw_event_type": event_type,
                }
            )
            continue

        if item_type in {"function_call_output", "custom_tool_call_output", "tool_result", "tool_call_output"} or "function_call_output" in event_type or "tool_result" in event_type:
            tool_call_id = _tool_call_id(event, item)
            tool_name = known_tool_names.get(tool_call_id) or _tool_name(event, item)
            entries.append(
                {
                    "id": f"tool-result:{run_id}:{tool_call_id}:{len(entries)}",
                    "kind": "tool_result",
                    "run_id": run_id,
                    "skill_id": skill_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "title": tool_name,
                    "summary": "Tool result received.",
                    "status": "completed",
                    "created_at": timestamp,
                    "args": _tool_args(event, item),
                    "output": _tool_output(event, item),
                    "raw_event_type": event_type,
                }
            )
            continue

        if item_type in {"reasoning", "reasoning_summary"} or "reasoning" in event_type:
            texts = "\n".join(_extract_history_texts(event)).strip()
            if texts:
                entries.append(
                    {
                        "id": f"thought:{run_id}:{len(entries)}",
                        "kind": "thought",
                        "run_id": run_id,
                        "skill_id": skill_id,
                        "title": "Reasoning",
                        "summary": texts,
                        "created_at": timestamp,
                        "raw_event_type": event_type,
                    }
                )
            continue

        if item_type == "agent_message":
            texts = _dedupe_history_texts(_extract_history_texts(event))
            for text in texts:
                entries.append(
                    {
                        "id": f"thought:{run_id}:{len(entries)}",
                        "kind": "thought",
                        "run_id": run_id,
                        "skill_id": skill_id,
                        "title": "Agent message",
                        "summary": text,
                        "created_at": timestamp,
                        "raw_event_type": event_type,
                    }
                )

    return entries[-60:]
