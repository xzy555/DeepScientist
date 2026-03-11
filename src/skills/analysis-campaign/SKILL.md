---
name: analysis-campaign
description: Use when a quest needs multiple follow-up runs such as ablations, robustness checks, error analysis, or failure analysis after a main experiment.
---

# Analysis Campaign

Use this skill when one follow-up run is not enough and the quest needs a coordinated evidence campaign.

## Interaction discipline

- Treat `artifact.interact(...)` as the main long-lived communication thread across TUI, web, and bound connectors.
- If `artifact.interact(...)` returns queued user requirements, treat them as the latest user instruction bundle before continuing the campaign.
- Emit `artifact.interact(kind='progress', reply_mode='threaded', ...)` only at real checkpoints, and normally no more frequently than every 5 to 15 tool calls.
- Prefer `bash_exec` for campaign slice commands so each run has a durable session id, quest-local log folder, and later `read/list/kill` control.
- Each progress update must state completed work, the durable output touched, and the immediate next slice.
- Progress message templates are references only. Adapt to the actual context and vary wording so messages feel human, respectful, and non-robotic.
- Use `reply_mode='blocking'` only for real user decisions that cannot be resolved from local evidence.
- For any blocking decision request, provide 1 to 3 concrete options, put the recommended option first, explain each option's actual content plus pros and cons, wait up to 1 day when feasible, then choose the best option yourself and notify the user of the chosen option if the timeout expires.
- If a threaded user reply arrives, interpret it relative to the latest campaign progress update before assuming the task changed completely.

## Stage purpose

The analysis-campaign stage exists to test the strength, boundaries, and failure modes of a result.
It preserves the core old DeepScientist analysis-experimenter discipline:

- each analysis run should correspond to one clear question
- campaign runs should stay isolated and comparable
- negative results must remain visible
- campaign-level conclusions should be aggregated explicitly

The campaign should behave like a disciplined evidence program, not an unstructured pile of extra runs.

## Non-negotiable rules

- Every analysis run must be code-based and fully automatable.
- Do not introduce human evaluation or subjective assessment into a campaign.
- Do not bring in a new dataset unless the quest scope explicitly changed.
- Every analysis slice must have a specific research question and a falsifiable or at least decision-relevant expectation.
- Do not aggregate campaign conclusions without per-run evidence.
- Do not bury null or contradictory findings.

## Use when

- writing reveals evidence gaps
- a main result needs ablations
- robustness or sensitivity needs to be checked
- a failure mode needs explanation
- efficiency or environment variation matters to the claim

## Do not use when

- the quest still lacks a credible main run or accepted baseline
- the next step is obviously another main experiment rather than follow-up evidence work

## Preconditions and gate

Before launching a campaign, confirm:

- the reference main run or accepted idea line
- the claim or question being tested
- the comparison target
- the metric or observable of interest
- the list of specific analysis questions

If the question list is fuzzy, sharpen it before running anything.

## Truth sources

Use:

- main experiment artifacts
- baseline artifacts
- recent decisions and milestone reports
- code and configs used in the accepted main line
- actual analysis outputs and logs
- `bash_exec` session ids and managed shell logs for campaign runs

Do not summarize a campaign from impressions alone.

## Required durable outputs

A campaign should usually leave behind:

- a campaign identifier
- one directory per analysis run
- one run artifact per analysis slice
- an aggregated campaign report
- a decision about the next move

In the current runtime, represent that with existing artifact actions only:

- one `decision` artifact with `action='launch_analysis_campaign'`
- one charter `report`
- one `run` artifact per slice
- optional `progress` artifacts during execution
- one aggregated `report`
- one closing `decision`

## Workflow

### 0. Launch the campaign durably

Before launching any slice, record the campaign start through artifacts:

1. write a `decision` artifact with:
   - `action='launch_analysis_campaign'`
   - `campaign_id`
   - `parent_run_id` or `parent_idea_id`
   - why the campaign is needed now
