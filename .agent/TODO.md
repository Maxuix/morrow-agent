# Subplan 3 — Deterministic Workflow Compiler

- [x] Make the pure Compiler the only normalization/validation/hash path (no IO).
- [x] Make `WorkflowCompilationService` the only transactional publication path (receipt/OCC/no-op).
- [x] Enforce graph validity: acyclicity, connectivity, binding edges, exported outputs.
- [x] Merge tool requirements under fixed precedence with typed diagnostics.
- [x] Freeze resolved models and per-node budgets from authoritative inputs.
- [x] Add focused compiler/publication tests for every hard gate and adjacent legal case.
- [x] Run focused and full offline validation.
- [x] Commit, fast-forward integrate, verify ancestry, and retire the topic branch.

Subplan 3 is complete and integrated into local `main`. Final offline gate: 1401 passed, 2 Live
deselected. Subplan 4 (Isolated Workflow Vertical Slice) is ready but not active. Remote
publication remains pending explicit authorization; no Live tests were run.
