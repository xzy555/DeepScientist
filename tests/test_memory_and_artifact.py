from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from deepscientist.artifact import ArtifactService
from deepscientist.config import ConfigManager
from deepscientist.home import ensure_home_layout, repo_root
from deepscientist.memory import MemoryService
from deepscientist.quest import QuestService
from deepscientist.shared import read_json, read_jsonl, write_json, write_yaml
from deepscientist.skills import SkillInstaller


class _FakeHeaders:
    def __init__(self, charset: str = "utf-8") -> None:
        self._charset = charset

    def get_content_charset(self) -> str:
        return self._charset


class _FakeUrlopenResponse:
    def __init__(self, body: str, *, charset: str = "utf-8") -> None:
        self._body = body.encode(charset)
        self.headers = _FakeHeaders(charset)

    def __enter__(self) -> "_FakeUrlopenResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def test_memory_documents_and_promotion(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("memory quest")
    quest_root = Path(quest["quest_root"])
    memory = MemoryService(temp_home)

    card = memory.write_card(
        scope="quest",
        kind="ideas",
        title="Reusable idea",
        body="A compact durable note.",
        quest_root=quest_root,
        quest_id=quest["quest_id"],
        tags=["test"],
    )
    assert Path(card["path"]).exists()

    documents = quest_service.list_documents(quest["quest_id"])
    memory_doc = next(item for item in documents if item["document_id"].startswith("memory::"))
    opened = quest_service.open_document(quest["quest_id"], memory_doc["document_id"])
    assert opened["writable"] is True
    assert "A compact durable note." in opened["content"]

    promoted = memory.promote_to_global(path=card["path"], quest_root=quest_root)
    assert Path(promoted["path"]).exists()
    assert promoted["scope"] == "global"

    skill_doc = next(item for item in documents if item["document_id"].startswith("skill::"))
    skill_opened = quest_service.open_document(quest["quest_id"], skill_doc["document_id"])
    assert skill_opened["writable"] is False


