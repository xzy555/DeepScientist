from __future__ import annotations

import copy
from collections import deque
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import subprocess
import json
import mimetypes
import re
import threading
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from ..artifact.metrics import build_metrics_timeline, extract_latest_metric
from ..config import ConfigManager
from ..connector_runtime import conversation_identity_key, normalize_conversation_id, parse_conversation_id
from ..file_lock import advisory_file_lock
from ..gitops import current_branch, export_git_graph, head_commit, init_repo
from ..home import repo_root
from ..registries import BaselineRegistry
from ..shared import append_jsonl, ensure_dir, generate_id, iter_jsonl, read_json, read_jsonl, read_jsonl_tail, read_text, read_yaml, resolve_within, run_command, sha256_text, slugify, utc_now, write_json, write_text, write_yaml
from ..skills import SkillInstaller
from ..web_search import extract_web_search_payload
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
from .stage_views import QuestStageViewBuilder

_UNSET = object()
_NUMERIC_QUEST_ID_PATTERN = re.compile(r"^\d{1,10}$")
_MAX_NUMERIC_QUEST_ID_VALUE = 9_999_999_999
_NUMERIC_QUEST_ID_PAD_WIDTH = 3
_CRASH_AUTO_RESUME_WINDOW = timedelta(hours=24)
_JSONL_CACHE_MAX_BYTES = 4 * 1024 * 1024
_CODEX_HISTORY_TAIL_LIMIT = 400
_JSONL_STREAM_CHUNK_BYTES = 64 * 1024
_EVENTS_OVERSIZED_LINE_BYTES = 8 * 1024 * 1024
_OVERSIZED_EVENT_PREFIX_BYTES = 4096
_EVENT_TYPE_BYTES_RE = re.compile(rb'"(?:type|event_type)"\s*:\s*"([^"]+)"')
_EVENT_TOOL_NAME_BYTES_RE = re.compile(rb'"tool_name"\s*:\s*"([^"]+)"')
_EVENT_RUN_ID_BYTES_RE = re.compile(rb'"run_id"\s*:\s*"([^"]+)"')


def _oversized_event_placeholder(*, prefix: bytes, line_bytes: int) -> dict[str, Any]:
    def _extract(pattern: re.Pattern[bytes]) -> str | None:
        match = pattern.search(prefix)
        if match is None:
            return None
        try:
            return match.group(1).decode("utf-8", errors="ignore").strip() or None
        except Exception:
            return None

    event_type = _extract(_EVENT_TYPE_BYTES_RE) or "runner.tool_result"
    tool_name = _extract(_EVENT_TOOL_NAME_BYTES_RE)
    run_id = _extract(_EVENT_RUN_ID_BYTES_RE)
    summary = f"Omitted oversized quest event payload ({line_bytes} bytes) while reading event history."
    payload: dict[str, Any] = {
        "type": event_type,
        "status": "omitted",
        "summary": summary,
        "oversized_event": True,
        "oversized_bytes": line_bytes,
    }
    if tool_name:
        payload["tool_name"] = tool_name
    if run_id:
        payload["run_id"] = run_id
    return payload