2. write a charter `report` with the planned slice list
3. update `plan.md` if the campaign materially changes the quest path

Do not start a multi-slice campaign from chat-only intent.

### 1. Define the campaign charter

State:

- campaign id
- parent run or parent idea
- main claim under test
- list of analysis questions
- what will be held fixed
- what may vary

The charter should also include:

- campaign type priority order
- expected slice count
- dependency structure between slices
- whether any slice requires isolated code changes or only reruns/config changes
- the top-level success condition for ending the campaign
- the top-level abandonment condition for stopping it early

For each analysis question, also state:

- why it matters to the main claim
- what result would strengthen the claim
- what result would weaken or complicate the claim
- whether the run is:
  - ablation
  - robustness
  - sensitivity
  - error analysis
  - efficiency
  - environment variation

If there are many possible slices, order them by decision value:

1. most claim-critical ablation or contradiction check
2. strongest robustness or sensitivity checks
3. failure-mode explanation
4. efficiency or secondary supporting analyses

Do not spend half the campaign budget on secondary slices before the claim-critical ones run.

### 2. Split into isolated analysis runs

Each analysis run should correspond to one need, such as:

- remove one component
- vary one hyperparameter family
- run additional seeds
- inspect one failure bucket
- test one environment variation

Avoid changing many factors at once unless the campaign is explicitly exploratory.

For each slice, define at minimum:

- research question
- hypothesis or expected pattern
- intervention
- controls or fixed conditions
- metric or observable
- stop condition
- evidence path expectations

Recommended extra per-slice fields:

- `slice_id`
- `run_kind`
- `parent_run_id`
- whether a code diff is required
- whether an isolated branch/worktree is required
- quantitative success criteria
- quantitative abandonment criteria
- contingency trigger for the next slice

Recommended `run_kind` naming in the current runtime:

- `analysis.ablation`
- `analysis.robustness`
- `analysis.sensitivity`
- `analysis.error`
- `analysis.efficiency`
- `analysis.environment`

Create the campaign with `artifact.create_analysis_campaign(...)` before starting any slice.
That tool should receive the full slice list, and each returned slice worktree becomes the required execution location for that slice.
Do not replace the normal campaign flow with repeated manual `artifact.prepare_branch(...)` calls.
After each slice finishes, call `artifact.record_analysis_slice(...)` immediately so the result is mirrored back to the parent branch and the next slice can be activated.

For slices that run longer than a quick smoke check:

- launch them with `bash_exec(mode='detach', ...)`
- monitor them with `bash_exec(mode='list')` and `bash_exec(mode='read', id=...)`
- use an explicit wait-and-check cadence of about `60s`, `120s`, `300s`, `600s`, `1800s`, then every `1800s` while still running
- if needed, use shell `sleep` between checks or an equivalent bounded `bash_exec(mode='await', id=..., timeout_seconds=...)`
- after the first meaningful signal and then at real checkpoints (e.g., completion, or roughly every ~30 minutes if still running), send `artifact.interact(kind='progress', ...)` so the user sees slice status, latest evidence, and the next check point
- stop them with `bash_exec(mode='kill', id=...)` if the slice is invalid, wedged, or superseded
- do not mark a slice complete until the managed log and outputs both confirm completion

### 3. Keep comparability

Comparability rules:

- keep the same evaluation contract unless the variation is the point
- state exactly what changed
- state exactly what stayed fixed
- keep naming and output paths clean so multiple runs can coexist

For code-modifying slices, the default durable layout should stay interpretable:

- working surface:
  - `.ds/worktrees/<slice_id>/` when isolated worktrees are used
- experiment surface:
  - `experiments/analysis/<campaign_id>/<slice_id>/`
- artifact surface:
  - `artifacts/runs/<artifact_id>.json`
  - `artifacts/reports/<artifact_id>.json`

