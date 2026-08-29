# S7P-09 Repeated Direct/Pi Baseline — Offline Harness Evidence

> Status: Phase A harness/runners and Pi-aligned Morrow Provider retry repair verified; the
> authorized reduced r16 pilot is executed and structurally valid, with one Morrow PASS. The
> primary repeated comparison remains incomplete. This is not an S7P-09 PASS claim.

## Scope and boundary

This slice implements and verifies the harness/runners without credential-value inspection or task
network access. Approved bounded readiness probes were run separately:

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

The default primary remains the deterministic 28-entry schedule. Under the current user-approved
budget constraint, an explicit `reduced-single-repetition-v1` plan may instead schedule 14 entries:
all ten Morrow tasks once and the four Pi comparison tasks once. This is a bounded pilot with one
observation per paired task, not a repeated-baseline or statistical PASS claim.

The next offline slice completed both Agent runners. `run-morrow` uses ordinary
bootstrap/AgentLoop/ToolExecutor composition with the bounded EvaluationApprovalPort, then projects
only safe in-memory calls, ToolFacts and terminal metrics. `run-pi` pins Pi 0.84.2's model/tool
surface, disables mutable user extensions/skills/templates/session state, and loads the sole
content-hashed policy extension. Its bash override uses macOS Seatbelt to deny network and `.git`
writes while limiting workspace writes and command duration.

The pre-admission end-to-end rehearsal found and closed one integration gap: the formal runners now
project their shared normalized trace into the existing `finalize` runtime-evidence schema. Pi is
also pinned to thinking `off`, matching Morrow's omitted reasoning-effort request field. No formal
run key was admitted against the superseded rehearsal plan.

The first formal campaign attempt later exposed a second evaluator-only gap. Its single admitted
Morrow/MORROW-001#1 run completed Provider/tool work, but `find_files` was not mapped to the shared
search capability, so the run finalized `BLOCKED_ENV` with runtime evidence unavailable. That
campaign is retained outside Git with a create-only `ABORTED` record and is never replaced. The
mapping now covers the complete production Morrow inventory plus move/rename endpoint paths; a new
campaign ID is required after verification.

After the later Pi-first core simplification, the conformance matrix also exposed a policy-ordering
regression: explicit `network`, `git_write` and `privilege_escalation` risk flags were evaluated
after the direct registered-command fast path. The policy now denies those explicit flags before
the fast path, while ordinary command content remains unparsed and registered commands remain
directly executable. The evaluator matrix and regression assertion now require all three denials.

The first cumulative-capacity audit also found that `campaign-capacity` only inspected the current
evidence root. It now accepts repeatable explicit prior roots, validates each prior root's own
comparison plan and immutable admissions, and includes each root's conservative
`max(reservation, known usage)` in the current ceiling. Admission checks use the same prior-root
inputs, so an empty new root cannot hide previously accounted usage.

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
- No credential value, task network access, dependency addition or public event change occurred. Pi
  uses the same Keychain credential by reference, not by copied value. Its exact-model no-tool probe
  passed; the repaired Morrow Agent probe also passed. The later authorized r15 and r16 formal
  pilots are recorded below with their immutable evidence.

## Offline validation on 2026-08-28

| Command | Result |
|---|---|
| `uv sync` | passed; 65 packages resolved, 59 checked |
| `uv run pytest -q tests/test_code_agent_mini_eval.py tests/test_process.py tests/test_capability_executor.py` | passed; 72 tests |
| `uv run pytest -q tests/test_code_agent_mini_eval.py` | passed; 57 tests |
| `uv run python evals/code-agent-mini/eval.py self-check` | passed; all 10 baselines failed and all 10 Gold states passed |
| `uv run pytest -q tests/acceptance/test_s7p08_single_agent_matrix.py` | passed; 3 tests |
| `uv run pytest -m 'not live'` | passed; 1338 tests, 2 live deselected, 84.69 s |
| `uv run ruff format --check .` | passed; 494 files already formatted |
| `uv run ruff check .` | passed |
| `uv run python -m compileall -q src tests evals/code-agent-mini` | passed |
| `uv run morrow --help` / `uv run morrow run --help` | passed |
| `uv run python evals/code-agent-mini/eval.py --help` | passed |
| `uv run python evals/code-agent-mini/eval.py permission-check /tmp` | passed; all eight conformance cases matched |
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

