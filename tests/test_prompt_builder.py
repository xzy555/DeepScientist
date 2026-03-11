from __future__ import annotations

from pathlib import Path

import pytest

from deepscientist.artifact import ArtifactService
from deepscientist.config import ConfigManager
from deepscientist.home import ensure_home_layout, repo_root
from deepscientist.memory import MemoryService
from deepscientist.prompts import PromptBuilder
from deepscientist.quest import QuestService
from deepscientist.skills import SkillInstaller


def _make_builder(temp_home: Path) -> tuple[PromptBuilder, dict]:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    snapshot = service.create("prompt builder quest")
    return PromptBuilder(repo_root(), temp_home), snapshot


def test_prompt_builder_includes_layered_runtime_context(temp_home: Path) -> None:
    builder, snapshot = _make_builder(temp_home)
    prompt = builder.build(
        quest_id=snapshot["quest_id"],
        skill_id="decision",
        user_message="Please decide the next step.",
        model="gpt-5.4",
    )

    assert "# DeepScientist Core System Prompt" in prompt
    assert "## Runtime Context" in prompt
    assert "## Quest Context" in prompt
    assert "## Recent Durable State" in prompt
    assert "## Priority Memory For This Turn" in prompt
    assert "## Recent Conversation Window" in prompt
    assert f"quest_root: {snapshot['quest_root']}" in prompt
    assert f"active_branch: {snapshot['branch']}" in prompt
    assert f"conversation_id: quest:{snapshot['quest_id']}" in prompt
    assert "research_head_branch:" in prompt
    assert "current_workspace_root:" in prompt
    assert "built_in_mcp_namespaces: memory, artifact, bash_exec" in prompt
    assert "artifact.arxiv(paper_id=..., full_text=False)" in prompt
    assert "Canonical stage skills root:" in prompt
    assert "Standard stage skill paths:" in prompt
    assert "## Current User Message" in prompt


@pytest.mark.parametrize(("skill_id",), [("decision",), ("baseline",), ("analysis-campaign",), ("write",)])
def test_prompt_builder_includes_requested_skill_and_paths(temp_home: Path, skill_id: str) -> None:
    builder, snapshot = _make_builder(temp_home)
    prompt = builder.build(
        quest_id=snapshot["quest_id"],
        skill_id=skill_id,
        user_message="Continue the quest.",
        model="gpt-5.4",
    )

    assert f"requested_skill: {skill_id}" in prompt
    assert str((repo_root() / "src" / "skills").resolve()) in prompt
    assert "Fallback mirrored skills root:" not in prompt


def test_prompt_builder_includes_recent_conversation_window(temp_home: Path) -> None:
    builder, snapshot = _make_builder(temp_home)
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest_service.append_message(snapshot["quest_id"], role="user", content="First user turn.", source="cli")
    quest_service.append_message(snapshot["quest_id"], role="assistant", content="First assistant turn.", source="codex")

    prompt = builder.build(
        quest_id=snapshot["quest_id"],
        skill_id="decision",
        user_message="Second user turn.",
        model="gpt-5.4",
    )

    assert "[user|cli] First user turn." in prompt
    assert "[assistant|codex] First assistant turn." in prompt


def test_prompt_builder_includes_priority_memory_for_stage_and_message(temp_home: Path) -> None:
    builder, snapshot = _make_builder(temp_home)
    quest_root = Path(snapshot["quest_root"])
    memory = MemoryService(temp_home)
    memory.write_card(
        scope="quest",
        kind="ideas",
        title="Adapter ablation plan",
        body="Adapter ablation should compare against the baseline and track metric wiring carefully.",
        quest_root=quest_root,
        quest_id=snapshot["quest_id"],
        tags=["stage:experiment", "topic:adapter"],
    )
    memory.write_card(
        scope="global",
        kind="knowledge",
        title="Experiment debugging playbook",
        body="When metrics look identical to baseline, inspect seeds, dataset contract, and metric wiring first.",
        tags=["stage:experiment", "type:playbook"],
    )

    prompt = builder.build(
        quest_id=snapshot["quest_id"],
        skill_id="experiment",
        user_message="Please inspect adapter metric wiring before the next run.",
        model="gpt-5.4",
    )

    assert "## Priority Memory For This Turn" in prompt
    assert "Adapter ablation plan" in prompt
    assert "Experiment debugging playbook" in prompt
    assert "matches current user message" in prompt or "recent experiment" in prompt


