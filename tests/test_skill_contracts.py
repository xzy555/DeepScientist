from __future__ import annotations

from deepscientist.home import repo_root


def _skill_text(skill_id: str) -> str:
    return (repo_root() / "src" / "skills" / skill_id / "SKILL.md").read_text(encoding="utf-8")


def _system_prompt_text() -> str:
    return (repo_root() / "src" / "prompts" / "system.md").read_text(encoding="utf-8")


def test_system_prompt_prioritizes_user_constraints_and_safe_efficiency() -> None:
    text = _system_prompt_text()

    assert "primary planning boundary" in text
    assert "best evidence-per-time-and-compute ratio" in text
    assert "larger safe batch size" in text
    assert "do not weaken comparability, trust, or the meaning of the final result" in text
    assert "safe efficiency levers that preserve those constraints and the comparability contract" in text


def test_system_prompt_defines_metric_contract_rules_and_optional_metric_md() -> None:
    text = _system_prompt_text()

    assert "make the canonical `metrics_summary` flat at the top level" in text
    assert "every canonical baseline metric entry should explain where it came from" in text
    assert "every main experiment submission must cover all required baseline metric ids" in text
    assert "`Result/metric.md` may be used as temporary scratch memory" in text


def test_idea_skill_requires_survey_delta_and_memory_reuse_contract() -> None:
    text = _skill_text("idea")

    assert "artifacts/idea/literature_survey.md" in text
    assert "reused prior survey coverage" in text
    assert "newly added papers or comparisons from this pass" in text
    assert "still-missing or unresolved overlaps" in text
    assert "survey delta with retrieval hints" in text
    assert "Executive Summary" in text
    assert "Codebase Analysis" in text
    assert "utility_score" in text
    assert "quality_score" in text
    assert "exploration_score" in text
    assert "at least `5` and usually `5-10`" in text
    assert "Do not write, promote, or submit a final idea" in text
    assert "standard citation format" in text
    assert "The selected idea draft must cite the survey papers" in text


def test_system_prompt_hardens_idea_literature_floor_and_reference_requirement() -> None:
    text = _system_prompt_text()

    assert "before writing or submitting the final selected idea, durably map at least 5 and usually 5 to 10 related and usable papers" in text
    assert "do not treat the literature floor as optional" in text
    assert "that final idea draft must use one consistent standard citation format" in text
    assert "a final idea draft that uses standard-format citations and a `References` or `Bibliography` section" in text


def test_baseline_skill_requires_plan_checklist_and_source_reading() -> None:
    text = _skill_text("baseline")

    assert "## Quick workflow" in text
    assert "## Required plan and checklist" in text
    assert "source paper and source repo first" in text
    assert "`PLAN.md` and `CHECKLIST.md`" in text
    assert "references/baseline-plan-template.md" in text
    assert "references/baseline-checklist-template.md" in text
    assert "ModelScope" in text
    assert "compatibility alias" in text
    assert "concise `1-2` sentence summary" in text
    assert "user's explicit requirements and non-negotiable constraints" in text
    assert "equivalence-preserving efficiency gains" in text
    assert "larger safe batch size" in text
    assert "accepted baseline meaning, effective evaluation contract, or trust judgment" in text
    assert "one clean implementation pass, one smoke test, and then one normal baseline run" in text
    assert "original paper's evaluation protocol as the canonical baseline contract" in text
    assert "multiple metrics, datasets, subtasks, or splits" in text
    assert "flat top-level dictionary keyed by the paper-facing metric ids" in text
    assert "`Result/metric.md` is optional temporary scratch memory only" in text


