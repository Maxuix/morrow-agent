# S7P-10 Stage 7 Entry Review

> Date: 2026-08-30
> Verdict: **GO** (upgraded after the bounded post-repair proof)
> Permitted scope: Stage 7 production Workflow work may begin under the frozen leaf boundaries

## Decision basis

The user explicitly accepts the existing reduced S7P-09 campaign as sufficient in run count and
declines a second repetition. The accepted immutable evidence set is the six-entry DeepSeek r19
prefix plus the eight-entry r20 continuation:

- 10 Morrow runs and four Pi 0.84.2 runs;
- identical Provider family, canonical model, task bytes, verifier and bounded capability policy;
- all 14 bundles structurally valid, with combined summary integrity
  `sha256:5f934893b7f2c616f5aa0f9d51e1b16e8dec32a11fdf145f35a4ee959c1dde8b`;
- Morrow: 3 `PASS`, 1 `FAIL_MODEL`, 2 `FAIL_RUNTIME`, 4 `BUDGET_EXHAUSTED`;
- Pi: 3 `FAIL_MODEL`, 1 `FAIL_RUNTIME`;
- 25,738,704 known tokens; three Morrow bundles retain unavailable total-token metrics and cost is
  unavailable rather than inferred as zero.

Accepting one repetition closes the S7P-09 campaign-size question. It does not create repeated or
statistical evidence, change any bundle, relabel a failure, or satisfy quality and metric thresholds
that the measured results did not meet.

The original review therefore issued CONDITIONAL GO and defined one bounded current-code
complex-run proof as its explicit upgrade path. The post-repair EXTERNAL-003 run completed normally
with complete request/tool accounting and an attributable `FAIL_MODEL` verifier result. That proof
closes the production leaf blocker without changing the historical campaign outcomes.

## Original hard-gate audit

| S7P-10 gate | Result | Evidence and reasoning |
|---|---|---|
| S7P-00 through S7P-09 complete with no P0 exception | **FAIL** | S7P-00 through S7P-08 passed. S7P-09 is closed on the user-accepted 14-run set, but Morrow did not meet the frozen 7/10, zero-runtime-failure or complete-usage gates. The second repetition is removed by the current user decision, not represented as performed. |
| `AgentLoop` remains a domain-neutral single-Agent leaf | **PASS** | `runtime/agent.py` owns one AgentRun's model/tool loop and contains no Workflow, NodeRun, Scheduler or multi-Agent dependency. Workflow feedback payloads remain Learning data only. |
| `ConversationLog` remains the sole chat-history grammar authority | **PASS** | `runtime/conversation.py` owns append validation; production writes are issued through `AgentLoop` and Session's durable commit wrapper. No Workflow writer exists. |
| TaskRun, AgentRun, Artifact, recovery, permission and event boundaries were not bypassed for evaluation | **PASS** | The evaluator used ordinary bootstrap, preparation, `AgentLoop`, `ToolExecutor`, TaskRun and ConversationLog composition. The current public-chain acceptance also exercises the same headless path. |
| Direct profile, tools, model, Skill, permission, context and budget can be frozen | **PASS** | `AgentRunSnapshot`/`PreparedAgentRunSpec` retain Provider/Model, run-policy and tool-schema digests, prompt/project-instruction evidence, Skills, MCP references and permission evidence; rehydration rejects drift. |
| Direct success, cost, rework and failure baseline is published | **FAIL** | Success, terminal classes, duration, tools, rework and interventions are published. Three total-token values and Provider cost remain unavailable, and the two historical `runtime_failed` terminals predate internal-source observation, so their exact source cannot be recovered from immutable evidence. |
| Basic Direct tasks can become a Direct Workflow leaf without changing loop/tool/completion semantics | **PASS** | Current composition already injects a frozen prepared runtime into the same `AgentLoop`; Stage 7 can wrap this leaf contract. The real current-provider chain completed text, continuation and a four-tool self-correcting task after the campaign commits. |
| Every unresolved item is proved non-blocking and routed forward | **FAIL at review time** | Model-quality failures and incomplete cost were acceptable comparison limitations, but the unclassified complex-run runtime terminals and four budget exhaustions had not yet been proved non-blocking for production Workflow migration. The targeted proof below now closes this narrow blocker. |

## Why the original verdict was conditional

The evidence is strong enough to begin Stage 7 design work without redesigning the leaf interface:
the offline 18-cell matrix passes, current AgentRun snapshots are rehydratable, and a later real
Provider journey completed conversation continuation plus four successful tool calls.

