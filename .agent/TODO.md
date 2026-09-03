# TODO

Active subplan: none. Stage 8 Subplan 3 review remediation completed on
`fix/stage8-core-api-review`.

- [x] Repair the six confirmed correctness bugs: shutdown cancellation semantics, approval
  exactly-once delivery/consume, writer-lock lifetime, atomic patch-apply receipt/replay driving,
  approval waiter registration/redaction, and Core Host build-failure cleanup.
- [x] Resolve the confirmed protocol/projection findings: actual ToolSet catalog, commit-safe event
  hints plus continuation node events, real pause receipt replay evidence, paginated reference
  client pulls, typed conflict responses, AgentRun allowlist and Provider query ownership.
- [x] Resolve the two transport/documentation nits and add focused deterministic regressions.
- [x] Run the touched-test matrix, full offline gate, Ruff format/check, compileall, CLI help and
  `git diff --check`; update acceptance/execution records and commit verified remediation.

Subplan 4 remains closed until this remediation finishes and the user explicitly activates it with
frontend toolchain authorization.
