---
name: experiment
description: Use when a quest is ready for a concrete implementation pass or a main experiment run tied to a selected idea and an accepted baseline.
---

# Experiment

Use this skill for the main evidence-producing runs of the quest.

## Interaction discipline

- Treat `artifact.interact(...)` as the main long-lived communication thread across TUI, web, and bound connectors.
- If `artifact.interact(...)` returns queued user requirements, treat them as the latest user instruction bundle before continuing the run plan.
- Emit `artifact.interact(kind='progress', reply_mode='threaded', ...)` only at real checkpoints, and normally no more frequently than every 5 to 15 tool calls.
- Each progress update must state completed work, the durable output touched, and the immediate next run step.
- Progress message templates are references only. Adapt to the actual context and vary wording so messages feel human, respectful, and non-robotic.
- Use `reply_mode='blocking'` only for real user decisions that cannot be resolved from local evidence.
- For any blocking decision request, provide 1 to 3 concrete options, put the recommended option first, explain each option's actual content plus pros and cons, wait up to 1 day when feasible, then choose the best option yourself and notify the user of the chosen option if the timeout expires.
- If a threaded user reply arrives, interpret it relative to the latest experiment progress update before assuming the task changed completely.
- Prefer `bash_exec` for experiment commands so each run gets a durable session id, quest-local log folder, and later `read/list/kill` control.

## Stage purpose

The experiment stage should turn a selected idea into auditable evidence.
It should preserve the strongest old experiment-planning and execution discipline:

- define the run contract before execution
- keep the run comparable to baseline
- capture configs, commands, logs, and metrics
- report both success and failure honestly
- route the next action through an explicit decision

The experiment stage is not just "run code".
It is the stage that converts an idea contract into evidence that other stages can trust.

## Non-negotiable rules

- Do not fabricate metrics, logs, claims, or improvement narratives.
- Do not introduce a new dataset or silently change splits or evaluation protocol.
- Do not change metric definitions or evaluation logic unless the change is explicitly justified and durably recorded.
- Do not stop after a quick sanity run if the agreed goal is a real experiment.
- Do not claim success before durable artifacts exist and the acceptance gate passes.
- Implement the claimed mechanism, not a convenient shortcut that changes the theory.
- Keep the baseline reference read-only.
- Avoid asking the user to fix the environment unless there is no credible agent-side path left.

## Use when

- a baseline is accepted
- an idea has been selected
- the evaluation contract is explicit
- the quest is ready for implementation and measurement

## Do not use when

- the baseline gate is unresolved
- the idea stage still has unresolved tradeoffs
- the main need is writing or follow-up analysis rather than a main run

## Preconditions and gate

Before a main run starts, confirm:

- selected idea or hypothesis
- baseline reference
- dataset and split
- primary metric
- stop condition
- resource budget
- target branch or isolated worktree when needed
- exact output location
- required metric keys for acceptance
- minimal experiment and abandonment condition from the idea stage

If any of these are materially unknown, stop and resolve them through `decision`.

## Working-boundary rules

Only modify the active quest workspace for this experiment line.

- treat the accepted baseline workspace as read-only
- do not derive branch or worktree assumptions from guesswork
- keep all durable outputs inside the quest
- if the runtime gives an explicit worktree path, use it exactly

## Resource and environment rules

- Follow the explicit resource assignment if one exists.
- If GPU assignment is explicit, respect it exactly and record it in the run manifest.
- Do not silently consume extra GPUs or broaden resource scope.
- Capture enough environment information that the run can later be reconstructed.
- If a new dependency appears necessary, record it as a risk and prefer a fallback if possible.

## Truth sources

Use:

- idea-stage outputs
- baseline artifacts
- current codebase and configs
- recent decisions
- task and metric contract
- shell logs and generated outputs from the actual run
- `bash_exec` session ids, progress markers, and exported logs from the actual run
- the selected idea handoff contract
- incident or failure-pattern memory from earlier runs

Do not claim run success without durable outputs.