The approved budget was a hard 50,000,000-token ceiling with no currency ceiling; the user later
added 30,000,000 tokens, making the active ceiling 80,000,000. Provider/runtime cost is recorded
when available and otherwise remains explicitly unavailable; it is not a campaign gate and is
never inferred as zero. After r16, retained formal attempts and conservative reservations leave
only 6,122,491 tokens, which cannot carry another complete campaign.
Before the first admission, the capacity command also accepts a conservative per-admission
reservation and remaining-admission count, and compares the complete planned schedule against the
same cumulative prior-root total.
The r15 and r16 source/profile/evidence pins were each created before their admissions; no prior
plan or run key was reused.

The fresh offline preflight and permission-equivalence check passed. The explicit cumulative audit
counts `27,577,509` retained tokens and rejects the conservative `42,000,000`-token reservation
for the 28-run schedule under the approved `50,000,000` ceiling, before any admission or model
request is made.
The reduced plan is checked separately with its 14-run reservation before any admission or model
request.

The final r14 reduced-plan check passed its offline conditions but was blocked by the then-active
`31,877,509 + 21,000,000 > 50,000,000` capacity calculation. The user subsequently added
30,000,000 tokens and authorized a fresh r15 pilot.

After the reduced pilot exposed an empty isolated Morrow configuration, the admission boundary was
repaired without adding another readiness layer. A Morrow admission now generates its minimal
isolated config from the frozen Provider/service/model selection and matching configured Keychain
reference, calls existing `build_active()`, and only then creates the immutable admission. This
load performs no model request or no-tool probe. Configuration failure consumes no run key. The
paused two-run campaign remains diagnostic evidence and is not resumed after this source change.

The post-fix pilot then exposed a second Provider boundary: 409/5xx and premature stream endings
were normalized as terminal `internal`, partial stream progress disabled retry, and the evaluator
rejected the whole safe trace when aggregate Provider usage was unavailable. The repaired adapter
matches Pi's transient classes for 408, 409, 429, 5xx, timeout, connection interruption and early
stream termination, while explicit quota, balance and billing failures remain terminal. A transient
`internal` now requires an explicit Provider-origin marker, so ordinary Morrow exceptions and
unattributed internal events do not retry. Per-run evaluation accepts known tool/runtime evidence
with token fields left as `unavailable`; it never substitutes zero. Frozen campaign comparison still
reports mandatory unknown token metrics as incomplete. With the user's separate authorization, the
ordinary bundled retry default is now aligned with Pi at three retries.

## Offline validation on 2026-08-29

| Command | Result |
|---|---|
| Focused Provider/runtime/evaluator tests | passed; 194 tests, one explicit Live test skipped |
| `uv run pytest -m 'not live'` | passed; 1363 tests, 2 Live tests deselected, 87.74 s |
| `uv run ruff format --check .` / `uv run ruff check .` | passed; 494 files formatted |
| `uv run python -m compileall -q src tests evals/code-agent-mini` | passed |
| Morrow and evaluator CLI help | passed |
| `uv run python evals/code-agent-mini/eval.py self-check` | passed; 10 baselines failed and 10 Gold states passed |
| `git diff --check` | passed |

No Live Provider request or formal admission was made by this repair validation.

After the separately authorized three-retry default was committed as `c0b14b9`, fresh reduced plan
r14 passed plan-check, preflight and permission equivalence but stopped before admission because
the approved 50M ceiling could not carry its conservative reservation. The user then authorized
r15 under an 80M ceiling. All 14 r15 admissions and bundles are valid; Morrow finalized 10/10 as
`FAIL_RUNTIME`, while Pi finalized 3/4 as `FAIL_RUNTIME` and 1/4 as `BLOCKED_ENV`. Morrow token
usage is unavailable, Pi has no complete usage basis for the comparison, and one Pi stream has no
usable evidence; the comparison gate therefore remains blocked on
mandatory metrics. The cumulative conservative capacity is `52,877,509`, below `80,000,000`, with
`27,122,491` remaining. No token field was replaced with zero for Morrow.

