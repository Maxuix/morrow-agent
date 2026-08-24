# Subplan 75 — Integrated Acceptance and Closeout

> Status: completed locally
> Branch: `codex/feat/stage6-closeout`
> Prerequisite: Subplan 74 complete

## Objective

Exercise Stage 6 as a whole in isolated state, close integration defects without adding new scope,
ship representative offline examples, run all gates and reconcile documentation with implemented
facts.

## Ownership

- `tests/acceptance/` or existing acceptance-test locations for Stage 6 integrated scenarios
- safe fixture examples: handwritten Skill, generated Draft fixture, Fake stdio MCP Server and Fake
  second Provider adapter
- `docs/acceptance/stage6-skills-and-extensions.md`, README/CLI usage updates
- `docs/ARCHITECTURE.md`, Stage 6 roadmap and `.agent/` closeout state after implementation facts pass
- only narrow production fixes proven necessary by acceptance

## Tasks

1. Create an isolated state/data/workspace fixture; do not read or mutate the user's real Morrow
   state, Credentials, Skills or external MCP configuration.
2. Exercise handwritten Skill validate/install/enable/select/resource/disable and Workspace isolation.
3. Exercise accepted SkillCandidate→Draft→edit→validate→approve→explicit enable, Usage recording,
   new version, pin, rollback and referenced-history retention.
4. Exercise Script approval, sandboxed output Artifact, denial, timeout/cancel and escape rejection.
5. Exercise Provider add/model sync/use and prove current-run freeze, next-new-run refresh,
   closed-replay no-read and historical recovery.
6. Exercise Fake MCP add/inspect/refresh/enable/select/call, launch+tool approval, all normalized result
   families, Server crash isolation, no retry, recovery, disable and next-run removal.
7. Exercise conflicts, spoofed Trust/annotations, missing dependencies, package/executable drift,
   doctor, backup v1 verification and v2 isolated restore.
8. Run the full offline/quality/CLI gate in `.agent/PLAN.md`; capture exact command outcomes and
   bounded evidence in the acceptance document.
9. Fix only confirmed integration defects within existing architecture; if a new dependency,
   capability-policy default, event lifecycle or Stage 7 behavior is required, stop and request scope.
10. Update README human workflows and CLI help. Update `docs/ARCHITECTURE.md` only now, from actual
    modules and ownership; reconcile Roadmap completion status and final proposal deviations.
11. Commit verified closeout, fast-forward merge, verify branch/main relationship and retire the
    clean branch/worktree. Push only if explicitly authorized and configured.

## Validation

- All integrated scenarios above pass with scripted/Fake providers and local Fake stdio MCP.
- `uv run pytest -m 'not live'`
- Ruff format/check, compileall, all five Stage 6 CLI help commands and `git diff --check` from the
  master plan.
- Confirm no live/network/credential path ran and no unexpected user files changed.

## Exit criteria

- All Stage 6 completion criteria and cross-cutting invariants have deterministic evidence.
- Documentation matches code rather than future design, and the roadmap is marked complete.
- All Subplans 63–75 are merged/retired, repository state is clean except explicitly preserved user
  files, and no required work remains.

## Completion evidence

- Added isolated fixtures under `tests/fixtures/stage6/` and integrated coverage under
  `tests/acceptance/test_stage6_integrated.py`; no real user state, credentials or external MCP
  configuration is read.
- The final offline gate passed with `1060 passed, 2 skipped, 2 deselected`; the Stage 6 matrix,
  quality checks, CLI help and exact outcomes are recorded in
  `docs/acceptance/stage6-skills-and-extensions.md`.
- The only confirmed integration defect was generated Draft acceptance becoming undiscoverable:
  its controlled approval reference was not retained in `managed-version.json`. The envelope,
  discovery projection and catalog Trust projection now preserve that bounded evidence; unapproved
  generated packages remain `unknown` Trust. `state backup --version 2` is now explicit in CLI help.
