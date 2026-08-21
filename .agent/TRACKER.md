# Progress Tracker

## Current status

Subplan 54's automated offline gate is complete, but the simulated-user evaluation committed at
`5cfb99f` confirmed two P1 blockers and one P2 preview defect. Subplan 55 is planned but has not been
authorized for implementation. The optional Live model-quality hold remains pending separately.

## Last completed task

The Stage 5 simulated-user report is committed at `5cfb99f`. Independent static adjudication
confirmed F1, F2, and F3 and found one adjacent inverted Project Knowledge edit guard. A bounded
Subplan 55 now specifies failing regressions, fixes, isolated user-flow replay, one independent
review/fix pass, and final gates; no production code was changed while drafting it.

Subplan 53 is verified and merged at `613ffdb`, including its one-time Grok review-fix pass. It adds
v12 MemorySelection and rebuildable terms, deterministic selection, frozen AgentRun/context
projections, bounded inspection surfaces, doctor/backup invariants, and the S53 acceptance docs.
Subplan 52 is verified and merged at `7dfe5af`, including its one-time Grok review-fix pass. It adds
prepared configuration OCC, recoverable YAML promotion/undo, Preference/Profile policy, provenance,
recovery, Session projections, and CLI/REPL controls. Subplan 51 is verified and merged at
`548f0bc`, including its one-time Grok review-fix pass.

## Active task

No implementation task is active. Proposed Subplan 55 awaits a user request to implement it.

## Next action

If implementation is authorized, create `fix/stage5-simulated-user-remediation` from `5cfb99f`,
activate only S55.1, and first preserve failing regression evidence.

## Blockers

Stage 5 user acceptance is blocked by F1 (headless Candidate decisions) and F2 (first Project
Knowledge Promotion). F3 is a confirmed UX defect. Live model quality evidence is pending explicit
authorization and a compatible credential; remote publication remains outside this planning request.

Remote publication is outside this planning request. Do not push without explicit authorization.

The two Stage 5 research discussions are untracked user files. They are preserved as input and are
not part of the implementation plan changes unless the user explicitly requests that they be
adopted into version control.

## Active boundary

- Subplan 54's automated gate is closed; Stage 5 user acceptance is reopened and proposed Subplan 55
  is planned but not active.
- First release exposes only `off` and `review_only`; `explicit_auto` remains closed.
- Automatic trigger is accepted TaskOutcome only; no `completed`/`corrected` Task status is added.
- No background worker, natural-language acceptance, embedding/vector dependency, Skill write, or
  Workflow activation is authorized.
- Profile/Preferences remain YAML authorities; Project Knowledge and learning audit records use the
  shared Operational Store.
- S51.1 is verified: v11 upgrade/rollback, decision/Knowledge/memory persistence, corruption and
  reserved Saga constraint tests passed on `feat/stage5-inbox-knowledge`.
- S51.2 is implemented and focused query/preview tests pass; the legacy API methods remain
  compatible while new typed view entry points live under `api.learning` and `api.memory`.
- S51.3–S51.6 are implemented: candidate decisions/rejection/suppression/expiry, Project Knowledge
  promotion and lifecycle, replay/OCC/workspace isolation, and preview-confirm CLI/REPL boundaries.
- S53.1 is implemented on the active branch: v12 MemoryQuery/Selection contracts, immutable
  selection/item and rebuildable-term tables, repository codecs/guards, v11 upgrade/future/
  corruption coverage, and legacy migration expectation updates. Focused validation passed with
  83 tests, Ruff format/check, compileall, and `git diff --check`.
- S53.2 is implemented: pure bounded mixed-language/code/path tokenizer, deterministic term
  generation, transactional rebuild/clear on Project Knowledge promotion and lifecycle changes,
  and bounded lexical candidate retrieval. Full offline validation passed with 760 tests, 2 skips,
  1 deselection, repository-wide Ruff format/check, compileall, and `git diff --check`.
- S53.3 is implemented: independent bounded selector with workspace/status/validity/sensitivity
  filters, explicit/category/lexical candidate sources, deterministic score tuple, category
  diversity, item/character budgets, explainable reasons, canonical rendering, and selection
  digests. Full offline validation passed with 763 tests, 2 skips, 1 deselection, Ruff
  format/check, compileall, and `git diff --check`.
