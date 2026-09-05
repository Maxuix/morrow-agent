# Progress Tracker

Subplan 12 active on `feat/stage8-readonly-parallel`, based on verified local main `9ada9b0`.
Implementation and entry evidence are in place: frozen read contracts/permissions, transactional
slot admission, existing request ledger reuse, stable gather, cancellation and recovery.
25 new parallel cases now cover the admission-barrier and catalog-filter edges. The prior 23
plus related serial/Pause/continuation matrix passed together (72 passed).
Ruff format/check, compileall, CLI help and diff check passed. Second full offline gate passed (1747 passed / 2 skipped / 2 Live deselected, 331.01s).
Production Core Host/API verification exposed missing workspace capability on the leaf Session;
the initializer now freezes the same host capability/profile already used by its ToolExecutor.
The real API case passed with three concurrent nodes. Final production-composition regression
is running (`/tmp/morrow-subplan12-production-final-offline.log`); integration remains pending.

Remote push and Live tests remain unauthorized.
