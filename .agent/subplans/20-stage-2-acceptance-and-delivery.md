# Subplan 20 — Stage 2 Acceptance and Delivery

> Stage: 2
> Slice: 4 of 4
> Status: complete
> Parent: [Stage 2 implementation plan](../PLAN.md)
> Depends on: Subplans 17–19

## Objective

Validate the final integrated tree against every Stage 2 contract and all Stage 1 regressions, exercise the real terminal/product boundaries, publish reproducible evidence, reconcile architecture/status documentation and make an evidence-backed Stage 2 completion decision.

This slice is validation and defect remediation, not a place to add new capabilities. A failing contract reopens the slice that owns it.

## Executable tasks

### S2.20.11 — Remediate final code-review findings

1. Enforce the total run deadline across Provider streaming.
2. Rebuild the Context View from the latest Snapshot on every PreparingRequest.
3. Make result/Cycle allocation canonical-wire-aware and bound every synthetic result.
4. Reject inconsistent model finish/message pairs as invalid responses.
5. Reject non-finite calculator results and non-standard JSON envelopes.
6. Remove/ignore editor swap artifacts and rerun all final gates.

### S2.20.1 — Build the requirement-to-evidence matrix

1. Create `docs/acceptance/stage-2-evidence.md`.
2. Map every Stage 2 completion criterion and every proposal acceptance-matrix item to:
   - owning implementation slice;
   - exact automated test(s);
   - exact manual/Live check where applicable;
   - observed result;
   - unresolved limitation.
3. Do not mark a compound criterion covered unless every branch has direct evidence.
4. List Stage 3/4/5 exclusions and their capability-boundary evidence.

### S2.20.2 — Complete protocol and Adapter acceptance

Verify offline with fake SDK chunks:

1. tools request versus text-only request;
2. text, pure calls and mixed content;
3. one and interleaved multiple calls;
4. usage-only chunks and one logical choice;
5. conflicting/missing IDs, duplicate IDs, invalid types/names/arguments and missing/conflicting finish;
6. stop-with-calls normalization;
7. raw arguments fidelity and `content:null`;
8. explicit serializer round trip;
9. reasoning/SDK/internal metadata isolation;
10. zero-progress versus progressed error classification.

No fixture is added for an unimplemented native Provider.

### S2.20.3 — Complete Conversation/Context acceptance

Verify:

1. single/multiple ToolCycles and every illegal result ordering;
2. open-Cycle exclusion of new User/Assistant/terminal;
3. success/cancel/failure/budget terminal legality;
4. immutable Snapshot and process-local reset/restart behavior;
5. Chat/Structured/Fallback projection separation;
6. clearing all results in one Cycle atomically;
7. oldest-first hard trimming at turn and Cycle boundaries;
8. current User/open Cycle/state/tool-definition protection;
9. protected-set context failure before Provider;
10. final serialized request size/pair validation;
11. no source Log/Session/Handoff mutation and no summary call.

### S2.20.4 — Complete ToolExecutor acceptance

Verify:

1. Pydantic Schema generation and strict JSON/object/type/range/extra validation;
2. deterministic `lookup_record` and `calculate`;
3. unknown tool, not-found, divide-by-zero and malformed input;
4. timeout, execution failure, internal failure and cancellation;
5. bounded validation messages and secret/traceback removal;
6. success truncation/original_chars and output-limit failure;
7. equal per-call Cycle allocation and minimum-envelope pre-admission;
8. no Runtime tool retry;
9. exact one-result-per-accepted-call.

### S2.20.5 — Complete AgentLoop/time acceptance

Verify:

1. at least two dependent tool rounds before final answer;
2. multiple calls in one response execute in original order;
3. model/tool/round/per-cycle/run/context/result/Cycle limits;
4. each call timeout is bounded by remaining run time;
5. model retry only on transient zero-progress failure;
6. text/tool progress prevents retry;
7. cancellation at every defined commit boundary;
8. synthetic cancelled/budget results close accepted calls;
9. post-cancel/error next user turn succeeds;
10. A A A and A B A B A B loop stops, near-match does not;
11. each task has exactly one start/completion and at most one fatal error;
12. fatal `error.stop_code` equals `turn.completed.stop_code`, no public Stage 1 `code` remains, and stop code/detail mappings match the recovery matrix;
13. combined-limit precedence is deterministic: under repeated zero-progress retries `model_call_limit` may intentionally win before `max_tool_rounds`, and the longest allowed loop pattern (`loop_max_pattern_cycles × loop_repeat_limit`) is detected before the hard round cap.

### S2.20.6 — Re-run the complete Stage 1 product surface