def test_experiment_skill_requires_incremental_seven_field_recording() -> None:
    text = _skill_text("experiment")

    assert "## Quick workflow" in text
    assert "## Required plan and checklist" in text
    assert "`PLAN.md` and `CHECKLIST.md`" in text
    assert "references/main-experiment-plan-template.md" in text
    assert "references/main-experiment-checklist-template.md" in text
    assert "selected idea summarized in `1-2` sentences" in text
    assert "minimal code-change map" in text
    assert "revise `PLAN.md` before spending more code or compute" in text
    assert "concise `1-2` sentence outcome summary" in text
    assert "rolling run log" in text or "rolling durable experiment log" in text
    assert "null hypothesis" in text
    assert "alternative hypothesis" in text
    assert "research question" in text
    assert "research type" in text
    assert "research objective" in text
    assert "experimental setup" in text
    assert "experimental results" in text
    assert "experimental analysis" in text
    assert "experimental conclusions" in text
    assert "Incremental-recording rule" in text
    assert "experiment tier: `auxiliary/dev` or `main/test`" in text
    assert "minimum -> solid -> maximum" in text
    assert "significance-testing plan" in text
    assert "evaluation_summary" in text
    assert "claim_update" in text
    assert "baseline_relation" in text
    assert "failure_mode" in text
    assert "next_action" in text
    assert "maximize valid evidence per unit time and compute" in text
    assert "equivalence-preserving efficiency upgrades" in text
    assert "larger safe batch size" in text
    assert "baseline comparability, treat it as a real experiment change" in text
    assert "one clean implementation pass, one bounded smoke or pilot run, and then one normal main run" in text
    assert "implement according to the current `PLAN.md`" in text
    assert "extra metrics are allowed, but missing required metrics are not" in text
    assert "record it as supplementary output rather than replacing the canonical comparator" in text
    assert "artifact.record_main_experiment(...)" in text


def test_analysis_campaign_skill_requires_outline_bound_campaign_fields() -> None:
    text = _skill_text("analysis-campaign")

    assert "## Quick workflow" in text
    assert "## Required plan and checklist" in text
    assert "`PLAN.md` and `CHECKLIST.md`" in text
    assert "references/campaign-plan-template.md" in text
    assert "references/campaign-checklist-template.md" in text
    assert "slice feasibility, ordering, comparators, or campaign interpretation changes materially" in text
    assert "concise `1-2` sentence summary" in text
    assert "do not launch it until a selected outline exists" in text
    assert "selected_outline_ref" in text
    assert "research_questions" in text
    assert "experimental_designs" in text
    assert "todo_items" in text
    assert "stable support" in text
    assert "contradiction" in text
    assert "`slice_class`, such as `auxiliary`, `claim-carrying`, or `supporting`" in text
    assert "move it from `minimum` to `solid`" in text
    assert "required_baselines" in text
    assert "comparison_baselines" in text
    assert "evaluation_summary" in text
    assert "takeaway" in text
    assert "comparability" in text
    assert "paper/paper_experiment_matrix.md" in text
    assert "`exp_id`" in text
    assert "highlight-validation" in text or "highlight validation" in text
    assert "efficiency or cost" in text


def test_write_skill_prefers_flexible_outline_flow_and_bundle_submission() -> None:
    text = _skill_text("write")

    assert "record one or more outline candidates" in text
    assert "artifact.submit_paper_outline(mode='candidate', ...)" in text
    assert "artifact.submit_paper_outline(mode='select'|'revise', ...)" in text
    assert "artifact.submit_paper_bundle(...)" in text
    assert "do not force extra outline rounds" in text
    assert "paper/latex/" in text
    assert "templates/iclr2026/" in text
    assert "general ML or AI writing with no stronger venue constraint, default to `templates/iclr2026/`" in text
    assert "motivation" in text
    assert "challenge" in text
    assert "resolution" in text
    assert "validation" in text
    assert "impact" in text
    assert "experiment-to-section mapping" in text
    assert "figure/table-to-data-source mapping" in text
    assert "verification checkpoints" in text
    assert "paper/paper_experiment_matrix.md" in text
    assert "paper/paper_experiment_matrix.json" in text
    assert "references/paper-experiment-matrix-template.md" in text
    assert "highlight hypotheses" in text
    assert "efficiency / cost / latency / token-overhead checks" in text
    assert "currently feasible" in text
    assert "non-optional rows" in text
    assert "citation legitimacy" in text
    assert "file-structure audit" in text
    assert "Organize for the reader's understanding" in text
    assert "paper/reviewer_first_pass.md" in text
    assert "problem -> why it matters -> current bottleneck -> our remedy -> evidence preview" in text
    assert "problem" in text and "what we do" in text and "how at a high level" in text and "main result or strongest evidence" in text
    assert "running example -> intuition -> formalism" in text
    assert "This paper is organized as follows" in text
    assert "do not attack prior work merely to make the current line look more novel" in text
    assert "Publication-grade figure refinement is recommended with AutoFigure-Edit" in text
    assert "https://github.com/ResearAI/AutoFigure-Edit" in text
    assert "https://deepscientist" in text


