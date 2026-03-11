from __future__ import annotations

import re
from pathlib import Path

from ..config import ConfigManager
from ..memory import MemoryService
from ..memory.frontmatter import load_markdown_document
from ..quest import QuestService
from ..registries import BaselineRegistry
from ..shared import read_text

STANDARD_SKILLS = (
    "scout",
    "baseline",
    "idea",
    "experiment",
    "analysis-campaign",
    "write",
    "finalize",
    "decision",
)

STAGE_MEMORY_PLAN = {
    "scout": {
        "quest": ("papers", "knowledge", "decisions"),
        "global": ("papers", "knowledge", "templates"),
    },
    "baseline": {
        "quest": ("papers", "decisions", "episodes", "knowledge"),
        "global": ("knowledge", "templates", "papers"),
    },
    "idea": {
        "quest": ("papers", "ideas", "decisions", "knowledge"),
        "global": ("papers", "knowledge", "templates"),
    },
    "experiment": {
        "quest": ("ideas", "decisions", "episodes", "knowledge"),
        "global": ("knowledge", "templates"),
    },
    "analysis-campaign": {
        "quest": ("ideas", "decisions", "episodes", "knowledge", "papers"),
        "global": ("knowledge", "templates", "papers"),
    },
    "write": {
        "quest": ("papers", "decisions", "knowledge", "ideas"),
        "global": ("templates", "knowledge", "papers"),
    },
    "finalize": {
        "quest": ("decisions", "knowledge", "episodes"),
        "global": ("knowledge", "templates"),
    },
    "decision": {
        "quest": ("decisions", "knowledge", "episodes", "ideas"),
        "global": ("knowledge", "templates"),
    },
}


