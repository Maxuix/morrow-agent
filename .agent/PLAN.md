# Stage 7 Preflight Reliability Repairs — S7P-03 Direct Coding Prompt Assembly

> Status: active
> Active subplan: 82 — Direct Coding profile and scoped project instructions
> Branch: `codex/feat/s7p-03-direct-coding-prompt`
> Base: verified local `main@e75c3b2`
> Source authority: the user-requested S7P-03 checklist, current code, and deterministic checks

## 1. Objective

Create one reusable Direct Coding prompt profile whose fixed safety boundary remains authoritative,
whose coding protocol tells the model how to inspect, change, verify and stop, and whose bounded
project-instruction projection automatically supplies root `AGENTS.md` plus deterministically
scoped nested instructions for explicit task targets. Freeze only prompt/source metadata on the
`AgentRunSnapshot`; keep instruction content in the verified in-process projection and fail closed
if recovery cannot reproduce the frozen hashes.

S7P-03 does not add AgentDefinition or Workflow persistence, file delete/move/rename tools,
completion verification, round-budget changes, Provider retry/backoff, steering, public event
lifecycle changes, runtime-policy default changes or dependencies.

## 2. Located defects

1. `render_system_boundary()` only describes identity, tool truthfulness and safety restrictions.
   It contains no inspect/edit/verify/stop protocol and no rule against unrequested plan/report or
   temporary-script files.
2. A current Direct context probe produces only two system messages, reports no coding protocol,
   and contains no `AGENTS.md` source even when the task explicitly names a repository file.
3. No project-instruction resolver exists under `src/morrow/`; `AGENTS.md` and compatible names are
   never discovered, decoded, scoped, bounded or assembled.
4. `ContextBuilder._system_messages()` orders the fixed boundary and then Skill/state/Preferences/
   Memory. It has no typed prompt-profile or role-prompt seam reusable by a future AgentDefinition.
5. `AgentRunSnapshot` has no prompt profile/version/digest or project-instruction source/hash
   evidence. Fresh runs and recovery therefore cannot prove that the same instructions were used.
6. The Code Agent Mini Eval profile already reserves `system_prompt` and `project_instructions`,
   but production currently cannot populate those fields from real runtime evidence.
7. Existing context tests prove Preferences and Skills remain low authority, but no test freezes the
   complete safety → coding protocol → role/project instructions → Skill/Memory/Preference → user
   assembly order or rejects instruction-originated permission escalation.

## 3. Frozen design decisions

### Prompt profile and authority

- Add a framework-neutral `DirectCodingProfile`/prompt assembler under the application prompt
  layer, not inside a Workflow. It exposes a stable profile ID, version, content digest, fixed
  coding protocol and an optional bounded role prompt for future AgentDefinition reuse.
- Keep `render_system_boundary(frozen_tools)` as the first and highest-authority system message.
  The coding protocol is the next fixed layer. Optional role prompt and project instructions are
  explicitly wrapped as lower-authority, workspace-controlled guidance that cannot grant tools,
  bypass approvals, widen the workspace, execute Shell text or override the fixed boundary.
- Preserve separate bounded system messages for Skill, state, Preferences, Memory and checkpoint.
  Freeze and test the deterministic order rather than concatenating untyped blobs.
- The fixed Direct protocol must require inspection before editing, a minimal evidence-backed
  change surface, preservation of user changes, risk-proportionate verification, completion based
  on verified outcomes rather than tool success, concrete blocker reporting, and no unrequested
  plans/reports/temp scripts.

### Project-instruction resolution

- `AGENTS.md` is the required supported filename. Compatible names such as `CLAUDE.md` are an
  explicit resolver configuration with lower filename priority and are disabled by default in the
  production Direct profile unless deliberately enabled by composition.
- Always inspect the workspace root. Extract at most a small bounded set of explicit workspace
  target paths from the user task (backticked or path-shaped tokens); expose an explicit
  `target_paths` seam so a future AgentDefinition/Workflow can supply already-resolved targets.
- For each target, walk only real ancestor directories from workspace root to the target directory.
  Select at most one instruction file per directory by configured filename priority, deduplicate
  common ancestors, and render root-to-leaf. Deeper instructions override shallower instructions
  only within their labelled directory scope; sibling scopes never affect each other.
- Resolve paths lexically and physically inside the confirmed workspace. Reject instruction-file
  symlinks, ancestry through symlinked directories, non-regular files, invalid UTF-8, NUL/control
  payloads, non-NFC text, excessive depth/count/per-file/aggregate bytes and hash drift. Missing
  optional files are not errors; an invalid file that exists fails closed with a bounded diagnostic.