def test_prompt_builder_includes_active_interactions(temp_home: Path) -> None:
    builder, snapshot = _make_builder(temp_home)
    quest_root = Path(snapshot["quest_root"])
    artifact = ArtifactService(temp_home)

    artifact.interact(
        quest_root,
        kind="decision_request",
        message="Should I continue with the current baseline route?",
        deliver_to_bound_conversations=False,
        include_recent_inbound_messages=False,
        options=[
            {"id": "continue", "label": "Continue", "description": "Keep the current route."},
            {"id": "reset", "label": "Reset", "description": "Revisit the baseline route."},
        ],
    )

    prompt = builder.build(
        quest_id=snapshot["quest_id"],
        skill_id="decision",
        user_message="Please use the latest user instruction.",
        model="gpt-5.4",
    )

    assert "Active interactions:" in prompt
    assert "Should I continue with the current baseline route?" in prompt


def test_prompt_builder_includes_progress_interact_cadence_guidance(temp_home: Path) -> None:
    builder, snapshot = _make_builder(temp_home)

    prompt = builder.build(
        quest_id=snapshot["quest_id"],
        skill_id="decision",
        user_message="Continue the quest.",
        model="gpt-5.4",
    )

    assert "5 to 15 tool calls" in prompt
    assert "do not send empty filler" in prompt
    assert "do not open or rewrite large binary assets" in prompt


def test_prompt_builder_mentions_decision_request_options_and_timeout(temp_home: Path) -> None:
    builder, snapshot = _make_builder(temp_home)

    prompt = builder.build(
        quest_id=snapshot["quest_id"],
        skill_id="decision",
        user_message="Please ask me before choosing the next expensive branch.",
        model="gpt-5.4",
    )

    assert "1 to 3 concrete options" in prompt
    assert "wait up to 1 day" in prompt
    assert "choose the best option yourself" in prompt
    assert "notify the user of the chosen option" in prompt


def test_prompt_builder_mentions_record_main_experiment_protocol(temp_home: Path) -> None:
    builder, snapshot = _make_builder(temp_home)

    prompt = builder.build(
        quest_id=snapshot["quest_id"],
        skill_id="experiment",
        user_message="Run the main experiment and report the result.",
        model="gpt-5.4",
    )

    assert "artifact.record_main_experiment(...)" in prompt
    assert "RUN.md" in prompt
    assert "RESULT.json" in prompt


def test_prompt_builder_mentions_long_running_bash_exec_monitoring_protocol(temp_home: Path) -> None:
    builder, snapshot = _make_builder(temp_home)

    prompt = builder.build(
        quest_id=snapshot["quest_id"],
        skill_id="experiment",
        user_message="Launch a long experiment and keep monitoring it carefully.",
        model="gpt-5.4",
    )

    assert "sleep about `60s`" in prompt
    assert "sleep about `120s`" in prompt
    assert "sleep about `300s`" in prompt
    assert "sleep about `600s`" in prompt
    assert "sleep about `1800s`" in prompt
    assert "artifact.interact(kind='progress', ...)" in prompt
    assert "bash_exec(mode='read', id=...)" in prompt
    assert "include a structured `comment`" in prompt


def test_prompt_builder_mentions_queued_user_message_mailbox(temp_home: Path) -> None:
    builder, snapshot = _make_builder(temp_home)
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest_service.append_message(snapshot["quest_id"], role="user", content="Please check config first.", source="web-react")

    prompt = builder.build(
        quest_id=snapshot["quest_id"],
        skill_id="baseline",
        user_message="Continue the quest.",
        model="gpt-5.4",
    )

    assert "pending_user_message_count: 1" in prompt
    assert "queued user messages waiting to be picked up via artifact.interact" in prompt