It is not strong enough for production Workflow migration. After r19/r20, current `main` repaired
long-horizon context selection, removed the misleading v1 repeated-cycle mode, and converted an
unrepresentable durable tool intent into a bounded `invalid_response` instead of an unexpected
runtime abort. It also permits safe code-search previews containing identifiers such as
`credential` or `api_key` while still rejecting value-shaped secrets. Those changes plausibly cover
some historical failure paths, but the immutable runtime failures contain only the public
`runtime_failed` code. Without an attributable terminal source or a current complex-run proof,
claiming that both blockers are closed would be inference rather than evidence.

Subplan 99 then unified the model failure chain and preserved safe Provider/Adapter/Runtime origin.
The authorized rerun reached a normal AgentRun terminal after nine completed model requests and nine
fully settled tool calls. Its failed verifier is mechanically `FAIL_MODEL`, not `FAIL_RUNTIME`; no
basic-tool blocker or unexpected workspace path occurred. This is the exact proof branch the review
declared sufficient, so the current verdict is GO.

## Permitted next work

GO permits:

- finalize Stage 7 domain boundaries and versioned schemas in documentation;
- design `AgentDefinition`, `WorkflowDefinition`, `WorkflowRevision`, `WorkflowRun`, `NodeRun` and
  Artifact contracts;
- build the production Workflow compiler/store/execution path around the existing frozen Direct
  AgentRun leaf;
- add Scheduler wiring and Direct-task migration incrementally with their own acceptance gates.

It does not permit:

- changing `AgentLoop`, tool execution or completion semantics for Workflow;
- presenting the 14-run evidence as repeated/statistical or as 7/10 quality;
- making Workflow the default without separate migration and product acceptance evidence.

## GO upgrade condition

Before production Direct Workflow migration, provide one bounded current-code complex-run proof
that either completes or produces an attributable non-runtime terminal while preserving complete
tool terminal accounting. This is a targeted admission check, not a requirement to repeat all 14
runs. If it exposes a leaf runtime defect, repair that defect and rerun only the affected proof.
Unavailable Provider cost may remain explicit provided Workflow budgets aggregate tokens and mark
cost unavailable rather than treating it as zero.

### Upgrade attempts

The authorized current-code EXTERNAL-003 proof did not meet this condition. Four tool calls were
fully accounted and succeeded, but the third model request ended with an internal model-call error,
no workspace change and verifier exit 1. Safe evidence cannot distinguish Provider-origin internal
failure from an unattributed Adapter exception. It retained CONDITIONAL GO.

After the failure-chain repair, the user authorized exactly one rerun. It completed in 65.385
seconds, changed only `react.py`, settled eight successful and one failed/recovered tool calls, and
finished all nine model requests without retry or runtime error. The frozen verifier reported four
failures, producing an attributable `FAIL_MODEL` result. The upgrade condition is met; see
[`s7p-10-go-upgrade-proof.md`](s7p-10-go-upgrade-proof.md).

## Evidence references

- `docs/acceptance/s7p-09-repeated-direct-pi-baseline.md`
- `docs/acceptance/s7p-08-single-agent-function-matrix.md`
- `docs/acceptance/current-chain-feasibility.md`
- `.agent/archive/subplans/legacy-sequence-36-100/90-s7p-09-repeated-direct-pi-baseline.md`
- `.agent/archive/subplans/legacy-sequence-36-100/97-s7p-10-stage7-entry-review.md`

## Validation

| Gate | Result |
|---|---|
| S7P-08 single-Agent acceptance matrix | 3 passed in 1.99 s |
| AgentRun preparation, ConversationLog and guardrail focus | 44 passed in 20.71 s |
| Complete offline suite | 1,284 passed, 2 Live tests deselected in 105.33 s |
| Ruff format/check | 484 files formatted; all checks passed |
| `compileall`, CLI help and `git diff --check` | passed |
| Post-repair EXTERNAL-003 proof | AgentRun completed; 9/9 tools settled; verifier `4 failed`; `FAIL_MODEL`; GO condition met |
| Post-repair focused / offline gates | 101 focused passed; 1,285 offline passed, 2 Live deselected |

The original review made no Live request. Its later upgrade proof made exactly one explicitly
authorized Provider-backed EXTERNAL-003 run; no Pi, MCP, probe or campaign repetition was run.