If the variation itself changes the evaluation setup, record that explicitly and do not present the run as a direct apples-to-apples comparison.

### 4. Record each analysis slice

Before a long slice starts, emit a `progress` artifact or `artifact.interact(kind='progress', ...)` update so the quest shows that the slice is active.

For each run, record:

- analysis question
- intervention
- metric or qualitative evidence
- whether the result strengthens, weakens, or complicates the claim
- paths to the evidence

Preferred per-slice summary shape:

- question
- implementation change
- main metric delta
- interpretation
- caveats

Each completed slice should also leave a `run` artifact containing at least:

- `campaign_id`
- `slice_id`
- `run_kind`
- `parent_run_id`
- `analysis_question`
- `fixed_conditions`
- `changed_factors`
- `metrics_summary`
- `metric_deltas`
- `success_criteria`
- `abandonment_criteria`
- `verdict`
- `reason`
- `paths`

If a slice fails before producing evidence, still record it as a failed or partial `run` artifact rather than silently skipping it.

### 5. Aggregate the campaign

The campaign report should explain:

- which findings are stable
- which findings are fragile
- what changed the interpretation of the main result
- which open questions still remain

Campaign reporting rules:

- focus on the highest-impact findings first
- results matter more than process narration
- if using tables, show only the most decision-relevant rows
- separate:
  - stable support
  - partial support
  - contradiction
  - unresolved ambiguity

When there are many slices, summarize the top `3-5` most important ones first, then point to the full evidence paths.

The aggregated report should also answer:

- should the main claim be strengthened, weakened, narrowed, or abandoned?
- which slice changed the interpretation most?
- which slice is still worth rerunning, and why?
- which planned slices were intentionally skipped because earlier results made them low value?

### 6. Route the next step

A campaign should end with an explicit next move:

- continue the campaign
- return to `experiment`
- move to `write`
- stop or reset the current line

Record the post-campaign route as a `decision` artifact.
When helpful, include a reflection block with:

- `what_worked`
- `what_failed`
- `learned_constraints`

and a `next_direction` block that states:

- objective
- key steps
- success criteria
- abandonment criteria

This makes the next stage executable without guesswork.

## Analysis-quality rules

Good campaign behavior:

- one clear question per run
- one-factor-at-a-time changes when possible
- clear comparison against the accepted reference line
- visibility of null and negative findings
- a logically ordered suite rather than a random batch

Strong campaign ordering usually looks like:

1. most claim-critical ablation or comparison
2. strongest robustness or sensitivity checks
3. failure-mode or error analysis
4. efficiency or secondary analysis

The exact order can vary, but the most claim-relevant evidence should appear first.

Weak campaign behavior:

- hidden scope expansion
- many untracked simultaneous changes
- campaign summary without per-run evidence
- ignoring contradictory analysis results
- reporting every minor slice with equal weight instead of prioritizing the important ones

## Memory rules

Write to memory only when the campaign yields reusable lessons, such as:

- robust failure patterns
- evaluation caveats
- reproducible sensitivity findings

The campaign’s main record belongs in run artifacts and the aggregated report.

## Artifact rules

Typical artifact sequence:

- decision artifact to launch the campaign
- report artifact for the charter
- progress artifacts during long campaigns
- run artifacts per analysis slice
- report artifact for the aggregated campaign summary
- decision artifact for the next anchor

## Failure and blocked handling

Record blocked or failed campaign states explicitly, such as:

- missing parent run
- analysis question under-specified
- campaign run failed before evidence was produced
- metrics not comparable
- campaign conclusion still ambiguous

A blocked campaign should still name the next best action.

## Exit criteria

Exit the analysis-campaign stage once one of the following is durably true:

- the campaign produced enough evidence for writing or decision-making
- the campaign exposed a problem that requires returning to `experiment` or `idea`
- the campaign is blocked and the blocker is durably recorded
