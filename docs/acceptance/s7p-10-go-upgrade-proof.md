# S7P-10 Targeted GO Upgrade Proof

> Date: 2026-08-30
> Result: **UPGRADE CONDITION NOT MET — CONDITIONAL GO remains**
> Task: EXTERNAL-003, difficult, one current-code Morrow run

## Scope

The user authorized the bounded proof required by the S7P-10 CONDITIONAL GO decision. This was not
a repeat of the accepted 14-run S7P-09 campaign and did not run Pi. The proof used:

- source `ff20a4a` (product behavior is local `main@59789bc`; the child commit adds execution-state
  documentation only);
- a fresh Git baseline for EXTERNAL-003 in a mode-0700 temporary root;
- an isolated Morrow state containing only the matching
  `opencode-go/deepseek-v4-flash` configuration and CredentialRef;
- ordinary bootstrap, AgentRun preparation, `AgentLoop`, `ToolExecutor`, durable observations and
  the evaluator ApprovalPort;
- the ordinary v2 RunPolicy and a 1,800-second external process alarm.

No credential value, raw model stream, reasoning, complete tool argument/result or traceback was
read, printed or retained in Git.

## Result

The AgentRun process returned normally after 98.603 seconds and created normalized evidence with
hash `sha256:e0c8f922f93e329871b6af71ca695ba756e7745135623916ff6a614ec2ef7a74`.

| Fact | Result |
|---|---|
| Model attempts | 3 |
| Usage | 15,349 input; 6,347 output; 21,696 total tokens; cost unavailable |
| Tool calls | 4 total; 4 succeeded; 0 failed/denied/cancelled/blocked |
| Tool accounting | 0 invalid arguments; 0 unaccounted calls; 0 basic-tool blockers |
| Tool sequence | two read-only rounds: `ls` + `read`, then `find` + `read` |
| Workspace | clean; no changed or unexpected path |
| Frozen verifier | exit 1 |
| Normalized terminal | `runtime_failed` |

Durable product observations provide the finer boundary that the evaluator's frozen normalized
projection omits:

1. Request 1 completed with `tool_calls` and 4,831 tokens.
2. Request 2 completed with `tool_calls` and 5,050 tokens.
3. Request 3 failed with model-request `error_code=internal` and 11,815 tokens.
4. AgentRun finished with `finish_reason=error`, `stop_code=internal` and no unexpected AgentLoop
   `stop_detail` phase.

This proves permission, tool-contract and terminal-accounting paths remained intact. It does not
prove the production leaf is ready: the safe evidence cannot distinguish an external Provider
internal failure from an unattributed Adapter exception inside the model-call boundary. The task
made no implementation change and failed its verifier.

## Decision

The GO upgrade condition required either a completed difficult task or an attributable non-runtime
terminal. This run achieved neither. S7P-10 therefore remains **CONDITIONAL GO**:

- Stage 7 documentation, domain modelling and isolated non-production Spikes remain permitted.
- Production Workflow execution and Direct-task migration remain blocked.
- The run is not repeated: retrying until success would replace diagnosis with sample selection.

The narrow next repair is safe Provider/Adapter failure attribution at the model-request boundary.
It must preserve redaction and distinguish an explicitly Provider-origin internal error from an
unexpected Adapter failure. After that repair, rerun only EXTERNAL-003 once under a fresh source and
evidence pin.

## Validation

| Gate | Result |
|---|---|
| Evaluator and AgentRun observability focus | 100 passed in 16.52 s |
| Complete offline suite | 1,284 passed, 2 Live tests deselected in 105.99 s |
| Ruff format/check | 485 files formatted; all checks passed |
| `compileall`, CLI help and `git diff --check` | passed |

The single EXTERNAL-003 proof was the only Live task run. No Pi or additional Provider probe was
executed.
