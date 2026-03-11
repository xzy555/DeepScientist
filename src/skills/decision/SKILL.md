---
name: decision
description: Use when the quest needs an explicit go, stop, branch, reuse-baseline, write, finalize, reset, or user-decision transition with reasons and evidence.
---

# Decision

Use this skill whenever continuation is non-trivial.

## Interaction discipline

- Treat `artifact.interact(...)` as the main long-lived communication thread across TUI, web, and bound connectors.
- If `artifact.interact(...)` returns queued user requirements, treat them as the latest user instruction bundle before making the next decision.
- Emit `artifact.interact(kind='progress', reply_mode='threaded', ...)` when the decision analysis spans multiple concrete steps.
- Message templates are references only. Adapt to context and vary wording so updates feel respectful, human, and non-robotic.
- Each progress update must state completed reasoning or evidence gathering, the durable output touched, and the immediate next step.
- Use `reply_mode='blocking'` for the actual decision request when the user must choose before safe continuation.
- For any blocking decision request, provide 1 to 3 concrete options, put the recommended option first, explain each option's actual content plus pros and cons, wait up to 1 day when feasible, then choose the best option yourself and notify the user of the chosen option if the timeout expires.
- If a threaded user reply arrives, interpret it relative to the latest decision or progress interaction before assuming the task changed completely.

## Stage purpose

`decision` is not a normal anchor.
It is a cross-cutting control skill that should be used whenever the quest must decide:

- whether to continue
- whether to branch
- whether to attach or reuse a baseline
- whether to launch an experiment
- whether to launch an analysis campaign
- whether to move to writing
- whether to finalize
- whether to reset
- whether to stop
- whether to ask the user for a structured decision

## Use when

- the next stage is not obvious
- the evidence is mixed
- the current line may need to stop
- the quest needs a branch or reset
- a user preference-sensitive choice remains
- a blocker needs an explicit route

## Required decision record

Every consequential decision should make clear:

- verdict
- action
- reason
- evidence paths
- next stage or next direction

## Recommended verdicts

- `good`
- `bad`
- `neutral`
- `blocked`

## Allowed actions

Use the following canonical actions:

- `continue`
- `launch_experiment`
- `launch_analysis_campaign`
- `branch`
- `prepare_branch`
- `reuse_baseline`
- `attach_baseline`
- `publish_baseline`
- `write`
- `finalize`
- `reset`
- `stop`
- `request_user_decision`

Choose the smallest action that genuinely resolves the current state.

In the current runtime, prefer these concrete flow actions:

- accepted idea -> `artifact.submit_idea(mode='create', ...)`
- revise active idea -> `artifact.submit_idea(mode='revise', ...)`
- launch analysis campaign -> `artifact.create_analysis_campaign(...)`
- finish one analysis slice -> `artifact.record_analysis_slice(...)`

Treat `prepare_branch` as a compatibility or recovery action, not the normal path.

## Truth sources

Make decisions from durable evidence:

- recent run artifacts
- report artifacts
- baseline state
- quest documents
- memory only as supporting context

Do not make major decisions from vibe or momentum.

## Workflow

### 1. State the question

Write the real question explicitly, such as:

- is the current idea promising enough to continue?
- is baseline reuse sufficient?
- is more analysis needed before writing?
- is the draft good enough to finalize?

### 2. Collect the evidence

Summarize only the decision-relevant evidence:

- strongest support
- strongest contradiction
- missing dependency
- known cost or risk

### 3. Choose verdict and action

Typical mapping:

- `good`
  - continue, branch, launch experiment, write, finalize
- `neutral`
  - branch, launch analysis campaign, request user decision
- `bad`
  - reset, stop
- `blocked`
  - reuse baseline, attach baseline, request user decision, stop

The action must match the actual state.

### 3.1 Selection among candidate packages

When the decision is about choosing among multiple candidate outputs, such as:

- experiment groups
- idea branches
- outline drafts
- revision candidates
- competing reports

do not decide implicitly.

Record:

- candidate ids or names
- the explicit selection criteria
- the winner
- why the winner is preferred
- why the main alternatives were not chosen

When the choice is about an experiment package or analysis package, also record:

- implementation priority order
- what you expect to learn from the chosen package

Typical criteria include:

- evidence quality
- feasibility
- comparability
- expected information gain
- narrative coherence
- downstream usefulness

If evaluator scores exist, use them.
Do not blindly follow a score if the underlying evidence is weak; explain the override when needed.

### 3.2 Research-route selection heuristic

When the decision is about choosing a research direction, experiment route, or branch to invest in:

- identify the core insufficiency being targeted
- prefer routes that address that insufficiency elegantly rather than only spending more compute, more stages, or more complexity
- prefer routes that respect the current codebase architecture unless there is strong evidence that a deeper break is justified
- balance breakthrough potential against implementation risk and verification cost

Good route-selection criteria often include:

- feasibility
- scientific importance
- methodological rigor
- expected information gain
- architectural fit
- complexity risk
- downstream narrative value

When selecting an experiment package, make the choice as if you must later justify:

- why this package is the best balance of implementability and scientific value
- what order the experiments should be implemented in
- what concrete learning each step is expected to produce

If one option is more novel but much less testable, say that explicitly instead of hiding the tradeoff.

### 4. State the reason

The reason should be concrete and evidence-backed.
Avoid generic wording like “seems better”.

When the decision is stage-shaping, prefer a richer structure that later stages can execute directly.
Useful optional fields include:

- `target_idea_id`
- `target_run_id`
- `campaign_id`
- `reflection`
  - `what_worked`
  - `what_failed`
  - `learned_constraints`
- `next_direction`
  - objective
  - key steps
  - success criteria
  - abandonment criteria
- `expected_roi`
  - `cost_estimate`
  - `confidence`
  - qualitative improvement estimate with justification

This is especially useful for:

- idea branch selection
- experiment package selection
- launch of an analysis campaign
- post-campaign routing
- stop / pivot / finalize choices

### 5. Request user input only when needed

Ask the user only when:

- multiple options are all plausible
- the choice depends on preference, cost, or scope
- the missing information cannot be derived locally

When asking, use a structured decision request with:

- concise question
- 1 to 3 concrete options
- tradeoffs, including the main pros and cons for each option
- recommended option first
- explicit reply format
- a stated timeout window; normally wait up to 1 day before self-resolving if no user reply arrives

### 6. Record the decision durably

Use `artifact.record(kind='decision', ...)` for the final decision.

If user input is needed, also use `artifact.interact(kind='decision_request', ...)`.
If the timeout expires without a user reply, choose the best option yourself, record why, and notify the user of the chosen option before moving on.

## Decision-quality rules

Good decisions:

- are evidence-backed
- name tradeoffs
- say what happens next
- say why the alternative was not chosen
- explicitly identify the winning candidate when choosing among multiple packages

Weak decisions:

- hide uncertainty
- lack evidence paths
- give vague approvals
- pretend blocked states are progress
- choose a winner without naming the rejected alternatives or criteria

## Memory rules

Write to memory only when the lesson is reusable across future decisions, such as:

- a recurring failure pattern
- a reliable stop condition
- a useful branching heuristic

The canonical record of the decision itself belongs in `artifact`.

## Exit criteria

Exit once the decision is durably recorded and the next stage or action is explicit.