def test_artifact_interact_and_prepare_branch(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("artifact quest")
    quest_root = Path(quest["quest_root"])
    artifact = ArtifactService(temp_home)

    quest_service.append_message(quest["quest_id"], role="user", content="请先告诉我 baseline 情况。", source="web")
    result = artifact.interact(
        quest_root,
        kind="progress",
        message="Baseline is ready; I am summarizing the current metrics.",
        deliver_to_bound_conversations=True,
        include_recent_inbound_messages=True,
    )
    assert result["status"] == "ok"
    assert result["delivered"] is True
    assert result["recent_inbound_messages"]

    outbox = temp_home / "logs" / "connectors" / "local" / "outbox.jsonl"
    assert outbox.exists()
    records = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any("Baseline is ready" in (item.get("message") or "") for item in records)

    branch = artifact.prepare_branch(quest_root, run_id="run-main-001")
    assert branch["ok"] is True
    assert branch["branch"] == "run/run-main-001"
    assert Path(branch["worktree_root"]).exists()


def test_artifact_managed_git_flow_updates_research_state_and_mirrors_analysis(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("artifact flow quest")
    quest_root = Path(quest["quest_root"])
    artifact = ArtifactService(temp_home)

    created = artifact.submit_idea(
        quest_root,
        mode="create",
        title="Adapter route",
        problem="Baseline saturates.",
        hypothesis="A lightweight adapter improves generalization.",
        mechanism="Insert a small residual adapter before the head.",
        decision_reason="This is the strongest next idea.",
        next_target="experiment",
    )
    idea_worktree = Path(created["worktree_root"])
    idea_md_path = Path(created["idea_md_path"])
    assert created["branch"].startswith(f"idea/{quest['quest_id']}-")
    assert idea_worktree.exists()
    assert idea_md_path.exists()

    revised = artifact.submit_idea(
        quest_root,
        mode="revise",
        idea_id=created["idea_id"],
        title="Adapter route v2",
        problem="Baseline still underfits hard examples.",
        hypothesis="A tuned adapter improves hard-example recall.",
        mechanism="Tune the adapter depth and placement.",
        decision_reason="Refine the same active route before coding.",
        next_target="experiment",
    )
    assert revised["worktree_root"] == created["worktree_root"]
    assert "Adapter route v2" in idea_md_path.read_text(encoding="utf-8")

    campaign = artifact.create_analysis_campaign(
        quest_root,
        campaign_title="Ablation suite",
        campaign_goal="Stress-test the promoted idea.",
        slices=[
            {
                "slice_id": "ablation",
                "title": "Adapter ablation",
                "goal": "Remove the adapter and compare.",
                "required_changes": "Disable the adapter path only.",
                "metric_contract": "Report full validation metrics.",
            },
            {
                "slice_id": "robustness",
                "title": "Robustness check",
                "goal": "Run the intended robustness configuration.",
                "required_changes": "Apply the robustness config only.",
                "metric_contract": "Report the same full evaluation metrics.",
            },
        ],
    )
    assert campaign["ok"] is True
    assert campaign["campaign_id"]
    assert len(campaign["slices"]) == 2
    first_slice = campaign["slices"][0]
    second_slice = campaign["slices"][1]
    assert Path(first_slice["worktree_root"]).exists()
    assert Path(second_slice["worktree_root"]).exists()

    state_after_campaign = quest_service.read_research_state(quest_root)
    assert state_after_campaign["active_analysis_campaign_id"] == campaign["campaign_id"]
    assert state_after_campaign["current_workspace_root"] == first_slice["worktree_root"]

    first_record = artifact.record_analysis_slice(
        quest_root,
        campaign_id=campaign["campaign_id"],
        slice_id="ablation",
        setup="Disable the adapter path only.",
        execution="Ran the full validation sweep.",
        results="Accuracy dropped as expected.",
        evidence_paths=["experiments/analysis/ablation/result.json"],
        metric_rows=[{"name": "acc", "value": 0.84}],
    )
    assert first_record["ok"] is True
    assert first_record["completed"] is False
    assert first_record["next_slice"]["slice_id"] == "robustness"
    assert Path(first_record["mirror_path"]).exists()

    second_record = artifact.record_analysis_slice(
        quest_root,
        campaign_id=campaign["campaign_id"],
        slice_id="robustness",
        setup="Apply the robustness configuration only.",
        execution="Ran the full robustness sweep.",
        results="The method stayed stable under the robustness setting.",
        evidence_paths=["experiments/analysis/robustness/result.json"],
        metric_rows=[{"name": "acc", "value": 0.86}],
    )
    assert second_record["ok"] is True
    assert second_record["completed"] is True
    assert second_record["returned_to_branch"] == created["branch"]
    assert Path(second_record["summary_path"]).exists()

    final_state = quest_service.read_research_state(quest_root)
    assert final_state["active_analysis_campaign_id"] is None
    assert final_state["current_workspace_root"] == str(idea_worktree)
    assert final_state["research_head_branch"] == created["branch"]

    events = read_jsonl(quest_root / ".ds" / "events.jsonl")
    campaign_event = next(
        item
        for item in reversed(events)
        if item.get("type") == "artifact.recorded"
        and item.get("flow_type") == "analysis_campaign"
        and item.get("protocol_step") == "complete"
    )
    assert campaign_event["workspace_root"] == str(idea_worktree)
    assert campaign_event["details"]["slice_count"] == 2


def test_record_main_experiment_writes_result_and_baseline_comparison(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("main experiment result quest")
    quest_root = Path(quest["quest_root"])
    artifact = ArtifactService(temp_home)

    artifact.record(
        quest_root,
        {
            "kind": "baseline",
            "publish_global": True,
            "baseline_id": "baseline-main",
            "name": "Main baseline",
            "primary_metric": {"name": "acc", "value": 0.84},
            "metrics_summary": {"acc": 0.84, "f1": 0.8},
            "baseline_variants": [
                {"variant_id": "main", "label": "Main", "metrics_summary": {"acc": 0.84, "f1": 0.8}}
            ],
            "default_variant_id": "main",
        },
    )
    artifact.attach_baseline(quest_root, "baseline-main", "main")

    idea = artifact.submit_idea(
        quest_root,
        mode="create",
        title="Adapter route",
        problem="Baseline saturates.",
        hypothesis="A small adapter improves the main score.",
        mechanism="Insert a light residual adapter.",
        decision_reason="Best next route.",
        next_target="experiment",
    )
    worktree_root = Path(idea["worktree_root"])
    (worktree_root / "src").mkdir(exist_ok=True)
    (worktree_root / "src" / "model.py").write_text("print('adapter')\n", encoding="utf-8")

    result = artifact.record_main_experiment(
        quest_root,
        run_id="main-001",
        title="Adapter main run",
        hypothesis="Adapter improves validation accuracy.",
        setup="Use the attached baseline training recipe.",
        execution="Ran the full validation sweep.",
        results="Accuracy improved.",
        conclusion="The adapter is promising enough for follow-up analysis.",
        metric_rows=[
            {"metric_id": "acc", "value": 0.89, "split": "val"},
            {"metric_id": "f1", "value": 0.85, "split": "val"},
        ],
        evidence_paths=["outputs/main-001/metrics.json"],
        config_paths=["configs/adapter.yaml"],
    )

    assert result["ok"] is True
    run_md = Path(result["run_md_path"])
    result_json = Path(result["result_json_path"])
    assert run_md.exists()
    assert result_json.exists()

    payload = read_json(result_json, {})
    assert payload["result_kind"] == "main_experiment"
    assert payload["baseline_ref"]["baseline_id"] == "baseline-main"
    assert payload["metrics_summary"]["acc"] == 0.89
    assert payload["baseline_comparisons"]["primary_metric_id"] == "acc"
    primary = next(item for item in payload["baseline_comparisons"]["items"] if item["metric_id"] == "acc")
    assert primary["delta"] == pytest.approx(0.05)
    assert payload["progress_eval"]["breakthrough"] is True
    assert payload["progress_eval"]["breakthrough_level"] in {"minor", "major"}

    snapshot = quest_service.snapshot(quest["quest_id"])
    assert snapshot["summary"]["latest_metric"]["key"] == "acc"
    assert snapshot["summary"]["latest_metric"]["delta_vs_baseline"] == pytest.approx(0.05)


def test_artifact_arxiv_overview_falls_back_to_arxiv_abstract(temp_home: Path, monkeypatch) -> None:
    ensure_home_layout(temp_home)
    artifact = ArtifactService(temp_home)

    def fake_urlopen(request, timeout=8):  # noqa: ANN001
        url = request.full_url
        if url.endswith("/overview/2010.11929.md"):
            raise TimeoutError("overview timed out")
        if url.endswith("/abs/2010.11929"):
            return _FakeUrlopenResponse(
                """
                <html>
                  <head>
                    <meta name="citation_title" content="An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale" />
                    <meta name="citation_author" content="Dosovitskiy, Alexey" />
                  </head>
                  <body>
                    <blockquote class="abstract mathjax">
                      <span class="descriptor">Abstract:</span>
                      Vision Transformers apply pure transformer layers directly to image patches.
                    </blockquote>
                  </body>
                </html>
                """
            )
        raise AssertionError(url)

    monkeypatch.setattr("deepscientist.artifact.arxiv.urlopen", fake_urlopen)
    result = artifact.arxiv("2010.11929")

    assert result["ok"] is True
    assert result["source"] == "arxiv_abstract"
    assert result["content_mode"] == "abstract"
    assert "An Image is Worth 16x16 Words" in result["content"]
    assert "Vision Transformers apply pure transformer layers" in result["content"]
    assert result["attempts"][0]["source"] == "alphaxiv_overview"
    assert result["attempts"][0]["ok"] is False


def test_artifact_arxiv_full_text_falls_back_to_html(temp_home: Path, monkeypatch) -> None:
    ensure_home_layout(temp_home)
    artifact = ArtifactService(temp_home)

    def fake_urlopen(request, timeout=8):  # noqa: ANN001
        url = request.full_url
        if url.endswith("/abs/2010.11929.md"):
            raise HTTPError(url, 404, "not found", hdrs=None, fp=None)
        if url.endswith("/html/2010.11929"):
            return _FakeUrlopenResponse(
                """
                <html>
                  <head>
                    <title>An Image is Worth 16x16 Words</title>
                  </head>
                  <body>
                    <article>
                      <h1>An Image is Worth 16x16 Words</h1>
                      <p>Introduction.</p>
                      <p>Methods.</p>
                    </article>
                  </body>
                </html>
                """
            )
        raise AssertionError(url)

    monkeypatch.setattr("deepscientist.artifact.arxiv.urlopen", fake_urlopen)
    result = artifact.arxiv("2010.11929", full_text=True)

    assert result["ok"] is True
    assert result["source"] == "arxiv_html"
    assert result["content_mode"] == "full_text"
    assert "Introduction." in result["content"]
    assert "Methods." in result["content"]
    assert result["attempts"][0]["source"] == "alphaxiv_full_text"
    assert result["attempts"][0]["ok"] is False


def test_artifact_interact_respects_primary_connector_policy(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    manager = ConfigManager(temp_home)
    manager.ensure_files()
    connectors = manager.load_named("connectors")
    connectors["telegram"]["enabled"] = True
    connectors["slack"]["enabled"] = True
    connectors["_routing"]["primary_connector"] = "telegram"
    connectors["_routing"]["artifact_delivery_policy"] = "primary_plus_local"
    write_yaml(manager.path_for("connectors"), connectors)

    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("artifact routing quest")
    quest_root = Path(quest["quest_root"])
    artifact = ArtifactService(temp_home)

    quest_service.bind_source(quest["quest_id"], "web")
    quest_service.bind_source(quest["quest_id"], "telegram:direct:tg-user-1")
    quest_service.bind_source(quest["quest_id"], "slack:direct:slack-user-1")

    result = artifact.interact(
        quest_root,
        kind="milestone",
        message="Primary connector routing test.",
        deliver_to_bound_conversations=True,
        include_recent_inbound_messages=False,
    )

    assert result["status"] == "ok"
    assert result["delivery_policy"] == "primary_plus_local"
    assert result["preferred_connector"] == "telegram"
    assert result["delivery_targets"] == ["local:default", "telegram:direct:tg-user-1"]

    local_records = read_jsonl(temp_home / "logs" / "connectors" / "local" / "outbox.jsonl")
    telegram_records = read_jsonl(temp_home / "logs" / "connectors" / "telegram" / "outbox.jsonl")
    slack_outbox = temp_home / "logs" / "connectors" / "slack" / "outbox.jsonl"

    assert any("Primary connector routing test." in str(item.get("message") or "") for item in local_records)
    assert any("Primary connector routing test." in str(item.get("text") or "") for item in telegram_records)
    assert not slack_outbox.exists()


def test_artifact_interact_auto_uses_single_enabled_connector_for_primary_only(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    manager = ConfigManager(temp_home)
    manager.ensure_files()
    connectors = manager.load_named("connectors")
    connectors["whatsapp"]["enabled"] = True
    connectors["_routing"]["primary_connector"] = None
    connectors["_routing"]["artifact_delivery_policy"] = "primary_only"
    write_yaml(manager.path_for("connectors"), connectors)

    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("single connector routing quest")
    quest_root = Path(quest["quest_root"])
    artifact = ArtifactService(temp_home)

    quest_service.bind_source(quest["quest_id"], "web")
    quest_service.bind_source(quest["quest_id"], "whatsapp:direct:+15550001111")

    result = artifact.interact(
        quest_root,
        kind="progress",
        message="Single connector auto-selection test.",
        deliver_to_bound_conversations=True,
        include_recent_inbound_messages=False,
    )

    assert result["preferred_connector"] == "whatsapp"
    assert result["delivery_policy"] == "primary_only"
    assert result["delivery_targets"] == ["whatsapp:direct:+15550001111"]

    whatsapp_records = read_jsonl(temp_home / "logs" / "connectors" / "whatsapp" / "outbox.jsonl")
    local_outbox = temp_home / "logs" / "connectors" / "local" / "outbox.jsonl"

    assert any("Single connector auto-selection test." in str(item.get("text") or "") for item in whatsapp_records)
    assert not local_outbox.exists()


def test_explorer_lists_real_files_and_path_documents_can_be_saved(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("explorer quest")
    quest_root = Path(quest["quest_root"])

    note_path = quest_root / "literature" / "notes.md"
    note_path.write_text("# Notes\n\nInitial baseline scouting.", encoding="utf-8")

    explorer = quest_service.explorer(quest["quest_id"])
    assert explorer["quest_root"] == str(quest_root.resolve())
    research = next(section for section in explorer["sections"] if section["id"] == "research")

    def flatten(nodes: list[dict]) -> list[dict]:
        items: list[dict] = []
        for node in nodes:
            items.append(node)
            items.extend(flatten(node.get("children") or []))
        return items

    research_nodes = flatten(research["nodes"])
    note_node = next(node for node in research_nodes if node.get("path") == "literature/notes.md")
    assert note_node["document_id"] == "path::literature/notes.md"
    assert note_node["writable"] is True

    opened = quest_service.open_document(quest["quest_id"], note_node["document_id"])
    assert "Initial baseline scouting." in opened["content"]

    saved = quest_service.save_document(
        quest["quest_id"],
        note_node["document_id"],
        "# Notes\n\nUpdated from explorer.",
        previous_revision=opened["revision"],
    )
    assert saved["ok"] is True

    reopened = quest_service.open_document(quest["quest_id"], note_node["document_id"])
    assert "Updated from explorer." in reopened["content"]


def test_explorer_opens_image_files_as_assets(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("image explorer quest")
    quest_root = Path(quest["quest_root"])

    figure_path = quest_root / "literature" / "figure.png"
    figure_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-png")

    explorer = quest_service.explorer(quest["quest_id"])
    research = next(section for section in explorer["sections"] if section["id"] == "research")

    def flatten(nodes: list[dict]) -> list[dict]:
        items: list[dict] = []
        for node in nodes:
            items.append(node)
            items.extend(flatten(node.get("children") or []))
        return items

    research_nodes = flatten(research["nodes"])
    figure_node = next(node for node in research_nodes if node.get("path") == "literature/figure.png")

    opened = quest_service.open_document(quest["quest_id"], figure_node["document_id"])
    assert opened["meta"]["renderer_hint"] == "image"
    assert opened["mime_type"] == "image/png"
    assert opened["content"] == ""
    assert "documents/asset" in opened["asset_url"]


def test_markdown_asset_upload_uses_sibling_assets_folder(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("markdown asset upload quest")
    quest_root = Path(quest["quest_root"])

    uploaded = quest_service.save_document_asset(
        quest["quest_id"],
        "brief.md",
        file_name="diagram.png",
        mime_type="image/png",
        content=b"\x89PNG\r\n\x1a\nquest-markdown-asset",
        kind="image",
    )

    assert uploaded["ok"] is True
    assert uploaded["relative_path"].startswith("brief.assets/")
    asset_path = quest_root / uploaded["relative_path"]
    assert asset_path.exists()
    assert asset_path.read_bytes().startswith(b"\x89PNG")

    opened = quest_service.open_document(quest["quest_id"], uploaded["asset_document_id"])
    assert opened["meta"]["renderer_hint"] == "image"
    assert opened["mime_type"] == "image/png"
    assert "documents/asset" in opened["asset_url"]


def test_explorer_can_switch_to_git_snapshot_and_open_historical_files(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("git snapshot explorer quest")
    quest_root = Path(quest["quest_root"])

    note_path = quest_root / "literature" / "notes.md"
    note_path.write_text("# Notes\n\nCommitted snapshot.", encoding="utf-8")
    from deepscientist.gitops import checkpoint_repo

    checkpoint_repo(quest_root, "Add literature note for snapshot explorer", allow_empty=False)
    note_path.write_text("# Notes\n\nLive working tree update.", encoding="utf-8")

    snapshot_explorer = quest_service.explorer(quest["quest_id"], revision="HEAD", mode="commit")
    assert snapshot_explorer["view"]["mode"] == "commit"
    assert snapshot_explorer["view"]["revision"] == "HEAD"
    assert snapshot_explorer["view"]["read_only"] is True

    research = next(section for section in snapshot_explorer["sections"] if section["id"] == "research")

    def flatten(nodes: list[dict]) -> list[dict]:
        items: list[dict] = []
        for node in nodes:
            items.append(node)
            items.extend(flatten(node.get("children") or []))
        return items

    research_nodes = flatten(research["nodes"])
    note_node = next(node for node in research_nodes if node.get("path") == "literature/notes.md")
    assert note_node["document_id"] == "git::HEAD::literature/notes.md"
    assert note_node["writable"] is False

    opened = quest_service.open_document(quest["quest_id"], note_node["document_id"])
    assert opened["source_scope"] == "git_snapshot"
    assert opened["writable"] is False
    assert "Committed snapshot." in opened["content"]
    assert "Live working tree update." not in opened["content"]

    save_attempt = quest_service.save_document(
        quest["quest_id"],
        note_node["document_id"],
        "# Notes\n\nShould not save to snapshot.",
        previous_revision=opened["revision"],
    )
    assert save_attempt["ok"] is False
    assert save_attempt["conflict"] is False


def test_explorer_lists_custom_root_files_and_binary_assets(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("custom explorer quest")
    quest_root = Path(quest["quest_root"])

    code_path = quest_root / "src" / "train.py"
    code_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.write_text("print('quest explorer works')\n", encoding="utf-8")

    image_path = quest_root / "figures" / "plot.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nquest-plot")

    pdf_path = quest_root / "docs" / "appendix.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n")

    explorer = quest_service.explorer(quest["quest_id"])
    quest_section = next(section for section in explorer["sections"] if section["id"] == "quest")

    def flatten(nodes: list[dict]) -> list[dict]:
        items: list[dict] = []
        for node in nodes:
            items.append(node)
            items.extend(flatten(node.get("children") or []))
        return items

    quest_nodes = flatten(quest_section["nodes"])

    code_node = next(node for node in quest_nodes if node.get("path") == "src/train.py")
    assert code_node["document_id"] == "path::src/train.py"
    opened_code = quest_service.open_document(quest["quest_id"], code_node["document_id"])
    assert opened_code["meta"]["renderer_hint"] == "code"
    assert "quest explorer works" in opened_code["content"]

    image_node = next(node for node in quest_nodes if node.get("path") == "figures/plot.png")
    opened_image = quest_service.open_document(quest["quest_id"], image_node["document_id"])
    assert opened_image["meta"]["renderer_hint"] == "image"
    assert opened_image["mime_type"] == "image/png"
    assert "documents/asset" in opened_image["asset_url"]

    pdf_node = next(node for node in quest_nodes if node.get("path") == "docs/appendix.pdf")
    opened_pdf = quest_service.open_document(quest["quest_id"], pdf_node["document_id"])
    assert opened_pdf["meta"]["renderer_hint"] == "pdf"
    assert opened_pdf["mime_type"] == "application/pdf"
    assert "documents/asset" in opened_pdf["asset_url"]


def test_artifact_interact_tracks_pending_request_and_user_reply(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("interactive artifact quest")
    quest_root = Path(quest["quest_root"])
    artifact = ArtifactService(temp_home)

    request = artifact.interact(
        quest_root,
        kind="decision_request",
        message="Should I launch the robustness campaign now?",
        deliver_to_bound_conversations=False,
        include_recent_inbound_messages=False,
        options=[
            {"id": "launch", "label": "Launch now", "description": "Run the campaign immediately."},
            {"id": "wait", "label": "Wait", "description": "Hold off until more evidence arrives."},
        ],
    )
    assert request["status"] == "ok"
    assert request["expects_reply"] is True
    assert request["open_request_count"] == 1
    snapshot_waiting = quest_service.snapshot(quest["quest_id"])
    assert snapshot_waiting["status"] == "waiting_for_user"
    assert snapshot_waiting["pending_decisions"]
    assert snapshot_waiting["active_interactions"]

    reply = quest_service.append_message(
        quest["quest_id"],
        role="user",
        content="Launch it now and focus on robustness first.",
        source="qq:group:demo",
    )
    snapshot_after_reply = quest_service.snapshot(quest["quest_id"])
    assert snapshot_after_reply["status"] == "running"
    assert any(item.get("status") == "answered" for item in snapshot_after_reply["active_interactions"])

    follow_up = artifact.interact(
        quest_root,
        kind="progress",
        message="Received your instruction; I am preparing the campaign charter.",
        deliver_to_bound_conversations=False,
        include_recent_inbound_messages=True,
    )
    assert follow_up["status"] == "ok"
    assert follow_up["recent_inbound_messages"]
    latest = follow_up["recent_inbound_messages"][-1]
    assert latest["message_id"] == reply["id"]
    assert latest["conversation_id"] == "qq:group:demo"
    assert latest["text"].startswith("Launch it now")


def test_bind_source_repairs_lowercased_connector_binding_and_preserves_chat_id_case(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("binding repair quest")

    quest_service.bind_source(quest["quest_id"], "qq:direct:cf8d2d559aa956b48751539adfb98865")
    repaired = quest_service.bind_source(quest["quest_id"], "qq:direct:CF8D2D559AA956B48751539ADFB98865")

    assert repaired["sources"] == ["qq:direct:CF8D2D559AA956B48751539ADFB98865"]


def test_artifact_delivery_prefers_connector_binding_case_for_qq(temp_home: Path, monkeypatch) -> None:
    ensure_home_layout(temp_home)
    manager = ConfigManager(temp_home)
    manager.ensure_files()
    connectors = manager.load_named("connectors")
    connectors["qq"]["enabled"] = True
    write_yaml(manager.path_for("connectors"), connectors)

    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("qq artifact delivery quest")
    quest_root = Path(quest["quest_root"])
    artifact = ArtifactService(temp_home)

    write_json(
        quest_root / ".ds" / "bindings.json",
        {"sources": ["local:default", "qq:direct:cf8d2d559aa956b48751539adfb98865"]},
    )
    write_json(
        temp_home / "logs" / "connectors" / "qq" / "bindings.json",
        {
            "bindings": {
                "qq:direct:CF8D2D559AA956B48751539ADFB98865": {
                    "quest_id": quest["quest_id"],
                    "updated_at": "2026-03-11T17:47:49+00:00",
                }
            }
        },
    )

    deliveries: list[str] = []

    class FakeBridge:
        def deliver(self, outbound: dict, config: dict) -> dict:  # noqa: ANN001
            deliveries.append(str(outbound.get("conversation_id") or ""))
            return {"ok": True, "transport": "qq-http"}

    monkeypatch.setattr("deepscientist.channels.qq.get_connector_bridge", lambda name: FakeBridge())

    result = artifact.interact(
        quest_root,
        kind="progress",
        message="QQ delivery should preserve the original openid casing.",
        deliver_to_bound_conversations=True,
        include_recent_inbound_messages=False,
    )

    assert result["delivered"] is True
    assert deliveries == ["qq:direct:CF8D2D559AA956B48751539ADFB98865"]


def test_artifact_record_and_snapshot_include_guidance_vm(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("guidance quest")
    quest_root = Path(quest["quest_root"])
    artifact = ArtifactService(temp_home)

    recorded = artifact.record(
        quest_root,
        {
            "kind": "baseline",
            "status": "completed",
            "baseline_id": "baseline-guidance",
            "summary": "Baseline recorded for guidance coverage.",
            "reason": "Need a durable baseline before ideation.",
            "primary_metric": "acc",
            "metrics_summary": {"acc": 0.87},
        },
    )

    assert recorded["ok"] is True
    assert recorded["guidance_vm"]["current_anchor"] == "baseline"
    assert recorded["guidance_vm"]["recommended_skill"] == "idea"

    payload = json.loads(Path(recorded["path"]).read_text(encoding="utf-8"))
    assert payload["guidance_vm"]["recommended_action"] == "continue"

    events = read_jsonl(quest_root / ".ds" / "events.jsonl")
    artifact_event = next(item for item in events if item.get("type") == "artifact.recorded")
    assert artifact_event["guidance_vm"]["recommended_skill"] == "idea"

    snapshot = quest_service.snapshot(quest["quest_id"])
    assert snapshot["guidance"]["recommended_skill"] == "idea"
    assert "baseline" in snapshot["guidance"]["current_anchor"]


def test_approval_record_closes_pending_interaction(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("approval closes interaction")
    quest_root = Path(quest["quest_root"])
    artifact = ArtifactService(temp_home)

    request = artifact.interact(
        quest_root,
        kind="decision_request",
        message="Approve the expensive baseline reproduction?",
        deliver_to_bound_conversations=False,
        include_recent_inbound_messages=False,
    )
    decision_id = request["artifact_id"]
    snapshot_waiting = quest_service.snapshot(quest["quest_id"])
    assert snapshot_waiting["status"] == "waiting_for_user"

    artifact.record(
        quest_root,
        {
            "kind": "approval",
            "decision_id": decision_id,
            "reason": "Approved by user command.",
        },
    )

    snapshot_after = quest_service.snapshot(quest["quest_id"])
    assert snapshot_after["status"] == "active"
    assert not snapshot_after["pending_decisions"]


def test_threaded_progress_auto_links_user_reply_without_waiting(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("threaded progress reply quest")
    quest_root = Path(quest["quest_root"])
    artifact = ArtifactService(temp_home)

    progress = artifact.interact(
        quest_root,
        kind="progress",
        message="老师，我已经完成仓库结构审计，正在整理下一步复现实验计划。",
        deliver_to_bound_conversations=False,
        include_recent_inbound_messages=False,
    )

    assert progress["status"] == "ok"
    assert progress["reply_mode"] == "threaded"

    snapshot_after_progress = quest_service.snapshot(quest["quest_id"])
    assert snapshot_after_progress["status"] != "waiting_for_user"
    assert snapshot_after_progress["default_reply_interaction_id"] == progress["interaction_id"]

    reply = quest_service.append_message(
        quest["quest_id"],
        role="user",
        content="继续，先把依赖和数据集入口确认下来。",
        source="web-react",
    )

    assert reply["reply_to_interaction_id"] == progress["interaction_id"]

    interaction_state = json.loads((quest_root / ".ds" / "interaction_state.json").read_text(encoding="utf-8"))
    latest_thread = interaction_state["recent_threads"][-1]
    assert latest_thread["interaction_id"] == progress["interaction_id"]
    assert latest_thread["last_reply_message_id"] == reply["id"]
    assert latest_thread["reply_count"] == 1

    follow_up = artifact.interact(
        quest_root,
        kind="progress",
        message="老师，我已经开始核对依赖版本。",
        deliver_to_bound_conversations=False,
        include_recent_inbound_messages=True,
    )

    assert follow_up["recent_inbound_messages"]
    latest = follow_up["recent_inbound_messages"][-1]
    assert latest["message_id"] == reply["id"]
    assert latest["reply_to_interaction_id"] == progress["interaction_id"]


def test_user_message_queue_is_delivered_only_when_artifact_interact_polls(temp_home: Path) -> None:
    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    quest_service = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home))
    quest = quest_service.create("queued mailbox quest")
    quest_root = Path(quest["quest_root"])
    artifact = ArtifactService(temp_home)

    first = quest_service.append_message(
        quest["quest_id"],
        role="user",
        content="先检查训练入口。",
        source="web-react",
    )
    second = quest_service.append_message(
        quest["quest_id"],
        role="user",
        content="然后核对依赖版本。",
        source="qq:group:demo",
    )

    queue_before = json.loads((quest_root / ".ds" / "user_message_queue.json").read_text(encoding="utf-8"))
    assert [item["message_id"] for item in queue_before["pending"]] == [first["id"], second["id"]]
    runtime_before = json.loads((quest_root / ".ds" / "runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_before["pending_user_message_count"] == 2

    polled = artifact.interact(
        quest_root,
        kind="progress",
        message="老师，我已经进入检查阶段。",
        deliver_to_bound_conversations=False,
        include_recent_inbound_messages=True,
    )

    assert polled["delivery_batch"] is not None
    assert [item["message_id"] for item in polled["recent_inbound_messages"]] == [first["id"], second["id"]]
    assert "这是最新用户的要求" in polled["agent_instruction"]
    assert "先检查训练入口。" in polled["agent_instruction"]
    assert "然后核对依赖版本。" in polled["agent_instruction"]

    queue_after = json.loads((quest_root / ".ds" / "user_message_queue.json").read_text(encoding="utf-8"))
    assert queue_after["pending"] == []
    assert [item["message_id"] for item in queue_after["completed"][-2:]] == [first["id"], second["id"]]

    runtime_after = json.loads((quest_root / ".ds" / "runtime_state.json").read_text(encoding="utf-8"))
    assert runtime_after["pending_user_message_count"] == 0
    assert runtime_after["last_delivered_batch_id"] == polled["delivery_batch"]["batch_id"]
    assert runtime_after["last_artifact_interact_at"] is not None

    no_new_message = artifact.interact(
        quest_root,
        kind="progress",
        message="老师，我继续推进检查。",
        deliver_to_bound_conversations=False,
        include_recent_inbound_messages=True,
    )

    assert no_new_message["recent_inbound_messages"] == []
    assert "当前用户并没有发送任何消息" in no_new_message["agent_instruction"]
    assert len(no_new_message["recent_interaction_records"]) >= 3
