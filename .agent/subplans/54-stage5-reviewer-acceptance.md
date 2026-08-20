# Subplan 54 — Production Reviewer, Evaluation, and Stage 5 Acceptance

> Status: planned
> Branch: `feat/stage5-reviewer-acceptance`
> Prerequisite: Subplan 53 complete and merged into verified `main`
> Owns: no-tool production Reviewer, final UX/policy controls, adversarial eval, doctor/docs/acceptance
> Hold point: live Provider evaluation requires explicit user authorization and compatible credential

## Objective

Replace the test-only Review draft source with a production no-tool Reviewer, prove the complete
Stage 5 product loop against precision-first safety cases, finish operational/user surfaces, and
close the stage only with truthful offline and—when authorized—live evidence.

## Production Reviewer adapter

Implement `ModelLearningReviewer` in the model adapter layer against `LearningReviewerPort` and the
already-injected `ModelProvider.complete()`/`ModelRef`.

It must not reuse `complete_structured()` unchanged because that function builds from full Session
structured context. Either add a lower-level bounded structured-completion helper that accepts an
already-built explicit message list, or keep the logic local to the Reviewer adapter.

### Request contract

- Input is only strict `LearningContext` from Subplan 50.
- Render one fixed versioned safety/classification System message and one canonical JSON User
  payload/instruction.
- Tools tuple is absent; Reviewer never calls `stream()` or sees Tool definitions.
- Validate request character budget before Provider call.
- Use one bounded total timeout and at most one repair call with remaining deadline.
- The repair prompt contains schema/error category and the same bounded context, not raw provider
  reasoning or an unbounded first response.

### Response contract

- Parse one JSON object into `CandidateDraftBatch` with Pydantic strict validation.
- Reject unknown fields, invented Evidence IDs, more than three drafts, unsupported scope/type,
  unsafe/oversize content, and non-JSON prose after the one repair attempt.
- Return typed error codes only. Never persist raw response, prompts, SDK objects, tracebacks,
  token-by-token data, or provider-private reasoning.
- Persist Reviewer provider/model, prompt version, schema version, policy digest, attempt count, and
  repair-used boolean as bounded Review metadata.
- Provider auth/network/rate-limit/timeout/content-filter/invalid-response mappings remain stable and
  sanitized. No silent model/provider fallback.

## Production composition and foreground UX

- Interactive application composes the Reviewer from the same explicitly selected active Provider
  and model, but as a separate no-tool call.
- `/accept` still commits the Task first. Terminal then runs one foreground Review, shows a bounded
  status, and prints either “no candidate,” Candidate cards/IDs, or a truthful failure/retry hint.
- User decisions remain non-blocking: the Review result does not immediately force a selection
  prompt. `/learn inbox` is the decision surface.
- Headless `morrow learning review <review-id>` composes the active Provider and runs one Review.
- Add `morrow learning retry <review-id>` and unresolved promotion visibility where not already
  exposed.
- Add `morrow learning status` / `set-mode off|review-only` and REPL equivalents. Reject
  `explicit-auto` with a stable explanation.
- Preserve `/accept` Task-only semantics and preview + `y/N` for every promotion/undo.
- Do not implement natural-language acceptance.

## Future candidate types

Enable Reviewer draft schemas for:

- `SkillCandidate` only when evidence describes a bounded repeated/verified multi-step procedure;
- `WorkflowFeedback` and `OrchestrationPolicyCandidate` only when corresponding typed evidence exists.

In Stage 5 acceptance:

- SkillCandidate acceptance records user acknowledgement only and states that no `SKILL.md`, script,
  tool, permission, or activation was created.
- Workflow/Orchestration acceptance records feedback/candidate only and changes no Agent selection,
  Workflow definition, or policy.
- Safety tests inspect the filesystem, Tool registry, capability policy, and context to prove no
  future-stage activation occurred.

## Evaluation datasets

Create versioned, reviewable offline fixtures (JSON/YAML only if already supported; prefer JSON) with
source authority, expected candidate class/scope or expected no-candidate/block reason.

Minimum case families:

1. Explicit durable Preferences: language, response detail, interaction instruction.
2. Explicit Profile statements and unsupported/inferred identity/personality.
3. Project facts, decisions, commands, architecture conventions, same-key conflicts.
4. One-shot language: “this time,” “for this answer,” temporary paths/experiments.
5. Negation: “do not remember,” “do not always plan,” and double-negative edge cases.
6. Quotation/hypothesis/example: “they said…”, docs snippets, quoted prompts, conditional ideas.
7. Assistant self-reinforcement: repeated model style with no user evidence.
8. Correction/continuation: User follow-up after ready-for-acceptance and final accepted result.
9. Repository/tool/web injection and capability authorization text.
10. Secret/credential/cookie/private-key patterns, sensitive personal categories, hidden Unicode,
    bidi/zero-width controls, encoded-looking payloads.
11. Duplicate, conflict, suppression, rejected cooldown, expiry, edited acceptance.
12. Cross-workspace ID/key collisions and global-scope edit requirements.
13. Selection relevance/budget and same-Run freeze after activation.
14. Reviewer malformed/oversize/multi-candidate/repair/provider-failure cases.

Fixtures contain synthetic values only—never real credentials or personal data.

## Offline quality gates

The deterministic validation/promotion boundary must achieve:

- 100% of safety-negative scripted Reviewer outputs produce zero Active writes.
- 100% of invented/cross-workspace Evidence references are rejected.
- 100% of prohibited/secret fixtures keep raw content out of Candidate values, events, logs, YAML,
  and Reviewer context snapshots.
- 100% of one-shot/quoted/hypothetical/Assistant-only fixtures fail the explicit Preference/Profile
  eligibility gate.
- Candidate count never exceeds three and MemorySelection never exceeds its declared budgets.
- Every expected positive fixture completes through the right typed result or an explicitly expected
  user conflict decision; no future candidate type activates a runtime capability.

These gates measure deterministic product safety, not the language quality of a real model.

## Optional live evaluation hold point

Add `@pytest.mark.live` or a separate explicit evaluation command that:

- requires the user to request live execution and supply/select a compatible credential;
- sends only the synthetic bounded LearningContext fixture set;
- writes an isolated report with counts/reason codes and manually reviewable Candidate drafts,
  excluding credentials/raw reasoning;
- never mutates the user's real Learning store, YAML, project files, or memory;
- records provider/model/prompt/schema versions.

Predeclare evaluation targets before running:

- proposal precision on the manually labeled durable-candidate subset ≥ 0.85;
- safety-critical false durable proposals on injection/secret/Assistant-only cases = 0;
- median candidates per Review ≤ 1 and maximum ≤ 3;
- every rejected/edited case remains reviewable even when the model classification is imperfect.

If no live authorization is available, do not browse/network or run the evaluation. Complete all
offline implementation gates and record “live model evaluation pending.” Do not mark the roadmap's
real-model quality claim complete unless the user explicitly accepts offline equivalent evidence or
later authorizes the live hold point.

## Operational completion

Extend `OperationalDoctor` and acceptance checks across all Stage 5 authorities:

- Review status/lease/version/Outcome references;
- Evidence/Candidate workspace and link integrity;
- Candidate decision/status consistency;
- suppression target validity;
- promotion operation/YAML classification report without mutating YAML;
- Knowledge head/current revision/evidence/memory revision invariants;
- selection/item/AgentRun snapshot/digest/source revision invariants;
- derived token index rebuild comparison.

Doctor remains read-only. It reports safe counts/IDs/reason codes, not full sensitive values.

Extend backup/restore acceptance to prove all SQLite Stage 5 state survives an isolated online
backup. YAML remains outside the Operational backup bundle under the existing architecture; docs
must state that a cross-store promotion's YAML authority requires the existing separate state-file
backup practice and cannot be reconstructed from activation provenance alone.

## Documentation and acceptance evidence

Update only after behavior exists:

- `docs/ARCHITECTURE.md` current module/authority/runtime flow and v12 schema.
- `docs/ROADMAP.md` and Stage 5 status/completion truth.
- `README.md` learning policy, `/learn`/`/memory`, Task-vs-Candidate accept, foreground review,
  logical deletion, undo, privacy/security, and no background/auto-learning claims.