def test_idea_skill_adds_problem_importance_and_first_principles_memo() -> None:
    text = _skill_text("idea")

    assert "the problem importance in one sentence" in text
    assert "the main challenge or bottleneck in one sentence" in text
    assert "whether the direction is emerging, stable, or late" in text
    assert "under-recognized" in text
    assert "a short first-principles memo" in text


def test_idea_skill_requires_bounded_divergence_and_why_now_checks() -> None:
    text = _skill_text("idea")

    assert "problem-first" in text
    assert "solution-first" in text
    assert "6-12" in text
    assert "usually `2-3`" in text or "usually 2 to 3" in text
    assert "strong durable evidence already narrows the route" in text
    assert "two-sentence pitch" in text
    assert "strongest-objection" in text or "strongest objection" in text
    assert "why now" in text
    assert "adjacent possible" in text
    assert "constraint manipulation" in text
    assert "negation or inversion" in text
    assert "composition / decomposition" in text


def test_idea_skill_documents_framework_selection_and_failure_recovery() -> None:
    text = _skill_text("idea")

    assert "Framework selection guide" in text
    assert "Integrated ideation workflow" in text
    assert "Treat it as a subroutine inside the main workflow" in text
    assert "Phase A. Diverge" in text
    assert "Phase B. Converge" in text
    assert "Phase C. Refine" in text
    assert "Common ideation failure modes and recovery moves" in text
    assert "premature convergence" in text
    assert "novelty without value" in text
    assert "false binary" in text


def test_finalize_and_decision_skills_require_bundle_and_outline_actions() -> None:
    finalize_text = _skill_text("finalize")
    decision_text = _skill_text("decision")

    assert "paper/paper_bundle_manifest.json" in finalize_text
    assert "baseline_inventory_path" in finalize_text
    assert "release/open_source/manifest.json" in finalize_text
    assert "outline_path" in finalize_text
    assert "pdf_path" in finalize_text
    assert "artifact.submit_paper_outline(mode='select', ...)" in decision_text
    assert "artifact.submit_paper_bundle(...)" in decision_text
    assert "method fidelity" in decision_text
    assert "story coherence" in decision_text
    assert "belief-change log" in finalize_text
    assert "artifact.interact(kind='milestone'" in decision_text


def test_intake_audit_skill_requires_state_normalization_and_route_handoff() -> None:
    text = _skill_text("intake-audit")

    assert "startup_contract.launch_mode = custom" in text or "`startup_contract.launch_mode = custom`" in text
    assert "entry_state_summary" in text
    assert "review_summary" in text
    assert "custom_brief" in text
    assert "state-audit-template.md" in text
    assert "trust-rank" in text
    assert "route to `rebuttal`" in text or "handoff to `rebuttal`" in text
    assert "memory.write" in text


def test_rebuttal_skill_requires_review_matrix_response_bundle_and_memory() -> None:
    text = _skill_text("rebuttal")

    assert "paper/rebuttal/review_matrix.md" in text
    assert "paper/rebuttal/action_plan.md" in text
    assert "paper/rebuttal/response_letter.md" in text
    assert "paper/rebuttal/text_deltas.md" in text
    assert "paper/rebuttal/evidence_update.md" in text
    assert "paper/paper_experiment_matrix.md" in text
    assert "paper/paper_experiment_matrix.json" in text
    assert "review-matrix-template.md" in text
    assert "action-plan-template.md" in text
    assert "evidence-update-template.md" in text
    assert "response-letter-template.md" in text
    assert "R1-C1" in text
    assert "analysis before execution" in text
    assert "Do not invent rebuttal-only special tools" in text
    assert "MVP plan" in text
    assert "Enhanced plan" in text
    assert "analysis-experiment TODO list" in text
    assert "`exp_id`" in text
    assert "component_ablation" in text
    assert "efficiency_cost" in text
    assert "[[AUTHOR TO FILL]]" in text
    assert "scout" in text
    assert "baseline" in text
    assert "analysis-campaign" in text
    assert "write" in text
    assert "memory.write" in text
    assert "evaluation_summary" in text
    assert "calm, direct, precise author voice" in text
    assert "1 to 2 full paragraphs" in text
    assert "local file paths" in text or "local paths" in text
    assert "neutral reviewer or AC" in text
    assert "strengths recognized across reviewers" in text