class PromptBuilder:
    def __init__(self, repo_root: Path, home: Path) -> None:
        self.repo_root = repo_root
        self.home = home
        self.quest_service = QuestService(home)
        self.memory_service = MemoryService(home)
        self.baseline_registry = BaselineRegistry(home)
        self.config_manager = ConfigManager(home)

    def build(
        self,
        *,
        quest_id: str,
        skill_id: str,
        user_message: str,
        model: str,
    ) -> str:
        snapshot = self.quest_service.snapshot(quest_id)
        runtime_config = self.config_manager.load_named("config")
        quest_root = Path(snapshot["quest_root"])
        active_anchor = str(snapshot.get("active_anchor") or skill_id)
        default_locale = str(runtime_config.get("default_locale") or "zh-CN")
        system_block = self._prompt_fragment("src/prompts/system.md")
        return "\n\n".join(
            [
                system_block,
                "",
                "## Runtime Context",
                f"ds_home: {self.home.resolve()}",
                f"quest_id: {quest_id}",
                f"quest_root: {quest_root}",
                f"research_head_branch: {snapshot.get('research_head_branch') or 'none'}",
                f"research_head_worktree_root: {snapshot.get('research_head_worktree_root') or 'none'}",
                f"current_workspace_branch: {snapshot.get('current_workspace_branch') or 'none'}",
                f"current_workspace_root: {snapshot.get('current_workspace_root') or 'none'}",
                f"active_idea_id: {snapshot.get('active_idea_id') or 'none'}",
                f"active_analysis_campaign_id: {snapshot.get('active_analysis_campaign_id') or 'none'}",
                f"active_anchor: {active_anchor}",
                f"active_branch: {snapshot.get('branch')}",
                f"requested_skill: {skill_id}",
                f"runner_name: codex",
                f"model: {model}",
                f"conversation_id: quest:{quest_id}",
                f"default_locale: {default_locale}",
                "built_in_mcp_namespaces: memory, artifact, bash_exec",
                "mcp_namespace_note: use bash_exec for durable shell execution; do not confuse these quest MCP servers with transient shell snippets.",
                "",
                "Canonical stage skills root:",
                str((self.repo_root / "src" / "skills").resolve()),
                "",
                "Standard stage skill paths:",
                self._skill_paths_block(),
                "",
                "## Quest Context",
                self._quest_context_block(quest_root),
                "",
                "## Recent Durable State",
                self._durable_state_block(snapshot, quest_root),
                "",
                "## Interaction Style",
                self._interaction_style_block(default_locale=default_locale, user_message=user_message, snapshot=snapshot),
                "",
                "## Priority Memory For This Turn",
                self._priority_memory_block(
                    quest_root,
                    skill_id=skill_id,
                    active_anchor=active_anchor,
                    user_message=user_message,
                ),
                "",
                "## Recent Conversation Window",
                self._conversation_block(quest_id),
                "",
                "## Current User Message",
                user_message.strip(),
            ]
        ).strip() + "\n"

    def _prompt_fragment(self, relative_path: str) -> str:
        path = self.repo_root / relative_path
        return self._markdown_body(path)

    def _skill_paths_block(self) -> str:
        lines = []
        for skill_id in STANDARD_SKILLS:
            primary = (self.repo_root / "src" / "skills" / skill_id / "SKILL.md").resolve()
            lines.append(f"- {skill_id}: primary={primary}")
        return "\n".join(lines)

    def _interaction_style_block(self, *, default_locale: str, user_message: str, snapshot: dict) -> str:
        normalized_locale = str(default_locale or "").lower()
        chinese_turn = normalized_locale.startswith("zh") or bool(re.search(r"[\u4e00-\u9fff]", user_message))
        bound_conversations = snapshot.get("bound_conversations") or []
        lines = [
            f"- configured_default_locale: {default_locale}",
            f"- current_turn_language_bias: {'zh' if chinese_turn else 'en'}",
            f"- bound_conversation_count: {len(bound_conversations)}",
            "- collaboration_mode: long-horizon, continuity-first, artifact-aware",
            "- response_pattern: acknowledge current state -> state the next action -> mention the artifact/file/checkpoint that will change",
            "- interaction_protocol: first message may be plain conversation; after that, treat artifact.interact threads as the main continuity spine across TUI, web, and connectors",
            "- progress_protocol: emit artifact.interact(kind='progress', reply_mode='threaded', ...) roughly every 5-15 tool calls or at each real checkpoint (prefer fewer, higher-signal updates over spam)",
            "- blocking_protocol: use reply_mode='blocking' only for true unresolved user decisions; ordinary progress updates should stay threaded and non-blocking",
            "- respect_protocol: write user-facing updates as respectful, human, supervisor-style reports; templates are references only and must be adapted to context (do not copy/paste the same template repeatedly)",
            "- non_research_mode_protocol: if the user message looks like a non-research request, ask for a second confirmation before engaging stage skills or research workflow; after completion, leave one blocking standby interaction instead of repeatedly pinging",
            "- workspace_discipline: read and modify code inside current_workspace_root; treat quest_root as the canonical repo identity and durable runtime root",
            "- binary_safety: do not open or rewrite large binary assets unless truly necessary; prefer summaries, metadata, and targeted inspection first",
        ]
        if chinese_turn:
            lines.extend(
                [
                    "- tone_hint: 使用自然、礼貌、专业、偏正式的中文；必要时可自然称呼用户为“老师”，但不要每句重复；避免机械模板腔。",
                    "- connector_reply_hint: 在聊天面里优先简明说明当前状态、下一步动作、预计回传内容。",
                ]
            )
        else:
            lines.extend(
                [
                    "- tone_hint: use a polite, professional, gentlemanly English tone.",
                    "- connector_reply_hint: keep chat replies concise but operational, with explicit next steps and evidence targets.",
                ]
            )
        return "\n".join(lines)

    def _quest_context_block(self, quest_root: Path) -> str:
        parts = []
        for title, filename in (
            ("Brief", "brief.md"),
            ("Plan", "plan.md"),
            ("Status", "status.md"),
            ("Summary", "SUMMARY.md"),
        ):
            text = read_text(quest_root / filename).strip() or "(empty)"
            parts.extend([f"{title} ({filename}):", text, ""])
        return "\n".join(parts).strip()

    def _durable_state_block(self, snapshot: dict, quest_root: Path) -> str:
        lines = [
            f"- active_baseline_id: {snapshot.get('active_baseline_id') or 'none'}",
            f"- active_baseline_variant_id: {snapshot.get('active_baseline_variant_id') or 'none'}",
            f"- active_run_id: {snapshot.get('active_run_id') or 'none'}",
            f"- research_head_branch: {snapshot.get('research_head_branch') or 'none'}",
            f"- research_head_worktree_root: {snapshot.get('research_head_worktree_root') or 'none'}",
            f"- current_workspace_branch: {snapshot.get('current_workspace_branch') or 'none'}",
            f"- current_workspace_root: {snapshot.get('current_workspace_root') or 'none'}",
            f"- active_idea_id: {snapshot.get('active_idea_id') or 'none'}",
            f"- active_idea_md_path: {snapshot.get('active_idea_md_path') or 'none'}",
            f"- active_analysis_campaign_id: {snapshot.get('active_analysis_campaign_id') or 'none'}",
            f"- next_pending_slice_id: {snapshot.get('next_pending_slice_id') or 'none'}",
            f"- workspace_mode: {snapshot.get('workspace_mode') or 'quest'}",
            f"- runtime_status: {snapshot.get('runtime_status') or snapshot.get('status') or 'unknown'}",
            f"- stop_reason: {snapshot.get('stop_reason') or 'none'}",
            f"- pending_decisions: {', '.join(snapshot.get('pending_decisions') or []) or 'none'}",
            f"- pending_user_message_count: {snapshot.get('pending_user_message_count') or 0}",
            f"- active_interaction_count: {len(snapshot.get('active_interactions') or [])}",
            f"- waiting_interaction_id: {snapshot.get('waiting_interaction_id') or 'none'}",
            f"- latest_thread_interaction_id: {snapshot.get('latest_thread_interaction_id') or 'none'}",
            f"- default_reply_interaction_id: {snapshot.get('default_reply_interaction_id') or 'none'}",
            f"- last_artifact_interact_at: {snapshot.get('last_artifact_interact_at') or 'none'}",
            f"- last_delivered_batch_id: {snapshot.get('last_delivered_batch_id') or 'none'}",
            f"- bound_conversations: {', '.join(snapshot.get('bound_conversations') or []) or 'none'}",
            f"- cloud_linked: {snapshot.get('cloud', {}).get('linked', False)}",
        ]
        active_workspace_root = Path(str(snapshot.get("current_workspace_root") or quest_root))
        attachment_root = active_workspace_root / "baselines" / "imported"
        if attachment_root.exists():
            attachments = [read_yaml(path, {}) for path in sorted(attachment_root.glob("*/attachment.yaml"))]
            attachments = [item for item in attachments if isinstance(item, dict) and item]
            if attachments:
                attachment = max(
                    attachments,
                    key=lambda item: (
                        str(item.get("attached_at") or ""),
                        str(item.get("source_baseline_id") or ""),
                    ),
                )
                entry = attachment.get("entry") if isinstance(attachment.get("entry"), dict) else {}
                contract = entry.get("metric_contract") if isinstance(entry.get("metric_contract"), dict) else {}
                primary_metric_id = str(contract.get("primary_metric_id") or "").strip() or "none"
                metric_ids = [
                    str(item.get("metric_id") or "").strip()
                    for item in contract.get("metrics", [])
                    if isinstance(item, dict) and str(item.get("metric_id") or "").strip()
                ]
                lines.extend(
                    [
                        f"- active_baseline_primary_metric_id: {primary_metric_id}",
                        f"- active_baseline_metric_ids: {', '.join(metric_ids) or 'none'}",
                    ]
                )
        lines.extend(["", "Active interactions:"])
        active_interactions = snapshot.get("active_interactions") or []
        if active_interactions:
            for item in active_interactions[-3:]:
                interaction_id = item.get("interaction_id") or item.get("artifact_id") or "interaction"
                status = item.get("status") or "unknown"
                message = str(item.get("message") or "").strip().replace("\n", " ")
                if len(message) > 180:
                    message = message[:177].rstrip() + "..."
                lines.append(f"- {interaction_id} [{status}] {message or '(no message)'}")
        else:
            lines.append("- none")
        if int(snapshot.get("pending_user_message_count") or 0) > 0:
            lines.extend(
                [
                    "",
                    "Queued user-message notice:",
                    "- There are queued user messages waiting to be picked up via artifact.interact(include_recent_inbound_messages=True).",
                    "- Before continuing a resumed or follow-up turn, retrieve that mailbox payload first.",
                ]
            )

        lines.extend(
            [
                "",
                "Recent artifacts:",
            ]
        )
        recent_artifacts = snapshot.get("recent_artifacts") or []
        if recent_artifacts:
            for item in recent_artifacts[-5:]:
                payload = item.get("payload") or {}
                label = payload.get("artifact_id") or Path(item.get("path", "")).stem or "artifact"
                summary = payload.get("summary") or payload.get("reason") or "No summary provided."
                lines.append(f"- {item.get('kind')}: {label} -> {summary}")
        else:
            lines.append("- none")

        lines.extend(["", "Recent runs:"])
        recent_runs = snapshot.get("recent_runs") or []
        if recent_runs:
            for item in recent_runs[-5:]:
                run_id = item.get("run_id") or "unknown-run"
                summary = item.get("summary") or "No summary provided."
                lines.append(f"- {run_id}: {summary}")
        else:
            lines.append("- none")

        lines.extend(["", "Recent quest memory cards:"])
        quest_cards = self.memory_service.list_recent(scope="quest", quest_root=quest_root, limit=5)
        if quest_cards:
            for card in quest_cards:
                lines.append(f"- {card.get('type')}: {card.get('title')} ({card.get('path')})")
        else:
            lines.append("- none")

        lines.extend(["", "Recent global memory cards:"])
        global_cards = self.memory_service.list_recent(scope="global", limit=3)
        if global_cards:
            for card in global_cards:
                lines.append(f"- {card.get('type')}: {card.get('title')} ({card.get('path')})")
        else:
            lines.append("- none")

        lines.extend(["", "Published baselines:"])
        baseline_entries = self.baseline_registry.list_entries()[-5:]
        if baseline_entries:
            for entry in baseline_entries:
                baseline_id = entry.get("baseline_id") or entry.get("entry_id") or "unknown-baseline"
                summary = entry.get("summary") or entry.get("task") or "No summary provided."
                lines.append(f"- {baseline_id}: {summary}")
        else:
            lines.append("- none")
        return "\n".join(lines)

    def _priority_memory_block(
        self,
        quest_root: Path,
        *,
        skill_id: str,
        active_anchor: str,
        user_message: str,
    ) -> str:
        stage = active_anchor if active_anchor in STAGE_MEMORY_PLAN else skill_id
        plan = STAGE_MEMORY_PLAN.get(stage, STAGE_MEMORY_PLAN["decision"])
        selected: list[dict] = []
        seen_paths: set[str] = set()

        for scope in ("quest", "global"):
            for kind in plan.get(scope, ()):
                cards = self.memory_service.list_recent(
                    scope=scope,
                    quest_root=quest_root if scope == "quest" else None,
                    kind=kind,
                    limit=2,
                )
                if not cards:
                    continue
                self._append_priority_memory(
                    selected,
                    seen_paths,
                    card=cards[-1],
                    scope=scope,
                    quest_root=quest_root,
                    reason=f"recent {stage} {kind} memory",
                )
                if len(selected) >= 6:
                    return self._format_priority_memory(selected)

        for query in self._memory_queries(user_message):
            matches = self.memory_service.search(query, scope="both", quest_root=quest_root, limit=6)
            for card in matches:
                scope = str(card.get("scope") or "quest")
                self._append_priority_memory(
                    selected,
                    seen_paths,
                    card=card,
                    scope=scope,
                    quest_root=quest_root,
                    reason=f"matches current user message: `{query}`",
                )
                if len(selected) >= 8:
                    return self._format_priority_memory(selected)

        return self._format_priority_memory(selected)

    def _append_priority_memory(
        self,
        selected: list[dict],
        seen_paths: set[str],
        *,
        card: dict,
        scope: str,
        quest_root: Path,
        reason: str,
    ) -> None:
        path = str(card.get("path") or "")
        if not path or path in seen_paths:
            return
        full = self.memory_service.read_card(
            path=path,
            scope=scope,
            quest_root=quest_root if scope == "quest" else None,
        )
        excerpt = " ".join(str(full.get("body") or "").split())
        if len(excerpt) > 260:
            excerpt = excerpt[:257].rstrip() + "..."
        selected.append(
            {
                "scope": scope,
                "type": full.get("type") or card.get("type") or "memory",
                "title": full.get("title") or card.get("title") or Path(path).stem,
                "path": path,
                "reason": reason,
                "excerpt": excerpt or str(card.get("excerpt") or ""),
            }
        )
        seen_paths.add(path)

    @staticmethod
    def _format_priority_memory(selected: list[dict]) -> str:
        if not selected:
            return "- none"
        lines: list[str] = []
        for item in selected:
            lines.append(f"- [{item['scope']}|{item['type']}] {item['title']} ({item['path']})")
            lines.append(f"  reason: {item['reason']}")
            if item.get("excerpt"):
                lines.append(f"  excerpt: {item['excerpt']}")
        return "\n".join(lines)

    @staticmethod
    def _memory_queries(user_message: str) -> list[str]:
        tokens: list[str] = []
        seen: set[str] = set()
        for token in re.findall(r"[A-Za-z0-9_./:-]{4,}|[\u4e00-\u9fff]{2,}", user_message):
            cleaned = token.strip().lower()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            tokens.append(cleaned)
            if len(tokens) >= 6:
                break
        return tokens

    def _conversation_block(self, quest_id: str, limit: int = 12) -> str:
        records = self.quest_service.history(quest_id, limit=limit)
        if not records:
            return "- none"
        lines = []
        for item in records[-limit:]:
            role = str(item.get("role") or "unknown")
            source = str(item.get("source") or "unknown")
            content = str(item.get("content") or "").strip().replace("\n", " ")
            if len(content) > 400:
                content = content[:397].rstrip() + "..."
            reply_to = str(item.get("reply_to_interaction_id") or "").strip()
            suffix = f" -> reply_to:{reply_to}" if reply_to else ""
            lines.append(f"- [{role}|{source}]{suffix} {content}")
        return "\n".join(lines)

    def _markdown_body(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        if text.startswith("---\n"):
            _metadata, body = self._split_frontmatter(path)
            return body.strip()
        return text.strip()

    @staticmethod
    def _split_frontmatter(path: Path) -> tuple[dict, str]:
        metadata, body = load_markdown_document(path)
        return metadata, body
