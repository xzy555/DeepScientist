from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..shared import ensure_dir, read_json, utc_now, write_json


def _format_state_label(value: str | None) -> str:
    normalized = str(value or "").strip().replace("_", " ").replace("-", " ")
    if not normalized:
        return "Unknown"
    return " ".join(part.capitalize() for part in normalized.split())


def _compact_text(value: object, *, limit: int = 240) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except TypeError:
            text = str(value)
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _normalize_branch_name(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _infer_stage_from_skill(skill_id: object) -> str | None:
    normalized = str(skill_id or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"scout", "research", "literature"}:
        return "scout"
    if normalized.startswith("baseline"):
        return "baseline"
    if normalized in {"idea", "scout+idea"}:
        return "idea"
    if normalized in {"experiment", "analysis-campaign", "analysis"}:
        return "experiment"
    if normalized in {"write", "finalize"}:
        return "writing"
    if normalized == "decision":
        return "decision"
    return normalized


def _infer_stage_from_event_type(event_type: object) -> str | None:
    normalized = str(event_type or "").strip().lower()
    if not normalized:
        return None
    if normalized.startswith("research."):
        return "scout"
    if normalized.startswith("baseline."):
        return "baseline"
    if normalized.startswith("idea."):
        return "idea"
    if normalized.startswith("experiment."):
        return "experiment"
    if normalized.startswith("write."):
        return "writing"
    if normalized.startswith("decision.") or normalized.startswith("pi."):
        return "decision"
    return None


def _infer_stage_from_artifact(record: dict[str, Any]) -> str | None:
    explicit = _infer_stage_from_skill(record.get("stage_key"))
    if explicit:
        return explicit
    explicit = _infer_stage_from_skill(record.get("skill_id"))
    if explicit:
        return explicit
    explicit = _infer_stage_from_skill(record.get("run_kind"))
    if explicit:
        return explicit
    kind = str(record.get("kind") or "").strip().lower()
    if kind == "baseline":
        return "baseline"
    if kind in {"decision", "approval"}:
        return "decision"
    if kind in {"report", "paper", "draft"}:
        return "writing"
    return None


def _load_artifact_record(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file() or path.suffix.lower() != ".json":
        return None
    payload = read_json(path, {})
    if isinstance(payload, dict) and payload:
        return payload
    return None


def _build_run_contexts(quest_root: Path, *, default_branch: str) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    artifact_roots = [quest_root / "artifacts"]
    worktrees_root = quest_root / ".ds" / "worktrees"
    if worktrees_root.exists():
        artifact_roots.extend(path / "artifacts" for path in sorted(worktrees_root.iterdir()) if path.is_dir())
    seen_paths: set[str] = set()
    for artifacts_root in artifact_roots:
        if not artifacts_root.exists():
            continue
        for artifact_path in sorted(path for path in artifacts_root.glob("*/*.json") if path.is_file()):
            resolved = str(artifact_path.resolve())
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            record = _load_artifact_record(artifact_path)
            if not record:
                continue
            run_id = str(record.get("run_id") or "").strip()
            if not run_id:
                continue
            branch_name = _normalize_branch_name(record.get("branch"), fallback=default_branch)
            stage_key = _infer_stage_from_artifact(record)
            current = contexts.get(run_id) or {
                "run_id": run_id,
                "branch_name": branch_name,
                "stage_key": stage_key,
                "worktree_rel_path": record.get("worktree_rel_path"),
                "summary": None,
                "updated_at": None,
            }
            summary = (
                _compact_text(record.get("summary"))
                or _compact_text(record.get("message"))
                or _compact_text(record.get("reason"))
            )
            updated_at = str(record.get("updated_at") or record.get("created_at") or "").strip() or None
            current["branch_name"] = current.get("branch_name") or branch_name
            current["stage_key"] = current.get("stage_key") or stage_key
            current["worktree_rel_path"] = current.get("worktree_rel_path") or record.get("worktree_rel_path")
            current["summary"] = current.get("summary") or summary
            current["updated_at"] = current.get("updated_at") or updated_at
            contexts[run_id] = current
    return contexts


def _resolve_entry_context(
    quest_root: Path,
    entry: dict[str, Any],
    *,
    default_branch: str,
    run_contexts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    run_id = str(entry.get("run_id") or "").strip() or None
    raw_event_type = str(entry.get("raw_event_type") or entry.get("kind") or "").strip()
    run_context = run_contexts.get(run_id or "")
    artifact_context: dict[str, Any] | None = None
    for raw_path in entry.get("paths") or []:
        try:
            path = Path(str(raw_path))
        except TypeError:
            continue
        if "artifacts" not in path.parts:
            continue
        artifact_context = _load_artifact_record(path)
        if artifact_context:
            break
    branch_name = _normalize_branch_name(
        (artifact_context or {}).get("branch") or (run_context or {}).get("branch_name"),
        fallback=default_branch,
    )
    stage_key = (
        _infer_stage_from_skill(entry.get("skill_id"))
        or _infer_stage_from_artifact(artifact_context or {})
        or (run_context or {}).get("stage_key")
        or _infer_stage_from_event_type(raw_event_type)
        or "general"
    )
    return {
        "run_id": run_id,
        "branch_name": branch_name,
        "stage_key": stage_key,
        "worktree_rel_path": (artifact_context or {}).get("worktree_rel_path")
        or (run_context or {}).get("worktree_rel_path"),
        "trace_confidence": "artifact"
        if artifact_context
        else "run_context"
        if run_context
        else "default_branch",
    }


def _build_action(entry: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_id": entry.get("id"),
        "kind": entry.get("kind"),
        "title": entry.get("title"),
        "summary": entry.get("summary"),
        "status": entry.get("status"),
        "created_at": entry.get("created_at"),
        "run_id": context.get("run_id"),
        "skill_id": entry.get("skill_id"),
        "branch_name": context.get("branch_name"),
        "stage_key": context.get("stage_key"),
        "worktree_rel_path": context.get("worktree_rel_path"),
        "tool_name": entry.get("tool_name"),
        "tool_call_id": entry.get("tool_call_id"),
        "mcp_server": entry.get("mcp_server"),
        "mcp_tool": entry.get("mcp_tool"),
        "args": entry.get("args"),
        "output": entry.get("output"),
        "reason": entry.get("reason"),
        "raw_event_type": entry.get("raw_event_type"),
        "paths": [str(item) for item in (entry.get("paths") or []) if item],
        "trace_confidence": context.get("trace_confidence"),
    }


def _summarize_trace(actions: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    for action in reversed(actions):
        summary = _compact_text(
            action.get("summary")
            or action.get("output")
            or action.get("args")
            or action.get("title")
        )
        if summary:
            return summary, str(action.get("status") or "").strip() or None
    return None, None


def _build_counts(actions: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "actions": len(actions),
        "tool_calls": 0,
        "tool_results": 0,
        "thoughts": 0,
        "artifacts": 0,
        "runs": 0,
    }
    for action in actions:
        kind = str(action.get("kind") or "").strip()
        if kind == "tool_call":
            counts["tool_calls"] += 1
        elif kind == "tool_result":
            counts["tool_results"] += 1
        elif kind == "thought":
            counts["thoughts"] += 1
        elif kind == "artifact":
            counts["artifacts"] += 1
        elif kind == "run":
            counts["runs"] += 1
    return counts


def _build_trace_item(
    *,
    selection_type: str,
    selection_ref: str,
    title: str,
    branch_name: str,
    stage_key: str | None,
    worktree_rel_path: str | None,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered_actions = sorted(actions, key=lambda item: str(item.get("created_at") or item.get("action_id") or ""))
    summary, status = _summarize_trace(ordered_actions)
    updated_at = (
        str(ordered_actions[-1].get("created_at") or "").strip()
        if ordered_actions
        else None
    ) or None
    run_ids = sorted({str(item.get("run_id") or "").strip() for item in ordered_actions if item.get("run_id")})
    skill_ids = sorted({str(item.get("skill_id") or "").strip() for item in ordered_actions if item.get("skill_id")})
    return {
        "selection_type": selection_type,
        "selection_ref": selection_ref,
        "title": title,
        "summary": summary,
        "status": status,
        "branch_name": branch_name,
        "stage_key": stage_key,
        "stage_title": _format_state_label(stage_key) if stage_key else None,
        "worktree_rel_path": worktree_rel_path,
        "updated_at": updated_at,
        "counts": _build_counts(ordered_actions),
        "run_ids": run_ids,
        "skill_ids": skill_ids,
        "actions": ordered_actions,
    }


class QuestNodeTraceManager:
    def __init__(self, quest_root: Path) -> None:
        self.quest_root = quest_root

    @property
    def materialized_path(self) -> Path:
        return ensure_dir(self.quest_root / ".ds" / "node_traces") / "index.json"

    def materialize(
        self,
        *,
        quest_id: str,
        workflow: dict[str, Any],
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        default_branch = str((snapshot or {}).get("branch") or "main").strip() or "main"
        run_contexts = _build_run_contexts(self.quest_root, default_branch=default_branch)
        entries = list(workflow.get("entries") or [])

        event_items: list[dict[str, Any]] = []
        stage_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        branch_groups: dict[str, list[dict[str, Any]]] = {}

        for raw_entry in entries:
            if not isinstance(raw_entry, dict):
                continue
            entry = dict(raw_entry)
            selection_ref = str(entry.get("id") or "").strip()
            if not selection_ref:
                continue
            context = _resolve_entry_context(
                self.quest_root,
                entry,
                default_branch=default_branch,
                run_contexts=run_contexts,
            )
            action = _build_action(entry, context)
            branch_name = str(context.get("branch_name") or default_branch)
            stage_key = str(context.get("stage_key") or "general")
            worktree_rel_path = context.get("worktree_rel_path")
            event_items.append(
                _build_trace_item(
                    selection_type="event_node",
                    selection_ref=selection_ref,
                    title=str(entry.get("title") or selection_ref),
                    branch_name=branch_name,
                    stage_key=stage_key,
                    worktree_rel_path=worktree_rel_path if isinstance(worktree_rel_path, str) else None,
                    actions=[action],
                )
            )
            stage_groups.setdefault((branch_name, stage_key), []).append(action)
            branch_groups.setdefault(branch_name, []).append(action)

        stage_items = [
            _build_trace_item(
                selection_type="stage_node",
                selection_ref=f"stage:{branch_name}:{stage_key}",
                title=f"{branch_name} · {_format_state_label(stage_key)}",
                branch_name=branch_name,
                stage_key=stage_key,
                worktree_rel_path=next(
                    (
                        str(action.get("worktree_rel_path"))
                        for action in actions
                        if action.get("worktree_rel_path")
                    ),
                    None,
                ),
                actions=actions,
            )
            for (branch_name, stage_key), actions in stage_groups.items()
        ]

        branch_items = [
            _build_trace_item(
                selection_type="branch_node",
                selection_ref=branch_name,
                title=branch_name,
                branch_name=branch_name,
                stage_key=None,
                worktree_rel_path=next(
                    (
                        str(action.get("worktree_rel_path"))
                        for action in actions
                        if action.get("worktree_rel_path")
                    ),
                    None,
                ),
                actions=actions,
            )
            for branch_name, actions in branch_groups.items()
        ]

        items = sorted(
            [*branch_items, *stage_items, *event_items],
            key=lambda item: (
                str(item.get("selection_type") or ""),
                str(item.get("updated_at") or ""),
                str(item.get("selection_ref") or ""),
            ),
        )

        payload = {
            "quest_id": quest_id,
            "generated_at": utc_now(),
            "items": items,
        }
        write_json(self.materialized_path, payload)
        return payload
