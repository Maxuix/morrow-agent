# S7P-09 Repeated Direct/Pi Baseline — Offline Harness Evidence

> Status: Phase A offline harness slice verified; formal campaign BLOCKED at the common-model and
> spend hold point. This document is not an S7P-09 PASS or Stage 7 readiness claim.

## Scope and boundary

This slice implements and verifies the parts of S7P-09 that require no Provider request,
credential inspection, paid model execution or task network access:

- strict create-only comparison-plan validation, including full pins, approved hold-point evidence,
  exact common Provider/model/revision, equal sampling/capability facts, explicit total token/cost
  ceilings and a content hash;
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
  protocol/baseline equality, complete metrics, Morrow protocol gates, stable-task quality deficit,
  Morrow-only basic-tool blockers, total budget and create-only `baseline.json` output.

The review also confirmed that ordinary `morrow run` deliberately injects a fail-closed headless
ApprovalPort. The generic bounded runner and evaluation ApprovalPort are therefore not yet claimed
as a complete formal Morrow runner: the next offline slice must compose that port through the
ordinary bootstrap/AgentLoop/ToolExecutor path and derive its safe trace from actual process-local
facts. A Pi policy extension/OS confinement runner must likewise be frozen and tested before a live
admission.

## Safety evidence

- Unknown comparison-plan fields, unresolved pins, `unavailable` hashes, placeholders, profile or
  model drift, schedule drift, unapproved hold points, zero ceilings and sensitive fields fail
  closed.
- Raw Pi content may contain credential-shaped text, traceback text, reasoning-like response text,
  full arguments and full tool results; normalization tests prove none reaches normalized JSON.
- Duplicate authoritative Pi messages, duplicate/missing tool terminal events, unknown event
  shapes and unknown terminal stops fail closed.
- Tool invalid-argument and basic-blocker counts come only from explicit safe annotations; they are
  not guessed from a generic failure or blocked state.
- Comparison rejects missing/extra/duplicate results, source/profile/protocol/dataset drift,
  different paired baseline trees/task contracts, incomplete metrics and non-monotonic admissions.
- No credential value, auth command, Provider call, model probe, live Pi run, task network access,
  dependency addition, runtime-policy change or public event change occurred.

## Offline validation on 2026-08-27

| Command | Result |
|---|---|
| `uv sync` | passed; 65 packages resolved, 59 checked |
| `uv run pytest -q tests/test_code_agent_mini_eval.py` | passed; 35 tests |
| `uv run python evals/code-agent-mini/eval.py self-check` | passed; all 10 baselines failed and all 10 Gold states passed |
| `uv run pytest -q tests/acceptance/test_s7p08_single_agent_matrix.py` | passed; 3 tests |
| `uv run pytest -m 'not live'` | passed; 1313 tests, 2 live deselected, final rerun 87.19 s |
| `uv run ruff format --check .` | passed; 492 files already formatted |
| `uv run ruff check .` | passed |
| `uv run python -m compileall -q src tests evals/code-agent-mini` | passed |
| `uv run morrow --help` / `uv run morrow run --help` | passed |
| `uv run python evals/code-agent-mini/eval.py --help` | passed |
| `git diff --check` | passed |

The focused evaluator and static gates were rerun after the final fail-closed audit changes. The
final branch gate is recorded in `.agent/LOG.md`; no command is represented as live evidence.

## Hold point

The only configured Morrow model is `opencode-go/mimo-v2.5`; Pi 0.84.2 does not expose it. Pi's
offline local catalog currently exposes `openai-codex` GPT-5.3/5.4/5.5/5.6 variants and several
`xai` models, but no candidate is automatically eligible: Morrow must support the same exact
service, canonical response model, revision, sampling, context/output and provider-reported cost
contract, and both credential-readiness checks must pass without printing credential values.

Formal work remains blocked until the user explicitly approves one exact common Provider/model,
the campaign-wide maximum token count and currency amount, and the non-secret permission mapping.
After that approval, bounded no-tool probes and readiness hashes can be frozen into the strict plan;
only then may the first create-only formal admission occur.
