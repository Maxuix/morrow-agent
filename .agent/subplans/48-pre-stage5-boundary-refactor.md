# Subplan 48 — Pre-Stage 5 Boundary Refactor

> Status: active
> Branch: `refactor/pre-stage5-boundaries`
> Scope: behavior-preserving refactor only

## Goal

Turn the audited runtime, persistence, SQLite, application, and CLI hotspots into explicit,
testable boundaries before new Stage 5 domains are introduced.

## Tasks

### S48.1 Explicit durable runtime contract

- Characterize current process-local and durable AgentLoop behavior.
- Define the real committer/coordinator capabilities used by runtime code.
- Remove optional durable capability probing and direct journal reach-through from AgentLoop.
- Preserve process-local chat and test fixtures through an explicit non-durable path.

Gate: loop, tool, persistence, permission, recovery, and architecture tests; Ruff on touched files.

### S48.2 AgentLoop state and tool-cycle extraction

- Introduce typed mutable run state instead of the current local-variable cluster.
- Extract durable/non-durable tool-call execution into a focused collaborator.
- Keep event construction/timing and all ConversationLog writes under AgentLoop ownership.

Gate: complete AgentLoop/event/tool regression set and cancellation/fault cases.

### S48.3 SessionPersistence decomposition

- Extract Turn submission/restoration, permission evidence, and durable tool execution coordinators.
- Retain a compatible Session committer facade and one transaction owner.
- Replace external private-field mutation with public synchronization operations.

Gate: durable conversation, tool persistence, recovery crash, permission, and API tests.

### S48.4 SQLite journal partition

- Introduce one internal transaction context for executor, timestamp, replayability, and touched
  Session tracking.
- Move SQL and row codecs into bounded-domain modules without changing Core ports or migrations.
- Retain one compatible `SqliteOperationalJournal` facade during migration.

Gate: journal, operational store, task, artifact, checkpoint, permission, recovery, doctor,
backup, and cleanup tests.

### S48.5 Application and composition boundaries

- Introduce an explicit application command context for receipts, events, clock, IDs, replay, and
  error translation.
- Inject explicit dependencies into permission and recovery command handlers.
- Share one headless operational service factory between CLI and bootstrap.
- Split CLI registration modules only where it reduces ownership and import fan-out.

Gate: application API, CLI operational, terminal, bootstrap/product acceptance, and architecture
tests.

### S48.6 Closeout

- Remove unused duplicate ports and stale compatibility code proven unnecessary.
- Recompute hotspot/dependency metrics and reconcile `docs/ARCHITECTURE.md`.
- Run all offline and quality gates, commit verified progress, fast-forward `main`, and retire the
  topic branch.

## Non-goals

- Stage 5 learning or memory implementation.
- Operational Store schema or migration changes.
- Public event or bundled policy changes.
- New dependencies or generic persistence frameworks.