- Read files directly through a read-only resolver. Project documents are never parsed as commands
  and no Shell/process/tool execution is triggered by their contents.

### Frozen evidence and recovery

- Add bounded prompt-profile and project-instruction metadata to `AgentRunSnapshot`: profile ID,
  version and digest; ordered source path/scope/kind/content hash/byte count; selection digest and
  resolver version. Do not put instruction content, task text, credentials or diagnostics in the
  durable snapshot.
- Resolve the instruction projection after read-only replay classification but before the journal
  transaction. The exact in-memory projection used to build the snapshot is installed after the
  commit, avoiding a second live read on the fresh-run path.
- Inject a project-instruction rehydrator into the turn restore/recovery paths. It re-reads only the
  frozen bounded sources and verifies every path, scope, version and digest. Missing, changed,
  symlinked or invalid evidence becomes `NEEDS_REPAIR`/quarantine rather than silently using live
  instructions or falling back to root-only context.
- Extend `RunContextProjection` with the verified prompt/project projection. `ContextBuilder`
  verifies the profile evidence and emits content from this projection; structured/non-chat views
  do not gain tools or execute project text.

### Compatibility and diagnostics

- Older AgentRun snapshots remain decodable. A snapshot with no prompt evidence uses the legacy
  boundary-only recovery projection; every newly admitted Direct run freezes the new profile.
- Resolver errors expose only a stable code plus bounded workspace-relative source/scope. They do
  not include instruction content, secrets, absolute host paths, tracebacks or full task text.
- Reuse current request-size accounting. Protected prompt layers may cause a typed
  `context_budget` failure; they must never be silently truncated into a different instruction set.
- Do not alter ToolSet, capability policy, permission snapshots, sandbox composition or public
  events. Prompt text is never an authorization source.

## 4. Test-first implementation sequence

1. Add failing prompt-assembly tests proving the current Direct request lacks the coding protocol,
   root `AGENTS.md`, nested scope ordering and frozen prompt/instruction evidence.
2. Implement the typed Direct Coding profile and assembler; freeze exact layer order and content
   digest, optional bounded role-prompt behavior, legacy snapshot compatibility and context-budget
   failure.
3. Implement the filesystem-only project-instruction resolver with explicit/path-extracted targets,
   filename priority, root-to-leaf/sibling scopes, count/depth/byte budgets and bounded diagnostics.
4. Add fail-closed matrices for oversized, invalid UTF-8, control/NUL, non-NFC, outside-root,
   symlinked, non-regular, changed and missing frozen instruction sources. Assert no process/Shell
   port is present or called.
5. Extend `AgentRunSnapshot`, `PreparedAgentRunSpec`, `RunContextProjection`, fresh admission and
   restore/recovery composition with reference-only prompt/project evidence and exact hash checks.
6. Add production-composition tests capturing the final OpenAI-compatible message order for an
   ordinary and auto-sandboxed Direct run. Assert project text cannot add a tool, change ToolSet,
   permission profile, recovery declaration or sandbox authority.
7. Add a scripted-Provider local task proving Morrow itself creates no plan/report/temp script and
   the final prompt contains the no-unrequested-artifact protocol. Do not claim this substitutes for
   the repeated external model measurement scheduled by S7P-09.
8. Publish `docs/acceptance/s7p-03-direct-coding-prompt.md`, update execution state, and run focused
   plus repository-wide offline gates.

## 5. Validation

```bash
uv run pytest -q tests/test_direct_coding_prompt.py tests/test_project_instructions.py
uv run pytest -q tests/test_context_projections.py tests/test_context_runtime.py
uv run pytest -q tests/test_agent_run_preparation.py tests/test_stage4_recovery_crash.py
uv run pytest -q tests/test_code_agent_mini_eval.py tests/test_agent_run_observability.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow run --help
git diff --check
```

No live Provider/model/Pi/MCP/network/credential test is authorized. Temporary workspaces,
captured fake Provider requests and scripted Providers provide deterministic offline evidence.

## 6. Completion, review and integration

- After coherent verified commits, the Luna Max implementation task must spawn a read-only Luna
  Max subagent in the same task to review the complete activation-base...HEAD diff. The review must
  check prompt authority/order, scope bleed, path/symlink races, recovery drift, secret/content
  persistence, compatibility, context-budget behavior and false-positive acceptance tests.
- The implementation task locally reproduces and fixes every confirmed finding, reruns all affected
  and full offline gates, commits acceptance/execution state and leaves its worktree clean.
- The implementation task does not merge, push, delete its branch/worktree, touch the three
  user-owned research documents or start S7P-04. This root task verifies ancestry/cleanliness,
  fast-forward merges into local `main`, then retires the clean task resources.
