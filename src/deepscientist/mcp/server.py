from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..artifact import ArtifactService
from ..artifact.metrics import MetricContractValidationError
from ..bash_exec import BashExecService
from ..memory import MemoryService
from .context import McpContext

DEFAULT_INLINE_BASH_LOG_LINE_LIMIT = 2000
DEFAULT_INLINE_BASH_LOG_HEAD_LINES = 500
DEFAULT_INLINE_BASH_LOG_TAIL_LINES = 1500
DEFAULT_INLINE_BASH_LOG_WINDOW_LINES = 200
MAX_INLINE_BASH_LOG_WINDOW_LINES = 2000
LONG_BASH_LOG_HINT = (
    "Use `bash_exec(mode='read', id=..., start=..., tail=...)` to inspect a specific log window, "
    "or `bash_exec(mode='read', id=..., tail=...)` to inspect the latest rendered lines."
)


def _metric_validation_error_payload(exc: MetricContractValidationError) -> dict[str, Any]:
    return exc.as_payload()


def _split_bash_log_lines(log_text: str) -> list[str]:
    return log_text.splitlines()


def _join_bash_log_lines(lines: list[str]) -> str:
    return "\n".join(lines)


def _normalize_bash_log_window_size(value: int | None, *, default: int = DEFAULT_INLINE_BASH_LOG_WINDOW_LINES) -> int:
    resolved = default if value is None else int(value)
    return max(1, min(resolved, MAX_INLINE_BASH_LOG_WINDOW_LINES))


def _build_bash_log_window(log_text: str, *, start: int | None = None, tail: int | None = None) -> dict[str, Any]:
    lines = _split_bash_log_lines(log_text)
    total = len(lines)
    line_limit = _normalize_bash_log_window_size(tail)
    if start is not None:
        requested_start = max(1, int(start))
        start_index = min(max(0, requested_start - 1), total)
    else:
        start_index = max(0, total - line_limit)
    selected = lines[start_index : start_index + line_limit]
    returned_count = len(selected)
    line_start = start_index + 1 if total else 1
    line_end = start_index + returned_count
    return {
        "log": _join_bash_log_lines(selected),
        "log_line_count": total,
        "log_windowed": True,
        "line_start": line_start,
        "line_end": line_end,
        "line_limit": line_limit,
        "returned_line_count": returned_count,
        "has_more_before": start_index > 0,
        "has_more_after": line_end < total,
        "log_read_hint": LONG_BASH_LOG_HINT,
    }


def _build_default_bash_log_payload(log_text: str) -> dict[str, Any]:
    lines = _split_bash_log_lines(log_text)
    total = len(lines)
    if total <= DEFAULT_INLINE_BASH_LOG_LINE_LIMIT:
        return {
            "log": log_text,
            "log_line_count": total,
            "log_truncated": False,
        }
    omitted = total - DEFAULT_INLINE_BASH_LOG_HEAD_LINES - DEFAULT_INLINE_BASH_LOG_TAIL_LINES
    marker = (
        f"[... omitted {omitted} lines from the middle of this log. {LONG_BASH_LOG_HINT}]"
    )
    preview_lines = (
        lines[:DEFAULT_INLINE_BASH_LOG_HEAD_LINES]
        + [marker]
        + lines[-DEFAULT_INLINE_BASH_LOG_TAIL_LINES :]
    )
    return {
        "log": _join_bash_log_lines(preview_lines),
        "log_line_count": total,
        "log_truncated": True,
        "log_preview_head_lines": DEFAULT_INLINE_BASH_LOG_HEAD_LINES,
        "log_preview_tail_lines": DEFAULT_INLINE_BASH_LOG_TAIL_LINES,
        "log_preview_omitted_lines": omitted,
        "log_read_hint": LONG_BASH_LOG_HINT,
    }


def _stream_bash_log_summary(path: Path) -> tuple[list[str], int, list[str]]:
    total = 0
    full_lines: list[str] = []
    head_lines: list[str] = []
    tail_lines: deque[str] = deque(maxlen=DEFAULT_INLINE_BASH_LOG_TAIL_LINES)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            total += 1
            if total <= DEFAULT_INLINE_BASH_LOG_LINE_LIMIT:
                full_lines.append(line)
                continue
            if total == DEFAULT_INLINE_BASH_LOG_LINE_LIMIT + 1:
                head_lines = full_lines[:DEFAULT_INLINE_BASH_LOG_HEAD_LINES]
                tail_lines.extend(full_lines[-DEFAULT_INLINE_BASH_LOG_TAIL_LINES :])
                full_lines = []
            tail_lines.append(line)
    return full_lines, total, list(head_lines or tail_lines)


