from __future__ import annotations

from pathlib import Path
from typing import Any

from ..channels import get_channel_factory, register_builtin_channels
from ..config import ConfigManager
from ..connector_runtime import conversation_identity_key, normalize_conversation_id
from ..gitops import (
    canonical_worktree_root,
    checkpoint_repo,
    create_worktree,
    current_branch,
    ensure_branch,
    export_git_graph,
    head_commit,
)
from ..registries import BaselineRegistry
from ..shared import (
    append_jsonl,
    ensure_dir,
    generate_id,
    read_json,
    read_jsonl,
    read_text,
    read_yaml,
    run_command,
    slugify,
    utc_now,
    write_json,
    write_text,
    write_yaml,
)
from ..quest import QuestService
from ..memory.frontmatter import dump_markdown_document, load_markdown_document
from .arxiv import read_arxiv_content
from .guidance import build_guidance_for_record, guidance_summary
from .metrics import (
    baseline_metric_lines,
    build_metrics_timeline,
    compare_with_baseline,
    compute_progress_eval,
    normalize_metric_contract,
    normalize_metric_rows,
    normalize_metrics_summary,
    selected_baseline_metrics,
    to_number,
)
from .schemas import ARTIFACT_DIRS, guidance_for_kind, validate_artifact_payload


class ArtifactService:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.baselines = BaselineRegistry(home)
        self.quest_service = QuestService(home)

    def _workspace_root_for(self, quest_root: Path, workspace_root: Path | None = None) -> Path:
        if workspace_root is not None:
            return workspace_root
        return self.quest_service.active_workspace_root(quest_root)

    def _workspace_relative(self, quest_root: Path, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return path.resolve().relative_to(quest_root.resolve()).as_posix()
        except ValueError:
            return str(path)

    def _git_config(self) -> dict[str, Any]:
        config = ConfigManager(self.home).load_named("config")
        payload = config.get("git") if isinstance(config.get("git"), dict) else {}
        return payload if isinstance(payload, dict) else {}

    def _should_auto_push(self) -> bool:
        return bool(self._git_config().get("auto_push", False))

    def _default_remote(self) -> str:
        return str(self._git_config().get("default_remote") or "origin").strip() or "origin"

    def _checkpoint_with_optional_push(
        self,
        workspace_root: Path,
        *,
        message: str,
        allow_empty: bool = False,
        push: bool | None = None,
    ) -> dict[str, Any]:
        commit_result = checkpoint_repo(workspace_root, message, allow_empty=allow_empty)
        push_enabled = self._should_auto_push() if push is None else bool(push)
        push_result: dict[str, Any] | None = None
        if push_enabled and bool(commit_result.get("committed")):
            branch = str(commit_result.get("branch") or current_branch(workspace_root) or "")
            remote = self._default_remote()
            result = run_command(["git", "push", remote, branch], cwd=workspace_root, check=False)
            push_result = {
                "attempted": True,
                "ok": result.returncode == 0,
                "remote": remote,
                "branch": branch,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        elif push_enabled:
            push_result = {
                "attempted": False,
                "ok": False,
                "remote": self._default_remote(),
                "branch": str(commit_result.get("branch") or current_branch(workspace_root) or ""),
                "stdout": "",
                "stderr": "No new commit was created.",
            }
        return {
            **commit_result,
            "push": push_result,
        }

    def _build_idea_markdown(
        self,
        *,
        idea_id: str,
        quest_id: str,
        title: str,
        problem: str,
        hypothesis: str,
        mechanism: str,
        expected_gain: str,
        risks: list[str],
        evidence_paths: list[str],
        decision_reason: str,
        next_target: str,
        branch: str,
        worktree_root: Path,
        created_at: str | None = None,
    ) -> str:
        metadata = {
            "id": idea_id,
            "type": "ideas",
            "kind": "idea",
            "title": title,
            "quest_id": quest_id,
            "scope": "quest",
            "branch": branch,
            "worktree_root": str(worktree_root),
            "next_target": next_target,
            "created_at": created_at or utc_now(),
            "updated_at": utc_now(),
            "tags": [f"branch:{branch}", f"next:{next_target}"],
        }
        body_lines = [
            f"# {title}",
            "",
            "## Problem",
            "",
            problem.strip() or "TBD",
            "",
            "## Hypothesis",
            "",
            hypothesis.strip() or "TBD",
            "",
            "## Mechanism",
            "",
            mechanism.strip() or "TBD",
            "",
            "## Expected Gain",
            "",
            expected_gain.strip() or "TBD",
            "",
            "## Decision Reason",
            "",
            decision_reason.strip() or "TBD",
            "",
            "## Risks",
            "",
        ]
        if risks:
            body_lines.extend([f"- {item}" for item in risks])
        else:
            body_lines.append("- None recorded yet.")
        body_lines.extend(["", "## Evidence Paths", ""])
        if evidence_paths:
            body_lines.extend([f"- `{item}`" for item in evidence_paths])
        else:
            body_lines.append("- None recorded yet.")
        body_lines.extend(
            [
                "",
                "## Next Target",
                "",
                next_target.strip() or "experiment",
                "",
            ]
        )
        return dump_markdown_document(metadata, "\n".join(body_lines).rstrip() + "\n")

    def _analysis_manifest_path(self, quest_root: Path, campaign_id: str) -> Path:
        return ensure_dir(quest_root / ".ds" / "analysis_campaigns") / f"{campaign_id}.json"

    def _read_analysis_manifest(self, quest_root: Path, campaign_id: str) -> dict[str, Any]:
        path = self._analysis_manifest_path(quest_root, campaign_id)
        payload = read_json(path, {})
        if not isinstance(payload, dict) or not payload:
            raise FileNotFoundError(f"Unknown analysis campaign `{campaign_id}`.")
        return payload

    def _write_analysis_manifest(self, quest_root: Path, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._analysis_manifest_path(quest_root, campaign_id)
        normalized = {**payload, "campaign_id": campaign_id, "updated_at": utc_now()}
        write_json(path, normalized)
        return normalized

    def _active_baseline_attachment(self, quest_root: Path, workspace_root: Path | None = None) -> dict[str, Any] | None:
        target_root = self._workspace_root_for(quest_root, workspace_root)
        attachments: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for root in (target_root, quest_root):
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

    def _main_run_artifacts(self, quest_root: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for root in self.quest_service.workspace_roots(quest_root):
            artifacts_root = root / "artifacts" / "runs"
            if not artifacts_root.exists():
                continue
            for path in sorted(artifacts_root.glob("*.json")):
                if not path.is_file():
                    continue
                key = str(path.resolve())
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                payload = read_json(path, {})
                if not isinstance(payload, dict) or not payload:
                    continue
                if str(payload.get("run_kind") or "").strip() != "main_experiment":
                    continue
                records.append(payload)
        records.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""))
        return records

    def _previous_primary_best(
        self,
        quest_root: Path,
        *,
        primary_metric_id: str | None,
        direction: str | None,
    ) -> float | None:
        metric_id = str(primary_metric_id or "").strip()
        normalized_direction = str(direction or "maximize").strip().lower() or "maximize"
        if not metric_id:
            return None
        best: float | None = None
        for record in self._main_run_artifacts(quest_root):
            summary = normalize_metrics_summary(record.get("metrics_summary"))
            value = to_number(summary.get(metric_id))
            if value is None:
                continue
            if best is None:
                best = value
                continue
            if normalized_direction == "maximize":
                if value > best:
                    best = value
            elif value < best:
                best = value
        return best

    def _format_metric_value(self, value: object, decimals: int | None = None) -> str:
        numeric = to_number(value)
        if numeric is None:
            return str(value)
        if decimals is None:
            return f"{numeric:.4f}".rstrip("0").rstrip(".")
        return f"{numeric:.{decimals}f}"

    def _git_changed_files(self, workspace_root: Path) -> list[str]:
        result = run_command(["git", "status", "--porcelain"], cwd=workspace_root, check=False)
        if result.returncode != 0:
            return []
        paths: list[str] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            raw = line[3:].strip() if len(line) >= 4 else line.strip()
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[1].strip()
            if raw:
                paths.append(raw)
        deduped: list[str] = []
        seen: set[str] = set()
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            deduped.append(path)
        return deduped

    def arxiv(self, paper_id: str, *, full_text: bool = False) -> dict[str, Any]:
        return read_arxiv_content(paper_id, full_text=full_text)

    def record(
        self,
        quest_root: Path,
        payload: dict,
        *,
        checkpoint: bool | None = None,
        workspace_root: Path | None = None,
        commit_message: str | None = None,
        push: bool | None = None,
    ) -> dict:
        errors = validate_artifact_payload(payload)
        if errors:
            return {
                "ok": False,
                "errors": errors,
                "warnings": [],
            }

        write_root = self._workspace_root_for(quest_root, workspace_root)
        record = self._build_record(quest_root, payload, workspace_root=write_root)
        guidance_vm = build_guidance_for_record(record)
        record["guidance_vm"] = guidance_vm
        artifact_id = record["artifact_id"]
        artifact_path = self._artifact_path(write_root, record["kind"], artifact_id)
        write_json(artifact_path, record)
        append_jsonl(write_root / "artifacts" / "_index.jsonl", self._index_line(record, artifact_path))

        should_checkpoint = self._should_checkpoint(record["kind"]) if checkpoint is None else checkpoint
        checkpoint_result = None
        if should_checkpoint:
            checkpoint_result = self._checkpoint_with_optional_push(
                write_root,
                message=commit_message or f"artifact: {record['kind']} {artifact_id}",
                allow_empty=False,
                push=push,
            )
        graph_manifest = None
        if record["kind"] in {"baseline", "decision", "milestone", "run", "report", "approval", "graph"}:
            graph_manifest = export_git_graph(quest_root, ensure_dir(quest_root / "artifacts" / "graphs"))
        append_jsonl(
            quest_root / ".ds" / "events.jsonl",
            {
                "type": "artifact.recorded",
                "quest_id": record["quest_id"],
                "artifact_id": artifact_id,
                "kind": record["kind"],
                "recorded_at": record["updated_at"],
                "status": record.get("status"),
                "summary": record.get("summary") or record.get("message"),
                "reason": record.get("reason"),
                "guidance": guidance_summary(guidance_vm) or guidance_for_kind(record["kind"]),
                "guidance_vm": guidance_vm,
                "paths": record.get("paths") or {},
                "interaction_id": record.get("interaction_id"),
                "expects_reply": record.get("expects_reply"),
                "reply_mode": record.get("reply_mode"),
                "options": record.get("options") or [],
                "allow_free_text": record.get("allow_free_text"),
                "reply_schema": record.get("reply_schema") or {},
                "reply_to_interaction_id": record.get("reply_to_interaction_id"),
                "attachments": record.get("attachments") or [],
                "artifact_path": str(artifact_path),
                "workspace_root": str(write_root),
                "branch": record.get("branch"),
                "head_commit": record.get("head_commit"),
                "flow_type": record.get("flow_type"),
                "protocol_step": record.get("protocol_step"),
                "idea_id": record.get("idea_id"),
                "campaign_id": record.get("campaign_id"),
                "slice_id": record.get("slice_id"),
                "details": record.get("details") or {},
                "checkpoint": checkpoint_result,
            },
        )
        self._touch_quest_updated_at(quest_root)

        baseline_registry_entry = None
        if record["kind"] == "baseline" and record.get("publish_global"):
            baseline_registry_entry = self.baselines.publish(
                {
                    "baseline_id": record.get("baseline_id", artifact_id),
                    "name": record.get("name", record.get("baseline_id", artifact_id)),
                    "source": record.get(
                        "source",
                        {
                            "kind": "artifact_publish",
                            "quest_id": record["quest_id"],
                            "quest_root": str(quest_root),
                            "git_commit": head_commit(write_root),
                        },
                    ),
                    "path": record.get(
                        "path",
                        str(write_root / "baselines" / "local" / record.get("baseline_id", artifact_id)),
                    ),
                    "baseline_kind": record.get("baseline_kind", "reproduced"),
                    "task": record.get("task"),
                    "dataset": record.get("dataset"),
                    "primary_metric": record.get("primary_metric"),
                    "metrics_summary": record.get("metrics_summary", {}),
                    "environment": record.get("environment", {}),
                    "tags": record.get("tags", []),
                    "summary": record.get("summary", ""),
                    "codebase_id": record.get("codebase_id"),
                    "codebase_root_path": record.get("codebase_root_path"),
                    "default_variant_id": record.get("default_variant_id"),
                    "baseline_variants": record.get("baseline_variants", []),
                    "metric_contract": record.get("metric_contract"),
                    "metric_objectives": record.get("metric_objectives", []),
                    "baseline_metrics_path": record.get("baseline_metrics_path"),
                    "baseline_results_index_path": record.get("baseline_results_index_path"),
                }
            )
        if record["kind"] == "approval":
            close_target = str(record.get("reply_to_interaction_id") or record.get("decision_id") or "").strip()
            if close_target:
                self._close_interaction_request(
                    quest_root,
                    interaction_id=close_target,
                    closing_artifact_id=artifact_id,
                )

        return {
            "ok": True,
            "artifact_id": artifact_id,
            "path": str(artifact_path),
            "guidance": guidance_summary(guidance_vm) or guidance_for_kind(record["kind"]),
            "guidance_vm": guidance_vm,
            "graph": graph_manifest,
            "recorded": record["kind"],
            "record": record,
            "workspace_root": str(write_root),
            "artifact_path": str(artifact_path),
            "checkpoint": checkpoint_result,
            "baseline_registry_entry": baseline_registry_entry,
        }

    def checkpoint(self, quest_root: Path, message: str, *, allow_empty: bool = False) -> dict:
        result = checkpoint_repo(quest_root, message, allow_empty=allow_empty)
        self._touch_quest_updated_at(quest_root)
        return {
            "ok": True,
            "message": message,
            "guidance": "Checkpoint created. Continue from the updated quest branch state.",
            **result,
        }

    def prepare_branch(
        self,
        quest_root: Path,
        *,
        run_id: str | None = None,
        idea_id: str | None = None,
        branch: str | None = None,
        branch_kind: str = "run",
        create_worktree_flag: bool = True,
        start_point: str | None = None,
    ) -> dict:
        parent_branch = current_branch(quest_root)
        start_ref = start_point or parent_branch
        branch_name = branch or self._default_branch_name(quest_root, run_id=run_id, idea_id=idea_id, branch_kind=branch_kind)
        branch_result = ensure_branch(quest_root, branch_name, start_point=start_ref, checkout=False)
        worktree_result = None
        worktree_root = None
        if create_worktree_flag:
            worktree_root = canonical_worktree_root(quest_root, run_id or branch_name)
            worktree_result = create_worktree(
                quest_root,
                branch=branch_name,
                worktree_root=worktree_root,
                start_point=start_ref,
            )
        artifact_result = self.record(
            quest_root,
            {
                "kind": "decision",
                "status": "prepared",
                "verdict": "prepared",
                "action": "prepare_branch",
                "reason": f"Prepared branch `{branch_name}` for the next quest step.",
                "branch": branch_name,
                "run_id": run_id,
                "idea_id": idea_id,
                "branch_kind": branch_kind,
                "parent_branch": parent_branch,
                "start_point": start_ref,
                "worktree_root": str(worktree_root) if worktree_root else None,
                "source": {"kind": "system", "role": "artifact"},
            },
            checkpoint=False,
        )
        return {
            "ok": True,
            "branch": branch_name,
            "branch_result": branch_result,
            "worktree": worktree_result,
            "worktree_root": str(worktree_root) if worktree_root else None,
            "parent_branch": parent_branch,
            "start_point": start_ref,
            "guidance": "Use this branch/worktree for the isolated idea or run. Keep durable outputs under quest_root.",
            "artifact": artifact_result,
        }

    def submit_idea(
        self,
        quest_root: Path,
        *,
        mode: str = "create",
        idea_id: str | None = None,
        title: str,
        problem: str = "",
        hypothesis: str = "",
        mechanism: str = "",
        expected_gain: str = "",
        evidence_paths: list[str] | None = None,
        risks: list[str] | None = None,
        decision_reason: str = "",
        next_target: str = "experiment",
    ) -> dict[str, Any]:
        normalized_mode = str(mode or "create").strip().lower()
        if normalized_mode not in {"create", "revise"}:
            raise ValueError("submit_idea mode must be `create` or `revise`.")

        quest_id = self._quest_id(quest_root)
        state = self.quest_service.read_research_state(quest_root)
        evidence_paths = [str(item).strip() for item in (evidence_paths or []) if str(item).strip()]
        risks = [str(item).strip() for item in (risks or []) if str(item).strip()]
        next_target = str(next_target or "experiment").strip().lower() or "experiment"

        if normalized_mode == "create":
            resolved_idea_id = str(idea_id or generate_id("idea")).strip()
            parent_workspace = self._workspace_root_for(quest_root)
            parent_branch = current_branch(parent_workspace)
            branch_name = f"idea/{quest_id}-{resolved_idea_id}"
            worktree_root = canonical_worktree_root(quest_root, f"idea-{resolved_idea_id}")
            branch_result = ensure_branch(quest_root, branch_name, start_point=parent_branch, checkout=False)
            worktree_result = create_worktree(
                quest_root,
                branch=branch_name,
                worktree_root=worktree_root,
                start_point=parent_branch,
            )
            ensure_dir(worktree_root / "memory" / "ideas" / resolved_idea_id)
            idea_md_path = worktree_root / "memory" / "ideas" / resolved_idea_id / "idea.md"
            markdown = self._build_idea_markdown(
                idea_id=resolved_idea_id,
                quest_id=quest_id,
                title=title,
                problem=problem,
                hypothesis=hypothesis,
                mechanism=mechanism,
                expected_gain=expected_gain,
                risks=risks,
                evidence_paths=evidence_paths,
                decision_reason=decision_reason,
                next_target=next_target,
                branch=branch_name,
                worktree_root=worktree_root,
            )
            write_text(idea_md_path, markdown)
            artifact = self.record(
                quest_root,
                {
                    "kind": "idea",
                    "status": "completed",
                    "summary": f"Idea `{resolved_idea_id}` created and promoted to the active research head.",
                    "reason": decision_reason or "A concrete idea was selected for continued research and implementation.",
                    "idea_id": resolved_idea_id,
                    "branch": branch_name,
                    "parent_branch": parent_branch,
                    "worktree_root": str(worktree_root),
                    "worktree_rel_path": self._workspace_relative(quest_root, worktree_root),
                    "flow_type": "idea_submission",
                    "protocol_step": "create",
                    "paths": {
                        "idea_md": str(idea_md_path),
                        "worktree_root": str(worktree_root),
                    },
                    "details": {
                        "title": title,
                        "problem": problem,
                        "hypothesis": hypothesis,
                        "mechanism": mechanism,
                        "expected_gain": expected_gain,
                        "next_target": next_target,
                        "evidence_paths": evidence_paths,
                        "risks": risks,
                    },
                },
                checkpoint=False,
                workspace_root=worktree_root,
            )
            research_state = self.quest_service.update_research_state(
                quest_root,
                active_idea_id=resolved_idea_id,
                research_head_branch=branch_name,
                research_head_worktree_root=str(worktree_root),
                current_workspace_branch=branch_name,
                current_workspace_root=str(worktree_root),
                active_idea_md_path=str(idea_md_path),
                active_analysis_campaign_id=None,
                analysis_parent_branch=None,
                analysis_parent_worktree_root=None,
                next_pending_slice_id=None,
                workspace_mode="idea",
                last_flow_type="idea_submission",
            )
            checkpoint_result = self._checkpoint_with_optional_push(
                worktree_root,
                message=f"idea: create {resolved_idea_id}",
            )
            interaction = self.interact(
                quest_root,
                kind="milestone",
                message=(
                    f"Idea `{resolved_idea_id}` is now active.\n"
                    f"- Branch: `{branch_name}`\n"
                    f"- Worktree: `{worktree_root}`\n"
                    f"- Idea file: `{idea_md_path}`\n"
                    f"- Next target: `{next_target}`"
                ),
                deliver_to_bound_conversations=True,
                include_recent_inbound_messages=False,
                attachments=[
                    {
                        "kind": "idea_submission",
                        "idea_id": resolved_idea_id,
                        "branch": branch_name,
                        "worktree_root": str(worktree_root),
                        "idea_md_path": str(idea_md_path),
                        "next_target": next_target,
                    }
                ],
            )
            return {
                "ok": True,
                "mode": normalized_mode,
                "idea_id": resolved_idea_id,
                "branch": branch_name,
                "parent_branch": parent_branch,
                "worktree_root": str(worktree_root),
                "idea_md_path": str(idea_md_path),
                "branch_result": branch_result,
                "worktree": worktree_result,
                "artifact": artifact,
                "checkpoint": checkpoint_result,
                "interaction": interaction,
                "research_state": research_state,
            }

        resolved_idea_id = str(idea_id or state.get("active_idea_id") or "").strip()
        if not resolved_idea_id:
            raise ValueError("submit_idea(mode='revise') requires an existing active `idea_id`.")
        branch_name = str(state.get("research_head_branch") or f"idea/{quest_id}-{resolved_idea_id}").strip()
        worktree_root = Path(
            str(state.get("research_head_worktree_root") or canonical_worktree_root(quest_root, f"idea-{resolved_idea_id}"))
        )
        ensure_dir(worktree_root / "memory" / "ideas" / resolved_idea_id)
        idea_md_path = worktree_root / "memory" / "ideas" / resolved_idea_id / "idea.md"
        created_at = None
        if idea_md_path.exists():
            metadata, _body = load_markdown_document(idea_md_path)
            created_at = metadata.get("created_at")
        markdown = self._build_idea_markdown(
            idea_id=resolved_idea_id,
            quest_id=quest_id,
            title=title,
            problem=problem,
            hypothesis=hypothesis,
            mechanism=mechanism,
            expected_gain=expected_gain,
            risks=risks,
            evidence_paths=evidence_paths,
            decision_reason=decision_reason,
            next_target=next_target,
            branch=branch_name,
            worktree_root=worktree_root,
            created_at=str(created_at) if created_at else None,
        )
        write_text(idea_md_path, markdown)
        artifact = self.record(
            quest_root,
            {
                "kind": "idea",
                "status": "completed",
                "summary": f"Idea `{resolved_idea_id}` revised on the active research branch.",
                "reason": decision_reason or "The current idea was refined before launching the next stage.",
                "idea_id": resolved_idea_id,
                "branch": branch_name,
                "worktree_root": str(worktree_root),
                "worktree_rel_path": self._workspace_relative(quest_root, worktree_root),
                "flow_type": "idea_submission",
                "protocol_step": "revise",
                "paths": {
                    "idea_md": str(idea_md_path),
                    "worktree_root": str(worktree_root),
                },
                "details": {
                    "title": title,
                    "problem": problem,
                    "hypothesis": hypothesis,
                    "mechanism": mechanism,
                    "expected_gain": expected_gain,
                    "next_target": next_target,
                    "evidence_paths": evidence_paths,
                    "risks": risks,
                },
            },
            checkpoint=False,
            workspace_root=worktree_root,
        )
        research_state = self.quest_service.update_research_state(
            quest_root,
            active_idea_id=resolved_idea_id,
            research_head_branch=branch_name,
            research_head_worktree_root=str(worktree_root),
            current_workspace_branch=branch_name,
            current_workspace_root=str(worktree_root),
            active_idea_md_path=str(idea_md_path),
            workspace_mode="idea",
            last_flow_type="idea_revision",
        )
        checkpoint_result = self._checkpoint_with_optional_push(
            worktree_root,
            message=f"idea: revise {resolved_idea_id}",
        )
        interaction = self.interact(
            quest_root,
            kind="progress",
            message=(
                f"Idea `{resolved_idea_id}` was revised.\n"
                f"- Branch: `{branch_name}`\n"
                f"- Worktree: `{worktree_root}`\n"
                f"- Idea file: `{idea_md_path}`\n"
                f"- Next target: `{next_target}`"
            ),
            deliver_to_bound_conversations=True,
            include_recent_inbound_messages=False,
            attachments=[
                {
                    "kind": "idea_revision",
                    "idea_id": resolved_idea_id,
                    "branch": branch_name,
                    "worktree_root": str(worktree_root),
                    "idea_md_path": str(idea_md_path),
                    "next_target": next_target,
                }
            ],
        )
        return {
            "ok": True,
            "mode": normalized_mode,
            "idea_id": resolved_idea_id,
            "branch": branch_name,
            "worktree_root": str(worktree_root),
            "idea_md_path": str(idea_md_path),
            "artifact": artifact,
            "checkpoint": checkpoint_result,
            "interaction": interaction,
            "research_state": research_state,
        }

    def record_main_experiment(
        self,
        quest_root: Path,
        *,
        run_id: str,
        title: str = "",
        hypothesis: str = "",
        setup: str = "",
        execution: str = "",
        results: str = "",
        conclusion: str = "",
        metric_rows: list[dict[str, Any]] | None = None,
        metrics_summary: dict[str, Any] | None = None,
        metric_contract: dict[str, Any] | None = None,
        evidence_paths: list[str] | None = None,
        changed_files: list[str] | None = None,
        config_paths: list[str] | None = None,
        notes: list[str] | None = None,
        dataset_scope: str = "full",
        verdict: str = "",
        status: str = "completed",
        baseline_id: str | None = None,
        baseline_variant_id: str | None = None,
    ) -> dict[str, Any]:
        state = self.quest_service.read_research_state(quest_root)
        if str(state.get("workspace_mode") or "").strip() == "analysis":
            raise ValueError(
                "record_main_experiment cannot run while the active workspace is an analysis slice. "
                "Finish or close the analysis campaign first."
            )

        run_identifier = str(run_id or "").strip()
        if not run_identifier:
            raise ValueError("record_main_experiment requires `run_id`.")

        active_idea_id = str(state.get("active_idea_id") or "").strip() or None
        workspace_root = self._workspace_root_for(quest_root)
        branch_name = str(state.get("research_head_branch") or current_branch(workspace_root)).strip()
        attachment = self._active_baseline_attachment(quest_root, workspace_root=workspace_root)
        baseline_entry = dict(attachment.get("entry") or {}) if isinstance(attachment, dict) else {}
        selected_variant = dict(attachment.get("selected_variant") or {}) if isinstance(attachment, dict) else {}
        resolved_baseline_id = (
            str(baseline_id or attachment.get("source_baseline_id") or baseline_entry.get("baseline_id") or "").strip() or None
            if isinstance(attachment, dict)
            else str(baseline_id or "").strip() or None
        )
        resolved_variant_id = (
            str(baseline_variant_id or attachment.get("source_variant_id") or selected_variant.get("variant_id") or "").strip()
            or None
            if isinstance(attachment, dict)
            else str(baseline_variant_id or "").strip() or None
        )

        normalized_metrics_summary = normalize_metrics_summary(metrics_summary)
        normalized_metric_rows = normalize_metric_rows(metric_rows or [], metrics_summary=normalized_metrics_summary)
        if not normalized_metrics_summary:
            normalized_metrics_summary = {
                str(item.get("metric_id") or "").strip(): item.get("value")
                for item in normalized_metric_rows
                if str(item.get("metric_id") or "").strip()
            }
        effective_metric_contract = normalize_metric_contract(
            metric_contract or baseline_entry.get("metric_contract"),
            baseline_id=resolved_baseline_id,
            metrics_summary=normalized_metrics_summary,
            primary_metric=baseline_entry.get("primary_metric"),
            baseline_variants=baseline_entry.get("baseline_variants"),
        )
        baseline_metrics = selected_baseline_metrics(baseline_entry, resolved_variant_id)
        comparisons = compare_with_baseline(
            metrics_summary=normalized_metrics_summary,
            metric_contract=effective_metric_contract,
            baseline_metrics=baseline_metrics,
        )
        previous_primary_best = self._previous_primary_best(
            quest_root,
            primary_metric_id=comparisons.get("primary_metric_id"),
            direction=((comparisons.get("primary") or {}).get("direction") if isinstance(comparisons, dict) else None),
        )
        progress_eval = compute_progress_eval(
            comparisons=comparisons,
            previous_primary_best=previous_primary_best,
        )
        resolved_changed_files = [str(item).strip() for item in (changed_files or []) if str(item).strip()]
        if not resolved_changed_files:
            resolved_changed_files = self._git_changed_files(workspace_root)
        resolved_evidence_paths = [str(item).strip() for item in (evidence_paths or []) if str(item).strip()]
        resolved_config_paths = [str(item).strip() for item in (config_paths or []) if str(item).strip()]
        resolved_notes = [str(item).strip() for item in (notes or []) if str(item).strip()]
        normalized_dataset_scope = str(dataset_scope or "full").strip().lower() or "full"
        primary = comparisons.get("primary") if isinstance(comparisons, dict) else {}
        primary_metric_id = str(progress_eval.get("primary_metric_id") or comparisons.get("primary_metric_id") or "").strip() or None
        primary_value = primary.get("run_value") if isinstance(primary, dict) else None
        primary_baseline = primary.get("baseline_value") if isinstance(primary, dict) else None
        primary_delta = progress_eval.get("delta_vs_baseline")
        decimals = primary.get("decimals") if isinstance(primary, dict) else None
        if not verdict:
            if progress_eval.get("breakthrough"):
                verdict = "supported"
            elif progress_eval.get("beats_baseline") is False:
                verdict = "inconclusive"
            else:
                verdict = "recorded"

        main_dir = ensure_dir(workspace_root / "experiments" / "main" / run_identifier)
        run_md_path = main_dir / "RUN.md"
        result_json_path = main_dir / "RESULT.json"

        summary_parts = [f"Main experiment `{run_identifier}` recorded on `{branch_name}`."]
        if primary_metric_id and primary_value is not None:
            summary_parts.append(
                f"{primary_metric_id}={self._format_metric_value(primary_value, decimals)}"
            )
        if primary_metric_id and primary_baseline is not None and primary_delta is not None:
            delta_text = self._format_metric_value(primary_delta, decimals)
            baseline_text = self._format_metric_value(primary_baseline, decimals)
            summary_parts.append(f"vs baseline {baseline_text} (Δ {delta_text})")
        if progress_eval.get("breakthrough"):
            summary_parts.append(f"Breakthrough: {progress_eval.get('breakthrough_level')}")
        summary = " ".join(summary_parts)

        run_lines = [
            f"# {title.strip() or run_identifier}",
            "",
            f"- Run id: `{run_identifier}`",
            f"- Branch: `{branch_name}`",
            f"- Worktree: `{workspace_root}`",
            f"- Idea: `{active_idea_id or 'none'}`",
            f"- Baseline: `{resolved_baseline_id or 'none'}`",
            f"- Baseline variant: `{resolved_variant_id or 'none'}`",
            f"- Dataset scope: `{normalized_dataset_scope}`",
            f"- Verdict: `{verdict}`",
            f"- Status: `{status}`",
            "",
            "## Hypothesis",
            "",
            hypothesis.strip() or "TBD",
            "",
            "## Setup",
            "",
            setup.strip() or "TBD",
            "",
            "## Execution",
            "",
            execution.strip() or "TBD",
            "",
            "## Results",
            "",
            results.strip() or "TBD",
            "",
            "## Conclusion",
            "",
            conclusion.strip() or progress_eval.get("reason") or "TBD",
            "",
            "## Metrics Summary",
            "",
        ]
        if normalized_metrics_summary:
            for metric_id, value in normalized_metrics_summary.items():
                run_lines.append(f"- `{metric_id}` = {self._format_metric_value(value)}")
        else:
            run_lines.append("- No metrics recorded.")
        run_lines.extend(["", "## Baseline Comparison", ""])
        comparison_items = comparisons.get("items") if isinstance(comparisons, dict) else []
        if comparison_items:
            for item in comparison_items:
                metric_id = str(item.get("metric_id") or "").strip() or "metric"
                run_value = self._format_metric_value(item.get("run_value"), item.get("decimals"))
                baseline_value = self._format_metric_value(item.get("baseline_value"), item.get("decimals"))
                delta_value = item.get("delta")
                delta_text = self._format_metric_value(delta_value, item.get("decimals")) if delta_value is not None else "n/a"
                verdict_text = (
                    "better"
                    if item.get("better") is True
                    else "worse"
                    if item.get("better") is False
                    else "not comparable"
                )
                run_lines.append(
                    f"- `{metric_id}`: run={run_value} baseline={baseline_value} delta={delta_text} ({verdict_text})"
                )
        else:
            run_lines.append("- No comparable baseline metrics found.")
        run_lines.extend(["", "## Changed Files", ""])
        if resolved_changed_files:
            run_lines.extend([f"- `{item}`" for item in resolved_changed_files])
        else:
            run_lines.append("- None recorded.")
        run_lines.extend(["", "## Evidence Paths", ""])
        if resolved_evidence_paths:
            run_lines.extend([f"- `{item}`" for item in resolved_evidence_paths])
        else:
            run_lines.append("- None recorded.")
        if resolved_config_paths:
            run_lines.extend(["", "## Config Paths", ""])
            run_lines.extend([f"- `{item}`" for item in resolved_config_paths])
        if resolved_notes:
            run_lines.extend(["", "## Notes", ""])
            run_lines.extend([f"- {item}" for item in resolved_notes])
        write_text(run_md_path, "\n".join(run_lines).rstrip() + "\n")

        result_payload = {
            "schema_version": 1,
            "result_kind": "main_experiment",
            "quest_id": self._quest_id(quest_root),
            "run_id": run_identifier,
            "title": title.strip() or run_identifier,
            "status": status,
            "verdict": verdict,
            "idea_id": active_idea_id,
            "branch": branch_name,
            "worktree_root": str(workspace_root),
            "head_commit": head_commit(workspace_root),
            "baseline_ref": {
                "baseline_id": resolved_baseline_id,
                "variant_id": resolved_variant_id,
                "metric_contract": effective_metric_contract,
                "metric_lines": baseline_metric_lines(baseline_entry, resolved_variant_id),
            },
            "run_context": {
                "dataset_scope": normalized_dataset_scope,
                "config_paths": resolved_config_paths,
                "notes": resolved_notes,
            },
            "hypothesis": hypothesis.strip(),
            "setup": setup.strip(),
            "execution": execution.strip(),
            "results_summary": results.strip(),
            "conclusion": conclusion.strip() or progress_eval.get("reason"),
            "metrics_summary": normalized_metrics_summary,
            "metric_rows": normalized_metric_rows,
            "metric_contract": effective_metric_contract,
            "baseline_comparisons": {
                key: value for key, value in comparisons.items() if key != "primary"
            },
            "progress_eval": progress_eval,
            "evidence_paths": resolved_evidence_paths,
            "files_changed": resolved_changed_files,
            "run_md_path": str(run_md_path),
        }
        write_json(result_json_path, result_payload)

        artifact = self.record(
            quest_root,
            {
                "kind": "run",
                "status": status,
                "run_id": run_identifier,
                "run_kind": "main_experiment",
                "summary": summary,
                "reason": conclusion.strip() or progress_eval.get("reason") or "Main experiment result recorded.",
                "idea_id": active_idea_id,
                "branch": branch_name,
                "worktree_root": str(workspace_root),
                "worktree_rel_path": self._workspace_relative(quest_root, workspace_root),
                "flow_type": "main_experiment",
                "protocol_step": "record",
                "paths": {
                    "run_md": str(run_md_path),
                    "result_json": str(result_json_path),
                },
                "details": {
                    "title": title.strip() or run_identifier,
                    "verdict": verdict,
                    "primary_metric_id": primary_metric_id,
                    "primary_value": primary_value,
                    "baseline_value": primary_baseline,
                    "delta_vs_baseline": primary_delta,
                    "breakthrough": progress_eval.get("breakthrough"),
                    "breakthrough_level": progress_eval.get("breakthrough_level"),
                    "changed_file_count": len(resolved_changed_files),
                    "evidence_count": len(resolved_evidence_paths),
                },
                "baseline_ref": {
                    "baseline_id": resolved_baseline_id,
                    "variant_id": resolved_variant_id,
                },
                "metrics_summary": normalized_metrics_summary,
                "metric_rows": normalized_metric_rows,
                "metric_contract": effective_metric_contract,
                "baseline_comparisons": {
                    key: value for key, value in comparisons.items() if key != "primary"
                },
                "progress_eval": progress_eval,
                "files_changed": resolved_changed_files,
                "evidence_paths": resolved_evidence_paths,
                "verdict": verdict,
            },
            commit_message=f"experiment: record main {run_identifier}",
            workspace_root=workspace_root,
        )
        interaction = self.interact(
            quest_root,
            kind="milestone",
            message=(
                f"Main experiment `{run_identifier}` has been recorded.\n"
                f"- Branch: `{branch_name}`\n"
                f"- Run log: `{run_md_path}`\n"
                f"- Result: `{result_json_path}`\n"
                f"- Verdict: `{verdict}`\n"
                f"- Breakthrough: `{progress_eval.get('breakthrough_level')}`"
            ),
            deliver_to_bound_conversations=True,
            include_recent_inbound_messages=False,
            attachments=[
                {
                    "kind": "main_experiment_recorded",
                    "run_id": run_identifier,
                    "branch": branch_name,
                    "worktree_root": str(workspace_root),
                    "run_md_path": str(run_md_path),
                    "result_json_path": str(result_json_path),
                    "verdict": verdict,
                    "primary_metric_id": primary_metric_id,
                    "delta_vs_baseline": primary_delta,
                    "breakthrough": progress_eval.get("breakthrough"),
                    "breakthrough_level": progress_eval.get("breakthrough_level"),
                }
            ],
        )
        return {
            "ok": True,
            "run_id": run_identifier,
            "run_md_path": str(run_md_path),
            "result_json_path": str(result_json_path),
            "artifact": artifact,
            "interaction": interaction,
            "metrics_summary": normalized_metrics_summary,
            "baseline_comparisons": {
                key: value for key, value in comparisons.items() if key != "primary"
            },
            "progress_eval": progress_eval,
        }

    def create_analysis_campaign(
        self,
        quest_root: Path,
        *,
        campaign_title: str,
        campaign_goal: str,
        parent_run_id: str | None = None,
        slices: list[dict[str, Any]],
    ) -> dict[str, Any]:
        state = self.quest_service.read_research_state(quest_root)
        active_idea_id = str(state.get("active_idea_id") or "").strip()
        if not active_idea_id:
            raise ValueError("An active idea is required before starting an analysis campaign.")
        if not slices:
            raise ValueError("At least one analysis slice is required.")
        parent_branch = str(state.get("research_head_branch") or current_branch(self._workspace_root_for(quest_root))).strip()
        parent_worktree_root = Path(str(state.get("research_head_worktree_root") or self._workspace_root_for(quest_root)))
        campaign_id = generate_id("analysis")
        charter_dir = ensure_dir(parent_worktree_root / "experiments" / "analysis-results" / campaign_id)
        charter_path = charter_dir / "campaign.md"
        slice_contexts: list[dict[str, Any]] = []
        for index, raw in enumerate(slices, start=1):
            slice_id = str(raw.get("slice_id") or generate_id("slice")).strip()
            title = str(raw.get("title") or slice_id).strip() or slice_id
            branch = f"analysis/{active_idea_id}/{campaign_id}-{slugify(slice_id, 'slice')}"
            worktree_root = canonical_worktree_root(quest_root, f"analysis-{campaign_id}-{slice_id}")
            ensure_branch(quest_root, branch, start_point=parent_branch, checkout=False)
            create_worktree(
                quest_root,
                branch=branch,
                worktree_root=worktree_root,
                start_point=parent_branch,
            )
            plan_dir = ensure_dir(worktree_root / "experiments" / "analysis" / campaign_id / slice_id)
            plan_path = plan_dir / "plan.md"
            requirement_lines = [
                f"# {title}",
                "",
                "## Goal",
                "",
                str(raw.get("goal") or "").strip() or "TBD",
                "",
                "## Hypothesis",
                "",
                str(raw.get("hypothesis") or "").strip() or "TBD",
                "",
                "## Required Changes",
                "",
                str(raw.get("required_changes") or "").strip() or "TBD",
                "",
                "## Metric Contract",
                "",
                str(raw.get("metric_contract") or "").strip() or "TBD",
                "",
                "## Environment Notes",
                "",
                str(raw.get("environment_notes") or "").strip() or "TBD",
                "",
                "## Must Not Simplify",
                "",
                str(raw.get("must_not_simplify") or "").strip() or "Full dataset / full protocol only unless explicitly approved.",
                "",
            ]
            write_text(plan_path, "\n".join(requirement_lines))
            slice_contexts.append(
                {
                    "index": index,
                    "slice_id": slice_id,
                    "title": title,
                    "status": "pending",
                    "branch": branch,
                    "worktree_root": str(worktree_root),
                    "plan_path": str(plan_path),
                    "run_kind": str(raw.get("run_kind") or "analysis.slice").strip() or "analysis.slice",
                    "goal": str(raw.get("goal") or "").strip(),
                    "hypothesis": str(raw.get("hypothesis") or "").strip(),
                    "required_changes": str(raw.get("required_changes") or "").strip(),
                    "metric_contract": str(raw.get("metric_contract") or "").strip(),
                    "environment_notes": str(raw.get("environment_notes") or "").strip(),
                    "must_not_simplify": str(raw.get("must_not_simplify") or "").strip(),
                }
            )

        charter_lines = [
            f"# {campaign_title}",
            "",
            "## Goal",
            "",
            campaign_goal.strip() or "TBD",
            "",
            "## Parent Branch",
            "",
            f"`{parent_branch}`",
            "",
            "## Parent Worktree",
            "",
            f"`{parent_worktree_root}`",
            "",
            "## Slices",
            "",
        ]
        for item in slice_contexts:
            charter_lines.extend(
                [
                    f"### {item['slice_id']} · {item['title']}",
                    "",
                    f"- Branch: `{item['branch']}`",
                    f"- Worktree: `{item['worktree_root']}`",
                    f"- Plan: `{item['plan_path']}`",
                    f"- Run kind: `{item['run_kind']}`",
                    f"- Goal: {item['goal'] or 'TBD'}",
                    f"- Requirement: {item['must_not_simplify'] or 'TBD'}",
                    "",
                ]
            )
        write_text(charter_path, "\n".join(charter_lines).rstrip() + "\n")
        manifest = self._write_analysis_manifest(
            quest_root,
            campaign_id,
            {
                "title": campaign_title,
                "goal": campaign_goal,
                "parent_run_id": parent_run_id,
                "active_idea_id": active_idea_id,
                "parent_branch": parent_branch,
                "parent_worktree_root": str(parent_worktree_root),
                "charter_path": str(charter_path),
                "slices": slice_contexts,
                "created_at": utc_now(),
            },
        )
        first_slice = slice_contexts[0]
        artifact = self.record(
            quest_root,
            {
                "kind": "report",
                "status": "completed",
                "report_type": "analysis_campaign_create",
                "summary": f"Analysis campaign `{campaign_id}` created with {len(slice_contexts)} slices.",
                "reason": "The main experiment completed and now requires structured follow-up analysis slices.",
                "idea_id": active_idea_id,
                "campaign_id": campaign_id,
                "branch": parent_branch,
                "worktree_root": str(parent_worktree_root),
                "flow_type": "analysis_campaign",
                "protocol_step": "create",
                "paths": {
                    "campaign_md": str(charter_path),
                },
                "details": {
                    "campaign_title": campaign_title,
                    "campaign_goal": campaign_goal,
                    "parent_run_id": parent_run_id,
                    "slice_count": len(slice_contexts),
                    "slices": [
                        {
                            "slice_id": item["slice_id"],
                            "title": item["title"],
                            "branch": item["branch"],
                            "worktree_root": item["worktree_root"],
                            "run_kind": item["run_kind"],
                            "goal": item["goal"],
                            "must_not_simplify": item["must_not_simplify"],
                        }
                        for item in slice_contexts
                    ],
                },
            },
            checkpoint=False,
            workspace_root=parent_worktree_root,
        )
        research_state = self.quest_service.update_research_state(
            quest_root,
            active_analysis_campaign_id=campaign_id,
            analysis_parent_branch=parent_branch,
            analysis_parent_worktree_root=str(parent_worktree_root),
            next_pending_slice_id=first_slice["slice_id"],
            current_workspace_branch=first_slice["branch"],
            current_workspace_root=first_slice["worktree_root"],
            workspace_mode="analysis",
            last_flow_type="analysis_campaign",
        )
        checkpoint_result = self._checkpoint_with_optional_push(
            parent_worktree_root,
            message=f"analysis: create {campaign_id}",
        )
        interaction = self.interact(
            quest_root,
            kind="milestone",
            message=(
                f"Analysis campaign `{campaign_id}` is ready.\n"
                f"- Parent branch: `{parent_branch}`\n"
                f"- Parent worktree: `{parent_worktree_root}`\n"
                f"- Next slice: `{first_slice['slice_id']}`\n"
                f"- Slice branch: `{first_slice['branch']}`\n"
                f"- Slice worktree: `{first_slice['worktree_root']}`\n"
                f"- Core requirement: {first_slice['must_not_simplify'] or 'Follow the full evaluation protocol.'}"
            ),
            deliver_to_bound_conversations=True,
            include_recent_inbound_messages=False,
            attachments=[
                {
                    "kind": "analysis_campaign",
                    "campaign_id": campaign_id,
                    "parent_branch": parent_branch,
                    "parent_worktree_root": str(parent_worktree_root),
                    "next_slice": first_slice,
                    "slices": slice_contexts,
                }
            ],
        )
        return {
            "ok": True,
            "campaign_id": campaign_id,
            "parent_branch": parent_branch,
            "parent_worktree_root": str(parent_worktree_root),
            "charter_path": str(charter_path),
            "slices": slice_contexts,
            "manifest": manifest,
            "artifact": artifact,
            "checkpoint": checkpoint_result,
            "interaction": interaction,
            "research_state": research_state,
        }

    def record_analysis_slice(
        self,
        quest_root: Path,
        *,
        campaign_id: str,
        slice_id: str,
        status: str = "completed",
        setup: str = "",
        execution: str = "",
        results: str = "",
        evidence_paths: list[str] | None = None,
        metric_rows: list[dict[str, Any]] | None = None,
        deviations: list[str] | None = None,
        dataset_scope: str = "full",
        subset_approval_ref: str | None = None,
    ) -> dict[str, Any]:
        state = self.quest_service.read_research_state(quest_root)
        manifest = self._read_analysis_manifest(quest_root, campaign_id)
        slices = [dict(item) for item in (manifest.get("slices") or [])]
        target = next((item for item in slices if str(item.get("slice_id") or "").strip() == slice_id), None)
        if target is None:
            raise FileNotFoundError(f"Unknown analysis slice `{slice_id}` in campaign `{campaign_id}`.")
        normalized_scope = str(dataset_scope or "full").strip().lower() or "full"
        if normalized_scope == "subset" and not str(subset_approval_ref or "").strip():
            raise ValueError("Subset analysis requires `subset_approval_ref`.")

        evidence_paths = [str(item).strip() for item in (evidence_paths or []) if str(item).strip()]
        deviations = [str(item).strip() for item in (deviations or []) if str(item).strip()]
        metric_rows = [item for item in (metric_rows or []) if isinstance(item, dict)]
        slice_worktree_root = Path(str(target.get("worktree_root") or ""))
        parent_worktree_root = Path(str(manifest.get("parent_worktree_root") or ""))
        parent_branch = str(manifest.get("parent_branch") or "")

        result_dir = ensure_dir(slice_worktree_root / "experiments" / "analysis" / campaign_id / slice_id)
        result_path = result_dir / "RESULT.md"
        result_lines = [
            f"# {target.get('title') or slice_id}",
            "",
            f"- Campaign: `{campaign_id}`",
            f"- Slice: `{slice_id}`",
            f"- Branch: `{target.get('branch')}`",
            f"- Worktree: `{slice_worktree_root}`",
            f"- Status: `{status}`",
            f"- Dataset scope: `{normalized_scope}`",
            "",
            "## Setup",
            "",
            setup.strip() or "TBD",
            "",
            "## Execution",
            "",
            execution.strip() or "TBD",
            "",
            "## Results",
            "",
            results.strip() or "TBD",
            "",
            "## Deviations",
            "",
        ]
        if deviations:
            result_lines.extend([f"- {item}" for item in deviations])
        else:
            result_lines.append("- None recorded.")
        result_lines.extend(["", "## Evidence Paths", ""])
        if evidence_paths:
            result_lines.extend([f"- `{item}`" for item in evidence_paths])
        else:
            result_lines.append("- None recorded.")
        if metric_rows:
            result_lines.extend(["", "## Metric Rows", ""])
            for row in metric_rows:
                result_lines.append(f"- `{row}`")
        if subset_approval_ref:
            result_lines.extend(["", "## Subset Approval", "", f"`{subset_approval_ref}`"])
        write_text(result_path, "\n".join(result_lines).rstrip() + "\n")

        metrics_summary: dict[str, Any] = {}
        for row in metric_rows:
            name = str(row.get("name") or row.get("metric") or "").strip()
            if name:
                metrics_summary[name] = row.get("value")
                continue
            keys = [key for key in row.keys() if key not in {"split", "seed", "note", "notes"}]
            if len(keys) == 1:
                metrics_summary[keys[0]] = row.get(keys[0])

        mirror_dir = ensure_dir(parent_worktree_root / "experiments" / "analysis-results" / campaign_id)
        mirror_path = mirror_dir / f"{slice_id}.md"
        mirror_lines = [
            f"# {target.get('title') or slice_id}",
            "",
            f"- Source branch: `{target.get('branch')}`",
            f"- Source worktree: `{slice_worktree_root}`",
            f"- Source result: `{result_path}`",
            f"- Status: `{status}`",
            "",
            "## Goal",
            "",
            str(target.get("goal") or "").strip() or "TBD",
            "",
            "## Core Requirement",
            "",
            str(target.get("must_not_simplify") or "").strip() or "Full protocol only.",
            "",
            "## Setup",
            "",
            setup.strip() or "TBD",
            "",
            "## Execution",
            "",
            execution.strip() or "TBD",
            "",
            "## Results",
            "",
            results.strip() or "TBD",
            "",
        ]
        write_text(mirror_path, "\n".join(mirror_lines).rstrip() + "\n")

        artifact = self.record(
            quest_root,
            {
                "kind": "run",
                "status": status,
                "run_id": f"{campaign_id}:{slice_id}",
                "run_kind": str(target.get("run_kind") or "analysis.slice"),
                "summary": f"Analysis slice `{slice_id}` recorded with status `{status}`.",
                "reason": "Each analysis slice must durably record setup, execution, results, and evidence.",
                "idea_id": manifest.get("active_idea_id"),
                "campaign_id": campaign_id,
                "slice_id": slice_id,
                "branch": str(target.get("branch") or ""),
                "parent_branch": parent_branch,
                "worktree_root": str(slice_worktree_root),
                "worktree_rel_path": self._workspace_relative(quest_root, slice_worktree_root),
                "metrics_summary": metrics_summary,
                "flow_type": "analysis_slice",
                "protocol_step": "record",
                "paths": {
                    "slice_result_md": str(result_path),
                    "parent_result_md": str(mirror_path),
                },
                "details": {
                    "title": target.get("title"),
                    "goal": target.get("goal"),
                    "must_not_simplify": target.get("must_not_simplify"),
                    "dataset_scope": normalized_scope,
                    "subset_approval_ref": subset_approval_ref,
                    "metric_rows": metric_rows,
                    "deviations": deviations,
                    "evidence_paths": evidence_paths,
                },
            },
            checkpoint=False,
            workspace_root=slice_worktree_root,
        )
        slice_checkpoint = self._checkpoint_with_optional_push(
            slice_worktree_root,
            message=f"analysis: complete {campaign_id}/{slice_id}",
        )
        parent_checkpoint = self._checkpoint_with_optional_push(
            parent_worktree_root,
            message=f"analysis: mirror {campaign_id}/{slice_id}",
        )

        updated_slices: list[dict[str, Any]] = []
        for item in slices:
            if str(item.get("slice_id") or "") != slice_id:
                updated_slices.append(item)
                continue
            updated = dict(item)
            updated["status"] = status
            updated["completed_at"] = utc_now()
            updated["result_path"] = str(result_path)
            updated["mirror_path"] = str(mirror_path)
            updated_slices.append(updated)
        next_slice = next((item for item in updated_slices if str(item.get("status") or "") == "pending"), None)
        manifest = self._write_analysis_manifest(
            quest_root,
            campaign_id,
            {
                **manifest,
                "slices": updated_slices,
            },
        )

        if next_slice is not None:
            research_state = self.quest_service.update_research_state(
                quest_root,
                active_analysis_campaign_id=campaign_id,
                next_pending_slice_id=next_slice.get("slice_id"),
                current_workspace_branch=next_slice.get("branch"),
                current_workspace_root=next_slice.get("worktree_root"),
                workspace_mode="analysis",
                last_flow_type="analysis_slice",
            )
            interaction = self.interact(
                quest_root,
                kind="progress",
                message=(
                    f"Analysis slice `{slice_id}` is complete.\n"
                    f"- Parent branch mirror updated: `{mirror_path}`\n"
                    f"- Next slice: `{next_slice['slice_id']}`\n"
                    f"- Next branch: `{next_slice['branch']}`\n"
                    f"- Next worktree: `{next_slice['worktree_root']}`\n"
                    f"- Core requirement: {next_slice.get('must_not_simplify') or 'Use the full intended evaluation protocol.'}"
                ),
                deliver_to_bound_conversations=True,
                include_recent_inbound_messages=False,
                attachments=[
                    {
                        "kind": "analysis_slice",
                        "campaign_id": campaign_id,
                        "completed_slice_id": slice_id,
                        "next_slice": next_slice,
                        "parent_result_md": str(mirror_path),
                    }
                ],
            )
            return {
                "ok": True,
                "campaign_id": campaign_id,
                "slice_id": slice_id,
                "status": status,
                "result_path": str(result_path),
                "mirror_path": str(mirror_path),
                "artifact": artifact,
                "slice_checkpoint": slice_checkpoint,
                "parent_checkpoint": parent_checkpoint,
                "next_slice": next_slice,
                "manifest": manifest,
                "interaction": interaction,
                "research_state": research_state,
                "completed": False,
            }

        summary_path = mirror_dir / "SUMMARY.md"
        summary_lines = [
            f"# Analysis Campaign {campaign_id}",
            "",
            f"- Parent branch: `{parent_branch}`",
            f"- Parent worktree: `{parent_worktree_root}`",
            "",
            "## Completed Slices",
            "",
        ]
        for item in updated_slices:
            summary_lines.append(
                f"- `{item['slice_id']}` · {item.get('status', 'completed')} · `{item.get('mirror_path') or item.get('result_path')}`"
            )
        write_text(summary_path, "\n".join(summary_lines).rstrip() + "\n")
        summary_artifact = self.record(
            quest_root,
            {
                "kind": "report",
                "status": "completed",
                "report_type": "analysis_campaign_summary",
                "summary": f"Analysis campaign `{campaign_id}` is complete and merged back into the parent experiment branch.",
                "reason": "All configured analysis slices completed, so the quest can return to writing on the parent branch.",
                "idea_id": manifest.get("active_idea_id"),
                "campaign_id": campaign_id,
                "branch": parent_branch,
                "worktree_root": str(parent_worktree_root),
                "flow_type": "analysis_campaign",
                "protocol_step": "complete",
                "paths": {
                    "summary_md": str(summary_path),
                },
                "details": {
                    "slice_count": len(updated_slices),
                    "completed_slices": [
                        {
                            "slice_id": item.get("slice_id"),
                            "status": item.get("status"),
                            "mirror_path": item.get("mirror_path"),
                        }
                        for item in updated_slices
                    ],
                },
            },
            checkpoint=False,
            workspace_root=parent_worktree_root,
        )
        parent_summary_checkpoint = self._checkpoint_with_optional_push(
            parent_worktree_root,
            message=f"analysis: summarize {campaign_id}",
        )
        research_state = self.quest_service.update_research_state(
            quest_root,
            active_analysis_campaign_id=None,
            next_pending_slice_id=None,
            current_workspace_branch=state.get("research_head_branch") or parent_branch,
            current_workspace_root=state.get("research_head_worktree_root") or str(parent_worktree_root),
            workspace_mode="idea",
            last_flow_type="analysis_campaign_complete",
        )
        interaction = self.interact(
            quest_root,
            kind="milestone",
            message=(
                f"All analysis slices in `{campaign_id}` are complete.\n"
                f"- Returned to parent branch: `{parent_branch}`\n"
                f"- Parent worktree: `{parent_worktree_root}`\n"
                f"- Analysis summary: `{summary_path}`\n"
                "You should now continue on the main experiment branch and start writing the paper."
            ),
            deliver_to_bound_conversations=True,
            include_recent_inbound_messages=False,
            attachments=[
                {
                    "kind": "analysis_campaign_complete",
                    "campaign_id": campaign_id,
                    "parent_branch": parent_branch,
                    "parent_worktree_root": str(parent_worktree_root),
                    "summary_path": str(summary_path),
                }
            ],
        )
        return {
            "ok": True,
            "campaign_id": campaign_id,
            "slice_id": slice_id,
            "status": status,
            "result_path": str(result_path),
            "mirror_path": str(mirror_path),
            "artifact": artifact,
            "slice_checkpoint": slice_checkpoint,
            "parent_checkpoint": parent_checkpoint,
            "summary_artifact": summary_artifact,
            "summary_checkpoint": parent_summary_checkpoint,
            "summary_path": str(summary_path),
            "manifest": manifest,
            "interaction": interaction,
            "research_state": research_state,
            "completed": True,
            "returned_to_branch": parent_branch,
            "returned_to_worktree_root": str(parent_worktree_root),
        }

    def publish_baseline(self, quest_root: Path, payload: dict) -> dict:
        data = dict(payload)
        data["kind"] = "baseline"
        data["publish_global"] = True
        return self.record(quest_root, data)

    def attach_baseline(self, quest_root: Path, baseline_id: str, variant_id: str | None = None) -> dict:
        attachment = self.baselines.attach(quest_root, baseline_id, variant_id)
        artifact = self.record(
            quest_root,
            {
                "kind": "report",
                "status": "completed",
                "report_type": "baseline_attachment",
                "report_id": generate_id("report"),
                "summary": f"Attached baseline `{baseline_id}`.",
                "reason": "Baseline reuse avoids repeating an already stable reproduction.",
                "baseline_id": baseline_id,
                "baseline_variant_id": attachment.get("source_variant_id"),
                "paths": {
                    "attachment_yaml": str(quest_root / "baselines" / "imported" / baseline_id / "attachment.yaml"),
                },
                "source": {"kind": "system", "role": "artifact"},
            },
        )
        return {
            "ok": True,
            "attachment": attachment,
            "artifact": artifact,
            "guidance": "Reuse the attached baseline metadata and metrics before deciding whether a new reproduction is necessary.",
        }

    def refresh_summary(self, quest_root: Path, *, reason: str | None = None) -> dict:
        workspace_root = self._workspace_root_for(quest_root)
        recent = self.recent(quest_root, limit=20)
        latest_runs = [item for item in recent if item.get("kind") == "runs"][-5:]
        latest_decisions = [item for item in recent if item.get("kind") == "decisions"][-5:]
        lines = [
            "# Quest Summary",
            "",
            f"- Updated at: {utc_now()}",
            f"- Branch: `{current_branch(workspace_root)}`",
            f"- Head: `{head_commit(workspace_root) or 'none'}`",
        ]
        if reason:
            lines.extend(["", f"- Refresh reason: {reason}"])
        if latest_decisions:
            lines.extend(["", "## Recent decisions"])
            for item in latest_decisions:
                payload = read_json(Path(item["path"]), {})
                lines.append(f"- `{payload.get('artifact_id')}`: {payload.get('reason', 'No reason provided.')}")
        if latest_runs:
            lines.extend(["", "## Recent runs"])
            for item in latest_runs:
                payload = read_json(Path(item["path"]), {})
                summary = payload.get("summary") or "No summary provided."
                lines.append(f"- `{payload.get('run_id') or payload.get('artifact_id')}`: {summary}")
        summary_path = workspace_root / "SUMMARY.md"
        write_text(summary_path, "\n".join(lines).rstrip() + "\n")
        artifact = self.record(
            quest_root,
            {
                "kind": "report",
                "status": "completed",
                "report_type": "summary_refresh",
                "report_id": generate_id("report"),
                "summary": "Quest summary refreshed from recent artifacts.",
                "reason": reason or "Summary refreshed after artifact updates.",
                "paths": {"summary_md": str(summary_path)},
                "source": {"kind": "system", "role": "artifact"},
            },
            workspace_root=workspace_root,
        )
        return {
            "ok": True,
            "summary_path": str(summary_path),
            "artifact": artifact,
            "guidance": "Use the refreshed SUMMARY.md as the compact quest state for the next turn.",
        }

    def render_git_graph(self, quest_root: Path) -> dict:
        graph_manifest = export_git_graph(quest_root, ensure_dir(quest_root / "artifacts" / "graphs"))
        artifact = self.record(
            quest_root,
            {
                "kind": "graph",
                "status": "generated",
                "graph_id": generate_id("graph"),
                "graph_type": "git_history",
                "summary": "Quest git graph exported.",
                "branch_summary": [graph_manifest.get("branch")],
                "head_commit": graph_manifest.get("head"),
                "commit_count": len(graph_manifest.get("lines", [])),
                "paths": {
                    "svg": graph_manifest.get("svg_path"),
                    "png": graph_manifest.get("png_path"),
                    "json": graph_manifest.get("json_path"),
                },
                "source": {"kind": "daemon"},
            },
            checkpoint=False,
        )
        return {
            "ok": True,
            "guidance": "Share the graph preview when you need to explain the research history or branching state.",
            "graph": graph_manifest,
            "artifact": artifact,
        }

    def interact(
        self,
        quest_root: Path,
        *,
        kind: str = "progress",
        message: str = "",
        response_phase: str = "ack",
        importance: str = "info",
        deliver_to_bound_conversations: bool = True,
        include_recent_inbound_messages: bool = True,
        recent_message_limit: int = 8,
        attachments: list[dict[str, Any]] | None = None,
        interaction_id: str | None = None,
        expects_reply: bool | None = None,
        reply_mode: str | None = None,
        options: list[dict[str, Any]] | None = None,
        allow_free_text: bool = True,
        reply_schema: dict[str, Any] | None = None,
        reply_to_interaction_id: str | None = None,
        supersede_open_requests: bool = True,
    ) -> dict:
        durable_kind = {
            "progress": "progress",
            "milestone": "milestone",
            "decision_request": "decision",
            "approval_result": "approval",
        }.get(kind, "progress")
        reply_mode_resolved = str(
            reply_mode
            or ("blocking" if kind == "decision_request" else "threaded" if kind in {"progress", "milestone"} else "none")
        ).strip().lower()
        if reply_mode_resolved not in {"none", "threaded", "blocking"}:
            reply_mode_resolved = "blocking" if kind == "decision_request" else "threaded"
        expects_reply_resolved = bool(expects_reply) if expects_reply is not None else reply_mode_resolved == "blocking"
        resolved_artifact_id = generate_id(durable_kind)
        resolved_interaction_id = interaction_id or (
            resolved_artifact_id if reply_mode_resolved != "none" or reply_to_interaction_id else None
        )
        payload: dict[str, Any] = {
            "kind": durable_kind,
            "artifact_id": resolved_artifact_id,
            "status": "active" if durable_kind == "progress" else "completed",
            "message": message,
            "summary": message,
            "interaction_phase": "request" if kind == "decision_request" else response_phase,
            "importance": importance,
            "attachments": attachments or [],
            "interaction_id": resolved_interaction_id,
            "expects_reply": expects_reply_resolved,
            "reply_mode": reply_mode_resolved,
            "options": options or [],
            "allow_free_text": allow_free_text,
            "reply_schema": reply_schema or {},
            "reply_to_interaction_id": reply_to_interaction_id,
            "source": {"kind": "agent", "role": "pi"},
        }
        if durable_kind == "decision":
            payload.update(
                {
                    "verdict": "pending_user",
                    "action": "request_user_decision",
                    "reason": message or "Decision request emitted for user review.",
                }
            )
        if durable_kind == "approval":
            payload.setdefault("reason", message or "Approval result emitted.")
        artifact = self.record(
            quest_root,
            payload,
            checkpoint=durable_kind in {"milestone", "decision", "approval"},
        )
        request_state = self._update_interaction_state(
            quest_root,
            artifact=artifact.get("record") or {},
            kind=kind,
            expects_reply=expects_reply_resolved,
            reply_mode=reply_mode_resolved,
            message=message,
            options=options or [],
            allow_free_text=allow_free_text,
            reply_schema=reply_schema or {},
            reply_to_interaction_id=reply_to_interaction_id,
            supersede_open_requests=supersede_open_requests,
        )
        delivery_targets: list[str] = []
        delivered = False
        if deliver_to_bound_conversations:
            connectors = self._connectors_config()
            targets = self._select_delivery_targets(
                self._bound_conversations(quest_root),
                connectors=connectors,
            )
            for target in targets:
                channel_name = self._normalize_channel_name(target)
                payload = {
                    "quest_root": str(quest_root),
                    "quest_id": self._quest_id(quest_root),
                    "conversation_id": target,
                    "kind": kind,
                    "message": message,
                    "response_phase": response_phase,
                    "importance": importance,
                    "artifact_id": artifact.get("artifact_id"),
                    "interaction_id": request_state.get("interaction_id"),
                    "expects_reply": expects_reply_resolved,
                    "reply_mode": reply_mode_resolved,
                    "options": options or [],
                    "allow_free_text": allow_free_text,
                    "reply_schema": reply_schema or {},
                    "reply_to_interaction_id": reply_to_interaction_id,
                    "attachments": attachments or [],
                }
                if self._send_to_channel(channel_name, payload, connectors=connectors):
                    delivery_targets.append(target)
                    delivered = True

        mailbox_payload = {
            "delivery_batch": None,
            "recent_inbound_messages": [],
            "recent_interaction_records": [],
            "agent_instruction": "当前用户并没有发送任何消息，请按照用户的要求继续进行任务。",
            "queued_message_count_before_delivery": 0,
            "queued_message_count_after_delivery": 0,
        }
        if include_recent_inbound_messages:
            mailbox_payload = self.quest_service.consume_pending_user_messages(
                quest_root,
                interaction_id=request_state.get("interaction_id"),
                limit=recent_message_limit,
            )
        self.quest_service.record_artifact_interaction(
            quest_root,
            interaction_id=request_state.get("interaction_id"),
            artifact_id=artifact.get("artifact_id"),
            kind=kind,
            message=message,
            response_phase=response_phase,
            reply_mode=reply_mode_resolved,
            created_at=(artifact.get("record") or {}).get("updated_at"),
        )

        return {
            "status": "ok",
            "artifact_id": artifact.get("artifact_id"),
            "interaction_id": request_state.get("interaction_id"),
            "expects_reply": expects_reply_resolved,
            "reply_mode": reply_mode_resolved,
            "delivered": delivered,
            "response_phase": response_phase,
            "delivery_targets": delivery_targets,
            "delivery_policy": self._delivery_policy(self._connectors_config()),
            "preferred_connector": self._preferred_connector(self._connectors_config()),
            "recent_inbound_messages": mailbox_payload.get("recent_inbound_messages") or [],
            "delivery_batch": mailbox_payload.get("delivery_batch"),
            "recent_interaction_records": mailbox_payload.get("recent_interaction_records") or [],
            "agent_instruction": mailbox_payload.get("agent_instruction"),
            "queued_message_count_before_delivery": mailbox_payload.get("queued_message_count_before_delivery", 0),
            "queued_message_count_after_delivery": mailbox_payload.get("queued_message_count_after_delivery", 0),
            "open_request_count": request_state.get("open_request_count", 0),
            "active_request": request_state.get("active_request"),
            "default_reply_interaction_id": request_state.get("default_reply_interaction_id"),
            "guidance": "如果收到新的用户要求，请先吸收这些要求；如果没有新消息，请继续当前任务并在真实检查点再次汇报。",
        }

    def recent(self, quest_root: Path, limit: int = 20) -> list[dict]:
        items: list[dict] = []
        seen: set[str] = set()
        for root in self.quest_service.workspace_roots(quest_root):
            artifacts_root = root / "artifacts"
            if not artifacts_root.exists():
                continue
            for folder in sorted(artifacts_root.glob("*")):
                if not folder.is_dir():
                    continue
                for path in sorted(folder.glob("*.json")):
                    key = str(path.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append({"path": str(path), "name": path.name, "kind": folder.name, "workspace_root": str(root)})
        return items[-limit:]

    def _build_record(self, quest_root: Path, payload: dict, *, workspace_root: Path | None = None) -> dict:
        timestamp = utc_now()
        kind = payload["kind"]
        artifact_id = payload.get("artifact_id") or payload.get("id") or generate_id(kind)
        quest_id = payload.get("quest_id") or self._quest_id(quest_root)
        status = payload.get("status") or self._default_status(kind)
        source = payload.get("source") or {"kind": "agent"}
        resolved_workspace = self._workspace_root_for(quest_root, workspace_root)
        active_branch = current_branch(resolved_workspace)
        active_head = head_commit(resolved_workspace)
        return {
            "kind": kind,
            "schema_version": 1,
            "artifact_id": artifact_id,
            "id": artifact_id,
            "quest_id": quest_id,
            "created_at": payload.get("created_at", timestamp),
            "updated_at": timestamp,
            "source": source,
            "status": status,
            "branch": payload.get("branch") or active_branch,
            "head_commit": payload.get("head_commit") or active_head,
            "workspace_root": payload.get("workspace_root") or str(resolved_workspace),
            "workspace_rel_path": payload.get("workspace_rel_path") or self._workspace_relative(quest_root, resolved_workspace),
            **payload,
        }

    def _artifact_path(self, quest_root: Path, kind: str, artifact_id: str) -> Path:
        directory = ensure_dir(quest_root / "artifacts" / ARTIFACT_DIRS[kind])
        return directory / f"{artifact_id}.json"

    @staticmethod
    def _index_line(record: dict, artifact_path: Path) -> dict:
        return {
            "artifact_id": record.get("artifact_id"),
            "kind": record.get("kind"),
            "status": record.get("status"),
            "quest_id": record.get("quest_id"),
            "path": str(artifact_path),
            "summary": record.get("summary") or record.get("message"),
            "updated_at": record.get("updated_at"),
        }

    @staticmethod
    def _default_status(kind: str) -> str:
        return {
            "progress": "active",
            "decision": "pending",
            "approval": "accepted",
            "graph": "generated",
        }.get(kind, "completed")

    @staticmethod
    def _should_checkpoint(kind: str) -> bool:
        return kind in {"baseline", "decision", "milestone", "run", "report", "approval"}

    def _touch_quest_updated_at(self, quest_root: Path) -> None:
        quest_path = quest_root / "quest.yaml"
        quest_data = read_yaml(quest_path, {})
        quest_data["updated_at"] = utc_now()
        write_yaml(quest_path, quest_data)

    def _set_quest_status(self, quest_root: Path, status: str) -> None:
        self.quest_service.update_runtime_state(
            quest_root=quest_root,
            status=status,
            stop_reason=None,
        )

    def _quest_id(self, quest_root: Path) -> str:
        quest_yaml = read_yaml(quest_root / "quest.yaml", {})
        return str(quest_yaml.get("quest_id") or quest_root.name)

    def _default_branch_name(
        self,
        quest_root: Path,
        *,
        run_id: str | None,
        idea_id: str | None,
        branch_kind: str,
    ) -> str:
        quest_id = self._quest_id(quest_root)
        if branch_kind == "idea" and idea_id:
            return f"idea/{quest_id}-{idea_id}"
        if branch_kind == "quest":
            return f"quest/{quest_id}"
        return f"run/{run_id or generate_id('run')}"

    def _bound_conversations(self, quest_root: Path) -> list[str]:
        state_path = quest_root / ".ds" / "bindings.json"
        payload = read_json(state_path, {"sources": ["local:default"]})
        sources = [self._normalize_conversation_id(str(item)) for item in (payload.get("sources") or ["local:default"])]
        connector_sources = self._connector_bound_conversations(self._quest_id(quest_root))
        return self._dedupe_targets([*connector_sources, *sources])

    def _connector_bound_conversations(self, quest_id: str) -> list[str]:
        root = self.home / "logs" / "connectors"
        if not root.exists():
            return []
        targets: list[str] = []
        for bindings_path in sorted(root.glob("*/bindings.json")):
            payload = read_json(bindings_path, {})
            bindings = payload.get("bindings") if isinstance(payload.get("bindings"), dict) else {}
            for conversation_id, binding in bindings.items():
                if not isinstance(binding, dict):
                    continue
                if str(binding.get("quest_id") or "").strip() != quest_id:
                    continue
                normalized = self._normalize_conversation_id(str(conversation_id))
                if normalized:
                    targets.append(normalized)
        return targets

    def _connectors_config(self) -> dict[str, Any]:
        return ConfigManager(self.home).load_named("connectors")

    @staticmethod
    def _delivery_policy(connectors: dict[str, Any]) -> str:
        routing = connectors.get("_routing") if isinstance(connectors.get("_routing"), dict) else {}
        policy = str(routing.get("artifact_delivery_policy") or "primary_plus_local").strip().lower()
        if policy in {"fanout_all", "primary_only", "primary_plus_local"}:
            return policy
        return "primary_plus_local"

    @staticmethod
    def _enabled_connectors(connectors: dict[str, Any]) -> list[str]:
        names: list[str] = []
        for name, config in connectors.items():
            if str(name).startswith("_") or name == "local":
                continue
            if isinstance(config, dict) and bool(config.get("enabled", False)):
                names.append(str(name).strip().lower())
        return names

    def _preferred_connector(self, connectors: dict[str, Any]) -> str | None:
        routing = connectors.get("_routing") if isinstance(connectors.get("_routing"), dict) else {}
        enabled = self._enabled_connectors(connectors)
        preferred = str(routing.get("primary_connector") or "").strip().lower()
        if preferred and preferred in enabled:
            return preferred
        if len(enabled) == 1:
            return enabled[0]
        return None

    def _select_delivery_targets(self, targets: list[str], *, connectors: dict[str, Any]) -> list[str]:
        if not targets:
            return ["local:default"]
        policy = self._delivery_policy(connectors)
        if policy == "fanout_all":
            return self._dedupe_targets(targets)

        preferred = self._preferred_connector(connectors)
        local_targets = [target for target in targets if self._normalize_channel_name(target) == "local"]
        preferred_targets = [
            target for target in targets if preferred and self._normalize_channel_name(target) == preferred
        ]
        non_local_targets = [target for target in targets if self._normalize_channel_name(target) != "local"]
        fallback_primary = preferred_targets or non_local_targets[:1]

        if policy == "primary_only":
            selected = fallback_primary or local_targets or targets[:1]
            return self._dedupe_targets(selected)

        selected = [*local_targets, *fallback_primary]
        if not selected:
            selected = targets[:1]
        return self._dedupe_targets(selected)

    @staticmethod
    def _dedupe_targets(targets: list[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for target in targets:
            normalized = str(target or "").strip()
            identity = conversation_identity_key(normalized)
            if not normalized or identity in seen:
                continue
            seen.add(identity)
            ordered.append(normalized)
        return ordered

    @staticmethod
    def _normalize_channel_name(target: str) -> str:
        source = (target or "local:default").split(":", 1)[0].strip().lower()
        if source in {"web", "cli", "api", "command", "local", "local-ui"}:
            return "local"
        return source or "local"

    def _send_to_channel(self, channel_name: str, payload: dict[str, Any], *, connectors: dict[str, Any] | None = None) -> bool:
        resolved_connectors = connectors or self._connectors_config()
        if channel_name != "local":
            config = resolved_connectors.get(channel_name, {})
            if not isinstance(config, dict) or not bool(config.get("enabled", False)):
                return False
        try:
            register_builtin_channels(home=self.home, connectors_config=resolved_connectors)
            factory = get_channel_factory(channel_name)
        except Exception:
            return False
        channel = factory(home=self.home, config=resolved_connectors.get(channel_name, {}))
        result = channel.send(payload)
        delivery = result.get("delivery")
        if isinstance(delivery, dict):
            return bool(delivery.get("ok", False) or delivery.get("queued", False))
        return bool(result.get("ok", False) or result.get("queued", False))

    def _recent_inbound_messages(self, quest_root: Path, *, limit: int) -> list[dict]:
        conversation_path = quest_root / ".ds" / "conversations" / "main.jsonl"
        cursor = self._read_interaction_state(quest_root)
        last_seen_id = cursor.get("last_seen_user_message_id")
        messages = [item for item in read_jsonl(conversation_path) if item.get("role") == "user"]
        unseen: list[dict] = []
        if last_seen_id:
            seen = False
            for item in messages:
                if seen:
                    unseen.append(item)
                elif item.get("id") == last_seen_id:
                    seen = True
            if not seen:
                unseen = messages[-limit:]
        else:
            unseen = messages[-limit:]
        if unseen:
            cursor["last_seen_user_message_id"] = unseen[-1].get("id")
            self._write_interaction_state(quest_root, cursor)
        serialized: list[dict[str, Any]] = []
        for item in unseen[-limit:]:
            conversation_id = self._normalize_conversation_id(str(item.get("source") or "local"))
            payload: dict[str, Any] = {
                "message_id": item.get("id"),
                "source": conversation_id.split(":", 1)[0],
                "conversation_id": conversation_id,
                "sender": item.get("role"),
                "created_at": item.get("created_at"),
                "text": item.get("content") or "",
                "content": item.get("content") or "",
            }
            reply_to = str(item.get("reply_to_interaction_id") or "").strip()
            if reply_to:
                payload["reply_to_interaction_id"] = reply_to
            serialized.append(payload)
        return serialized

    def _read_interaction_state(self, quest_root: Path) -> dict[str, Any]:
        state = read_json(self._interaction_state_path(quest_root), {})
        state.setdefault("open_requests", [])
        state.setdefault("recent_threads", [])
        return state

    def _write_interaction_state(self, quest_root: Path, state: dict[str, Any]) -> None:
        write_json(self._interaction_state_path(quest_root), state)

    @staticmethod
    def _interaction_state_path(quest_root: Path) -> Path:
        return quest_root / ".ds" / "interaction_state.json"

    def _update_interaction_state(
        self,
        quest_root: Path,
        *,
        artifact: dict[str, Any],
        kind: str,
        expects_reply: bool,
        reply_mode: str,
        message: str,
        options: list[dict[str, Any]],
        allow_free_text: bool,
        reply_schema: dict[str, Any],
        reply_to_interaction_id: str | None,
        supersede_open_requests: bool,
    ) -> dict[str, Any]:
        state = self._read_interaction_state(quest_root)
        open_requests = [dict(item) for item in (state.get("open_requests") or [])]
        recent_threads = [dict(item) for item in (state.get("recent_threads") or [])]
        interaction_id = str(artifact.get("interaction_id") or artifact.get("artifact_id") or generate_id("interact"))
        now = utc_now()

        if reply_to_interaction_id:
            open_requests = self._close_interaction_request_in_memory(
                open_requests,
                interaction_id=str(reply_to_interaction_id),
                closing_artifact_id=str(artifact.get("artifact_id") or ""),
                closed_at=now,
            )

        if reply_mode in {"threaded", "blocking"}:
            thread_record = {
                "interaction_id": interaction_id,
                "artifact_id": artifact.get("artifact_id"),
                "kind": kind,
                "reply_mode": reply_mode,
                "status": "waiting" if reply_mode == "blocking" else "active",
                "message": message,
                "options": options,
                "allow_free_text": allow_free_text,
                "reply_schema": reply_schema,
                "created_at": artifact.get("updated_at") or now,
                "updated_at": artifact.get("updated_at") or now,
            }
            recent_threads = self._upsert_recent_thread(recent_threads, thread_record)
            state["last_outbound_interaction_id"] = interaction_id
            state["latest_thread_interaction_id"] = interaction_id

        active_request: dict[str, Any] | None = None
        if reply_mode == "blocking":
            if supersede_open_requests:
                for index, item in enumerate(open_requests):
                    if item.get("status") not in {"waiting", "answered"}:
                        continue
                    updated = dict(item)
                    updated["status"] = "superseded"
                    updated["closed_at"] = now
                    updated["superseded_by"] = interaction_id
                    open_requests[index] = updated
            active_request = {
                "interaction_id": interaction_id,
                "artifact_id": artifact.get("artifact_id"),
                "kind": kind,
                "status": "waiting",
                "message": message,
                "options": options,
                "allow_free_text": allow_free_text,
                "reply_schema": reply_schema,
                "created_at": artifact.get("updated_at") or now,
            }
            open_requests.append(active_request)
            self._set_quest_status(quest_root, "waiting_for_user")

        state["open_requests"] = open_requests[-20:]
        state["recent_threads"] = recent_threads[-30:]
        state["default_reply_interaction_id"] = self._default_reply_interaction_id(
            open_requests=state["open_requests"],
            recent_threads=state["recent_threads"],
        )
        self._write_interaction_state(quest_root, state)
        waiting = [item for item in state["open_requests"] if str(item.get("status") or "") == "waiting"]
        if not waiting:
            self._resume_from_waiting_if_needed(quest_root)
        return {
            "interaction_id": interaction_id,
            "open_request_count": len(waiting),
            "active_request": active_request,
            "default_reply_interaction_id": state.get("default_reply_interaction_id"),
        }

    @staticmethod
    def _normalize_conversation_id(source: str) -> str:
        return normalize_conversation_id(source)

    def _close_interaction_request(
        self,
        quest_root: Path,
        *,
        interaction_id: str,
        closing_artifact_id: str,
    ) -> None:
        state = self._read_interaction_state(quest_root)
        open_requests = self._close_interaction_request_in_memory(
            list(state.get("open_requests") or []),
            interaction_id=interaction_id,
            closing_artifact_id=closing_artifact_id,
            closed_at=utc_now(),
        )
        state["open_requests"] = open_requests[-20:]
        state["default_reply_interaction_id"] = self._default_reply_interaction_id(
            open_requests=state["open_requests"],
            recent_threads=state.get("recent_threads") or [],
        )
        self._write_interaction_state(quest_root, state)
        if not any(str(item.get("status") or "") == "waiting" for item in open_requests):
            self._resume_from_waiting_if_needed(quest_root)

    @staticmethod
    def _close_interaction_request_in_memory(
        open_requests: list[dict[str, Any]],
        *,
        interaction_id: str,
        closing_artifact_id: str,
        closed_at: str,
    ) -> list[dict[str, Any]]:
        updated_requests = [dict(item) for item in open_requests]
        for index, item in enumerate(updated_requests):
            candidate_ids = {
                str(item.get("interaction_id") or "").strip(),
                str(item.get("artifact_id") or "").strip(),
            }
            if str(interaction_id) not in candidate_ids:
                continue
            updated = dict(item)
            updated["status"] = "closed"
            updated["closed_at"] = closed_at
            updated["closed_by_artifact_id"] = closing_artifact_id
            updated_requests[index] = updated
        return updated_requests

    @staticmethod
    def _upsert_recent_thread(
        recent_threads: list[dict[str, Any]],
        thread_record: dict[str, Any],
    ) -> list[dict[str, Any]]:
        updated_threads = [dict(item) for item in recent_threads]
        interaction_id = str(thread_record.get("interaction_id") or "")
        for index, item in enumerate(updated_threads):
            candidate_ids = {
                str(item.get("interaction_id") or "").strip(),
                str(item.get("artifact_id") or "").strip(),
            }
            if interaction_id in candidate_ids:
                updated_threads[index] = {**item, **thread_record}
                return updated_threads
        updated_threads.append(thread_record)
        return updated_threads

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

    def _resume_from_waiting_if_needed(self, quest_root: Path) -> None:
        runtime_state = self.quest_service._read_runtime_state(quest_root)
        if str(runtime_state.get("status") or "") != "waiting_for_user":
            return
        self.quest_service.update_runtime_state(
            quest_root=quest_root,
            status="running" if runtime_state.get("active_run_id") else "active",
            stop_reason=None,
        )
