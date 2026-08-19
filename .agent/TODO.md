# TODO

## Current stage

Stage 4 production implementation is active at the Command/Query/Event, CLI, doctor, and backup
surface. Deterministic context checkpoints and immutable Session fork have landed; Full Access
remains inactive.

## Active subplan

Subplan 43 — Command/Query/Event, CLI, Doctor, and Backup.

## Tasks

- [>] S4.43.1 Define stable Command/Query DTOs, error mapping, command receipts, cursor pagination,
  and workspace isolation for completed domains.
- [ ] S4.43.2 Implement sanitized versioned application events in the same business transaction.
- [ ] S4.43.3 Prove the existing public runtime event lifecycle remains unchanged.
- [ ] S4.43.4 Implement CLI/REPL Session, Task, Artifact, fork, archive, acceptance, and recovery flows.
- [ ] S4.43.5 Implement read-only doctor and health/quarantine reporting.
- [ ] S4.43.6 Implement online backup plus Artifact manifest/copy and restore verification.
- [ ] S4.43.7 Add deterministic dry-run orphan cleanup for exact managed targets.
- [ ] S4.43.8 Run application/CLI/crash/backup/security regressions and update user/architecture docs.

Only Subplan 43 may be executed. Grants and Full Access remain inactive.