def test_review_skill_requires_independent_audit_outputs_and_followup_routing() -> None:
    text = _skill_text("review")

    assert "independent skeptical audit" in text
    assert "paper/review/review.md" in text
    assert "paper/review/revision_log.md" in text
    assert "paper/review/experiment_todo.md" in text
    assert "paper/paper_experiment_matrix.md" in text
    assert "paper/paper_experiment_matrix.json" in text
    assert "review-report-template.md" in text
    assert "revision-log-template.md" in text
    assert "experiment-todo-template.md" in text
    assert "C1" in text and "C2" in text and "C3" in text
    assert "scout" in text
    assert "baseline" in text
    assert "analysis-campaign" in text
    assert "write" in text
    assert "memory.write" in text
    assert "evaluation_summary" in text
    assert "review_followup_policy" in text
    assert "manuscript_edit_mode" in text
    assert "latex_required" in text
    assert "matrix exp id" in text
    assert "highlight hypotheses" in text
    assert "copy-ready replacement sentence" in text or "copy-ready replacement" in text


def test_stage_skill_progress_contracts_match_tool_call_keepalive_policy() -> None:
    aligned_skills = (
        "intake-audit",
        "baseline",
        "idea",
        "experiment",
        "analysis-campaign",
        "write",
        "finalize",
        "decision",
        "review",
        "rebuttal",
        "scout",
    )

    for skill_id in aligned_skills:
        text = _skill_text(skill_id)
        assert "roughly 12 tool calls or about 8 minutes" in text
        assert "Do not update by tool-call cadence." not in text


def test_experiment_and_analysis_skills_require_smoke_then_detach_tail_monitoring() -> None:
    experiment_text = _skill_text("experiment")
    analysis_text = _skill_text("analysis-campaign")
    baseline_text = _skill_text("baseline")

    for text in (baseline_text, experiment_text, analysis_text):
        assert "smoke test" in text
        assert "bash_exec(mode='detach', ...)" in text
        assert "2000 lines or fewer" in text
        assert "first 500 lines plus the last 1500 lines" in text
        assert "bash_exec(mode='read', id=..., start=..., tail=...)" in text
        assert "tail_limit=..., order='desc'" in text
        assert "after_seq=last_seen_seq" in text
        assert "bash_exec(mode='history')" in text
        assert "watchdog_overdue" in text
        assert "bash_exec(mode='kill', id=..., wait=true, timeout_seconds=...)" in text
        assert "canonical sleep choice" in text
        assert "bash_exec(command='sleep N', mode='await', timeout_seconds=N+buffer, ...)" in text
        assert "do not set `timeout_seconds` exactly equal to `N`" in text
        assert "prefer `bash_exec(mode='await', id=..., timeout_seconds=...)` instead of starting a new sleep command" in text

    assert "tqdm" in baseline_text
    assert "tqdm" in experiment_text
    assert "tqdm" in analysis_text


def test_baseline_skill_prefers_fast_path_over_upfront_ceremony() -> None:
    text = _skill_text("baseline")

    assert "Default to the lightest baseline path" in text
    assert "run a bounded smoke test as soon as that contract is concrete enough" in text
    assert "Do not delay an early smoke test just because a fuller write-up is not done yet" in text
    assert "do not stall just to precreate every one of these files" in text
    assert "do not require every optional checklist or template before the first smoke test" in text
    assert "Do not build a new wrapper, registry, or result-export scaffold unless" in text
    assert "The templates below are references, not prerequisites for the first smoke test" in text
