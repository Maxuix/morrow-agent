# TODO

## Current stage

Stage 4 and its user-authorized post-acceptance architecture refactor are complete. Stage 5 remains
inactive.

## Active subplan

None.

## Tasks

- [x] S4.46.1 Freeze dependency/transaction invariants and consolidate Recovery lifecycle ownership.
- [x] S4.46.2 Extract domain application handlers behind the compatible operational API facade.
- [x] S4.46.3 Activate narrow SQLite journal ports while sharing one transaction session and
  confining the concrete adapter to cross-domain composition.
- [x] S4.46.4 Decompose AgentLoop event emission without changing ConversationLog ownership or
  public event lifecycle.
- [x] S4.46.5 Remove CLI reach-through to API journal internals and establish domain command seams.
- [x] S4.46.6 Reconcile docs, run full gates, and commit verified work.
