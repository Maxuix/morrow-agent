# Stage 7 Static Workflow Runtime Acceptance

> Decision: **PASS (offline engineering)**
> Scope: deterministic Scripted Providers, injected clocks/faults, current packaged definitions,
> Operational Store, Artifact store, application/query services and CLI.
> Live status: **not run**; no exact real-Provider campaign or compatible credential was authorized.

## Result

Stage 7 delivers one foreground, serial Workflow Scheduler over the existing AgentLoop leaf. It
supports immutable AgentDefinition versions and Workflow revisions, isolated multi-node DAGs,
typed Artifact handoff, an opt-in invoking-session Direct adapter, recovery/cancellation/revocation,
bounded management queries and four explicitly publishable built-in templates. Ordinary Direct
remains runnable and is still the default.

The closeout entrance is `tests/test_stage7_acceptance.py`. Its matrix freezes 28 owner-test
selectors spanning the contracts and execution paths below; its executable comparisons use only
Scripted Providers. The complete Stage 7 matrix passed **220 tests**. The final repository-wide
gate is recorded in the closeout section after it is run.

## Four phase gates

| Gate | Delivered truth | Representative evidence | Decision |
|---|---|---|---|
| 7A Contracts | Pure validate, explicit idempotent publication, immutable exact refs, OCC, tool-requirement precedence, single connected DAG and profile-aware durable contracts | `test_stage7_agent_definitions.py`, `test_stage7_workflow_domain.py`, `test_stage7_workflow_store.py`, `test_stage7_workflow_compiler.py` | PASS |
| 7B Serial execution | One Scheduler for one-node and multi-node isolated runs; stable topological order, serial admission, aggregate request budget, frozen deadline, cancellation and crash/blocked recovery without rerunning completed nodes | `test_stage7_isolated_workflow_slice.py`, `test_stage7_serial_scheduler.py` | PASS |
| 7C Multi-Agent semantics | Explorer → Coder → Reviewer uses typed durable submissions and Artifact-only handoff; Writer capture is durable; missing output fails as `output_contract_unsatisfied`; blocking review completes as `needs_revision` | `test_stage7_multi_agent_pipeline.py` | PASS |
| 7D Productization | Opt-in invoking-session Direct parity, management/query boundary, complete CLI polling, four packaged sources, backup/doctor coverage | `test_stage7_direct_adapter.py`, `test_stage7_workflow_management.py`, `test_stage7_workflow_cli.py` | PASS |

The public `ApplicationEvent` lifecycle was not changed because it was not separately authorized.
CLI/query polling is the complete Stage 7 observation path; Stage 8 must obtain contract authority
before adding an event stream.

## Invariant roll-up

- Session-owned `ConversationLog` remains the chat-history authority. Workflow code does not append
  transcript messages; every leaf still runs through `AgentLoop.run_task()` and the existing
  ToolExecutor path.
- A WorkflowRun freezes one exact immutable revision. Agent versions, model refs, effective tools,
  permissions and per-node request caps are frozen at their defined boundaries; source/head edits
  do not drift an admitted run.
- Inputs and node results move through typed, immutable Artifacts. A structured result becomes
  authoritative only after a durable `submit_node_result` submission validates against the frozen
  output contract; final assistant prose is never parsed as structured data.
- Required/optional/forbidden tool declarations merge with deny-first precedence and cannot expand
  the task policy or access-mode ceiling. Stage 7 admits only one node at a time, so Writer nodes
  are serialized within a managed WorkflowRun.
- Ordinary disable gates new admission only. Exact emergency revocation is additive, audited and
  one-way; it closes an affected admitted run through `cancelled(reason=policy_revoked)` at the next
  admission/recovery boundary.
- Root READY follows durable required-output publication and binding. Acceptance inherits only the
  latest matching READY transition and Workflow snapshot; older Workflow or ordinary-Direct
  evidence is rejected.
- Workflow root output uses the shared value-sensitive safety profile. Benign names such as
  `password_validation.py` remain legal; credential-shaped values are omitted/redacted and add
  `workflow_evidence_redacted=true` without preventing terminal closure.
- A malformed or unavailable definition/run remains local to that definition/run. Ordinary Direct
  and unrelated legal definitions remain operable.

The sole-writer review found no second chat store, Artifact byte store, Scheduler, publication
path, or Workflow business state machine in AgentLoop. CLI construction opens concrete stores at
the composition edge, but commands delegate mutations and projections to application services;
it does not implement SQL/YAML state transitions.

## Deterministic Direct comparison

The paired closeout case sends the same bounded objective to ordinary Direct and the opt-in
one-node Direct Workflow with one Scripted Provider response each.

| Fact | Ordinary Direct | Direct Workflow |
|---|---:|---:|
| Task/verifier outcome | completed | `completed/succeeded` |
| User-visible rework | none | none |
| Reviewer findings | not applicable | not applicable |
| Primary generation admissions | 1 observed Provider call | 1 durable `purpose=agent` admission |
| Compaction | none observed | excluded: no compaction admission seam |
| Usage/cost | unavailable | unavailable |
| Elapsed time | not recorded; deterministic test avoids wall-clock claims | not recorded; deterministic test avoids wall-clock claims |

This evidence proves adapter parity and runtime accounting, not model-quality superiority. The
multi-node fixtures prove role and contract behavior but use scripted outputs, so they cannot show
that a template reduces real-user rework. No template is promoted or selected automatically;
ordinary Direct remains the default and every Workflow/template remains explicit opt-in preview.

## Late-failure rerun cost

The fixed whole-graph failure mapping is measured explicitly. In a two-node graph, the upstream
node succeeded once and the downstream node exhausted the existing three-attempt Provider retry
chain: the failed WorkflowRun recorded **4** primary admissions. After the user explicitly reopened
the root, a new WorkflowRun executed both nodes from attempt 1 and recorded **2** more admissions.
The scenario therefore costs **6** admissions in total and repeats one already-successful upstream
request. The failed run is immutable and never resumes in place.

This is correct Stage 7 behavior, but it makes child-run continuation—retrying failed work without
re-executing completed nodes—the highest-priority Stage 8 orchestration follow-up. Bounded
read-only parallelism follows only after its separate rate-limit, per-request claim,
process/cwd/env-isolation and result-visibility entry gates pass.

## Deferred and unavailable evidence

- No Live/real-Provider comparison was run. Cost, token usage and elapsed-quality comparisons are
  unavailable rather than treated as zero or as a failed engineering gate.
- Automatic workflow selection, GraphPlanner, GraphPatch/Replanner, GUI, runtime graph editing,
  background execution and every form of concurrent admission remain outside Stage 7.
- No dependency, bundled runtime-policy default or public event lifecycle changed during Stage 7.

## Validation

Closeout-focused results:

```text
uv run pytest -q tests/test_stage7_acceptance.py  -> 3 passed
uv run pytest -q tests/test_stage7_*.py           -> 220 passed
```

Final closeout results:

```text
uv run pytest -m 'not live'              -> 1523 passed, 2 Live deselected
uv run ruff format --check .             -> 558 files already formatted
uv run ruff check .                      -> passed
uv run python -m compileall -q src tests -> passed
uv run morrow --help                     -> passed
uv run morrow agent --help               -> passed
uv run morrow workflow --help            -> passed
git diff --check                         -> passed
```
