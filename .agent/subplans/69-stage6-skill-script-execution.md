# Subplan 69 — Skill Script Execution

> Status: completed locally
> Branch: `codex/feat/stage6-skill-scripts`
> Prerequisite: Subplan 68 complete

## Objective

Execute approved Skill scripts through a dedicated service with frozen package integrity, read-only
Skill input, isolated output, existing capability/approval/audit semantics and Artifact results. Do
not expose a general shell or silently fall back to an unconfined host process.

## Ownership

- `src/morrow/application/skills/scripts.py`
- focused reusable process primitives extracted from `services/process.py` only where necessary
- Skill script tool adapter beside other Skill application modules
- thin bootstrap registration and Artifact import integration
- `tests/test_skill_scripts.py` plus permission/recovery tests

## Tasks

1. Define a structured request containing selection/version ID, script relative path, argv, bounded
   environment names, input Artifact refs and timeout. Shell strings are rejected.
2. Implement `SkillScriptExecutionService` over `ProcessAdapter`, cancellation, `SecretRedactor` and
   result projection primitives, not over workspace-rooted `ProcessExecutionService`.
3. Revalidate managed envelope/tree/script digest immediately before launch and execute from a
   read-only frozen package view or verified temporary copy.
4. Create an isolated temporary output root; only declared input Artifacts/resources are readable.
   Do not expose writable project paths, Morrow state roots, CredentialStore or ambient secrets.
5. Map manifest requests and actual execution shape to one OperationIntent. Default is sandboxed,
   no network, no credentials, no outside-workspace and no destructive/external effect. Missing
   sandbox rejects rather than degrading.
6. Route the registered script tool through ToolExecutor, CapabilityPolicy, approval, cancellation,
   ToolExecution Journal and recovery declaration. A script request cannot add capabilities.
7. Redact/truncate stdout/stderr and import declared output files into ArtifactStore after path/type/
   size checks. Conversation/model receives bounded summaries and Artifact refs only.
8. Clean temporary state on success/failure/cancellation without deleting durable Artifact evidence.

## Validation

- argv validation, cwd/root escape, symlink/TOCTOU, env filtering, network/credential requests,
  missing sandbox, timeout/cancel, output escape/size/type, redaction and Artifact import.
- Approval/denial and crash classification through existing ToolExecution; no auto retry after entry.
- Prove ProcessExecutionService retains workspace behavior and Skill service cannot write user/builtin
  or project files.
- Focused script/process/permission/recovery tests, standard quality commands and full non-live suite.

## Exit criteria

- A valid generated/imported script runs only under its frozen, approved constraints.
- Script execution is not a second process policy path and cannot widen its own authority.
- No general shell tool or ambient host access was introduced.

## Exit evidence

- Implemented `SkillScriptRequest`/`SkillScriptResult`, verified `FrozenSkillPackage` capture,
  `SkillScriptExecutionService`, ToolExecutor registration, capability policy routing, recovery
  declaration and redacted Artifact output publication.
- Added focused coverage for request bounds, frozen-package drift, sandbox availability, root and
  symlink escape, input mutation, permission denial, redaction, timeout and temporary cleanup.
- Validation: focused Skill/Tool/Process/Recovery suite `82 passed, 2 skipped`; full non-live suite
  `1023 passed, 3 skipped, 2 deselected`; Ruff format/check, compileall and `git diff --check`
  passed.
- Git checkpoint: `73f99db feat(stage6): add isolated skill script execution`; no remote push.