## Required durable outputs

A meaningful experiment pass should leave behind:

- a run directory under `artifacts/experiment/<run_id>/` or the quest-equivalent canonical location
- `artifact_manifest.json`
- `run_manifest.json`
- `metrics.json`
- `metrics.md`
- `summary.md`
- `runlog.summary.md`
- durable command, config, and log pointers
- exported shell log, typically `bash.log`
- a run artifact with explicit deltas versus baseline
- a decision about what should happen next

Recommended additional files:

- `claim_validation.md`
- environment snapshot files such as:
  - Python version
  - package freeze
  - GPU info when applicable

`run_manifest.json` should capture at least:

- `run_id`
- quest / branch context
- baseline reference or commit
- full commands
- config paths and key resolved hyperparameters
- dataset identifier or version
- seeds
- environment snapshot paths
- start time, end time, and final status

If a command needed for environment capture is unavailable, record that gap in the manifest and summary.

## Workflow

### 1. Define the run contract

Before implementation or execution, state:

- `run_id`
- hypothesis
- baseline id or variant
- metric targets
- expected changed files
- expected outputs
- stop condition
- compute or runtime budget
- minimal experiment
- abandonment condition
- strongest alternative hypothesis
- exact metric keys that will decide success or failure

If the run contract changes materially later, record the change durably.

If multiple candidate experiment packages exist, prefer the one with the best balance of:

- technical feasibility
- research importance
- methodological rigor

Do not choose a package only because it sounds ambitious.

### 2. Run a preflight check

Before editing or executing:

- confirm the dataset path, version, and split contract
- confirm the baseline metrics reference
- confirm the selected idea claim and code-level plan
- look up prior incidents or repeated failure patterns when available
- confirm output directories and naming
- confirm that the intended run still matches the current quest decision

If a repeated failure pattern already exists, apply the mitigation first and record that choice.

Also confirm before comparison work:

- the baseline verification is trustworthy enough
- the planned comparison still uses the same metric contract

### 3. Confirm the execution workspace

The normal experiment workspace is the current active idea worktree returned by `artifact.submit_idea(...)`.

- do not create a fresh manual branch for the main experiment unless recovery or debugging truly requires it
- implement and run inside the current active idea workspace
- if the idea itself changed materially before execution, revise it first with `artifact.submit_idea(mode='revise', ...)`
- after a real main run finishes, record it with `artifact.record_main_experiment(...)` before moving to analysis or writing

### 4. Implement the minimum required change

Implementation rules:

- keep the change hypothesis-bound
- prefer small, explainable edits
- avoid unrelated cleanup during a main run
- record which files matter for later review
- preserve theory fidelity between the idea claim and the code change
- add robustness checks when the mechanism risks NaN, inf, or unstable behavior

Prefer to complete one experiment cleanly before expanding to the next, unless parallel execution is explicitly justified and isolated.

### 5. Execute the run

Run with auditable commands and durable outputs.

Execution rules:

- use non-interactive commands
- prefer `bash_exec` instead of ephemeral shell invocations
- use the intended dataset and split
- keep logs durable
- report progress for long runs
- avoid silent metric-definition changes
- avoid silently changing the baseline comparison recipe
- run the full agreed evaluation, not only a smoke test

You may do a quick sanity run first, but if the stage goal is a real experiment you must continue to the real evaluation unless the run is blocked and recorded.

Pilot-before-scale rule:

- start with a bounded pilot when the modification is non-trivial
- use the pilot to catch implementation mistakes early
- record pilot outcomes explicitly
- do not mistake pilot success for final evidence

### 5.1 Long-running command protocol

For commands that may run longer than a few minutes:

- launch with `bash_exec(mode='detach', ...)`
- monitor through durable logs rather than only live terminal output
- use `bash_exec(mode='list')` and `bash_exec(mode='read', id=...)` to monitor or revisit managed commands
- use an explicit wait-and-check loop such as:
  - wait about `60s`, then inspect logs
  - wait about `120s`, then inspect logs
  - wait about `300s`, then inspect logs
  - wait about `600s`, then inspect logs
  - wait about `1800s`, then inspect logs
  - then keep checking about every `1800s` while the run is still active
