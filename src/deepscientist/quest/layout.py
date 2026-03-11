from __future__ import annotations

from pathlib import Path

from ..shared import utc_now


QUEST_DIRECTORIES = (
    "artifacts/approvals",
    "artifacts/baselines",
    "artifacts/decisions",
    "artifacts/graphs",
    "artifacts/ideas",
    "artifacts/milestones",
    "artifacts/progress",
    "artifacts/reports",
    "artifacts/runs",
    "baselines/imported",
    "baselines/local",
    "experiments/analysis",
    "experiments/main",
    "handoffs",
    "literature",
    "tmp",
    "memory/decisions",
    "memory/episodes",
    "memory/ideas",
    "memory/knowledge",
    "memory/papers",
    "paper",
    ".codex/skills",
    ".claude/agents",
    ".ds/bash_exec",
    ".ds/conversations",
    ".ds/codex_history",
    ".ds/runs",
    ".ds/worktrees",
)


def initial_quest_yaml(quest_id: str, goal: str, quest_root: Path, runner: str, title: str | None = None) -> dict:
    timestamp = utc_now()
    return {
        "quest_id": quest_id,
        "title": title or goal,
        "quest_root": str(quest_root.resolve()),
        "status": "active",
        "active_anchor": "baseline",
        "default_runner": runner,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def initial_brief(goal: str) -> str:
    return "\n".join(
        [
            f"# Quest Brief",
            "",
            f"## Goal",
            "",
            goal,
            "",
            "## Initial Notes",
            "",
            "- Establish or attach a baseline.",
            "- Capture the first concrete decision with explicit reasons.",
            "",
        ]
    )


def initial_plan() -> str:
    return "\n".join(
        [
            "# Plan",
            "",
            "- [ ] Establish or attach a reusable baseline",
            "- [ ] Record at least one candidate idea",
            "- [ ] Write a decision artifact with explicit reason",
            "- [ ] Run the first experiment or decide to stop",
            "",
        ]
    )


def initial_status() -> str:
    return "# Status\n\nQuest created. Waiting for baseline setup or reuse.\n"


def initial_summary() -> str:
    return "# Summary\n\nNo completed milestones yet.\n"


def gitignore() -> str:
    return "\n".join(
        [
            ".ds/*.pid",
            ".ds/*.sock",
            ".ds/*.tmp",
            ".ds/worktrees/",
            "tmp/",
            "__pycache__/",
            ".pytest_cache/",
            "",
        ]
    )