def _build_default_bash_log_payload_from_path(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "log": "",
            "log_line_count": 0,
            "log_truncated": False,
        }
    full_lines, total, preview_seed = _stream_bash_log_summary(path)
    if total <= DEFAULT_INLINE_BASH_LOG_LINE_LIMIT:
        return {
            "log": _join_bash_log_lines(full_lines),
            "log_line_count": total,
            "log_truncated": False,
        }
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        tail_lines: deque[str] = deque(maxlen=DEFAULT_INLINE_BASH_LOG_TAIL_LINES)
        for raw_line in handle:
            tail_lines.append(raw_line.rstrip("\n"))
    omitted = total - DEFAULT_INLINE_BASH_LOG_HEAD_LINES - DEFAULT_INLINE_BASH_LOG_TAIL_LINES
    marker = (
        f"[... omitted {omitted} lines from the middle of this log. {LONG_BASH_LOG_HINT}]"
    )
    preview_lines = preview_seed[:DEFAULT_INLINE_BASH_LOG_HEAD_LINES] + [marker] + list(tail_lines)
    return {
        "log": _join_bash_log_lines(preview_lines),
        "log_line_count": total,
        "log_truncated": True,
        "log_preview_head_lines": DEFAULT_INLINE_BASH_LOG_HEAD_LINES,
        "log_preview_tail_lines": DEFAULT_INLINE_BASH_LOG_TAIL_LINES,
        "log_preview_omitted_lines": omitted,
        "log_read_hint": LONG_BASH_LOG_HINT,
    }


def _build_bash_log_window_from_path(path: Path, *, start: int | None = None, tail: int | None = None) -> dict[str, Any]:
    if not path.exists():
        return {
            "log": "",
            "log_line_count": 0,
            "log_windowed": True,
            "line_start": 1,
            "line_end": 0,
            "line_limit": _normalize_bash_log_window_size(tail),
            "returned_line_count": 0,
            "has_more_before": False,
            "has_more_after": False,
            "log_read_hint": LONG_BASH_LOG_HINT,
        }
    line_limit = _normalize_bash_log_window_size(tail)
    if start is not None:
        requested_start = max(1, int(start))
        selected: list[str] = []
        total = 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                total += 1
                if total < requested_start:
                    continue
                if len(selected) < line_limit:
                    selected.append(raw_line.rstrip("\n"))
        returned_count = len(selected)
        line_start = requested_start if total else 1
        line_end = requested_start + returned_count - 1 if returned_count else requested_start - 1
        return {
            "log": _join_bash_log_lines(selected),
            "log_line_count": total,
            "log_windowed": True,
            "line_start": line_start,
            "line_end": line_end,
            "line_limit": line_limit,
            "returned_line_count": returned_count,
            "has_more_before": line_start > 1,
            "has_more_after": line_end < total,
            "log_read_hint": LONG_BASH_LOG_HINT,
        }

    tail_lines: deque[str] = deque(maxlen=line_limit)
    total = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            total += 1
            tail_lines.append(raw_line.rstrip("\n"))
    returned_count = len(tail_lines)
    line_start = max(1, total - returned_count + 1) if total else 1
    line_end = total
    return {
        "log": _join_bash_log_lines(list(tail_lines)),
        "log_line_count": total,
        "log_windowed": True,
        "line_start": line_start,
        "line_end": line_end,
        "line_limit": line_limit,
        "returned_line_count": returned_count,
        "has_more_before": line_start > 1,
        "has_more_after": False,
        "log_read_hint": LONG_BASH_LOG_HINT,
    }