- if needed, use shell `sleep` between checks or an equivalent bounded `bash_exec(mode='await', id=..., timeout_seconds=...)`
- after the first meaningful signal and then at real checkpoints (e.g., completion, or roughly every ~30 minutes if still running), send `artifact.interact(kind='progress', ...)` with the latest real status and next check point
- do not report completion until logs and output files both confirm completion

Always preserve the managed `bash_exec` log and export it into the experiment artifact directory when the run artifact is written.

### 5.2 Progress marker protocol

Long loops should emit structured progress markers rather than noisy raw progress bars.

- use single-line JSON progress markers
- keep them throttled
- treat them as UI signals, not narrative prose
- do not paste raw progress lines into summaries

If the codebase uses `tqdm` or similar tooling, disable or redirect the native bar and emit concise structured progress instead.

### 6. Validate the outputs

After the run, verify:

- outputs correspond to the intended code/config
- metrics are complete and interpretable
- comparison to baseline is fair
- any failure mode or confounder is visible
- required metric keys are present and finite
- the result can be mapped back to the original claim
- the summary states a clear go or no-go recommendation

Create a durable claim-validation record that maps:

- claim
- metric key
- expected direction
- observed result
- verdict:
  - `supported`
  - `refuted`
  - `inconclusive`

Also verify baseline comparability before claiming deltas:

- was the baseline verification stable?
- was the evaluation path the same?
- are the compared metric keys identical?
- do known caveats make the delta weaker than it first appears?

### 7. Record the run

Every meaningful main run must be recorded through `artifact.record_main_experiment(...)`.

That call is responsible for writing:

- `experiments/main/<run_id>/RUN.md`
- `experiments/main/<run_id>/RESULT.json`
- the durable `run` artifact payload
- baseline comparisons
- breakthrough status derived by the system

`artifact.record_main_experiment(...)` should include at least:

- `run_id`
- title
- hypothesis
- setup
- execution
- results
- conclusion
- baseline reference
- `metrics_summary`
- `metric_rows` when available
- the metric contract actually used
- verdict
- evidence paths
- changed files
- relevant config paths when applicable

Do not treat a main run as durably complete until `artifact.record_main_experiment(...)` succeeds.

Recommended per-run documentation fields:

1. research question
2. research type
3. research objective
4. experimental setup
5. experimental results
6. experimental analysis
7. experimental conclusions

`RUN.md` should make it easy for later stages to answer:

- what changed?
- how can this run be reproduced?
- what are the main results?
- why did it work or fail?
- what should happen next?

When the run is analysis-heavy or meant to fill a writing evidence gap, prefer a structured summary with:

1. research question
2. research type
3. objective and success criteria
4. setup
5. results
6. analysis
7. conclusion

Recording rules:

- record results incrementally, not only at the end
- include timestamps when helpful
- include failed attempts, partial runs, and unexpected outcomes
- do not leave placeholder sections for later if the information is already known
- report exactly what happened, not what you hoped would happen

### 8. Decide the next move

The experiment stage should normally end with one of:

- continue the current line
- branch a new line
- launch an analysis campaign
- move to writing
- reset or stop

Do not let the stage end without an explicit next direction.

## Run-quality rules

A credible main run should satisfy:

- comparable against baseline
- method change is knowable from code and config
- metric source is durable
- outcome can be explained by the intended intervention or its failure
- commands, configs, and seeds are reconstructable
- environment context is reconstructable
- frontend or later readers can trace code and diff context to command, logs, and metrics

If the result is confounded, say so directly.

## Acceptance gate

Before marking the run complete, verify all of the following:

- all required baseline metric keys are present
- metric values are finite numbers
- claim-to-metric traceability is recorded
- run manifest includes exact command, config, seed, and environment snapshot
- the summary states go or no-go and why
- artifacts are sufficient for another stage to reconstruct the run