def _iter_jsonl_records_safely(
    path: Path,
    *,
    oversized_line_bytes: int = _EVENTS_OVERSIZED_LINE_BYTES,
):
    if not path.exists():
        return
    with path.open("rb") as handle:
        buffer = bytearray()
        prefix = bytearray()
        current_bytes = 0
        oversized = False
        while True:
            chunk = handle.read(_JSONL_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            start = 0
            while start <= len(chunk):
                newline_index = chunk.find(b"\n", start)
                has_newline = newline_index >= 0
                segment = chunk[start:newline_index] if has_newline else chunk[start:]

                if oversized:
                    current_bytes += len(segment)
                    if has_newline:
                        yield _oversized_event_placeholder(prefix=bytes(prefix), line_bytes=current_bytes)
                        prefix = bytearray()
                        current_bytes = 0
                        oversized = False
                        start = newline_index + 1
                        continue
                    break

                next_bytes = current_bytes + len(segment)
                if next_bytes > oversized_line_bytes:
                    combined_prefix = bytes(buffer)
                    remaining = max(0, _OVERSIZED_EVENT_PREFIX_BYTES - len(combined_prefix))
                    if remaining:
                        combined_prefix += segment[:remaining]
                    prefix = bytearray(combined_prefix)
                    buffer.clear()
                    current_bytes = next_bytes
                    oversized = True
                    if has_newline:
                        yield _oversized_event_placeholder(prefix=bytes(prefix), line_bytes=current_bytes)
                        prefix = bytearray()
                        current_bytes = 0
                        oversized = False
                        start = newline_index + 1
                        continue
                    break

                buffer.extend(segment)
                current_bytes = next_bytes
                if has_newline:
                    raw = bytes(buffer).strip()
                    buffer.clear()
                    line_bytes = current_bytes
                    current_bytes = 0
                    if raw:
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            payload = None
                        if isinstance(payload, dict):
                            yield payload
                    start = newline_index + 1
                    continue
                break

        if oversized:
            yield _oversized_event_placeholder(prefix=bytes(prefix), line_bytes=current_bytes)
        elif buffer:
            raw = bytes(buffer).strip()
            if raw:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    yield payload


class QuestService:
    def __init__(self, home: Path, skill_installer: SkillInstaller | None = None) -> None:
        self.home = home
        self.quests_root = home / "quests"
        self.skill_installer = skill_installer
        self.baseline_registry = BaselineRegistry(home)
        self._file_cache_lock = threading.Lock()
        self._file_cache: dict[str, dict[str, Any]] = {}
        self._jsonl_cache_lock = threading.Lock()
        self._jsonl_cache: dict[str, dict[str, Any]] = {}
        self._snapshot_cache_lock = threading.Lock()
        self._snapshot_cache: dict[str, dict[str, Any]] = {}
        self._codex_history_cache_lock = threading.Lock()
        self._codex_history_cache: dict[str, dict[str, Any]] = {}
        self._runtime_state_locks_lock = threading.Lock()
        self._runtime_state_locks: dict[str, threading.Lock] = {}

    def _quest_root(self, quest_id: str) -> Path:
        return self.quests_root / quest_id

    def _normalized_binding_sources(self, sources: list[Any] | None) -> list[str]:
        local_present = False
        external_source: str | None = None
        for raw in sources or []:
            normalized = self._normalize_binding_source(raw)
            if not normalized:
                continue
            if normalized == "local:default":
                local_present = True
                continue
            parsed = parse_conversation_id(normalized)
            connector = str((parsed or {}).get("connector") or "").strip().lower()
            if connector == "local":
                local_present = True
                continue
            external_source = normalized
        if external_source:
            return ["local:default", external_source]
        if local_present:
            return ["local:default"]
        return ["local:default"]

    def _binding_sources_payload(self, quest_root: Path) -> dict[str, list[str]]:
        bindings_path = quest_root / ".ds" / "bindings.json"
        payload = read_json(bindings_path, {"sources": ["local:default"]})
        raw_sources = payload.get("sources") if isinstance(payload, dict) else ["local:default"]
        sources = self._normalized_binding_sources(raw_sources if isinstance(raw_sources, list) else ["local:default"])
        return {"sources": sources}

    def preferred_locale(self, quest_root: Path | None = None) -> str:
        if quest_root is not None:
            try:
                quest_yaml = self.read_quest_yaml(quest_root)
            except Exception:
                quest_yaml = {}
            if isinstance(quest_yaml, dict):
                for key in ("locale", "default_locale", "user_locale", "user_language", "language"):
                    value = str(quest_yaml.get(key) or "").strip()
                    if value:
                        return value.lower()
        config = ConfigManager(self.home).load_named("config")
        return str(config.get("default_locale") or "en-US").lower()

    def localized_copy(self, *, zh: str, en: str, quest_root: Path | None = None) -> str:
        return zh if self.preferred_locale(quest_root).startswith("zh") else en

    @staticmethod
    def _quest_yaml_path(quest_root: Path) -> Path:
        return quest_root / "quest.yaml"

    def _quest_id_state_path(self) -> Path:
        return self.home / "runtime" / "quest_id_state.json"

    def _quest_id_lock_path(self) -> Path:
        return self.home / "runtime" / "quest_id_state.lock"

    @staticmethod
    def _runtime_state_lock_path(quest_root: Path) -> Path:
        return quest_root / ".ds" / "runtime_state.lock"

    @staticmethod
    def _normalize_baseline_gate(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in {"pending", "confirmed", "waived"}:
            raise ValueError("`baseline_gate` must be one of: pending, confirmed, waived.")
        return normalized

    def read_quest_yaml(self, quest_root: Path) -> dict[str, Any]:
        payload = self._read_cached_yaml(self._quest_yaml_path(quest_root), {})
        if not isinstance(payload, dict):
            payload = {}
        normalized = dict(payload)
        normalized.setdefault("active_anchor", "baseline")
        normalized.setdefault("baseline_gate", "pending")
        normalized.setdefault("confirmed_baseline_ref", None)
        normalized.setdefault("requested_baseline_ref", None)
        normalized.setdefault("startup_contract", None)
        return normalized

    @staticmethod
    def _research_state_path(quest_root: Path) -> Path:
        return quest_root / ".ds" / "research_state.json"

    @staticmethod
    def _lab_canvas_state_path(quest_root: Path) -> Path:
        return quest_root / ".ds" / "lab_canvas_state.json"

    def _default_research_state(self, quest_root: Path) -> dict[str, Any]:
        return {
            "version": 1,
            "active_idea_id": None,
            "research_head_branch": None,
            "research_head_worktree_root": None,
            "current_workspace_branch": None,
            "current_workspace_root": None,
            "active_idea_md_path": None,
            "active_idea_draft_path": None,
            "active_analysis_campaign_id": None,
            "analysis_parent_branch": None,
            "analysis_parent_worktree_root": None,
            "paper_parent_branch": None,
            "paper_parent_worktree_root": None,
            "paper_parent_run_id": None,
            "next_pending_slice_id": None,
            "workspace_mode": "quest",
            "last_flow_type": None,
            "updated_at": utc_now(),
        }

    def _default_lab_canvas_state(self, quest_root: Path) -> dict[str, Any]:
        return {
            "version": 1,
            "layout_json": {
                "branch": {},
                "event": {},
                "stage": {},
                "preferences": {},
            },
            "updated_at": utc_now(),
        }

    def read_research_state(self, quest_root: Path) -> dict[str, Any]:
        self._initialize_runtime_files(quest_root)
        defaults = self._default_research_state(quest_root)
        payload = self._read_cached_json(self._research_state_path(quest_root), defaults)
        if not isinstance(payload, dict):
            payload = defaults
        merged = {**defaults, **payload}
        worktree_root = str(merged.get("research_head_worktree_root") or "").strip()
        if worktree_root and not Path(worktree_root).exists():
            merged["research_head_worktree_root"] = None
        current_root = str(merged.get("current_workspace_root") or "").strip()
        if current_root and not Path(current_root).exists():
            merged["current_workspace_root"] = None
        parent_root = str(merged.get("analysis_parent_worktree_root") or "").strip()
        if parent_root and not Path(parent_root).exists():
            merged["analysis_parent_worktree_root"] = None
        paper_parent_root = str(merged.get("paper_parent_worktree_root") or "").strip()
        if paper_parent_root and not Path(paper_parent_root).exists():
            merged["paper_parent_worktree_root"] = None
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

    def read_lab_canvas_state(self, quest_root: Path) -> dict[str, Any]:
        self._initialize_runtime_files(quest_root)
        defaults = self._default_lab_canvas_state(quest_root)
        payload = self._read_cached_json(self._lab_canvas_state_path(quest_root), defaults)
        if not isinstance(payload, dict):
            payload = defaults
        merged = {**defaults, **payload}
        layout_json = dict(merged.get("layout_json") or {}) if isinstance(merged.get("layout_json"), dict) else {}
        for key in ("branch", "event", "stage", "preferences"):
            if not isinstance(layout_json.get(key), dict):
                layout_json[key] = {}
        merged["layout_json"] = layout_json
        return merged

    def update_lab_canvas_state(
        self,
        quest_root: Path,
        *,
        layout_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.read_lab_canvas_state(quest_root)
        normalized_layout = dict(layout_json or {}) if isinstance(layout_json, dict) else {}
        for key in ("branch", "event", "stage", "preferences"):
            if not isinstance(normalized_layout.get(key), dict):
                normalized_layout[key] = {}
        payload = {
            **current,
            "layout_json": normalized_layout,
            "updated_at": utc_now(),
        }
        write_json(self._lab_canvas_state_path(quest_root), payload)
        return payload

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

    @staticmethod
    def _artifact_item_identity(path: Path, payload: dict[str, Any], *, kind: str) -> str:
        normalized_kind = str(kind or payload.get("kind") or path.parent.name or "artifact").strip() or "artifact"
        artifact_id = str(payload.get("artifact_id") or payload.get("id") or "").strip()
        if artifact_id:
            return f"{normalized_kind}:artifact:{artifact_id}"
        branch_name = str(payload.get("branch") or "").strip()
        run_id = str(payload.get("run_id") or "").strip()
        if normalized_kind == "runs" and run_id and branch_name:
            return f"{normalized_kind}:branch_run:{branch_name}:{run_id}"
        if normalized_kind == "runs" and run_id:
            return f"{normalized_kind}:run:{run_id}"
        idea_id = str(payload.get("idea_id") or "").strip()
        if normalized_kind == "ideas" and idea_id and branch_name:
            return f"{normalized_kind}:branch_idea:{branch_name}:{idea_id}"
        if normalized_kind == "ideas" and idea_id:
            return f"{normalized_kind}:idea:{idea_id}"
        return f"path:{path.resolve()}"

    @staticmethod
    def _artifact_item_rank(payload: dict[str, Any], *, path: Path, mtime_ns: int) -> tuple[str, str, int, int, str]:
        return (
            str(payload.get("updated_at") or ""),
            str(payload.get("created_at") or ""),
            len(payload),
            mtime_ns,
            str(path),
        )

    def _collect_artifacts(self, quest_root: Path) -> list[dict[str, Any]]:
        artifacts_by_identity: dict[str, dict[str, Any]] = {}
        for root in self._artifact_roots(quest_root):
            artifacts_root = root / "artifacts"
            if not artifacts_root.exists():
                continue
            for folder in sorted(artifacts_root.iterdir()):
                if not folder.is_dir():
                    continue
                for path in sorted(folder.glob("*.json")):
                    item = self._read_cached_json(path, {})
                    payload = item if isinstance(item, dict) else {}
                    try:
                        mtime_ns = path.stat().st_mtime_ns
                    except OSError:
                        mtime_ns = 0
                    artifact = {
                        "kind": folder.name,
                        "path": str(path),
                        "payload": item,
                        "workspace_root": str(root),
                    }
                    identity = self._artifact_item_identity(path, payload, kind=folder.name)
                    existing = artifacts_by_identity.get(identity)
                    existing_payload = existing.get("payload") if isinstance((existing or {}).get("payload"), dict) else {}
                    existing_path = Path(str((existing or {}).get("path") or path))
                    try:
                        existing_mtime_ns = existing_path.stat().st_mtime_ns if existing else 0
                    except OSError:
                        existing_mtime_ns = 0
                    if existing is None or self._artifact_item_rank(
                        payload,
                        path=path,
                        mtime_ns=mtime_ns,
                    ) >= self._artifact_item_rank(
                        existing_payload,
                        path=existing_path,
                        mtime_ns=existing_mtime_ns,
                    ):
                        artifacts_by_identity[identity] = artifact
        artifacts = list(artifacts_by_identity.values())
        artifacts.sort(
            key=lambda item: str(
                ((item.get("payload") or {}).get("updated_at"))
                or ((item.get("payload") or {}).get("created_at"))
                or item.get("path")
                or ""
            )
        )
        return artifacts

    def _active_baseline_attachment(self, quest_root: Path, workspace_root: Path) -> dict[str, Any] | None:
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
                payload = self._read_cached_yaml(path, {})
                baseline_id = str(payload.get("source_baseline_id") or "").strip() if isinstance(payload, dict) else ""
                if baseline_id and self.baseline_registry.is_deleted(baseline_id):
                    continue
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
        return extract_latest_metric(payload)

    @staticmethod
    def _parse_numeric_quest_id(value: str | None) -> int | None:
        raw = str(value or "").strip()
        if not _NUMERIC_QUEST_ID_PATTERN.fullmatch(raw):
            return None
        numeric_value = int(raw)
        if numeric_value < 1 or numeric_value > _MAX_NUMERIC_QUEST_ID_VALUE:
            return None
        return numeric_value

    @staticmethod
    def _format_numeric_quest_id(value: int) -> str:
        if value < 1:
            raise ValueError("Sequential quest ids must be positive integers.")
        text = str(value)
        if len(text) > 10:
            raise ValueError("Sequential quest ids support at most 10 digits.")
        if len(text) >= _NUMERIC_QUEST_ID_PAD_WIDTH:
            return text
        return text.zfill(_NUMERIC_QUEST_ID_PAD_WIDTH)

    @contextmanager
    def _quest_id_state_lock(self):
        lock_path = self._quest_id_lock_path()
        ensure_dir(lock_path.parent)
        with advisory_file_lock(lock_path):
            yield

    @contextmanager
    def _runtime_state_lock(self, quest_root: Path):
        lock_key = str(quest_root.resolve())
        with self._runtime_state_locks_lock:
            thread_lock = self._runtime_state_locks.setdefault(lock_key, threading.Lock())
        with thread_lock:
            lock_path = self._runtime_state_lock_path(quest_root)
            ensure_dir(lock_path.parent)
            with advisory_file_lock(lock_path):
                yield

    def _scan_next_numeric_quest_id(self) -> int:
        max_numeric_id = 0
        if not self.quests_root.exists():
            return 1
        for quest_root in sorted(self.quests_root.iterdir()):
            if not quest_root.is_dir():
                continue
            numeric_value = self._parse_numeric_quest_id(quest_root.name)
            if numeric_value is None:
                continue
            max_numeric_id = max(max_numeric_id, numeric_value)
        return max_numeric_id + 1

    def _read_quest_id_state_locked(self) -> dict[str, Any]:
        state_path = self._quest_id_state_path()
        scanned_next_numeric_id = self._scan_next_numeric_quest_id()
        payload = read_json(state_path, {})
        should_write = not state_path.exists()
        if not isinstance(payload, dict):
            payload = {}
            should_write = True
        next_numeric_id = payload.get("next_numeric_id")
        if isinstance(next_numeric_id, str) and next_numeric_id.isdigit():
            next_numeric_id = int(next_numeric_id)
        if not isinstance(next_numeric_id, int) or next_numeric_id < 1:
            next_numeric_id = scanned_next_numeric_id
            should_write = True
        elif next_numeric_id < scanned_next_numeric_id:
            next_numeric_id = scanned_next_numeric_id
            should_write = True
        state = {
            "version": 1,
            "next_numeric_id": next_numeric_id,
            "updated_at": str(payload.get("updated_at") or utc_now()),
        }
        if payload.get("version") != 1:
            should_write = True
        if should_write:
            state["updated_at"] = utc_now()
            write_json(state_path, state)
        return state

    def _write_quest_id_state_locked(self, next_numeric_id: int) -> None:
        write_json(
            self._quest_id_state_path(),
            {
                "version": 1,
                "next_numeric_id": next_numeric_id,
                "updated_at": utc_now(),
            },
        )

    def _allocate_next_numeric_quest_id(self) -> str:
        with self._quest_id_state_lock():
            state = self._read_quest_id_state_locked()
            next_numeric_id = int(state.get("next_numeric_id") or 1)
            quest_id = self._format_numeric_quest_id(next_numeric_id)
            self._write_quest_id_state_locked(next_numeric_id + 1)
            return quest_id

    def preview_next_numeric_quest_id(self) -> str:
        with self._quest_id_state_lock():
            state = self._read_quest_id_state_locked()
            next_numeric_id = int(state.get("next_numeric_id") or 1)
            return self._format_numeric_quest_id(next_numeric_id)

    def _reserve_numeric_quest_id(self, quest_id: str) -> None:
        numeric_value = self._parse_numeric_quest_id(quest_id)
        if numeric_value is None:
            return
        with self._quest_id_state_lock():
            state = self._read_quest_id_state_locked()
            next_numeric_id = max(int(state.get("next_numeric_id") or 1), numeric_value + 1)
            if next_numeric_id != int(state.get("next_numeric_id") or 1):
                self._write_quest_id_state_locked(next_numeric_id)

    def _normalize_quest_id(self, quest_id: str | None) -> tuple[str, bool]:
        raw = str(quest_id or "").strip().lower()
        if not raw:
            return self._allocate_next_numeric_quest_id(), True
        slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("._-")
        if not slug:
            return self._allocate_next_numeric_quest_id(), True
        return slug[:80], False

    def create(
        self,
        goal: str,
        quest_id: str | None = None,
        runner: str = "codex",
        title: str | None = None,
        *,
        requested_baseline_ref: dict[str, Any] | None = None,
        startup_contract: dict[str, Any] | None = None,
    ) -> dict:
        quest_id, auto_generated = self._normalize_quest_id(quest_id)
        quest_root = self._quest_root(quest_id)
        if quest_root.exists():
            raise FileExistsError(f"Quest already exists: {quest_id}")
        if not auto_generated:
            self._reserve_numeric_quest_id(quest_id)
        ensure_dir(quest_root)
        for relative in QUEST_DIRECTORIES:
            ensure_dir(quest_root / relative)
        write_yaml(
            self._quest_yaml_path(quest_root),
            initial_quest_yaml(
                quest_id,
                goal,
                quest_root,
                runner,
                title=title,
                requested_baseline_ref=dict(requested_baseline_ref) if isinstance(requested_baseline_ref, dict) else None,
                startup_contract=dict(startup_contract) if isinstance(startup_contract, dict) else None,
            ),
        )
        write_text(quest_root / "brief.md", initial_brief(goal))
        write_text(quest_root / "plan.md", initial_plan())
        write_text(quest_root / "status.md", initial_status())
        write_text(quest_root / "SUMMARY.md", initial_summary())
        write_text(quest_root / ".gitignore", gitignore())
        self._write_active_user_requirements(
            quest_root,
            latest_requirement=None,
        )
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
            items.append(self.summary_compact(quest_id))
        return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)

    def _path_states(self, paths: list[Path]) -> tuple[tuple[str, tuple[int, int, int] | None], ...]:
        states: list[tuple[str, tuple[int, int, int] | None]] = []
        for path in paths:
            try:
                label = str(path.relative_to(self.home))
            except ValueError:
                label = str(path)
            states.append((label, self._path_state(path)))
        return tuple(states)

    def _glob_states(self, root: Path, pattern: str) -> tuple[tuple[str, tuple[int, int, int] | None], ...]:
        if not root.exists():
            return ()
        states: list[tuple[str, tuple[int, int, int] | None]] = []
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            try:
                label = str(path.relative_to(root))
            except ValueError:
                label = path.name
            states.append((label, self._path_state(path)))
        return tuple(states)

    def _artifact_collection_state(self, quest_root: Path) -> tuple[tuple[str, tuple[tuple[str, tuple[int, int, int] | None], ...]], ...]:
        states: list[tuple[str, tuple[tuple[str, tuple[int, int, int] | None], ...]]] = []
        for root in self.workspace_roots(quest_root):
            artifacts_root = root / "artifacts"
            if not artifacts_root.exists():
                continue
            try:
                label = str(root.relative_to(quest_root))
            except ValueError:
                label = str(root)
            states.append((label, self._glob_states(artifacts_root, "*/*.json")))
        return tuple(states)

    def _codex_meta_state(self, quest_root: Path) -> tuple[tuple[str, tuple[int, int, int] | None], ...]:
        return self._glob_states(quest_root / ".ds" / "codex_history", "*/meta.json")

    def _baseline_attachment_state(self, quest_root: Path) -> tuple[tuple[str, tuple[tuple[str, tuple[int, int, int] | None], ...]], ...]:
        states: list[tuple[str, tuple[tuple[str, tuple[int, int, int] | None], ...]]] = []
        for root in self.workspace_roots(quest_root):
            attachment_root = root / "baselines" / "imported"
            if not attachment_root.exists():
                continue
            try:
                label = str(root.relative_to(quest_root))
            except ValueError:
                label = str(root)
            states.append((label, self._glob_states(attachment_root, "*/attachment.yaml")))
        return tuple(states)

    def _snapshot_state(self, quest_root: Path) -> tuple[Any, ...]:
        core_paths = [
            self._quest_yaml_path(quest_root),
            quest_root / "status.md",
            quest_root / ".ds" / "runtime_state.json",
            quest_root / ".ds" / "research_state.json",
            quest_root / ".ds" / "user_message_queue.json",
            quest_root / ".ds" / "interaction_state.json",
            quest_root / ".ds" / "bindings.json",
            quest_root / ".ds" / "conversations" / "main.jsonl",
            quest_root / ".ds" / "bash_exec" / "summary.json",
        ]
        return (
            self._path_states(core_paths),
            self._artifact_collection_state(quest_root),
            self._codex_meta_state(quest_root),
            self._baseline_attachment_state(quest_root),
        )

    def _compact_summary_state(self, quest_root: Path) -> tuple[Any, ...]:
        core_paths = [
            self._quest_yaml_path(quest_root),
            quest_root / "status.md",
            quest_root / ".ds" / "runtime_state.json",
            quest_root / ".ds" / "research_state.json",
            quest_root / ".ds" / "interaction_state.json",
            quest_root / ".ds" / "bindings.json",
            quest_root / ".ds" / "bash_exec" / "summary.json",
        ]
        return (
            self._path_states(core_paths),
            self._baseline_attachment_state(quest_root),
        )

    def summary_compact(self, quest_id: str) -> dict[str, Any]:
        quest_root = self._quest_root(quest_id)
        cache_key = f"compact:{self._cache_key_for_path(quest_root)}"
        state = self._compact_summary_state(quest_root)
        with self._snapshot_cache_lock:
            cached = self._snapshot_cache.get(cache_key)
            if cached and cached.get("state") == state:
                return copy.deepcopy(cached.get("payload"))

        quest_yaml = self.read_quest_yaml(quest_root)
        research_state = self.read_research_state(quest_root)
        workspace_root = self.active_workspace_root(quest_root)
        runtime_state = self._read_runtime_state(quest_root)
        interaction_state = self._read_interaction_state(quest_root)
        open_requests = [
            dict(item)
            for item in (interaction_state.get("open_requests") or [])
            if str(item.get("status") or "") in {"waiting", "answered"}
        ]
        waiting_interaction_id = self._latest_waiting_interaction_id(open_requests)
        default_reply_interaction_id = str(interaction_state.get("default_reply_interaction_id") or "").strip() or None
        recent_threads = [dict(item) for item in (interaction_state.get("recent_threads") or [])][-5:]
        if not default_reply_interaction_id:
            default_reply_interaction_id = self._default_reply_interaction_id(
                open_requests=open_requests,
                recent_threads=recent_threads,
            )
        pending_decisions = [
            str(item.get("artifact_id") or item.get("interaction_id") or "")
            for item in open_requests
            if str(item.get("status") or "") == "waiting"
            and (item.get("artifact_id") or item.get("interaction_id"))
        ]
        attachment = self._active_baseline_attachment(quest_root, workspace_root)
        active_baseline_id = None
        active_baseline_variant_id = None
        if attachment:
            active_baseline_id = attachment.get("source_baseline_id")
            active_baseline_variant_id = attachment.get("source_variant_id")
        elif isinstance(quest_yaml.get("confirmed_baseline_ref"), dict):
            confirmed_ref = dict(quest_yaml.get("confirmed_baseline_ref") or {})
            active_baseline_id = confirmed_ref.get("baseline_id")
            active_baseline_variant_id = confirmed_ref.get("variant_id")

        status_line = "Quest created."
        status_text = self._read_cached_text(quest_root / "status.md").strip().splitlines()
        if status_text:
            for line in status_text:
                line = line.strip().lstrip("#").strip()
                if line and line.lower() not in {"status", "summary"}:
                    status_line = line
                    break

        from ..bash_exec import BashExecService

        bash_summary = BashExecService(self.home).summary(quest_root)
        interaction_watchdog = self.artifact_interaction_watchdog_status(quest_root)
        payload = {
            "quest_id": quest_yaml.get("quest_id", quest_id),
            "title": quest_yaml.get("title", quest_id),
            "quest_root": str(quest_root.resolve()),
            "status": runtime_state.get("display_status") or runtime_state.get("status") or quest_yaml.get("status", "idle"),
            "runtime_status": runtime_state.get("status") or quest_yaml.get("status", "idle"),
            "display_status": runtime_state.get("display_status") or runtime_state.get("status") or quest_yaml.get("status", "idle"),
            "active_anchor": quest_yaml.get("active_anchor", "baseline"),
            "baseline_gate": quest_yaml.get("baseline_gate", "pending"),
            "confirmed_baseline_ref": quest_yaml.get("confirmed_baseline_ref"),
            "requested_baseline_ref": quest_yaml.get("requested_baseline_ref"),
            "startup_contract": quest_yaml.get("startup_contract"),
            "runner": quest_yaml.get("default_runner", "codex"),
            "active_baseline_id": active_baseline_id,
            "active_baseline_variant_id": active_baseline_variant_id,
            "active_run_id": runtime_state.get("active_run_id"),
            "pending_decisions": pending_decisions,
            "waiting_interaction_id": waiting_interaction_id,
            "default_reply_interaction_id": default_reply_interaction_id,
            "pending_user_message_count": int(runtime_state.get("pending_user_message_count") or 0),
            "stop_reason": runtime_state.get("stop_reason"),
            "active_interaction_id": runtime_state.get("active_interaction_id"),
            "last_artifact_interact_at": runtime_state.get("last_artifact_interact_at"),
            "last_tool_activity_at": runtime_state.get("last_tool_activity_at"),
            "last_tool_activity_name": runtime_state.get("last_tool_activity_name"),
            "tool_calls_since_last_artifact_interact": int(runtime_state.get("tool_calls_since_last_artifact_interact") or 0),
            "seconds_since_last_artifact_interact": interaction_watchdog.get("seconds_since_last_artifact_interact"),
            "last_delivered_batch_id": runtime_state.get("last_delivered_batch_id"),
            "last_delivered_at": runtime_state.get("last_delivered_at"),
            "bound_conversations": self._binding_sources_payload(quest_root).get("sources") or ["local:default"],
            "created_at": quest_yaml.get("created_at"),
            "updated_at": quest_yaml.get("updated_at"),
            "branch": research_state.get("current_workspace_branch") or research_state.get("research_head_branch"),
            "summary": {
                "status_line": status_line,
                "latest_metric": None,
                "latest_bash_session": bash_summary.get("latest_session"),
            },
            "counts": {
                "memory_cards": 0,
                "artifacts": 0,
                "pending_decision_count": len(pending_decisions),
                "analysis_run_count": 0,
                "pending_user_message_count": int(runtime_state.get("pending_user_message_count") or 0),
                "bash_session_count": int(bash_summary.get("session_count") or 0),
                "bash_running_count": int(bash_summary.get("running_count") or 0),
            },
            "interaction_watchdog": interaction_watchdog,
            "recent_artifacts": [],
            "recent_runs": [],
        }
        with self._snapshot_cache_lock:
            self._snapshot_cache[cache_key] = {
                "state": state,
                "payload": copy.deepcopy(payload),
            }
        return payload

    def _read_cached_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            cache_key = str(path.resolve())
            with self._jsonl_cache_lock:
                self._jsonl_cache.pop(cache_key, None)
            return []
        cache_key = str(path.resolve())
        stat = path.stat()
        state = (
            stat.st_ino,
            getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
            stat.st_size,
        )
        if stat.st_size > _JSONL_CACHE_MAX_BYTES:
            with self._jsonl_cache_lock:
                self._jsonl_cache.pop(cache_key, None)
            return read_jsonl(path)
        with self._jsonl_cache_lock:
            cached = self._jsonl_cache.get(cache_key)
            if cached and cached.get("state") == state:
                return cached.get("records") or []
        items = read_jsonl(path)
        with self._jsonl_cache_lock:
            self._jsonl_cache[cache_key] = {
                "state": state,
                "records": items,
            }
        return items

    @staticmethod
    def _read_jsonl_cursor_slice(
        path: Path,
        *,
        after: int = 0,
        before: int | None = None,
        limit: int = 200,
        tail: bool = False,
    ) -> tuple[list[tuple[int, dict[str, Any]]], int, bool]:
        normalized_limit = max(int(limit or 0), 0)
        if not path.exists():
            return [], 0, False
        if normalized_limit <= 0:
            total = sum(1 for _ in _iter_jsonl_records_safely(path))
            return [], total, False

        if before is not None:
            stop_cursor = max(int(before) - 1, 0)
            window: deque[tuple[int, dict[str, Any]]] = deque(maxlen=normalized_limit)
            total = 0
            for payload in _iter_jsonl_records_safely(path):
                total += 1
                if total >= before:
                    break
                window.append((total, payload))
            has_more = bool(window and window[0][0] > 1)
            return list(window), total, has_more

        if tail:
            window = deque(maxlen=normalized_limit)
            total = 0
            for payload in _iter_jsonl_records_safely(path):
                total += 1
                window.append((total, payload))
            has_more = total > len(window)
            return list(window), total, has_more

        collected: list[tuple[int, dict[str, Any]]] = []
        total = 0
        saw_more = False
        normalized_after = max(int(after or 0), 0)
        for payload in _iter_jsonl_records_safely(path):
            total += 1
            if total <= normalized_after:
                continue
            if len(collected) < normalized_limit:
                collected.append((total, payload))
                continue
            saw_more = True
        return collected, total, saw_more

    @staticmethod
    def _path_state(path: Path) -> tuple[int, int, int] | None:
        if not path.exists():
            return None
        stat = path.stat()
        return (
            stat.st_ino,
            getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
            stat.st_size,
        )

    @staticmethod
    def _cache_key_for_path(path: Path) -> str:
        try:
            return str(path.resolve())
        except FileNotFoundError:
            return str(path.absolute())

    def _read_cached_path(
        self,
        path: Path,
        *,
        default: Any,
        loader: Any,
    ) -> Any:
        cache_key = self._cache_key_for_path(path)
        state = self._path_state(path)
        if state is None:
            with self._file_cache_lock:
                self._file_cache.pop(cache_key, None)
            return copy.deepcopy(default)
        with self._file_cache_lock:
            cached = self._file_cache.get(cache_key)
            if cached and cached.get("state") == state:
                return copy.deepcopy(cached.get("value"))
        value = loader(path, default)
        with self._file_cache_lock:
            self._file_cache[cache_key] = {
                "state": state,
                "value": value,
            }
        return copy.deepcopy(value)

    def _read_cached_json(self, path: Path, default: Any = None) -> Any:
        return self._read_cached_path(path, default=default, loader=read_json)

    def _read_cached_yaml(self, path: Path, default: Any = None) -> Any:
        return self._read_cached_path(path, default=default, loader=read_yaml)

    def _read_cached_text(self, path: Path, default: str = "") -> str:
        value = self._read_cached_path(path, default=default, loader=read_text)
        return str(value) if value is not None else default

    def _parse_codex_history_cached(
        self,
        history_root: Path,
        *,
        quest_id: str,
        run_id: str,
        skill_id: str | None,
    ) -> list[dict[str, Any]]:
        history_path = history_root / "events.jsonl"
        cache_key = f"{self._cache_key_for_path(history_path)}::{quest_id}::{run_id}::{skill_id or ''}"
        state = self._path_state(history_path)
        if state is None:
            with self._codex_history_cache_lock:
                self._codex_history_cache.pop(cache_key, None)
            return []
        with self._codex_history_cache_lock:
            cached = self._codex_history_cache.get(cache_key)
            if cached and cached.get("state") == state:
                return copy.deepcopy(cached.get("entries") or [])
        entries = _parse_codex_history(
            history_root,
            quest_id=quest_id,
            run_id=run_id,
            skill_id=skill_id,
        )
        with self._codex_history_cache_lock:
            self._codex_history_cache[cache_key] = {
                "state": state,
                "entries": copy.deepcopy(entries),
            }
        return entries

    def snapshot_fast(self, quest_id: str) -> dict:
        return self._snapshot(quest_id)

    def snapshot(self, quest_id: str) -> dict:
        return self._snapshot(quest_id)

    def _snapshot(self, quest_id: str) -> dict:
        quest_root = self._quest_root(quest_id)
        cache_key = f"snapshot:{self._cache_key_for_path(quest_root)}"
        state = self._snapshot_state(quest_root)
        with self._snapshot_cache_lock:
            cached = self._snapshot_cache.get(cache_key)
            if cached and cached.get("state") == state:
                return copy.deepcopy(cached.get("payload"))
        workspace_root = self.active_workspace_root(quest_root)
        research_state = self.read_research_state(quest_root)
        quest_yaml = self.read_quest_yaml(quest_root)
        graph_dir = quest_root / "artifacts" / "graphs"
        graph_svg = graph_dir / "git-graph.svg"
        history = self._read_cached_jsonl(quest_root / ".ds" / "conversations" / "main.jsonl")
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
                run_data = self._read_cached_json(meta_path, {})
                if run_data:
                    recent_runs.append(run_data)
                    if latest_metric is None and run_data.get("summary"):
                        latest_metric = {"key": "summary", "value": run_data.get("summary")}
        attachment = self._active_baseline_attachment(quest_root, workspace_root)
        if attachment:
            active_baseline_id = attachment.get("source_baseline_id")
            active_baseline_variant_id = attachment.get("source_variant_id")
        elif isinstance(quest_yaml.get("confirmed_baseline_ref"), dict):
            confirmed_ref = dict(quest_yaml.get("confirmed_baseline_ref") or {})
            active_baseline_id = confirmed_ref.get("baseline_id")
            active_baseline_variant_id = confirmed_ref.get("variant_id")
        status_line = "Quest created."
        status_text = self._read_cached_text(quest_root / "status.md").strip().splitlines()
        if status_text:
            for line in status_text:
                line = line.strip().lstrip("#").strip()
                if line and line.lower() not in {"status", "summary"}:
                    status_line = line
                    break
        runtime_state = self._read_runtime_state(quest_root)
        from ..bash_exec import BashExecService

        bash_service = BashExecService(self.home)
        bash_summary = bash_service.summary(quest_root)
        latest_bash_session = bash_summary.get("latest_session")
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
            "active_user_requirements": str(self._active_user_requirements_path(quest_root)),
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
            "bash_session_count": int(bash_summary.get("session_count") or 0),
            "bash_running_count": int(bash_summary.get("running_count") or 0),
        }
        interaction_watchdog = self.artifact_interaction_watchdog_status(quest_root)
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
        payload = {
            "quest_id": quest_yaml.get("quest_id", quest_id),
            "title": quest_yaml.get("title", quest_id),
            "quest_root": str(quest_root.resolve()),
            "status": runtime_state.get("display_status") or runtime_state.get("status") or quest_yaml.get("status", "idle"),
            "runtime_status": runtime_state.get("status") or quest_yaml.get("status", "idle"),
            "display_status": runtime_state.get("display_status") or runtime_state.get("status") or quest_yaml.get("status", "idle"),
            "active_anchor": quest_yaml.get("active_anchor", "baseline"),
            "baseline_gate": quest_yaml.get("baseline_gate", "pending"),
            "confirmed_baseline_ref": quest_yaml.get("confirmed_baseline_ref"),
            "requested_baseline_ref": quest_yaml.get("requested_baseline_ref"),
            "startup_contract": quest_yaml.get("startup_contract"),
            "runner": quest_yaml.get("default_runner", "codex"),
            "active_workspace_root": str(workspace_root),
            "research_head_branch": research_state.get("research_head_branch"),
            "research_head_worktree_root": research_state.get("research_head_worktree_root"),
            "current_workspace_branch": research_state.get("current_workspace_branch"),
            "current_workspace_root": research_state.get("current_workspace_root"),
            "active_idea_id": research_state.get("active_idea_id"),
            "active_idea_md_path": research_state.get("active_idea_md_path"),
            "active_idea_draft_path": research_state.get("active_idea_draft_path"),
            "active_analysis_campaign_id": research_state.get("active_analysis_campaign_id"),
            "analysis_parent_branch": research_state.get("analysis_parent_branch"),
            "analysis_parent_worktree_root": research_state.get("analysis_parent_worktree_root"),
            "paper_parent_branch": research_state.get("paper_parent_branch"),
            "paper_parent_worktree_root": research_state.get("paper_parent_worktree_root"),
            "paper_parent_run_id": research_state.get("paper_parent_run_id"),
            "next_pending_slice_id": research_state.get("next_pending_slice_id"),
            "workspace_mode": research_state.get("workspace_mode") or "quest",
            "active_baseline_id": active_baseline_id,
            "active_baseline_variant_id": active_baseline_variant_id,
            "active_run_id": runtime_state.get("active_run_id"),
            "pending_decisions": pending_decisions,
            "active_interactions": active_interactions,
            "recent_reply_threads": recent_reply_threads,
            "waiting_interaction_id": waiting_interaction_id,
            "latest_thread_interaction_id": latest_thread_interaction_id,
            "default_reply_interaction_id": default_reply_interaction_id or latest_thread_interaction_id,
            "pending_user_message_count": int(runtime_state.get("pending_user_message_count") or 0),
            "stop_reason": runtime_state.get("stop_reason"),
            "active_interaction_id": runtime_state.get("active_interaction_id"),
            "retry_state": runtime_state.get("retry_state"),
            "last_transition_at": runtime_state.get("last_transition_at"),
            "last_artifact_interact_at": runtime_state.get("last_artifact_interact_at"),
            "last_tool_activity_at": runtime_state.get("last_tool_activity_at"),
            "last_tool_activity_name": runtime_state.get("last_tool_activity_name"),
            "tool_calls_since_last_artifact_interact": int(runtime_state.get("tool_calls_since_last_artifact_interact") or 0),
            "seconds_since_last_artifact_interact": interaction_watchdog.get("seconds_since_last_artifact_interact"),
            "last_delivered_batch_id": runtime_state.get("last_delivered_batch_id"),
            "last_delivered_at": runtime_state.get("last_delivered_at"),
            "bound_conversations": self._binding_sources_payload(quest_root).get("sources") or ["local:default"],
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
            "interaction_watchdog": interaction_watchdog,
            "team": {"mode": "single", "active_workers": []},
            "cloud": {"linked": False, "base_url": "https://deepscientist.cc"},
            "history_count": len(history),
            "artifact_count": len(artifacts),
            "recent_artifacts": artifacts[-5:],
            "recent_runs": recent_runs[-5:],
            "guidance": guidance,
        }
        with self._snapshot_cache_lock:
            self._snapshot_cache[cache_key] = {
                "state": state,
                "payload": copy.deepcopy(payload),
            }
        return payload

    def append_message(
        self,
        quest_id: str,
        role: str,
        content: str,
        source: str = "local",
        *,
        attachments: list[dict[str, Any]] | None = None,
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
        if isinstance(attachments, list) and attachments:
            record["attachments"] = [dict(item) for item in attachments if isinstance(item, dict)]
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
                "attachments": record.get("attachments") or [],
                "created_at": timestamp,
            },
        )
        if role == "user":
            self._enqueue_user_message(quest_root, record)
            self._write_active_user_requirements(
                quest_root,
                latest_requirement=record,
            )
            quest_data = read_yaml(quest_root / "quest.yaml", {})
            runtime_state = self._read_runtime_state(quest_root)
            status = str(runtime_state.get("status") or quest_data.get("status") or "")
            next_status = status
            if status == "waiting_for_user":
                interaction_state = read_json(quest_root / ".ds" / "interaction_state.json", {"open_requests": []})
                still_waiting = any(str(item.get("status") or "") == "waiting" for item in (interaction_state.get("open_requests") or []))
                if not still_waiting:
                    next_status = "running"
            elif status in {"stopped", "paused", "completed"}:
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
        self.update_runtime_state(
            quest_root=quest_root,
            active_run_id=None,
            status=status if status is not None else _UNSET,
            stop_reason=stop_reason,
            retry_state=None,
        )
        return self.snapshot(quest_id)

    def mark_completed(
        self,
        quest_id: str,
        *,
        stop_reason: str = "completed_by_user_approval",
    ) -> dict:
        quest_root = self._quest_root(quest_id)
        self.update_runtime_state(
            quest_root=quest_root,
            status="completed",
            active_run_id=None,
            active_interaction_id=None,
            stop_reason=stop_reason,
        )
        return self.snapshot(quest_id)

    def bind_source(self, quest_id: str, source: str) -> dict:
        quest_root = self._quest_root(quest_id)
        bindings_path = quest_root / ".ds" / "bindings.json"
        bindings = self._binding_sources_payload(quest_root)
        normalized_source = self._normalize_binding_source(source)
        next_sources = self._normalized_binding_sources([*(bindings.get("sources") or []), normalized_source])
        changed = list(bindings.get("sources") or []) != next_sources
        if changed:
            bindings["sources"] = next_sources
            write_json(bindings_path, bindings)
        return bindings

    def binding_sources(self, quest_id: str) -> list[str]:
        quest_root = self._quest_root(quest_id)
        return list(self._binding_sources_payload(quest_root).get("sources") or ["local:default"])

    def set_binding_sources(self, quest_id: str, sources: list[str]) -> dict:
        quest_root = self._quest_root(quest_id)
        bindings_path = quest_root / ".ds" / "bindings.json"
        payload = {"sources": self._normalized_binding_sources(sources)}
        write_json(bindings_path, payload)
        return payload

    def unbind_source(self, quest_id: str, source: str) -> dict:
        quest_root = self._quest_root(quest_id)
        bindings_path = quest_root / ".ds" / "bindings.json"
        bindings = self._binding_sources_payload(quest_root)
        normalized_source = self._normalize_binding_source(source)
        normalized_key = conversation_identity_key(normalized_source)
        changed = False
        sources: list[str] = []
        for item in list(bindings.get("sources") or []):
            existing = self._normalize_binding_source(str(item))
            if conversation_identity_key(existing) == normalized_key:
                changed = True
                continue
            sources.append(existing)
            if existing != item:
                changed = True
        normalized_sources = self._normalized_binding_sources(sources)
        if normalized_sources != list(bindings.get("sources") or []):
            changed = True
        if changed:
            bindings["sources"] = normalized_sources
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
        quest_yaml_path = self._quest_yaml_path(quest_root)
        if not quest_yaml_path.exists():
            raise FileNotFoundError(f"Unknown quest `{quest_id}`.")

        quest_data = self.read_quest_yaml(quest_root)
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

    def update_baseline_state(
        self,
        quest_root: Path,
        *,
        baseline_gate: str | None = None,
        confirmed_baseline_ref: dict[str, Any] | None | object = _UNSET,
        active_anchor: str | None | object = _UNSET,
    ) -> dict[str, Any]:
        quest_yaml_path = self._quest_yaml_path(quest_root)
        if not quest_yaml_path.exists():
            raise FileNotFoundError(f"Unknown quest `{quest_root.name}`.")

        quest_data = self.read_quest_yaml(quest_root)
        changed = False

        if baseline_gate is not None:
            normalized_gate = self._normalize_baseline_gate(baseline_gate)
            if quest_data.get("baseline_gate") != normalized_gate:
                quest_data["baseline_gate"] = normalized_gate
                changed = True

        if confirmed_baseline_ref is not _UNSET:
            normalized_ref = dict(confirmed_baseline_ref) if isinstance(confirmed_baseline_ref, dict) else None
            if quest_data.get("confirmed_baseline_ref") != normalized_ref:
                quest_data["confirmed_baseline_ref"] = normalized_ref
                changed = True

        if active_anchor is not _UNSET:
            normalized_anchor = str(active_anchor or "").strip()
            if not normalized_anchor:
                raise ValueError("`active_anchor` cannot be empty.")
            from ..prompts.builder import STANDARD_SKILLS

            if normalized_anchor not in STANDARD_SKILLS:
                allowed = ", ".join(STANDARD_SKILLS)
                raise ValueError(f"Unsupported active anchor `{normalized_anchor}`. Allowed values: {allowed}.")
            if quest_data.get("active_anchor") != normalized_anchor:
                quest_data["active_anchor"] = normalized_anchor
                changed = True

        if changed:
            quest_data["updated_at"] = utc_now()
            write_yaml(quest_yaml_path, quest_data)
        return quest_data

    def update_startup_context(
        self,
        quest_root: Path,
        *,
        requested_baseline_ref: dict[str, Any] | None | object = _UNSET,
        startup_contract: dict[str, Any] | None | object = _UNSET,
    ) -> dict[str, Any]:
        quest_yaml_path = self._quest_yaml_path(quest_root)
        if not quest_yaml_path.exists():
            raise FileNotFoundError(f"Unknown quest `{quest_root.name}`.")

        quest_data = self.read_quest_yaml(quest_root)
        changed = False

        if requested_baseline_ref is not _UNSET:
            normalized_requested = (
                dict(requested_baseline_ref) if isinstance(requested_baseline_ref, dict) else None
            )
            if quest_data.get("requested_baseline_ref") != normalized_requested:
                quest_data["requested_baseline_ref"] = normalized_requested
                changed = True

        if startup_contract is not _UNSET:
            normalized_contract = dict(startup_contract) if isinstance(startup_contract, dict) else None
            if quest_data.get("startup_contract") != normalized_contract:
                quest_data["startup_contract"] = normalized_contract
                changed = True

        if changed:
            quest_data["updated_at"] = utc_now()
            write_yaml(quest_yaml_path, quest_data)
        return quest_data

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
            last_transition_at = self._runtime_recovery_timestamp(runtime_state, quest_data)
            recoverable = self._runtime_recovery_eligible(
                previous_status=previous_status,
                active_run_id=active_run_id or None,
                last_transition_at=last_transition_at,
            )
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
            if recoverable:
                summary = f"{summary} Auto-resume is eligible within the 24-hour recovery window."
            append_jsonl(
                quest_root / ".ds" / "events.jsonl",
                {
                    "event_id": generate_id("evt"),
                    "type": "quest.runtime_reconciled",
                    "quest_id": quest_root.name,
                    "previous_status": previous_status,
                    "abandoned_run_id": active_run_id or None,
                    "last_transition_at": last_transition_at,
                    "recoverable": recoverable,
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
                    "last_transition_at": last_transition_at,
                    "recoverable": recoverable,
                    "status": "stopped",
                }
            )
        return reconciled

    @staticmethod
    def _parse_runtime_timestamp(value: Any) -> datetime | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        candidate = normalized.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _runtime_recovery_timestamp(self, runtime_state: dict[str, Any], quest_data: dict[str, Any]) -> str | None:
        for candidate in (
            runtime_state.get("last_transition_at"),
            quest_data.get("updated_at"),
            quest_data.get("created_at"),
        ):
            parsed = self._parse_runtime_timestamp(candidate)
            if parsed is None:
                continue
            return parsed.isoformat()
        return None

    def _runtime_recovery_eligible(
        self,
        *,
        previous_status: str,
        active_run_id: str | None,
        last_transition_at: str | None,
    ) -> bool:
        if previous_status != "running" and not str(active_run_id or "").strip():
            return False
        parsed = self._parse_runtime_timestamp(last_transition_at)
        if parsed is None:
            return False
        return datetime.now(UTC) - parsed <= _CRASH_AUTO_RESUME_WINDOW

    def history(self, quest_id: str, limit: int = 100) -> list[dict]:
        return self._read_cached_jsonl(self._quest_root(quest_id) / ".ds" / "conversations" / "main.jsonl")[-limit:]

    def workflow(self, quest_id: str) -> dict:
        quest_root = self._quest_root(quest_id)
        workspace_root = self.active_workspace_root(quest_root)
        snapshot = self.snapshot(quest_id)
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
            resolved_document_id = document_id or self._path_to_document_id(
                normalized,
                quest_root=quest_root,
                workspace_root=workspace_root,
            )
            changed_files.append(
                {
                    "path": normalized,
                    "source": source,
                    "document_id": resolved_document_id,
                    "writable": writable,
                }
            )

        for relative in ("brief.md", "plan.md", "status.md", "SUMMARY.md"):
            add_file(
                str(workspace_root / relative),
                source="document",
                document_id=relative,
                writable=True,
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
                    self._parse_codex_history_cached(
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

    def events(
        self,
        quest_id: str,
        *,
        after: int = 0,
        before: int | None = None,
        limit: int = 200,
        tail: bool = False,
    ) -> dict:
        event_path = self._quest_root(quest_id) / ".ds" / "events.jsonl"
        normalized_limit = max(limit, 0)
        direction = "after"
        if before is not None:
            direction = "before"
        elif tail and normalized_limit > 0:
            direction = "tail"
        sliced_records, total_records, has_more = self._read_jsonl_cursor_slice(
            event_path,
            after=after,
            before=before,
            limit=normalized_limit,
            tail=tail,
        )
        enriched = []
        for cursor, item in sliced_records:
            enriched.append(
                {
                    "cursor": cursor,
                    "event_id": item.get("event_id") or f"evt-{quest_id}-{cursor}",
                    **item,
                }
            )
        if before is not None:
            next_cursor = enriched[-1]["cursor"] if enriched else max(min(int(before or 0) - 1, total_records), 0)
        elif tail:
            next_cursor = total_records
        else:
            next_cursor = enriched[-1]["cursor"] if enriched else max(int(after or 0), 0)
        oldest_cursor = enriched[0]["cursor"] if enriched else None
        newest_cursor = enriched[-1]["cursor"] if enriched else None
        return {
            "quest_id": quest_id,
            "cursor": next_cursor,
            "has_more": has_more,
            "oldest_cursor": oldest_cursor,
            "newest_cursor": newest_cursor,
            "direction": direction,
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

    def stage_view(self, quest_id: str, selection: dict[str, Any] | None = None) -> dict[str, Any]:
        quest_root = self._quest_root(quest_id)
        resolved_selection = dict(selection or {})
        selection_ref = str(resolved_selection.get("selection_ref") or "").strip()
        selection_type = str(resolved_selection.get("selection_type") or "stage_node").strip() or None
        if (
            selection_type == "branch_node"
            and selection_ref
            and not str(resolved_selection.get("branch_name") or "").strip()
        ):
            resolved_selection["branch_name"] = selection_ref
        trace = None
        if selection_ref:
            try:
                trace_payload = self.node_trace(quest_id, selection_ref, selection_type=selection_type)
                trace = trace_payload.get("trace") if isinstance(trace_payload, dict) else None
            except FileNotFoundError:
                trace = None
        return QuestStageViewBuilder(
            self,
            quest_root,
            snapshot=self.snapshot(quest_id),
            selection=resolved_selection,
            trace=trace if isinstance(trace, dict) else None,
        ).build()

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

    def explorer(
        self,
        quest_id: str,
        revision: str | None = None,
        mode: str | None = None,
        profile: str | None = None,
    ) -> dict:
        if revision:
            return self._revision_explorer(quest_id, revision=revision, mode=mode or "ref")

        quest_root = self._quest_root(quest_id)
        workspace_root = self.active_workspace_root(quest_root)
        git_status = self._git_status_map(workspace_root)

        root_nodes = self._tree_children(
            workspace_root,
            workspace_root,
            git_status=git_status,
            changed_paths={},
            profile=profile,
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
                "profile": profile,
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

        resolution_root = (
            quest_root
            if document_id.startswith(("questpath::", "memory::"))
            else workspace_root
        )
        try:
            path, writable, scope, source_kind = self._resolve_document(resolution_root, document_id)
        except FileNotFoundError:
            legacy_relative = None
            if document_id.startswith("path::"):
                legacy_relative = document_id.split("::", 1)[1].lstrip("/")
            if legacy_relative and legacy_relative.startswith("literature/arxiv/"):
                path, writable, scope, source_kind = self._resolve_document(
                    quest_root, f"questpath::{legacy_relative}"
                )
            else:
                raise
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
        if document_id.startswith("questpath::"):
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
    def _path_to_document_id(
        path: str | Path | None,
        *,
        quest_root: Path,
        workspace_root: Path,
    ) -> str | None:
        if not path:
            return None
        try:
            candidate = Path(path).expanduser()
            if not candidate.is_absolute():
                candidate = (workspace_root / candidate).resolve()
            else:
                candidate = candidate.resolve()
        except OSError:
            return None

        try:
            relative_to_workspace = candidate.relative_to(workspace_root.resolve()).as_posix()
            return f"path::{relative_to_workspace}"
        except ValueError:
            pass

        try:
            relative_to_quest = candidate.relative_to(quest_root.resolve()).as_posix()
            return f"questpath::{relative_to_quest}"
        except ValueError:
            return None

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
        asset_root = (
            quest_root
            if document_id.startswith(("questpath::", "memory::"))
            else workspace_root
        )
        asset_path = resolve_within(asset_root, asset_relative)
        ensure_dir(asset_path.parent)
        asset_path.write_bytes(content)
        asset_document_scope = "questpath" if document_id.startswith(("questpath::", "memory::")) else "path"
        asset_document_id = f"{asset_document_scope}::{asset_relative}"
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

    def _read_interaction_state(self, quest_root: Path) -> dict[str, Any]:
        state = self._read_cached_json(quest_root / ".ds" / "interaction_state.json", {})
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
    def _active_user_requirements_path(quest_root: Path) -> Path:
        return quest_root / "memory" / "knowledge" / "active-user-requirements.md"

    @staticmethod
    def _default_message_queue() -> dict[str, Any]:
        return {
            "version": 1,
            "pending": [],
            "completed": [],
        }

    def _default_runtime_state(
        self,
        quest_root: Path,
        *,
        quest_yaml: dict[str, Any] | None = None,
        queue_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        quest_yaml = dict(quest_yaml or self.read_quest_yaml(quest_root))
        queue_payload = dict(queue_payload or self._read_message_queue(quest_root))
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
            "last_tool_activity_at": None,
            "last_tool_activity_name": None,
            "tool_calls_since_last_artifact_interact": 0,
            "pending_user_message_count": pending_count,
            "last_delivered_batch_id": None,
            "last_delivered_at": None,
            "retry_state": None,
        }

    def _default_agent_status(self, quest_root: Path) -> dict[str, Any]:
        quest_yaml = self.read_quest_yaml(quest_root)
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
        lab_canvas_state_path = self._lab_canvas_state_path(quest_root)
        if not lab_canvas_state_path.exists():
            write_json(lab_canvas_state_path, self._default_lab_canvas_state(quest_root))
        agent_status_path = self._agent_status_path(quest_root)
        if not agent_status_path.exists():
            write_json(agent_status_path, self._default_agent_status(quest_root))

    def _read_message_queue(self, quest_root: Path) -> dict[str, Any]:
        payload = self._read_cached_json(self._message_queue_path(quest_root), self._default_message_queue())
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
        quest_yaml = self.read_quest_yaml(quest_root)
        queue_payload = self._read_message_queue(quest_root)
        defaults = self._default_runtime_state(
            quest_root,
            quest_yaml=quest_yaml,
            queue_payload=queue_payload,
        )
        payload = self._read_cached_json(self._runtime_state_path(quest_root), defaults)
        if not isinstance(payload, dict):
            payload = defaults
        merged = {**defaults, **payload}
        merged["pending_user_message_count"] = int(merged.get("pending_user_message_count") or 0)
        merged["tool_calls_since_last_artifact_interact"] = int(merged.get("tool_calls_since_last_artifact_interact") or 0)
        merged["retry_state"] = dict(merged.get("retry_state") or {}) if isinstance(merged.get("retry_state"), dict) else None
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
        last_tool_activity_at: str | None | object = _UNSET,
        last_tool_activity_name: str | None | object = _UNSET,
        tool_calls_since_last_artifact_interact: int | object = _UNSET,
        pending_user_message_count: int | object = _UNSET,
        last_delivered_batch_id: str | None | object = _UNSET,
        last_delivered_at: str | None | object = _UNSET,
        display_status: str | None | object = _UNSET,
        retry_state: dict[str, Any] | None | object = _UNSET,
    ) -> dict[str, Any]:
        with self._runtime_state_lock(quest_root):
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
            elif status is not _UNSET and str(state.get("status") or "") not in {"stopped", "paused", "error", "completed"}:
                state["stop_reason"] = None
            if active_interaction_id is not _UNSET:
                state["active_interaction_id"] = str(active_interaction_id).strip() if active_interaction_id else None
            if last_artifact_interact_at is not _UNSET:
                state["last_artifact_interact_at"] = last_artifact_interact_at
            if last_tool_activity_at is not _UNSET:
                state["last_tool_activity_at"] = last_tool_activity_at
            if last_tool_activity_name is not _UNSET:
                state["last_tool_activity_name"] = str(last_tool_activity_name).strip() if last_tool_activity_name else None
            if tool_calls_since_last_artifact_interact is not _UNSET:
                state["tool_calls_since_last_artifact_interact"] = max(0, int(tool_calls_since_last_artifact_interact))
            if pending_user_message_count is not _UNSET:
                state["pending_user_message_count"] = max(0, int(pending_user_message_count))
            if last_delivered_batch_id is not _UNSET:
                state["last_delivered_batch_id"] = str(last_delivered_batch_id).strip() if last_delivered_batch_id else None
            if last_delivered_at is not _UNSET:
                state["last_delivered_at"] = last_delivered_at
            if retry_state is not _UNSET:
                state["retry_state"] = dict(retry_state) if isinstance(retry_state, dict) else None
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
            "attachments": [dict(item) for item in (record.get("attachments") or []) if isinstance(item, dict)],
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

    def _write_active_user_requirements(
        self,
        quest_root: Path,
        *,
        latest_requirement: dict[str, Any] | None,
    ) -> Path:
        quest_yaml = self.read_quest_yaml(quest_root)
        quest_goal = str(quest_yaml.get("title") or quest_yaml.get("quest_id") or quest_root.name).strip()
        user_messages = [
            item
            for item in read_jsonl(quest_root / ".ds" / "conversations" / "main.jsonl")
            if str(item.get("role") or "") == "user"
        ]
        latest = latest_requirement or (user_messages[-1] if user_messages else None)
        lines = [
            "# Active User Requirements",
            "",
            f"- updated_at: {utc_now()}",
            f"- quest_id: {quest_yaml.get('quest_id') or quest_root.name}",
            "",
            "## Long-Term Goal",
            "",
            quest_goal or "No long-term goal recorded yet.",
            "",
            "## Working Rule",
            "",
            "Treat the requirements in this file as higher priority than stale background plans.",
            "",
            "## Latest Added Requirement",
            "",
        ]
        if latest:
            lines.extend(
                [
                    f"- source: {latest.get('source') or 'local'}",
                    f"- created_at: {latest.get('created_at') or utc_now()}",
                    "",
                    str(latest.get("content") or "").strip() or "No latest requirement text was captured.",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "No explicit user requirement has been recorded yet.",
                    "",
                ]
            )
        lines.extend(
            [
                "## Active Requirement History",
                "",
            ]
        )
        if user_messages:
            for index, item in enumerate(user_messages[-12:], start=1):
                source = str(item.get("source") or "local").strip() or "local"
                created_at = str(item.get("created_at") or "").strip() or "unknown"
                content = str(item.get("content") or "").strip() or "(empty)"
                lines.append(f"{index}. [{source}] [{created_at}] {content}")
        else:
            lines.append("1. No user messages yet.")
        path = self._active_user_requirements_path(quest_root)
        write_text(path, "\n".join(lines).rstrip() + "\n")
        return path

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
        surface_actions: list[dict[str, Any]] | None = None,
        connector_hints: dict[str, Any] | None = None,
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
            "surface_actions": [dict(item) for item in (surface_actions or []) if isinstance(item, dict)],
            "connector_hints": dict(connector_hints) if isinstance(connector_hints, dict) else {},
            "created_at": timestamp,
        }
        append_jsonl(self._interaction_journal_path(quest_root), payload)
        self.update_runtime_state(
            quest_root=quest_root,
            active_interaction_id=interaction_id or artifact_id,
            last_artifact_interact_at=timestamp,
            last_tool_activity_at=timestamp,
            last_tool_activity_name="artifact.interact",
            tool_calls_since_last_artifact_interact=0,
            pending_user_message_count=len((self._read_message_queue(quest_root).get("pending") or [])),
        )
        return payload

    def record_tool_activity(
        self,
        quest_root: Path,
        *,
        tool_name: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        timestamp = created_at or utc_now()
        current_state = self._read_runtime_state(quest_root)
        next_count = int(current_state.get("tool_calls_since_last_artifact_interact") or 0) + 1
        payload = {
            "event_id": generate_id("evt"),
            "type": "tool_activity",
            "quest_id": quest_root.name,
            "tool_name": str(tool_name or "").strip() or "tool",
            "tool_calls_since_last_artifact_interact": next_count,
            "created_at": timestamp,
        }
        append_jsonl(self._interaction_journal_path(quest_root), payload)
        self.update_runtime_state(
            quest_root=quest_root,
            last_tool_activity_at=timestamp,
            last_tool_activity_name=payload["tool_name"],
            tool_calls_since_last_artifact_interact=next_count,
        )
        return payload

    @staticmethod
    def _seconds_since_iso_timestamp(value: str | None) -> int | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        candidate = normalized.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(int((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()), 0)

    def artifact_interaction_watchdog_status(self, quest_root: Path) -> dict[str, Any]:
        runtime_state = self._read_runtime_state(quest_root)
        last_artifact_interact_at = str(runtime_state.get("last_artifact_interact_at") or "").strip() or None
        last_tool_activity_at = str(runtime_state.get("last_tool_activity_at") or "").strip() or None
        return {
            "last_artifact_interact_at": last_artifact_interact_at,
            "seconds_since_last_artifact_interact": self._seconds_since_iso_timestamp(last_artifact_interact_at),
            "tool_calls_since_last_artifact_interact": int(runtime_state.get("tool_calls_since_last_artifact_interact") or 0),
            "last_tool_activity_at": last_tool_activity_at,
            "seconds_since_last_tool_activity": self._seconds_since_iso_timestamp(last_tool_activity_at),
            "last_tool_activity_name": str(runtime_state.get("last_tool_activity_name") or "").strip() or None,
        }

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
                "attachments": [dict(attachment) for attachment in (item.get("attachments") or []) if isinstance(attachment, dict)],
                "reply_to_interaction_id": item.get("reply_to_interaction_id"),
            }
            for item in delivered_messages
        ]
        if delivered_messages:
            lines = [
                self.localized_copy(
                    quest_root=quest_root,
                    zh="这是最新用户的要求（按时间顺序拼接）。这些消息优先于当前后台子任务：",
                    en="These are the latest user requirements in chronological order. They take priority over the current background subtask:",
                ),
                "",
                self.localized_copy(
                    quest_root=quest_root,
                    zh="- 先暂停当前非必要子任务，不要继续沿着旧计划埋头推进。",
                    en="- Pause any non-essential current subtask instead of continuing the stale plan blindly.",
                ),
                self.localized_copy(
                    quest_root=quest_root,
                    zh="- 立即发送一条有实际内容的 follow-up artifact.interact(...)；如果当前 connector 的运行时已经替你发过即时回执，就不要再重复发送一条只有“已收到/处理中”的确认。",
                    en="- Immediately send one substantive follow-up artifact.interact(...); if the active connector runtime already sent the transport-level receipt acknowledgement, do not send a redundant receipt-only message such as 'received' or 'processing'.",
                ),
                self.localized_copy(
                    quest_root=quest_root,
                    zh="- 如果可以直接回答，就在这次 follow-up artifact.interact(...) 里直接完整回答。",
                    en="- If you can answer directly, give the full answer in that follow-up artifact.interact(...).",
                ),
                self.localized_copy(
                    quest_root=quest_root,
                    zh="- 如果暂时不能直接回答，就在这次 follow-up artifact.interact(...) 里说明你将先处理该用户请求，给出简短计划、最近回传点与预计输出。",
                    en="- If you cannot answer directly yet, explain in that follow-up artifact.interact(...) that you will handle this user request first, and include a short plan, nearest report-back point, and expected output.",
                ),
                self.localized_copy(
                    quest_root=quest_root,
                    zh="- 完成该用户请求后，再立刻调用 artifact.interact(...) 汇报完整结果。",
                    en="- After completing that user request, immediately call artifact.interact(...) again with the full result.",
                ),
                self.localized_copy(
                    quest_root=quest_root,
                    zh="- 只有在用户新消息没有改变任务主线时，才恢复原来的后台任务。",
                    en="- Resume the older background task only if the new user message did not change the main objective.",
                ),
                "",
            ]
            for index, item in enumerate(delivered_messages, start=1):
                source = str(item.get("conversation_id") or item.get("source") or "local")
                lines.append(f"{index}. [{source}] {item.get('content') or ''}")
            agent_instruction = "\n".join(lines).strip()
        else:
            lines = [
                self.localized_copy(
                    quest_root=quest_root,
                    zh="当前用户并没有发送任何消息，请按照用户的要求继续进行任务。",
                    en="No new user message has arrived. Continue the task according to the user's requirements.",
                ),
                "",
                self.localized_copy(
                    quest_root=quest_root,
                    zh="以下是最近 10 次与 artifact 交互相关的记录：",
                    en="Here are the latest 10 artifact-related interaction records:",
                ),
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
                lines.append(
                    self.localized_copy(
                        quest_root=quest_root,
                        zh="1. 暂无历史交互记录。",
                        en="1. No recent interaction records.",
                    )
                )
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
        if document_id.startswith("questpath::"):
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
                    "folder_kind": self._snapshot_folder_kind(child, relative),
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

    @staticmethod
    def _snapshot_folder_kind(tree: dict[str, dict | None], relative: str) -> str | None:
        normalized = str(relative or "").strip().replace("\\", "/")
        if not normalized or normalized.startswith(".ds/"):
            return None
        if not isinstance(tree, dict):
            return None
        if tree.get("main.tex") is None and "main.tex" in tree:
            return "latex"
        for name, child in tree.items():
            if child is not None:
                continue
            if Path(name).suffix.lower() == ".tex":
                return "latex"
        return None

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
        profile: str | None = None,
        depth: int = 0,
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
                relative = path.relative_to(quest_root).as_posix()
                if self._skip_explorer_profile_relative(relative, profile):
                    continue
            except OSError:
                continue
            try:
                is_dir = path.is_dir()
            except OSError:
                continue
            if is_dir:
                truncate_children = self._truncate_explorer_directory(relative, profile=profile, depth=depth)
                children = (
                    []
                    if truncate_children
                    else self._tree_children(
                        quest_root,
                        path,
                        git_status=git_status,
                        changed_paths=changed_paths,
                        profile=profile,
                        depth=depth + 1,
                    )
                )
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
        folder_kind = self._folder_kind_for(path, relative)
        return {
            "id": f"dir::{relative}",
            "name": path.name,
            "path": relative,
            "kind": "directory",
            "scope": scope,
            "folder_kind": folder_kind,
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
    def _skip_explorer_profile_relative(relative: str, profile: str | None) -> bool:
        if profile != "mobile":
            return False
        normalized = relative.strip("/")
        if not normalized:
            return False
        parts = PurePosixPath(normalized).parts
        top = parts[0] if parts else normalized
        if top in {".codex", ".claude", ".ds", "tmp", "userfiles", "artifacts"}:
            return True
        if top.startswith(".") and normalized not in {".gitignore"}:
            return True
        return False

    @staticmethod
    def _truncate_explorer_directory(relative: str, *, profile: str | None, depth: int) -> bool:
        if profile != "mobile":
            return False
        normalized = relative.strip("/")
        if not normalized:
            return False
        parts = PurePosixPath(normalized).parts
        top = parts[0] if parts else normalized
        if top == "memory":
            return False
        if top == "baselines":
            return depth >= 1
        if top in {"literature", "paper", "experiments", "handoffs"}:
            return depth >= 2
        return depth >= 1

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
    def _folder_kind_for(path: Path, relative: str) -> str | None:
        try:
            if not path.exists() or not path.is_dir():
                return None
        except OSError:
            return None
        if QuestService._looks_like_latex_folder(path, relative):
            return "latex"
        return None

    @staticmethod
    def _looks_like_latex_folder(path: Path, relative: str) -> bool:
        normalized = str(relative or "").strip().replace("\\", "/")
        if not normalized:
            return False
        try:
            if (path / "main.tex").is_file():
                return True
        except OSError:
            return False
        if normalized.startswith(".ds/"):
            return False
        try:
            return any(item.is_file() and item.suffix.lower() == ".tex" for item in path.iterdir())
        except OSError:
            return False

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


def _structured_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)


def _is_bash_exec_item(event: dict, item: dict) -> bool:
    server = str(item.get("server") or event.get("server") or "").strip()
    tool = str(item.get("tool") or event.get("tool") or "").strip()
    return server == "bash_exec" and tool == "bash_exec"


def _tool_args(event: dict, item: dict) -> str:
    if _is_bash_exec_item(event, item):
        for value in (
            item.get("arguments"),
            event.get("arguments"),
            item.get("input"),
            event.get("input"),
        ):
            text = _structured_text(value)
            if text:
                return text
        return ""
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
    if _is_bash_exec_item(event, item):
        for value in (
            item.get("result"),
            item.get("output"),
            item.get("content"),
            event.get("result"),
            event.get("output"),
            event.get("content"),
            item.get("aggregated_output"),
            event.get("aggregated_output"),
        ):
            text = _structured_text(value)
            if text:
                return text
        return ""
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
        if server == "bash_exec" and tool == "bash_exec" and isinstance(arguments.get("id"), str):
            metadata["bash_id"] = arguments.get("id")
    result_payload = _mcp_result_payload(item)
    if server == "bash_exec" and tool == "bash_exec":
        for key in (
            "bash_id",
            "status",
            "command",
            "workdir",
            "cwd",
            "kind",
            "comment",
            "started_at",
            "finished_at",
            "exit_code",
            "stop_reason",
            "last_progress",
            "log_path",
            "watchdog_after_seconds",
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

    for raw in read_jsonl_tail(history_path, _CODEX_HISTORY_TAIL_LIMIT):
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
            search_payload = extract_web_search_payload(item)
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
