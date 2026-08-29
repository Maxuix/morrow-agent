# TODO

## Current task

Resume Subplan 90 from the consolidated local `main`, repair the permission-equivalence regression,
then refreeze and capacity-check a new S7P-09 campaign without reusing prior admissions. The
authorized r16 14-run reduced pilot is now executed and retained as bounded evaluation evidence;
it has one Morrow PASS, but only one repetition and incomplete Provider usage.

## Tasks

- `[x]` Verify the consolidated baseline, harness self-check, permission matrix and historical
  campaign-capacity state.
- `[x]` Restore explicit `network`, `git_write` and `privilege_escalation` denial precedence without
  reintroducing command-content heuristics.
- `[x]` Run the full offline/static/CLI/diff gate and commit the verified repair.
- `[x]` Add explicit prior-campaign roots to cumulative capacity accounting and admission checks.
- `[x]` Add a conservative full-remaining-schedule reservation check before the first admission.
- `[x]` Refreeze a new current-source comparison plan, profiles and protected evidence root.
- `[x]` Run preflight and capacity checks with every retained campaign root; stop before admission if
  the approved ceiling is insufficient.
- `[x]` Add the explicit 14-run reduced single-repetition plan variant without weakening the default
  28-run primary contract.
- `[x]` Refreeze and capacity-check the reduced plan; stop before admission if retained usage still
  leaves insufficient headroom.
- `[!]` Execute the reduced pilot sequentially: the diagnostic root contains 2/14 failed/blocked
  admissions, and the post-fix root contains 1/14 `BLOCKED_ENV` admission. Pause all remaining
  entries until Provider usage/runtime reliability is repaired and the plan is refrozen.
- `[x]` Port Pi's transient Provider retry boundary and preserve unavailable usage as partial
  evaluation evidence; keep the bundled default retry count unchanged pending separate approval.
- `[x]` Apply and verify the separately authorized bundled default of three retries.
- `[!]` Refreeze and execute a fresh 14-run reduced pilot without reusing prior admissions. The r14
  plan and preflight passed, but cumulative capacity blocked its 21,000,000-token reservation.
- `[!]` Execute authorized r15 under the added 30,000,000-token ceiling. All 14 bundles validate,
  but Provider/runtime evidence is incomplete (Morrow 10/10 `FAIL_RUNTIME`; Pi 3/4
  `FAIL_RUNTIME`, 1/4 `BLOCKED_ENV`), so paired comparison remains blocked.
- `[x]` Verify Mimo with one non-stream and four streaming short probes, then refreeze and execute
  fresh reduced r16 in frozen order without reusing r15 admissions. All 14 bundles validate:
  Morrow 1 `PASS`, 2 `FAIL_MODEL`, 1 `BUDGET_EXHAUSTED`, 6 `FAIL_RUNTIME`; Pi 2 `FAIL_MODEL`,
  2 `FAIL_RUNTIME`.
- `[!]` Complete the primary repeated comparison. The reduced r16 sample has only one repetition,
  mandatory Morrow usage is partially unavailable, and the standard comparison gate therefore
  remains `NOT_EVALUATED`.
- `[!]` Refreeze a 14-run `opencode-go/deepseek-v4-flash` campaign under a fresh model-scoped 21M
  ceiling. Morrow failed its first readiness request as `invalid_response`, while the authorized Pi
  probe completed with 536 tokens. Stop before plan creation or formal admission and repair the
  Morrow Adapter response-compatibility blocker.
- `[x]` Diagnose the DeepSeek Adapter failure without retaining response content. OpenCode emits
  monotonic cumulative usage on multiple stream chunks; Morrow rejects changed repeated usage as a
  conflict and maps the resulting `ValueError` to terminal `invalid_response`.
- `[x]` Port Pi's provider-neutral stream-usage behavior: each valid usage payload replaces the
  prior snapshot, malformed usage degrades telemetry without discarding valid semantic completion,
  and no model/provider ID branch is introduced. Focused and complete offline gates pass; the real
  repaired Morrow probe completes with 3,171 tokens.
- `[x]` Diagnose the retained DeepSeek r18 `invalid_response`: an otherwise valid tool-call stream
  intermittently contains whitespace-only optional text, causing `AssistantMessage.content`
  validation to fail after assembly.
- `[x]` Match Pi's provider-neutral tolerance by normalizing whitespace-only text to `None` only
  when valid tool calls exist. Deterministic regression, the complete offline gate and an exact-size
  Live structural sample all pass without weakening ordinary final-text validation.
- `[x]` Fold frozen isolated Morrow configuration and `build_active()` keyring loading directly into
  admission; do not add a readiness command or model probe.

## Earlier completed tasks

- `[x]` Inspect the actual process, capability, file/search/Git/sandbox and project-instruction paths.
- `[x]` Remove semantic Shell/Git risk classification from the core process service.
- `[x]` Stop keyword-based sensitive-resource blocking in workspace file, search, Git and snapshot
  services; retain exact active-credential output redaction.
- `[x]` Run registered core workspace mutations and commands without per-call heuristic approval,
  while retaining read-only, Full Access and Skill/MCP authority boundaries.
- `[x]` Replace task-derived nested instruction discovery with one root availability-first load.
- `[x]` Update focused tests, architecture, roadmap and stale acceptance selectors.
- `[x]` Run the complete offline/static/CLI/diff validation gate and record final evidence.
- `[x]` Commit the verified repair as one coherent checkpoint; do not merge or resume evaluation
  without explicit authorization.

## Boundaries

- Preserve unrelated user changes and untracked notes.
- Do not add a replacement validation/policy layer for removed keyword heuristics.
- Do not reuse any prior S7P-09 plan, admission, schedule or formal run key.
- Do not weaken workspace path confinement, stale-revision checks, atomic publication, output bounds,
  exact known-secret redaction, Full Access grants, or Skill/MCP extension policy.