- S53.4 is implemented: foreground admission now builds and persists one MemorySelection with the
  AgentRun snapshot, freezes effective global/workspace/session Preferences with source revisions,
  recovery reuses the exact selection, and restore/recovery fail closed on missing or mismatched
  selection references. Focused admission/recovery/rollback tests plus the full offline gate passed:
  767 tests, 2 skips, 1 deselected; Ruff, compileall, CLI help, and diff check passed.
- S53.5 is implemented: durable Sessions now install a verified RunContextProjection, ContextBuilder
  consumes the exact frozen Profile/Preferences and canonical untrusted Project Knowledge block,
  same-Run live changes remain invisible, and new Runs/legacy process-local Sessions retain explicit
  behavior. Full offline validation passed: 771 tests, 2 skips, 1 deselected; Ruff, compileall,
  CLI help, and diff check passed.
- S53 is complete: its one-time Grok review/fix pass found two confirmed bugs, two risks, and one
  feasible suggestion; all feasible findings were independently fixed once. The final gate passed
  785 tests, 2 skips, 1 deselected, plus Ruff, compileall, CLI help, and diff check. Local `main`
  is fast-forwarded to `613ffdb`; the S53 topic branch is retired.
- S54.1 is complete: `ModelLearningReviewer` now uses explicit no-tool messages, bounded request/
  response budgets, one total deadline with at most one repair, strict candidate/evidence checks,
  sanitized provider errors, active-provider composition, and bounded repair metadata in results/
  events. The full offline gate passed 790 tests, 2 skips, 1 deselected; the checkpoint is `c955bbc`.
- S54.2 is complete: Learning policy status/mode controls, explicit-auto refusal, review/retry CLI and
  REPL commands, active-provider headless composition, zero-candidate notifications, and foreground
  cancellation behavior are covered. Full offline validation passed 792 tests, 2 skips, 1 deselected;
  the checkpoint is `9ef6f01`.
- S54.3 is complete: Skill/Workflow/Orchestration future candidate acceptance is covered as a
  candidate-only outcome; no Knowledge head, memory activation, workspace file, or future runtime
  state is created. Full offline validation passed 794 tests, 2 skips, 1 deselected; checkpoint is
  `4ff41a4`.
- S54.4 is complete: versioned synthetic JSON cases and a deterministic no-Provider evaluator cover
  durable/temporary/negative/quoted/hypothetical/Assistant-only, injection/secret/hidden-Unicode/
  capability, duplicate/suppression/workspace, malformed Reviewer, future candidate-only, and
  Selection budget/freeze boundaries. The report records 27/27 pure-evaluator cases passed, 5
  safety-negative cases, and zero evaluator writes; the scripted real-runner safety gate also
  records zero Candidate/Knowledge/Memory Active writes. Focused tests passed 16; the full offline
  gate passed 804 tests,
  2 skips, 1 deselected; checkpoint is `6f77940`.
- S54.5 is complete: read-only Learning doctor coverage is split into focused Review/Evidence,
  Candidate/Suppression, and Promotion/Knowledge modules; backup verification covers v10–v12
  Learning references and isolated SQLite bundles exclude YAML/credentials. Product acceptance docs
  record REPL/headless, restart, crash/OCC, workspace isolation, future-candidate, doctor, and
  backup evidence. The focused doctor/backup set passed 23 tests; the full offline gate passed
  `808 passed, 2 skipped, 1 deselected`; Ruff format/check, compileall, CLI help, and diff check
  passed. S54.6 was then activated; its Live hold remains pending.
- S52 is complete: prepared configuration revisions/digests, YAML promotion Saga, Preference/
  Profile whitelist, foreground recovery, activation provenance/undo, Session projection updates,
  recoverable after-state finalization, explicit REPL global scope, and activation memory events.
  Grok reported 15 findings; five real recovery/OCC defects and feasible presentation/recovery gaps
  were independently fixed once. Final offline gate: 752 passed, 2 skipped, 1 deselected; no second
  review was run.
- S51 Grok review found four confirmed issues; all four plus edited-payload duplicate/suppression
  revalidation and replay/presentation polish were independently fixed once. The final gate passed
  739 tests, Ruff, compileall, CLI help, and `git diff --check`; no second review was run.
- AgentLoop and ConversationLog ownership, public runtime events, bundled capability policy, and
  credentials remain unchanged.
