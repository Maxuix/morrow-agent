# Subplan 61 — Preference v2 Closeout and Quality Gates

> Status: planned; not active
> Branch: `refactor/stage5-preference-v2-closeout`
> Prerequisite: Subplan 60 merged
> Owns: legacy public-surface retirement, doctor/backup, evaluation harness, docs and full gate

## Goal

Remove obsolete active-path complexity, prove migration/recovery/quality boundaries, and make the
repository and user documentation describe the implemented Preference v2 behavior truthfully.

## Tasks

### S61.1 Retire obsolete active paths

- Remove fixed `language`, `response_detail`, and `instructions` from public active Preference models,
  tool schemas, CLI help, Reviewer prompt/schema, and normal tests.
- Remove persistent/non-durable/quotation keyword marker classification from the Preference path.
  Keep only isolated legacy migration/snapshot/Candidate decoders with explicit names and tests.
- Delete dead adapters, duplicated merges, and stale compatibility branches once `rg` and coverage
  prove no supported caller remains.
- Split files that accumulated unrelated responsibilities; do not conceal a god file behind private
  helpers or re-export cycles.

### S61.2 Doctor, backup, and restore

- Extend read-only doctor checks for v13 job/Evidence/proposal/write-batch links, lease/retry state,
  workspace isolation, YAML revision/digest consistency, tombstone lifecycle, and migration state.
- Extend SQLite backup verification for v13 references while keeping global/workspace YAML and
  credentials outside the SQLite bundle according to the existing authority boundary.
- Add full isolated state backup/restore acceptance that separately verifies YAML entries and SQLite
  audit data without copying credential material.
- Verify doctor remains read-only and reports bounded IDs/reason codes only.

### S61.3 Semantic evaluation corpus

- Replace field-schema-led Preference quality cases with versioned natural-language cases covering
  add, paraphrase, replace, remove, multiple operations, global/workspace scope, corrections,
  temporary requests, quotation, hypothetical text, Assistant/tool/repository content, secrets,
  injection, hidden controls, and capability grants.
- The offline evaluator checks contracts and zero-write safety with scripted outputs; it explicitly
  does not claim semantic accuracy.
- Update the opt-in real-Provider harness to score operation recall, precision, replace/remove target
  accuracy, zero safety-negative Active writes, latency/attempts, and next-AgentRun adherence. It
  writes only sanitized bounded reports under isolated state.
- Freeze the scoring arithmetic before execution: 12 positive intents with at least seven target
  cases; pass requires at least 11/12 positive operations, 90% proposal precision, at least 6/7
  correct replace/remove targets, zero safety-negative Active writes, and at least 9/10 adherence
  probes. Report every numerator and denominator.
- Do not run the real-Provider corpus until the master plan's post-implementation clean-tree condition
  is met.

### S61.4 Documentation and product consistency

- Reconcile `docs/ARCHITECTURE.md`, Stage 5 roadmap, root roadmap, README, command help, acceptance
  docs, schema/version tables, authority diagrams, async lifecycle, and migration guidance.
- Clearly distinguish Preference injection from Project Knowledge MemorySelection and direct approved
  management from inferred Inbox proposals.
- Record the old real-Provider `0/3`, `0/2`, timeout, and stale-Session evidence as the baseline that
  motivated the refactor; do not overwrite it with new results.
- Update `.agent` state and create the post-implementation test checklist, but do not mark user
  acceptance complete before those tests run.

### S61.5 Integrated gate and subplan review

- Run all focused migration, Writer, Reviewer, worker, context, doctor, backup, CLI, Task, Tool,
  permission, and Project Knowledge regressions.
- Run the full non-live suite and repository quality/CLI help gates.
- Perform S61's one Grok review/fix cycle, close the branch, and merge it before the separate final
  integrated Grok review required by the master plan.

## Primary files

- legacy Preference/learning modules proven obsolete by search/tests
- `src/morrow/application/doctor.py` and bounded Preference doctor helper
- backup verification modules
- `src/morrow/resources/` evaluation corpus
- `tests/test_preference_evaluation.py` and opt-in live tests
- `README.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, Stage 5 acceptance/roadmap docs

## Acceptance

- Normal source/help/tool schemas contain no fixed Preference field taxonomy or keyword semantic
  classifier; only named migration/history code can decode it.
- Doctor and backup catch every declared v13/YAML inconsistency and expose no sensitive payload.
- Offline safety evaluator proves zero Active writes for all negative cases without claiming model
  quality.
- Full non-live test, Ruff, compileall, CLI help, and diff gates pass.
- Documentation matches actual background, migration, Inbox, Writer, injection, and hold behavior.
- Stage 5 remains “implementation complete; post-implementation user/live acceptance pending” until
  the master protocol runs.

## Validation

```bash
uv run pytest -q tests/test_preference_evaluation.py tests/test_preference_doctor_backup.py \
  tests/test_stage5_doctor_backup.py tests/test_preference_migration_acceptance.py \
  tests/test_stage5_learning_evaluation.py tests/test_stage_boundary.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow preferences --help
uv run morrow learning --help
uv run morrow memory --help
git diff --check
```

## Review and closeout

After the implementation checkpoint and gates pass, invoke exactly one `$grok-delegate` `/review`
for S61 and wait for the complete result. Independently adjudicate, fix confirmed/valuable findings
once, rerun the affected and S61 gates, and do not re-review the fix. Commit closeout, fast-forward
merge, and retire the clean branch.

Then follow the master plan: run one separate integrated Grok `/review` across S56–S61, perform its
single adjudication/fix pass, rerun the complete gate, commit a clean implementation closeout, and
only then begin the post-implementation simulated-user and real-Provider protocol.