def build_memory_server(context: McpContext) -> FastMCP:
    service = MemoryService(context.home)
    server = FastMCP(
        "memory",
        instructions=(
            "Quest-aware DeepScientist memory namespace. "
            "Use list_recent to recover context at turn start or resume, "
            "search before repeating literature/debug work, "
            "read only the few selected cards that matter now, "
            "write durable findings instead of chat transcripts, "
            "and promote_to_global only for stable cross-quest lessons. "
            "Prefer quest-local scope when quest context exists."
        ),
        log_level="ERROR",
    )

    @server.tool(
        name="write",
        description=(
            "Write a Markdown memory card with YAML frontmatter. "
            "Use after a non-trivial paper finding, reusable lesson, failure pattern, or idea rationale that should survive beyond chat."
        ),
    )
    def write(
        kind: str,
        title: str,
        body: str = "",
        markdown: str | None = None,
        scope: str = "quest",
        tags: list[str] | str | None = None,
        metadata: dict[str, Any] | None = None,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_scope = _resolve_scope(context, scope)
        quest_root = context.require_quest_root() if resolved_scope == "quest" else None
        return service.write_card(
            scope=resolved_scope,
            kind=kind,
            title=title,
            body=body,
            markdown=markdown,
            quest_root=quest_root,
            quest_id=context.quest_id,
            tags=tags,
            metadata=metadata,
        )

    @server.tool(
        name="read",
        description=(
            "Read a memory card by id or path. "
            "Use after list_recent or search surfaced a specific card worth reusing now."
        ),
    )
    def read(
        card_id: str | None = None,
        path: str | None = None,
        scope: str = "quest",
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_scope = _resolve_scope(context, scope)
        quest_root = context.require_quest_root() if resolved_scope == "quest" else None
        return service.read_card(card_id=card_id, path=path, scope=resolved_scope, quest_root=quest_root)

    @server.tool(
        name="search",
        description=(
            "Search memory cards by metadata or body text. "
            "Use before broad literature search, retries, route decisions, or repeated debugging."
        ),
    )
    def search(
        query: str,
        scope: str = "quest",
        limit: int = 10,
        kind: str | None = None,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_scope = _resolve_search_scope(context, scope)
        quest_root = context.quest_root if resolved_scope in {"quest", "both"} else None
        items = service.search(query, scope=resolved_scope, quest_root=quest_root, limit=limit, kind=kind)
        return {"ok": True, "count": len(items), "items": items}

    @server.tool(
        name="list_recent",
        description=(
            "List the most recently updated memory cards. "
            "Use to recover quest context at turn start, after resume, or after a long pause."
        ),
    )
    def list_recent(
        scope: str = "quest",
        limit: int = 10,
        kind: str | None = None,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_scope = _resolve_search_scope(context, scope)
        if resolved_scope == "both":
            quest_items = service.list_recent(scope="quest", quest_root=context.require_quest_root(), limit=limit, kind=kind)
            global_items = service.list_recent(scope="global", limit=limit, kind=kind)
            items = (quest_items + global_items)[-limit:]
        else:
            quest_root = context.quest_root if resolved_scope == "quest" else None
            items = service.list_recent(scope=resolved_scope, quest_root=quest_root, limit=limit, kind=kind)
        return {"ok": True, "count": len(items), "items": items}

    @server.tool(
        name="promote_to_global",
        description=(
            "Promote a quest memory card into global memory. "
            "Use only for stable, cross-quest reusable lessons."
        ),
    )
    def promote_to_global(
        card_id: str | None = None,
        path: str | None = None,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.promote_to_global(card_id=card_id, path=path, quest_root=context.require_quest_root())

    return server


def build_artifact_server(context: McpContext) -> FastMCP:
    service = ArtifactService(context.home)
    server = FastMCP(
        "artifact",
        instructions=(
            "Quest-aware DeepScientist artifact namespace. "
            "Use artifact as the quest control plane for ideas, branches, worktrees, decisions, progress, run records, reports, approvals, and user interaction state. "
            "Git behavior is exposed through artifact only."
        ),
        log_level="ERROR",
    )

    @server.tool(name="record", description="Write a structured artifact record under the current quest.")
    def record(payload: dict[str, Any], comment: str | dict[str, Any] | None = None) -> dict[str, Any]:
        enriched = dict(payload)
        if comment is not None and "comment" not in enriched:
            enriched["comment"] = comment
        if context.run_id and "run_id" not in enriched:
            enriched["run_id"] = context.run_id
        if context.active_anchor and "anchor" not in enriched:
            enriched["anchor"] = context.active_anchor
        if context.agent_role:
            source = dict(enriched.get("source") or {})
            source.setdefault("kind", "agent")
            source.setdefault("role", context.agent_role)
            if context.run_id:
                source.setdefault("run_id", context.run_id)
            enriched["source"] = source
        return service.record(
            context.require_quest_root(),
            enriched,
            workspace_root=context.worktree_root,
        )

    @server.tool(name="checkpoint", description="Create a Git checkpoint in the current quest repository.")
    def checkpoint(
        message: str,
        allow_empty: bool = False,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.checkpoint(
            context.worktree_root or context.require_quest_root(),
            message,
            allow_empty=allow_empty,
        )

    @server.tool(name="prepare_branch", description="Prepare an idea or run branch and optional worktree.")
    def prepare_branch(
        run_id: str | None = None,
        idea_id: str | None = None,
        branch: str | None = None,
        branch_kind: str = "run",
        create_worktree_flag: bool = True,
        start_point: str | None = None,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.prepare_branch(
            context.require_quest_root(),
            run_id=run_id or context.run_id,
            idea_id=idea_id,
            branch=branch,
            branch_kind=branch_kind,
            create_worktree_flag=create_worktree_flag,
            start_point=start_point,
        )

    @server.tool(
        name="activate_branch",
        description=(
            "Activate one existing durable research branch as the current workspace without creating a new lineage node. "
            "Use this when you need to revisit an older idea/main-result branch for more experiments or a fresh decision."
        ),
    )
    def activate_branch(
        branch: str | None = None,
        idea_id: str | None = None,
        run_id: str | None = None,
        anchor: str | None = "auto",
        promote_to_head: bool = False,
        create_worktree_if_missing: bool = True,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.activate_branch(
            context.require_quest_root(),
            branch=branch,
            idea_id=idea_id,
            run_id=run_id,
            anchor=anchor,
            promote_to_head=promote_to_head,
            create_worktree_if_missing=create_worktree_if_missing,
        )

    @server.tool(
        name="submit_idea",
        description=(
            "Create or revise the active research idea. "
            "Normal research flow should use mode=create together with lineage_intent=continue_line or branch_alternative, so each durable idea submission becomes a new branch/worktree and a new user-visible research node. "
            "mode=revise is maintenance-only for refining the current active idea.md in place. "
            "When foundation_ref is omitted, lineage_intent infers the parent and default foundation from the active research line."
        ),
    )
    def submit_idea(
        mode: str = "create",
        idea_id: str | None = None,
        lineage_intent: str | None = None,
        title: str = "",
        problem: str = "",
        hypothesis: str = "",
        mechanism: str = "",
        expected_gain: str = "",
        evidence_paths: list[str] | None = None,
        risks: list[str] | None = None,
        decision_reason: str = "",
        foundation_ref: dict[str, Any] | str | None = None,
        foundation_reason: str = "",
        next_target: str = "experiment",
        draft_markdown: str = "",
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.submit_idea(
            context.require_quest_root(),
            mode=mode,
            idea_id=idea_id,
            lineage_intent=lineage_intent,
            title=title,
            problem=problem,
            hypothesis=hypothesis,
            mechanism=mechanism,
            expected_gain=expected_gain,
            evidence_paths=evidence_paths,
            risks=risks,
            decision_reason=decision_reason,
            foundation_ref=foundation_ref,
            foundation_reason=foundation_reason,
            next_target=next_target,
            draft_markdown=draft_markdown,
        )

    @server.tool(
        name="list_research_branches",
        description=(
            "List research branches with branch number, active idea, foundation info, and corresponding main-experiment results. "
            "Use before creating the next idea when you need to compare possible foundations."
        ),
    )
    def list_research_branches(comment: str | dict[str, Any] | None = None) -> dict[str, Any]:
        return service.list_research_branches(context.require_quest_root())

    @server.tool(
        name="resolve_runtime_refs",
        description=(
            "Resolve the current canonical research ids and refs. "
            "Use this before supplementary work when you need the active idea, latest main run, active campaign, outline, or reply-thread ids without guessing."
        ),
    )
    def resolve_runtime_refs(comment: str | dict[str, Any] | None = None) -> dict[str, Any]:
        return service.resolve_runtime_refs(context.require_quest_root())

    @server.tool(
        name="get_analysis_campaign",
        description=(
            "Get one analysis campaign manifest with todo items, slice status, and next pending slice. "
            "Pass campaign_id='active' or omit it to recover the active campaign."
        ),
    )
    def get_analysis_campaign(
        campaign_id: str | None = "active",
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.get_analysis_campaign(context.require_quest_root(), campaign_id=campaign_id)

    @server.tool(
        name="record_main_experiment",
        description=(
            "Record the completed main experiment on the active idea workspace. "
            "This writes RUN.md and RESULT.json, compares metrics to the attached baseline, "
            "derives breakthrough status, and notifies bound conversations."
        ),
    )
    def record_main_experiment(
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
        evaluation_summary: dict[str, Any] | None = None,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return service.record_main_experiment(
                context.require_quest_root(),
                run_id=run_id,
                title=title,
                hypothesis=hypothesis,
                setup=setup,
                execution=execution,
                results=results,
                conclusion=conclusion,
                metric_rows=metric_rows,
                metrics_summary=metrics_summary,
                metric_contract=metric_contract,
                evidence_paths=evidence_paths,
                changed_files=changed_files,
                config_paths=config_paths,
                notes=notes,
                dataset_scope=dataset_scope,
                verdict=verdict,
                status=status,
                baseline_id=baseline_id,
                baseline_variant_id=baseline_variant_id,
                evaluation_summary=evaluation_summary,
                strict_metric_contract=True,
            )
        except MetricContractValidationError as exc:
            return _metric_validation_error_payload(exc)

    @server.tool(
        name="create_analysis_campaign",
        description=(
            "Create a structured analysis campaign from the current workspace/result node. "
            "Use this for one or more extra experiments; each slice receives its own child branch/worktree and explicit requirements."
        ),
    )
    def create_analysis_campaign(
        campaign_title: str,
        campaign_goal: str,
        slices: list[dict[str, Any]],
        parent_run_id: str | None = None,
        campaign_origin: dict[str, Any] | None = None,
        selected_outline_ref: str | None = None,
        research_questions: list[str] | None = None,
        experimental_designs: list[str] | None = None,
        todo_items: list[dict[str, Any]] | None = None,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.create_analysis_campaign(
            context.require_quest_root(),
            campaign_title=campaign_title,
            campaign_goal=campaign_goal,
            parent_run_id=parent_run_id,
            slices=slices,
            campaign_origin=campaign_origin,
            selected_outline_ref=selected_outline_ref,
            research_questions=research_questions,
            experimental_designs=experimental_designs,
            todo_items=todo_items,
        )

    @server.tool(
        name="submit_paper_outline",
        description=(
            "Persist a paper outline candidate, select an approved outline, or revise the selected outline. "
            "Use this before analysis campaigns that should support final writing claims."
        ),
    )
    def submit_paper_outline(
        mode: str = "candidate",
        outline_id: str | None = None,
        title: str = "",
        note: str = "",
        story: str = "",
        ten_questions: list[str] | None = None,
        detailed_outline: dict[str, Any] | None = None,
        review_result: str | None = None,
        selected_reason: str | None = None,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.submit_paper_outline(
            context.require_quest_root(),
            mode=mode,
            outline_id=outline_id,
            title=title,
            note=note,
            story=story,
            ten_questions=ten_questions,
            detailed_outline=detailed_outline,
            review_result=review_result,
            selected_reason=selected_reason,
        )

    @server.tool(
        name="list_paper_outlines",
        description=(
            "List candidate/revised paper outlines and the selected outline reference. "
            "Use this before writing-facing analysis campaigns or when you need a valid outline_id."
        ),
    )
    def list_paper_outlines(comment: str | dict[str, Any] | None = None) -> dict[str, Any]:
        return service.list_paper_outlines(context.require_quest_root())

    @server.tool(
        name="submit_paper_bundle",
        description=(
            "Persist the final paper bundle manifest, including outline, draft, LaTeX/PDF outputs, and build reports."
        ),
    )
    def submit_paper_bundle(
        title: str | None = None,
        summary: str = "",
        outline_path: str | None = None,
        draft_path: str | None = None,
        writing_plan_path: str | None = None,
        references_path: str | None = None,
        claim_evidence_map_path: str | None = None,
        compile_report_path: str | None = None,
        pdf_path: str | None = None,
        latex_root_path: str | None = None,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.submit_paper_bundle(
            context.require_quest_root(),
            title=title,
            summary=summary,
            outline_path=outline_path,
            draft_path=draft_path,
            writing_plan_path=writing_plan_path,
            references_path=references_path,
            claim_evidence_map_path=claim_evidence_map_path,
            compile_report_path=compile_report_path,
            pdf_path=pdf_path,
            latex_root_path=latex_root_path,
        )

    @server.tool(
        name="record_analysis_slice",
        description=(
            "Record the full setup, execution, and result for one analysis slice. "
            "This also mirrors the result back to the parent experiment branch and moves to the next slice automatically."
        ),
    )
    def record_analysis_slice(
        campaign_id: str,
        slice_id: str,
        status: str = "completed",
        setup: str = "",
        execution: str = "",
        results: str = "",
        evidence_paths: list[str] | None = None,
        metric_rows: list[dict[str, Any]] | None = None,
        deviations: list[str] | None = None,
        claim_impact: str | None = None,
        reviewer_resolution: str | None = None,
        manuscript_update_hint: str | None = None,
        next_recommendation: str | None = None,
        dataset_scope: str = "full",
        subset_approval_ref: str | None = None,
        comparison_baselines: list[dict[str, Any]] | None = None,
        evaluation_summary: dict[str, Any] | None = None,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.record_analysis_slice(
            context.require_quest_root(),
            campaign_id=campaign_id,
            slice_id=slice_id,
            status=status,
            setup=setup,
            execution=execution,
            results=results,
            evidence_paths=evidence_paths,
            metric_rows=metric_rows,
            deviations=deviations,
            claim_impact=claim_impact,
            reviewer_resolution=reviewer_resolution,
            manuscript_update_hint=manuscript_update_hint,
            next_recommendation=next_recommendation,
            dataset_scope=dataset_scope,
            subset_approval_ref=subset_approval_ref,
            comparison_baselines=comparison_baselines,
            evaluation_summary=evaluation_summary,
        )

    @server.tool(name="publish_baseline", description="Publish a quest baseline to the global baseline registry.")
    def publish_baseline(
        payload: dict[str, Any],
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        enriched = dict(payload)
        if comment is not None and "comment" not in enriched:
            enriched["comment"] = comment
        enriched.setdefault("source", {"kind": "artifact_publish", "quest_id": context.quest_id, "quest_root": str(context.require_quest_root())})
        return service.publish_baseline(context.require_quest_root(), enriched)

    @server.tool(name="attach_baseline", description="Attach a published baseline to the current quest.")
    def attach_baseline(
        baseline_id: str,
        variant_id: str | None = None,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.attach_baseline(context.require_quest_root(), baseline_id, variant_id)

    @server.tool(
        name="confirm_baseline",
        description=(
            "Confirm the active quest baseline and open the stage gate into idea work. "
            "The baseline path must point at a quest-local baseline under baselines/local or baselines/imported."
        ),
    )
    def confirm_baseline(
        baseline_path: str,
        baseline_id: str | None = None,
        variant_id: str | None = None,
        summary: str | None = None,
        baseline_kind: str | None = None,
        metric_contract: dict[str, Any] | None = None,
        metric_directions: dict[str, str] | None = None,
        metrics_summary: dict[str, Any] | None = None,
        primary_metric: dict[str, Any] | None = None,
        auto_advance: bool = True,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return service.confirm_baseline(
                context.require_quest_root(),
                baseline_path=baseline_path,
                comment=comment,
                baseline_id=baseline_id,
                variant_id=variant_id,
                summary=summary,
                baseline_kind=baseline_kind,
                metric_contract=metric_contract,
                metric_directions=metric_directions,
                metrics_summary=metrics_summary,
                primary_metric=primary_metric,
                auto_advance=auto_advance,
                strict_metric_contract=True,
            )
        except MetricContractValidationError as exc:
            return _metric_validation_error_payload(exc)

    @server.tool(
        name="waive_baseline",
        description="Explicitly waive the baseline gate and advance with a durable written reason.",
    )
    def waive_baseline(
        reason: str,
        auto_advance: bool = True,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.waive_baseline(
            context.require_quest_root(),
            reason=reason,
            comment=comment,
            auto_advance=auto_advance,
        )

    @server.tool(
        name="arxiv",
        description=(
            "Interact with the quest-local arXiv library. "
            "Use mode='read' to read one paper by id with local-first automatic persistence, "
            "or mode='list' to list the saved arXiv items for the current quest."
        ),
    )
    def arxiv(
        paper_id: str | None = None,
        mode: str = "read",
        full_text: bool = False,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.arxiv(
            paper_id,
            mode=mode,
            full_text=full_text,
            quest_root=context.require_quest_root(),
        )

    @server.tool(name="refresh_summary", description="Refresh SUMMARY.md from recent artifact state.")
    def refresh_summary(
        reason: str | None = None,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.refresh_summary(context.require_quest_root(), reason=reason)

    @server.tool(name="render_git_graph", description="Render the quest Git graph to JSON, SVG, and PNG.")
    def render_git_graph(comment: str | dict[str, Any] | None = None) -> dict[str, Any]:
        return service.render_git_graph(context.require_quest_root())

    @server.tool(name="interact", description="Send a structured user-facing update and optionally fetch new inbound messages.")
    def interact(
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
        surface_actions: list[dict[str, Any]] | None = None,
        connector_hints: dict[str, Any] | None = None,
        allow_free_text: bool = True,
        reply_schema: dict[str, Any] | None = None,
        reply_to_interaction_id: str | None = None,
        supersede_open_requests: bool = True,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.interact(
            context.require_quest_root(),
            kind=kind,
            message=message,
            response_phase=response_phase,
            importance=importance,
            deliver_to_bound_conversations=deliver_to_bound_conversations,
            include_recent_inbound_messages=include_recent_inbound_messages,
            recent_message_limit=recent_message_limit,
            attachments=attachments,
            interaction_id=interaction_id,
            expects_reply=expects_reply,
            reply_mode=reply_mode,
            options=options,
            surface_actions=surface_actions,
            connector_hints=connector_hints,
            allow_free_text=allow_free_text,
            reply_schema=reply_schema,
            reply_to_interaction_id=reply_to_interaction_id,
            supersede_open_requests=supersede_open_requests,
        )

    @server.tool(
        name="complete_quest",
        description=(
            "Mark the quest as completed after the user explicitly approved completion via a blocking "
            "artifact.interact(...) request whose reply_schema.decision_type is `quest_completion_approval`."
        ),
    )
    def complete_quest(
        summary: str = "",
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.complete_quest(
            context.require_quest_root(),
            summary=summary,
        )

    return server


def build_bash_exec_server(context: McpContext) -> FastMCP:
    service = BashExecService(context.home)
    server = FastMCP(
        "bash_exec",
        instructions=(
            "Quest-aware DeepScientist bash execution namespace with detached execution, durable logs, and progress tracking. "
            "Use bash_exec when commands should be monitored, revisited from logs, stopped later, or resumed after interruption."
        ),
        log_level="ERROR",
    )

    @server.tool(
        name="bash_exec",
        description=(
            "Execute a bash command inside the current quest. "
            "mode=detach returns immediately. mode=await/create waits for completion. "
            "mode=read returns the saved log. It returns the full saved log up to 2000 lines, "
            "or a 500-line head plus 1500-line tail preview for longer logs. "
            "Use start/tail for rendered line windows and tail_limit/after_seq for seq-based monitoring. "
            "mode=kill requests termination. "
            "mode=list shows known quest-local bash sessions. mode=history shows a compact reverse-chronological bash id list."
        ),
    )
    def bash_exec(
        command: str = "",
        mode: str = "detach",
        id: str | None = None,
        reason: str | None = None,
        workdir: str | None = None,
        env: dict[str, Any] | None = None,
        export_log: bool = False,
        export_log_to: str | None = None,
        timeout_seconds: int | None = None,
        status: str | None = None,
        kind: str | None = None,
        agent_ids: list[str] | None = None,
        agent_instance_ids: list[str] | None = None,
        chat_session_id: str | None = None,
        limit: int = 20,
        start: int | None = None,
        tail: int | None = None,
        tail_limit: int | None = None,
        before_seq: int | None = None,
        after_seq: int | None = None,
        order: str = "asc",
        include_log: bool = False,
        wait: bool = False,
        force: bool = False,
        comment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        quest_root = context.require_quest_root().resolve()
        normalized_mode = (mode or "detach").strip().lower()
        if normalized_mode == "create":
            normalized_mode = "await"
        if normalized_mode not in {"detach", "await", "read", "kill", "list", "history"}:
            raise ValueError("Mode must be one of `detach`, `await`, `create`, `read`, `kill`, `list`, or `history`.")
        if normalized_mode in {"list", "history"}:
            resolved_limit = 500 if normalized_mode == "history" and limit == 20 else max(1, min(limit, 500))
            items = service.list_sessions(
                quest_root,
                status=status,
                kind=kind,
                agent_ids=agent_ids,
                agent_instance_ids=agent_instance_ids,
                chat_session_id=chat_session_id,
                limit=resolved_limit,
            )
            history_lines = [service.format_history_line(item) for item in items]
            counts: dict[str, int] = {}
            for item in items:
                item_status = str(item.get("status") or "unknown")
                counts[item_status] = counts.get(item_status, 0) + 1
            payload = {
                "count": len(items),
                "items": items,
                "status_counts": counts,
                "summary": service.summary(quest_root),
                "history_lines": history_lines,
            }
            if normalized_mode == "history":
                return {
                    "count": len(items),
                    "lines": history_lines,
                    "items": items,
                }
            return payload
        if normalized_mode == "read":
            bash_id = service.resolve_session_id(quest_root, id)
            session = service.get_session(quest_root, bash_id)
            normalized_order = (order or "asc").strip().lower()
            if normalized_order not in {"asc", "desc"}:
                normalized_order = "asc"
            if tail is not None and tail_limit is not None:
                raise ValueError("Use either `tail` or `tail_limit`, not both.")
            use_line_window = start is not None or tail is not None or (start is not None and tail_limit is not None)
            if use_line_window and (before_seq is not None or after_seq is not None):
                raise ValueError("`start`/`tail` cannot be combined with `before_seq` or `after_seq`.")
            if use_line_window and normalized_order != "asc":
                raise ValueError("`start`/`tail` windows only support `order='asc'`.")
            if use_line_window:
                payload = service.build_tool_result(
                    context,
                    session=session,
                    include_log=False,
                    export_log=export_log,
                    export_log_to=export_log_to,
                )
                payload.update(
                    _build_bash_log_window_from_path(
                        service.terminal_log_path(quest_root, bash_id),
                        start=start,
                        tail=tail if tail is not None else tail_limit,
                    )
                )
                return payload
            use_tail = tail_limit is not None or before_seq is not None or after_seq is not None or normalized_order != "asc"
            if use_tail:
                resolved_tail_limit = max(1, min(int(tail_limit or 200), 1000))
                entries, tail_meta = service.read_log_entries(
                    quest_root,
                    bash_id,
                    limit=resolved_tail_limit,
                    before_seq=before_seq,
                    after_seq=after_seq,
                    order=normalized_order,
                    prefer_visible=True,
                )
                payload = service.build_tool_result(
                    context,
                    session=session,
                    include_log=include_log,
                    export_log=export_log,
                    export_log_to=export_log_to,
                )
                payload["tail"] = entries
                payload["tail_limit"] = tail_meta.get("tail_limit")
                payload["tail_start_seq"] = tail_meta.get("tail_start_seq")
                payload["latest_seq"] = tail_meta.get("latest_seq")
                payload["after_seq"] = tail_meta.get("after_seq")
                payload["before_seq"] = tail_meta.get("before_seq")
                payload["order"] = normalized_order
                return payload
            payload = service.build_tool_result(
                context,
                session=session,
                include_log=False,
                export_log=export_log,
                export_log_to=export_log_to,
            )
            payload.update(_build_default_bash_log_payload_from_path(service.terminal_log_path(quest_root, bash_id)))
            return payload
        if normalized_mode == "kill":
            bash_id = service.resolve_session_id(quest_root, id)
            session = service.request_stop(
                quest_root,
                bash_id,
                reason=reason,
                user_id=f"agent:{context.agent_role or 'pi'}",
                force=force,
            )
            if wait:
                session = service.wait_for_session(quest_root, bash_id, timeout_seconds=timeout_seconds)
            return service.build_tool_result(context, session=session, include_log=False)
        if normalized_mode == "await" and not command:
            bash_id = service.resolve_session_id(quest_root, id)
            session = service.wait_for_session(quest_root, bash_id, timeout_seconds=timeout_seconds)
            return service.build_tool_result(
                context,
                session=session,
                include_log=False,
                export_log=export_log,
                export_log_to=export_log_to,
            )
        if not (command or "").strip():
            raise ValueError("command is required for `detach` and `await`.")
        session = service.start_session(
            context,
            command=command,
            mode=normalized_mode,
            workdir=workdir,
            env=env,
            timeout_seconds=timeout_seconds,
            comment=comment,
        )
        if normalized_mode == "detach":
            return service.build_tool_result(context, session=session, include_log=False)
        session = service.wait_for_session(quest_root, str(session["bash_id"]), timeout_seconds=timeout_seconds)
        return service.build_tool_result(
            context,
            session=session,
            include_log=False,
            export_log=export_log,
            export_log_to=export_log_to,
        )

    return server


def _resolve_scope(context: McpContext, scope: str) -> str:
    normalized = (scope or "quest").strip().lower()
    if normalized == "quest" and context.quest_root is None:
        raise ValueError("Quest-local memory call requires quest context.")
    if normalized not in {"quest", "global"}:
        raise ValueError("Scope must be `quest` or `global`.")
    return normalized


def _resolve_search_scope(context: McpContext, scope: str) -> str:
    normalized = (scope or "quest").strip().lower()
    if normalized in {"quest", "both"} and context.quest_root is None:
        return "global"
    if normalized not in {"quest", "global", "both"}:
        raise ValueError("Scope must be `quest`, `global`, or `both`.")
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="DeepScientist built-in MCP server")
    parser.add_argument("--namespace", choices=("memory", "artifact", "bash_exec"), required=True)
    args = parser.parse_args()
    context = McpContext.from_env()
    if args.namespace == "memory":
        build_memory_server(context).run("stdio")
    elif args.namespace == "artifact":
        build_artifact_server(context).run("stdio")
    else:
        build_bash_exec_server(context).run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