On the final code state, verify:

1. ten ordered ordinary chat turns through AgentLoop;
2. streaming text, empty/abnormal response, retry and Ctrl+C behavior;
3. Provider configure/test/show and credential resolution;
4. natural-language config routing, previews and confirmation;
5. StructuredCompletion repair/timeout and Handoff model/fallback generation;
6. explicit `/continue`, `/handoff update`, `/new`, session switch and `/exit`;
7. workspace identity/relink, state revision/backup and degraded modes;
8. EOF/Ctrl+D and terminal confirmation paths;
9. dirty semantics after completed/cancelled/failed tool tasks;
10. Handoff/config model calls carry no tools or ToolMessage envelopes.
11. Stage 1 public error/completion assertions are migrated to the approved `stop_code`/visible-text contract without weakening failure classification.

### S2.20.7 — Validate product and package behavior

1. Build/install the package in an isolated environment and verify:
   - imports and CLI help;
   - bundled `agent-policy.toml` discovery;
   - bootstrap failure for an intentionally malformed/missing test policy;
   - default offline network guard;
   - no ConversationLog persistence across process restart.
2. Run a real terminal session with a deterministic scripted Provider where possible, covering:
   - mixed model text → tool step → final text;
   - tool error then model recovery;
   - cancellation during model and tool activity;
   - healthy follow-up chat;
   - Handoff update and `/new`.
3. Verify visible text appears once, tool step boundaries are clear and call IDs/full payloads are hidden.
4. Scan captured events, terminal output, state files/backups, Handoff and logs with credential/argument/result sentinels.

### S2.20.8 — Run optional real-Provider smoke

If and only if a compatible credential/model is explicitly available:

1. opt in to one secret-safe Live function-calling smoke using only the in-memory tools;
2. observe request compatibility, at least one real tool call, result replay and final text;
3. record exact model/Adapter capability and sanitized outcome;
4. never echo/store the credential or treat an unrun Live check as passed.

Absence of a Live credential does not fail the approved Stage 2 offline gate. An attempted Live failure must be classified and either fixed or recorded as a confirmed Provider/model limitation before completion is claimed.

### S2.20.9 — Run final quality gates

Run on the final tree:

- `uv run pytest -m 'not live'`;
- strict test collection including the opt-in Live marker;
- `uv run ruff format --check .`;
- `uv run ruff check .`;
- `uv run python -m compileall -q src tests`;
- package build/install/import/CLI smoke;
- capability-boundary and side-effect tests;
- credential/raw-payload sentinel scans;
- `git diff --check`.

Record exact commands, counts, skips and results. Do not reuse earlier-slice output as final-tree evidence.

### S2.20.10 — Reconcile documentation and finish

1. Update `docs/ARCHITECTURE.md` to the actual Stage 2 ownership graph and runtime flow.
2. Update `docs/ROADMAP.md` and the Stage 2 roadmap status only after all mandatory gates pass.
3. Update README usage/limitations for tool steps, stop reasons, cancellation and the no-local-side-effect boundary.
4. Complete the acceptance evidence with exact final results and any optional Live status.
5. Reconcile PLAN, TODO, TRACKER and LOG; remove obsolete wording rather than preserving alternate active plans.
6. Mark Stage 2 complete only if no mandatory criterion or confirmed P1-equivalent defect remains.
7. Do not begin Stage 3 planning/implementation as part of this subplan.

## Mandatory final gates

- Complete offline suite has zero failures and zero unexpected skips.
- Every accepted call/result and every public lifecycle invariant has direct automated coverage.
- Terminal mixed-content/cancellation behavior has offline renderer coverage and a real terminal check.
- Stage 1 product regressions pass on the final tree.
- Policy resource survives package build/install.
- No secret, full argument/result, traceback, reasoning or SDK object leaks through public surfaces.
- Only approved in-memory tools are enabled and the project workspace remains unchanged.
- ConversationLog does not persist and no Stage 4 summary/memory path exists.

## Completion criteria

- `docs/acceptance/stage-2-evidence.md` is complete and reproducible.
- All Stage 2 definition-of-done items have direct final-tree evidence.
- All mandatory quality, package, terminal and Stage 1 regression gates pass.
- Documentation describes observed code without contradicting the roadmap.
- Stage 2 is explicitly marked complete; Stage 3 remains unstarted.

## Deliverables

- Final Stage 2 defect fixes and regression coverage.
- Complete acceptance/evidence matrix.
- Package and terminal validation evidence.
- Updated architecture, roadmap, README and execution state.
- Evidence-backed Stage 2 completion decision.