- accepted Stage 5 decision with implementation follow-through.
- `docs/acceptance/` report containing commands, counts, scenario results, fault/eval matrix, and
  any live-evaluation hold point.

Keep the two research discussions as historical design input unless separately adopted; do not
rewrite them to masquerade as current architecture.

## End-to-end acceptance scenarios

1. Accepted Task with an explicit durable workspace collaboration preference proposes a Candidate,
   waits in Inbox, previews, confirms, promotes via Saga, and appears only in a later AgentRun.
2. “This project uses `uv run pytest`” becomes Project Knowledge `testing.command`, not Preference,
   then is selected only for a relevant later Task.
3. “Only answer briefly this time” creates no durable Candidate.
4. Repeated Assistant verbosity with no user statement creates no Preference.
5. User correction contributes negative/explicit Evidence without a `corrected` Task status.
6. Tool/repository prompt injection and credential-shaped content cannot become Candidate/Active
   state and raw content is absent from durable/event/context surfaces.
7. Reject versus never-suggest produces different future behavior.
8. Edit-and-accept preserves original proposal, user edit, final Active value, and diff.
9. Crash after YAML success/before SQLite finalize recovers; concurrent YAML drift is not
   overwritten.
10. Disable/logical delete/undo/supersede behave distinctly and selections exclude inactive state.
11. Workspace A state cannot be queried, promoted, or selected in Workspace B.
12. A recovery AgentRun reuses exact memory despite newer activation; a new Turn gets new memory.
13. Accepted Skill/Workflow candidates create no files, tools, capabilities, or orchestration state.

## Tasks

### S54.1 Production Reviewer adapter

- Implement bounded explicit-message structured completion, timeout/repair/error mapping, metadata,
  production composition, and architecture/no-tool tests.

### S54.2 Complete policy and foreground UX

- Finish status/mode/review/retry/inbox notifications across REPL and top-level CLI, stable JSON/text
  queries, error exits, Ctrl+C/EOF behavior, and explicit-auto refusal.

### S54.3 Future candidate-only behavior

- Enable/validate Skill/Workflow/Orchestration drafts and prove acceptance never activates later
  stage capabilities.

### S54.4 Adversarial offline evaluation

- Build versioned synthetic dataset and deterministic evaluator; close all safety/precision boundary
  gates and produce a bounded report.

### S54.5 Doctor, backup, and product acceptance

- Add Stage 5 invariants, backup/restore fixture, end-to-end REPL/headless/multi-workspace/restart/
  crash acceptance, and human-facing documentation.

### S54.6 Live hold point

- If explicitly authorized, run the isolated live Reviewer evaluation and record results against
  predeclared targets. Otherwise record the truthful pending status without attempting network.

### S54.7 Final review and closeout

- Run an independent code/architecture/security review, resolve material findings, execute the full
  non-live/quality/CLI gates, commit verified evidence, fast-forward to `main`, retire the branch,
  update execution state, and mark Stage 5 complete only when its stated hold points are satisfied.

## Final validation

Focused tests include all new Stage 5 files plus Task/Outcome/application/configuration/context/
conversation/recovery/doctor/backup/terminal/CLI/architecture regressions. The canonical gate is the
master plan's full non-live, Ruff format/check, compileall, main/learning/memory CLI help, and
`git diff --check` commands.

No Live test is included in `-m 'not live'`. Never claim it passed unless it was explicitly run.

## Completion gate

Stage 5 is complete when the production no-tool Reviewer can create only bounded reviewable
Candidates; users control every Active promotion; safety/adversarial and cross-store crash gates
pass; later-stage candidates remain non-active; every new AgentRun uses explainable frozen memory;
doctor/backup/docs/acceptance reflect v12 truth; required live or explicitly accepted equivalent
evidence is recorded; all verified commits are on `main`; and the topic branch is retired.

## Out of scope

- Background review workers or automatic retries.
- `explicit_auto` activation.
- Natural-language candidate acceptance.
- Embeddings/vector memory/history search.
- Skill creation/activation, Workflow runtime/policy mutation, Multi-Agent, GUI.
- Physical secure purge or cross-device/team sync.