If these checks fail, record the run as partial or blocked rather than pretending it is complete.

## Memory rules

Write to memory only when the lesson is reusable, such as:

- experiment failure patterns
- stable implementation lessons
- evaluation pitfalls
- validated mechanism scope and caveats

The canonical record of the run itself belongs in `artifact`, not only in memory.

Preferred memory usage:

- quest `ideas`:
  - the current idea contract and claim boundary
- quest `decisions`:
  - run-scope choices
  - retry or branch decisions
  - stop conditions that must not drift
- quest `episodes`:
  - failed runs
  - debugging episodes
  - suspicious-result investigations
  - repeated infrastructure or resource failures
- quest `knowledge`:
  - validated mechanism scope
  - evaluation caveats
  - stable implementation lessons worth reusing in later runs of this quest
- global `knowledge`:
  - reusable debugging heuristics
  - stable reproducibility lessons
  - cross-quest experiment design playbooks
- global `templates`:
  - run-manifest patterns
  - claim-validation templates
  - experiment summary templates

Use tags to refine retrieval when helpful, for example:

- `stage:experiment`
- `type:failure-pattern`
- `type:metric-contract`
- `type:claim-validation`
- `topic:<mechanism>`

Recommended read timing:

- before the first run:
  - consult quest `ideas`, `decisions`, and relevant `knowledge`
- before a retry:
  - search quest `episodes` first
- before changing execution strategy materially:
  - re-check quest `decisions`
- after suspicious results:
  - consult recent `episodes` and stable debugging `knowledge`

At stage end:

- successful runs should leave at least one reusable knowledge note if the lesson generalizes
- failed or partial runs should leave an incident note when the failure pattern is reusable

## Artifact rules

Typical artifact sequence:

- progress artifact for long runs
- `artifact.record_main_experiment(...)` at main-run completion
- milestone or report artifact for major findings
- decision artifact to choose next stage

Preferred artifact choices:

- use `progress` for long-running execution updates
- use `artifact.record_main_experiment(...)` for each meaningful completed main experiment
- use `run` for analysis slice records when `artifact.record_analysis_slice(...)` writes them
- use `report` for:
  - analysis-rich summaries
  - suspicious-result investigations
  - post-run interpretation
- use `milestone` when a major stage checkpoint is reached
- use `decision` for:
  - continue
  - branch
  - analysis
  - write
  - reset
  - stop
- use `approval` when an explicit user approval is captured for an expensive or risky run change

Use `artifact.checkpoint(...)` when code evolution is meaningful and should be preserved in Git.
After a meaningful experiment checkpoint or completion, emit `artifact.interact(kind='progress' | 'milestone', ...)` so the user sees the concrete result and next step.

## Failure and blocked handling

A failed main run is still useful if it is explained well.

Record:

- what was attempted
- where the failure occurred
- whether the failure is likely methodological or infrastructural
- what retry, branch, or reset is justified
- the single best next action

Prefer a primary failure type such as:

- `data_contract_mismatch`
- `resource_exhausted`
- `numeric_instability`
- `implementation_bug`
- `evaluation_pipeline_failure`
- `external_dependency_blocked`

Blocked experiment states commonly include:

- missing baseline reference
- unknown metric contract
- environment failure
- run failed before producing metrics
- metrics produced but not comparable

When results are suspicious, such as identical to baseline, implausibly perfect, or inconsistent across repeats, diagnose systematically:

1. fix the subset and seeds
2. isolate preprocessing, tokenization, model init, training, and evaluation one by one
3. compare intermediate outputs on the same inputs
4. align inputs first, then outputs, then metrics

## Exit criteria

Exit the experiment stage once one of the following is durably true:

- a main run is completed and recorded
- the run failed and the blocker is durably recorded
- the next step is clearly `analysis-campaign`, `write`, another `experiment`, or `reset`
