# Subplan 12 — Bounded Read-Only Parallelism

> Status: active — explicitly authorized 2026-09-05; entry evidence under validation
> Branch: `feat/stage8-readonly-parallel`
> Activation base: latest verified `main` with Subplan 2 integrated
> Prerequisite: Subplan 2 verified AND the roadmap §8H/§三 entry conditions verified: stable
> ToolEffect classification, provider rate-limit ownership settled, per-request atomic accounting/optional-cap claim
> available, process/cwd/env isolation stress-tested, parallel visibility barrier validated.
> This subplan may slip past other Stage 8 subplans; it never blocks the GUI core chain.
> Roadmap authority: stage-8 §三 (entry conditions), §8H, §16.2 (parallel cases)

## Objective

Unlock bounded read-only parallel admission for compile-time-provable read-only fan-out frontiers
without depending on a role-specific template or building a general-purpose executor platform.

## Deliverables

- Read-only frontier proof at admission time: frozen effective ToolSet, ToolEffect and
  PermissionSnapshot are the evidence — role names are not; unknown/opaque effects never enter
  parallel admission; read-contract drift fails the target node and never falls back.
- Per-request atomic accounting/optional-cap claim: before each Provider request, atomically record
  it and, only when configured, claim against Workflow remaining allowance under the node's frozen
  cap using an idempotency key (run/node/request sequence); settle actuals after the response; no
  whole-node worst-case reservation, no in-memory ledger, no second request counter.
- Concurrency slot cap, deterministic gather (persistence and downstream visibility ordered by
  stable node order, not completion order), single-Writer invariant.
- Deterministic cancellation of the frontier, exactly-once settlement per admitted NodeRun, and
  partial-completion recovery that never reruns completed nodes.
- Stable serial fallback when proof/capacity is unavailable.

## Key semantics

- Explicit limits/concurrency are never over-admitted; cancel/recovery semantics match the
  serial path exactly.
- Writers remain serialized; this slice changes nothing about mutation ordering.

## Validation

- Barrier/event-based concurrency proofs (no sleeps): simultaneous admission, optional-cap
  over-claim rejection, deterministic gather order, cancel/recovery parity with the serial path,
  drift rejection without fallback.
- Full offline gate, Stage 7/8 matrices, Ruff format/check, compileall, `git diff --check`.

## Out of scope

Parallel Writers, Git worktree orchestration, distributed leases, transparent retry after side
effects, general executor platform.

## Activation decisions

- Base: verified local main `9ada9b0`; no remote publication or Live tests authorized.
- Reuse the existing transactional request admission/settlement ledger. Adapter error
  classification and Retry-After normalization feed the existing leaf AgentLoop, which
  remains the sole Provider retry owner; the Scheduler never retries Provider requests.
- Parallel work uses isolated Sessions and explicit workspace capabilities; opaque tools,
  process tools and Writers retain stable serial execution. No process-global cwd/env mutation.
- Gather orders Workflow output bindings and NodeRun completion. Leaf journals and
  immutable candidate Artifacts remain durable as they arrive so recovery retains work.
- No dependency, packaged policy default, or public event lifecycle change is required.
