# S7P-09 Repeated Direct/Pi Baseline — Offline Harness Evidence

> Status: Phase A offline harness/runners and Morrow Provider repair verified; model and Token
> budget approved; formal campaign pending final immutable pins. This is not an S7P-09 PASS claim.

## Scope and boundary

This slice implements and verifies the harness/runners without credential-value inspection, paid
task execution or task network access. Approved bounded readiness probes were run separately:

- strict create-only comparison-plan validation, including full pins, approved hold-point evidence,
  exact common Provider/model/revision, equal sampling/capability facts, an explicit total Token
  ceiling, an explicit nullable cost ceiling and a content hash;
- the deterministic 28-entry schedule: 20 Morrow and eight Pi 0.84.2 runs, with Morrow→Pi order in
  repetition 1 and Pi→Morrow in repetition 2 for all four paired tasks;
- Pi 0.84.2 event normalization from authoritative `message_end`, `turn_end`,
  `tool_execution_start/end`, compaction and retry events;
- Morrow safe process-local trace normalization, strict tool terminal accounting, bounded paths,
  context landmarks, validation status, retry/compaction counts, rework and intervention facts;
- the real Morrow `CapabilityPolicy` versus Pi adapter conformance matrix, plus an evaluation
  ApprovalPort that accepts only already-confined policy reasons and cannot override a denial;
- create-only admissions, evidence-root confinement/mode/free-space checks, per-admission budget
  reservations, a 1,800-second bounded subprocess watchdog and raw-stream hash-only metadata;
- mechanical Morrow/Pi bundle comparison, including exact admissions, profile/source/dataset/
  protocol/baseline equality, complete required metrics, Morrow protocol gates, stable-task quality deficit,
  Morrow-only basic-tool blockers, total budget and create-only `baseline.json` output.

The next offline slice completed both Agent runners. `run-morrow` uses ordinary
bootstrap/AgentLoop/ToolExecutor composition with the bounded EvaluationApprovalPort, then projects
only safe in-memory calls, ToolFacts and terminal metrics. `run-pi` pins Pi 0.84.2's model/tool
surface, disables mutable user extensions/skills/templates/session state, and loads the sole
content-hashed policy extension. Its bash override uses macOS Seatbelt to deny network and `.git`
writes while limiting workspace writes and command duration.

## Safety evidence

- Unknown comparison-plan fields, unresolved pins, `unavailable` hashes, placeholders, profile or
  model drift, schedule drift, unapproved hold points, a non-positive Token ceiling, a non-null
  non-positive cost ceiling and sensitive fields fail closed.
- Raw Pi content may contain credential-shaped text, traceback text, reasoning-like response text,
  full arguments and full tool results; normalization tests prove none reaches normalized JSON.
- Duplicate authoritative Pi messages, duplicate/missing tool terminal events, unknown event
  shapes and unknown terminal stops fail closed.
- Tool invalid-argument and basic-blocker counts come only from explicit safe annotations; they are
  not guessed from a generic failure or blocked state.
- Comparison rejects missing/extra/duplicate results, source/profile/protocol/dataset drift,
  different paired baseline trees/task contracts, incomplete required metrics and non-monotonic
  admissions. With the approved null currency ceiling, unavailable cost remains explicit but does
  not block comparison; token usage remains mandatory.
- No credential value, formal task run, task network access, dependency addition, runtime-policy
  change or public event change occurred. Pi uses the same Keychain credential by reference, not by
  copied value. Its exact-model no-tool probe passed; the repaired Morrow Agent probe also passed.

## Offline validation on 2026-08-27

| Command | Result |
|---|---|
| `uv sync` | passed; 65 packages resolved, 59 checked |
| `uv run pytest -q tests/test_code_agent_mini_eval.py tests/test_provider.py tests/test_tool_contract_audit.py` | passed; 116 tests, 1 live skipped |
| `uv run python evals/code-agent-mini/eval.py self-check` | passed; all 10 baselines failed and all 10 Gold states passed |
| `uv run pytest -q tests/acceptance/test_s7p08_single_agent_matrix.py` | passed; 3 tests |
| `uv run pytest -m 'not live'` | passed; 1323 tests, 2 live deselected, 83.29 s |
| `uv run ruff format --check .` | passed; 492 files already formatted |
| `uv run ruff check .` | passed |
| `uv run python -m compileall -q src tests evals/code-agent-mini` | passed |
| `uv run morrow --help` / `uv run morrow run --help` | passed |
| `uv run python evals/code-agent-mini/eval.py --help` | passed |
| Pi offline extension load | passed; policy parsed by Pi 0.84.2 without a model request |
| Morrow non-stream Provider readiness | passed |
| Repaired Morrow Agent no-tool probe | passed; `stop`, 7,139 tokens, zero tool calls |
| Pi no-secret auth check | passed; `ready/api_key` |
| Pi exact-model no-tool probe | passed; `mimo-v2.5`, stop, 404 tokens |
| `git diff --check` | passed |

The focused evaluator and static gates were rerun after the final fail-closed audit changes. The
final branch gate is recorded in `.agent/LOG.md`; no command is represented as live evidence.

## Hold point

The user approved `opencode-go/mimo-v2.5` for both Agents. Pi 0.84.2's installed catalog exposes the
exact entry at `https://opencode.ai/zen/go/v1`, with a 1,000,000-token context window and
128,000-token maximum output. Pi's default selection points to it; no credential value was read or
copied. Pi auth readiness is `ready/api_key`, and its bounded no-tool probe returned the exact
provider/model with a normal stop, 404 total tokens and complete Provider cost.

The approved budget is a hard 5,000,000-token ceiling with no currency ceiling. Provider/runtime
cost is recorded when available and otherwise remains explicitly unavailable; it is not a campaign
gate and is never inferred as zero. Morrow's repaired bounded Agent probe completed `stop` with
7,139 Provider tokens and zero tool calls. Formal work remains held only for exact served
revision/sampling evidence and final immutable pins. No formal admission may start before those
facts are frozen.
