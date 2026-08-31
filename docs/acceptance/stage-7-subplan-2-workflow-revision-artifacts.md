# Stage 7 Subplan 2 — Workflow Revision and Artifact Contracts

Date: 2026-09-01. Result: local implementation accepted and fast-forward integrated into `main`.
Implementation commit: `f0a2385`. Remote publication remains pending explicit authorization.

## Delivered

- Strict editable Workflow source and YAML/OCC adapter; compiled immutable Revision with stable
  node identity, exact model/Agent refs, canonical hash, lineage and frozen source publication facts.
  Head enable state is separate; revocation is additive and one-way.
- Exact TaskContract input, discriminated input bindings, completion-required output slots,
  independent exports and inspectable observations; exported-ref capacity reuses TaskOutcome's bound.
- Operational Store v24 repositories for Revision/head/revocation, WorkflowRun/NodeRun, request-cap/
  deadline snapshots, root nonterminal uniqueness and immutable Artifact bindings. Node completion
  cannot omit required outputs; terminal writes replay identically and cannot rewrite history.
- Fresh isolated Session/internal Task ownership with ordinary Task/Turn mutation guards and
  user-list/learning exclusion. The explicit internal lifecycle boundary preserves Session/Task
  matching. Durable AgentRun attribution is an immutable reference, not a second snapshot.
- Existing Artifact bytes/store authority with typed TaskContract/TextResult publication, deterministic
  NodeRun/slot identity and available-result replay. TextResult references/digests the final Assistant
  instead of copying chat authority. The later producer verifies and supplies the durable record.
- Persisted internal Artifact/TaskOutcome TextSafetyProfile; shared value-sensitive input refusal and
  output redaction. Benign security vocabulary round-trips, actual synthetic credential fixtures are
  omitted/redacted, incomplete TextResult and `workflow_evidence_redacted=true` remain truthful.
  Generic APIs and old rows remain legacy-strict. Accepted Workflow-profile Outcomes enter the
  existing LearningReview request path; no Workflow-specific learning classifier was added.
- Existing backup/doctor composition extended with raw exact-path Workflow desired-source capture,
  malformed draft restoration, desired-ahead warnings and authoritative hash/reference error checks.
  Backup manifests contain Artifact identity/hash/path facts, never Workflow payload excerpts.

The refusal-call audit and deferred execution wiring are recorded in the completed child plan.

## Validation run

| Gate | Result |
|---|---|
| Workflow domain + store tests | 31 passed |
| Declared domain/store/operational-store/backup/doctor focused set | 78 passed |
| `uv run pytest -m 'not live' -q` on final implementation tree | 1376 passed, 2 deselected |
| `uv run ruff format --check .` | 533 files already formatted |
| `uv run ruff check .` | passed |
| `uv run python -m compileall -q src tests` | passed |
| `uv run morrow --help` | passed |
| `git diff --check` | passed |

Coverage includes source-field rejection, exact input/output semantics, export capacity, safe/unsafe
text calibration and profile rehydration; revision/head/revocation round-trip; all ordinary Task
mutations and Turn admission for internal leaves and active roots; post-terminal ordinary use;
AgentRun attribution; output completion/replay; cancellation intent/recovery states; Outcome ready-epoch
references, learning request creation and backup; v23 Task/Artifact/Outcome default migration; future
schema refusal; malformed source restore and head/hash corruption checks.

No third-party dependency, Live Provider/MCP test, network campaign, public event lifecycle change or
bundled runtime-policy default change was included.

## Boundaries and handoff

There is no Workflow compiler, application publication service, Scheduler, Start/run CLI or execution
path yet. The repository stores already-compiled values; Subplan 3 remains the sole future compiler/
publication owner. Structured payload producers/submission, execution profile selection, staging
recovery, root acceptance snapshot carry-forward, and invoking-session scope remain with their
declared later consumers. Ordinary Direct chat still uses the existing path.

The feature branch was fast-forward merged, ancestry-verified and deleted. No additional worktree
exists. Subplan 3 is ready, not activated.