## Authorized r15 pilot outcome

The protected evidence root is
`/Users/ruirui/Documents/Project/Agent/s7p09-evidence-96772d6-reduced-r15`. The corrected
execution path used an external runtime-input file so finalization could atomically rebuild the
canonical bundle artifacts. Structural validation passed for all 14/14 bundles with no invalid
bundles. The provider/runtime result distribution is:

| Agent | Valid bundles | Result | Runtime/usage note |
|---|---:|---|---|
| Morrow | 10/10 | `FAIL_RUNTIME` 10/10 | one attempt each; token usage unavailable; no tool calls |
| Pi | 4/4 | `FAIL_RUNTIME` 3/4; `BLOCKED_ENV` 1/4 | three completed runtime failures; one empty stream after bounded process interruption |

`compare_campaign` correctly rejects the pilot because mandatory usage metrics are incomplete. The
observed blocker is provider/runtime response availability, not a capacity overrun or a missing
finalization artifact. The r15 evidence is diagnostic and cannot be presented as a comparison PASS.

## Authorized r16 pilot outcome

After one non-stream and four streaming short Mimo probes completed successfully, the fresh
protected campaign `s7p-09-mimo-v25-90b0e9b-reduced-r16` was pinned to clean
`main@90b0e9b9ce8603118f39ba41cda08cf138487cb5`. Plan validation, campaign preflight, all eight
permission-equivalence cases and the complete 14-run reservation check passed before admission.
The protected evidence root is
`/Users/ruirui/Documents/Project/Agent/s7p09-evidence-90b0e9b-reduced-r16`.

All 14 admissions were created in frozen order, executed once and finalized into valid bundles:

| Agent | Valid bundles | Result distribution |
|---|---:|---|
| Morrow | 10/10 | 1 `PASS`; 2 `FAIL_MODEL`; 1 `BUDGET_EXHAUSTED`; 6 `FAIL_RUNTIME` |
| Pi | 4/4 | 2 `FAIL_MODEL`; 2 `FAIL_RUNTIME` |

Morrow passed `EXTERNAL-002` with 101,407 tokens. `EXTERNAL-001` passed its verifier but remained
`FAIL_MODEL` because Python bytecode was an unexpected workspace change. `MORROW-006` exhausted
the frozen budget after 1,062,637 tokens. Pi completed both external paired tasks but neither
passed its verifier. The two Morrow/Pi product-task pairs failed at runtime before usable output.

The generic summary validator accepted every discovered bundle and reported zero invalid or
duplicate runs. Its standard 20-run-per-Agent gate remains `NOT_EVALUATED` because this approved
variant intentionally contains one repetition and only four Pi runs; unavailable Morrow token
fields are also preserved rather than replaced with zero. Cumulative conservative capacity after
r16 is `73,877,509 / 80,000,000`, leaving `6,122,491`. The evidence supports “Mimo is usable but
intermittently unreliable for this formal workload,” not a completed Morrow/Pi baseline claim.

## DeepSeek replacement readiness repair

The requested `opencode-go/deepseek-v4-flash` replacement was stopped before campaign admission
when Morrow returned `invalid_response`, while an exact-model Pi probe completed with 536 tokens.
Sanitized stream inspection showed that OpenCode emits cumulative usage snapshots on multiple
chunks. Morrow previously required repeated usage values to remain identical, so the first increase
raised a local `ValueError`; the Provider stream itself contained visible text and a normal `stop`.

The generic OpenAI-compatible Adapter now follows Pi 0.84.2's provider-neutral behavior: each valid
usage payload replaces the previous snapshot, so terminal or cumulative streams both retain their
latest valid accounting. Individual payload validation remains strict. Malformed usage becomes
explicitly unavailable but does not invalidate valid semantic response evidence. No DeepSeek or
OpenCode-specific branch was added.

The repaired Morrow probe completed with 3,171 total tokens. Focused regression passed 167 tests,
and the complete offline gate passed 1,363 tests with two explicit Live tests deselected. No formal
DeepSeek campaign admission was consumed.
