# TODO

Active subplan: `5-generic-workflow-foundation` on `refactor/general-workflow-runtime`.

- [x] Replace packaged role-specific node transfers with the generic `TextResult@1` result chain;
  keep legacy structured contracts compatible but optional.
- [x] Expose only Direct and the minimal multi-Agent starter as packaged Workflow suggestions;
  make built-in role prompts contract-neutral.
- [x] Add public Workflow clone-to-user-source support and prove arbitrary node insertion/editing.
- [x] Make request caps and admission timeout opt-in; update compiler, run evidence, scheduler,
  continuation/rerun, durable admission, integrity checks and GUI projections for `None`.
- [x] Update current documentation and execution-state authority.
- [x] Run focused and full offline/static/frontend validation.
- [>] Execute fresh disposable public-surface E2E and compatible configured Provider-backed lane;
  persist sanitized acceptance evidence.
- [ ] Commit coherent progress, fast-forward verified work to `main`, verify ancestry, and retire the
  clean topic branch. Remote push is not authorized.
