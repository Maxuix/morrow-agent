# Handoff Removal Refactor Plan

> Status: complete
> Active subplan: none
> Scope: post-Stage-2 cleanup; this plan does not start Stage 3 or Stage 4

## Objective

Remove the transitional workspace Handoff feature completely from the current product,
runtime, domain model, state API, configuration surface, tests, package, and active
documentation before persistent Session architecture begins.

The completed tree must have one honest continuity boundary:

```text
Persisted now
├── Workspace identity
├── Profile
├── Global/workspace Preferences
└── Provider configuration and credential references

Process-local now
└── Session-owned ConversationLog

Deferred to Stage 4
├── Persistent Sessions
├── Session resume/list/archive/delete
├── Fork
├── Context summaries/checkpoints
└── Long-term memory
```

No renamed Handoff, temporary checkpoint, current-goal file, summary-on-exit path, or
compatibility writer may replace the removed feature in this refactor.

## Product decision

Handoff is no longer a user-facing product concept or a supported persistence mechanism.
It was a Stage 1 continuity experiment and became a Stage 2 compatibility surface, but it
would duplicate the planned persistent Session authority and increase future migration,
approval, context, and multi-client costs.

After this refactor:

- startup does not discover, display, load, or validate a Handoff;
- ordinary chat never receives Handoff state in model context;
- `/handoff` and the current Handoff-backed `/continue` behavior do not exist;
- `/new` still starts a fresh process-local Session, with explicit discard confirmation
  when the current Session is dirty;
- `/exit` still protects dirty process-local content with explicit discard confirmation;
- no exit, switch, cancellation, or error path calls a summary model or publishes
  workspace continuation state;
- legacy `handoff.yaml` and `.bak` files are ignored and never deleted automatically;
- cross-process conversation continuation is explicitly unavailable until Stage 4.

## Authority and precedence

1. The user decision to remove Handoff now owns the product direction.
2. This plan owns removal scope, ordering, temporary compatibility, and acceptance.
3. The active subplan owns the executable tasks for one bounded slice.
4. Current code and validation results override stale plan wording and must be reconciled
   before proceeding.
5. Completed Stage 1/2 roadmaps, reviews, acceptance evidence, and Git history remain
   historical records; they must not be rewritten to claim Handoff never existed.

## Baseline and impact inventory

The accepted Stage 2 product/source/test baseline is commit `831c4ea`
(`feat: complete stage 2 agent core`). Before implementation starts, worktree differences
from that baseline are limited to this `.agent/` planning set. Handoff currently reaches
12 production files with direct Handoff coupling, one additional user-facing package
tagline, and 11 test files.

### Production surface

| Area | Current Handoff coupling | Final action |
|---|---|---|
| `services/handoff.py` | generation, repair fallback, publish, dirty clear | delete file |
| `core/models.py` | `Decision`, `Handoff`, `HandoffDocument`, patch target | remove symbols |
| `core/ports.py` | Handoff load/backup/write/clear store API | remove methods |
| `adapters/state/yaml.py` | `handoff.yaml` typed document methods | remove methods/imports |
| `runtime/session.py` | loaded value, source revision, continuation state | remove fields/property |
| `application/context.py` | system injection and fallback projection | remove purpose/branches |
| `application/commands.py` | `/handoff`, `/continue`, revisions and actions | remove/replace paths |
| `interfaces/terminal.py` | update/clear/load/save/switch/exit handling | remove and simplify |
| `interfaces/cli.py` | onboarding goal, startup discovery and display | remove behavior |
| `services/workspace.py` | inspection, read-only coupling, initial write | remove coupling |
| `services/preferences.py` | Handoff patch whitelist and apply path | remove target/logic |
| `bootstrap.py` | service construction, injection and return tuple | remove wiring |
| `__init__.py` | `Pick up where you left off` package tagline | replace with neutral truthful text |

### Test surface

The affected files are:

- `tests/test_context_projections.py`
- `tests/test_context_runtime.py`
- `tests/test_conversation_and_loop.py`
- `tests/test_core_contracts.py`
- `tests/test_preferences_and_orchestration.py`
- `tests/test_stage2_e2e.py`
- `tests/test_stage2_product_acceptance.py`
- `tests/test_stage_boundary.py`
- `tests/test_state_and_workspace.py`
- `tests/test_structured_and_handoff.py`
- `tests/test_terminal.py`

Tests that cover generic structured completion, ConversationLog, context projections,
atomic YAML publication, workspace isolation, or terminal cancellation remain. Only the
Handoff-specific behavior is removed or replaced with the post-removal product contract.

## Execution strategy

The refactor is split into four ordered subplans. Each subplan must finish with an
integrated green tree. After Subplan 21, temporary compatibility is allowed only for
uncalled model/port/YAML definitions that Subplan 22 deletes immediately. No production
caller, configuration route, startup path, or runtime branch may read or write Handoff at
that boundary.

| Order | Subplan | Status | Depends on | Result |
|---|---|---|---|---|
| 21 | [Product and Runtime Removal](subplans/21-handoff-product-runtime-removal.md) | completed | Stage 2 accepted | No user/runtime flow can load, generate, inject, edit, clear, or save Handoff |
| 22 | [Domain, State, and Configuration Excision](subplans/22-handoff-domain-state-excision.md) | completed | 21 | No production symbol, store method, patch target, or packaged module remains |
| 23 | [Documentation and Historical Reconciliation](subplans/23-handoff-documentation-reconciliation.md) | completed | 22 | Current docs describe the reduced product honestly; historical evidence is classified |
| 24 | [Acceptance and Delivery](subplans/24-handoff-removal-acceptance.md) | completed | 21–23 | Full offline/package/product gates prove complete removal without Stage 3/4 leakage |

Only one subplan may be active at a time. A failed gate reopens the subplan that owns the
broken contract.

## Cross-cutting contracts

1. **No replacement bridge:** do not introduce `Checkpoint`, `ResumeState`, `TaskState`,
   `current_goal.yaml`, automatic summary, or persistent ConversationLog in this plan.
2. **Single chat history authority:** Session-owned `ConversationLog` remains the sole
   chat-history writer and remains process-local.
3. **Dirty-content safety:** until Session persistence exists, `/new` and `/exit` must
   require an explicit discard confirmation for a dirty Session; they must not invoke a
   model or write continuation state.
4. **No legacy reads or writes:** production callers must not discover, validate, load,
   migrate, clear, overwrite, back up, or delete legacy Handoff files after Subplan 21;
   Subplan 22 deletes the then-uncalled port/YAML definitions.
5. **Non-destructive user data policy:** the refactor leaves existing `handoff.yaml` and
   `.bak` bytes untouched. Cleanup, import, or deletion requires a future explicit user
   decision.
6. **Narrow corruption isolation:** an ignored legacy Handoff file cannot place the
   workspace, Profile, or Preferences into read-only mode.
7. **Generic infrastructure survives:** structured completion, chat/structured context
   projection, atomic YAML publication, revisions, backups, Profile, Preferences,
   workspace identity, AgentLoop, tools, policy, and event lifecycle remain unless a
   focused test proves Handoff-only ownership.
8. **No compatibility aliases:** do not retain deprecated Handoff commands, model aliases,
   store stubs, no-op loaders, hidden flags, or warning-only runtime branches.
9. **No Stage 3/4 expansion:** no local file/Shell/Git/network/browser tools, approvals,
   persistent Session store, summary model, memory, background work, or Fork enters this
   plan.
10. **Honest documentation:** active docs must state that chat history is process-local and
    cross-process resume is unavailable. Historical documents must be labeled rather than
    silently rewritten.
11. **Secret and project safety:** no state content, credentials, tool arguments/results,
    tracebacks, or project files are exposed or modified by the removal.
12. **No dependency changes:** the refactor adds no third-party dependency.

### Locked command and exit contract

| Input/state | Required result |
|---|---|
| `/handoff` or `/continue` | exactly `CommandResult([f"未知命令：{command}"])` with no action/value; no deprecated/removed alias |
| dirty `/new`, confirmed | `discard_new`; reset only the process-local Session; exit status unchanged |
| dirty `/new`, cancelled | remain in the current Session |
| clean `/exit` or clean input EOF | process exit code `0` |
| dirty `/exit`, confirmed | discard process-local Session and exit code `0` |
| dirty `/exit`, cancelled | remain in the REPL |
| EOF while answering a dirty-discard confirmation | exit code `2`, with no reset or state write |

Read-only mode has no separate Handoff-save/continue branch. `/status` describes dirty
state as an unsaved in-process conversation, not as content waiting to be handed off.

### Locked degraded-state contract

- corrupt or future Profile data makes the workspace read-only and prevents Profile and
  workspace-Preferences writes;
- corrupt or future legacy `handoff.yaml(.bak)` is ignored, does not make the workspace
  read-only, does not block Profile/Preferences writes, and remains byte-identical;
- corrupt Preferences isolates only the affected Preferences layer and has no
  `/continue` or Handoff-loading consequence.

### Locked composition contract

`build_session_application()` must return a small named composition object (for example,
`SessionApplication`) containing `session`, `context_builder`, `commands`, and
`orchestrator`. Do not replace the current five-element positional tuple with a shorter
positional tuple. Every caller must use named fields.

## Legacy data policy

The refactor intentionally performs no filesystem deletion and no automatic migration.

```text
Existing ~/.morrow/workspaces/<workspace_id>/handoff.yaml(.bak)
→ remains byte-for-byte untouched
→ is no longer read by production code
→ cannot affect startup or read-only state
→ is documented as unsupported legacy data
```

If Stage 4 later chooses to import this data, that work must be explicitly planned against
the new Session schema. This plan makes no compatibility promise beyond non-destruction.

## Documentation policy

Documentation cleanup has two classes:

- **Current authority:** README, ARCHITECTURE, ROADMAP overview, current stage entry
  conditions, command documentation, state ownership, and package behavior must remove
  Handoff as a supported capability.
- **Historical evidence:** completed Stage 1/2 roadmaps, proposals, reviews, acceptance
  evidence, LOG entries, and completed subplans remain truthful records. They receive a
  concise historical/deprecation marker where needed; their old acceptance claims are not
  rewritten as current behavior.

Every repository Handoff reference must be classified as current-to-remove,
historical-to-retain, legacy-data/negative-test documentation, or an unrelated
external-system use. No unclassified occurrence may remain.

## Validation gates

Every subplan runs its focused tests and the complete offline/quality gate below so that
no ordered slice hands a red tree to the next slice. Documentation-only Subplan 23 still
runs the full offline suite because active documentation and package help are part of the
product contract.

```bash
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

Subplan-specific mandatory checks:

- **Subplan 21:** no production caller can load/write Handoff; configuration and natural-
  language paths cannot select it; startup and ordinary chat ignore sentinel legacy files;
  degraded-state, command, exit-code, no-model-call, and no-workspace-write contracts pass.
- **Subplan 22:** precise source scans prove removed classes, file names, commands, fields,
  actions, and persistence APIs are absent; test-scan results are limited to explicit
  unknown-command, negative-boundary, and legacy non-destruction assertions; the built
  wheel has no Handoff module. Do not scan the bare word `Decision`, which would falsely
  match `GateDecision`.
- **Subplan 23:** every repository reference is recorded in the reference-classification
  artifact as current-to-remove, historical-to-retain, legacy-data documentation, or an
  unrelated external-system use.
- **Subplan 24:** repeat all runtime, source, package, documentation, legacy-data, and
  capability gates on the final tree.
- **Every subplan:** Profile/Preferences isolation and state safety, ConversationLog and
  AgentLoop history/tool-cycle behavior, and the Stage 3/4/5 capability boundary remain
  green. Optional Live tests remain opt-in and are not required for removal.

## Principal risks and containment

| Risk | Containment |
|---|---|
| Deleting Handoff removes the only cross-process continuity path | State the temporary product limitation explicitly; do not create another bridge |
| Dirty `/new` or `/exit` silently loses process-local work | Keep deterministic discard confirmation without model/state writes |
| Generic structured/context/state infrastructure is removed accidentally | Split Handoff-specific tests from generic tests before deleting symbols |
| Legacy corrupt files continue to lock a workspace | Add startup tests with corrupt/future legacy Handoff sentinels that must be ignored |
| Stage 2 acceptance claims become misleading | Publish new post-removal acceptance and mark old evidence historical |
| Large cross-layer diff hides stale symbols | Use ordered subplans and source/package negative scans |
| Old user files are deleted unintentionally | No cleanup command or unlink path; byte-for-byte sentinel test |
| `/continue` semantics linger under a compatibility alias | Remove the old route completely; Session resume is redesigned in Stage 4 |

## Definition of done

The refactor is complete only when:

- no current user flow exposes Handoff or Handoff-backed continuation;
- no runtime state, model context, command, terminal action, bootstrap dependency, or
  workspace inspection references Handoff;
- no Handoff domain type, service, store port, YAML method, config target, source file, or
  packaged module remains;
- existing legacy Handoff files are ignored and left untouched;
- dirty process-local `/new` and `/exit` remain explicit and deterministic;
- Profile, Preferences, Provider, ConversationLog, AgentLoop, tool loop, context budgets,
  cancellation, and state safety have no regression;
- active product and architecture docs describe process-local Session behavior honestly;
- every remaining Handoff mention is explicitly historical, a legacy-data/negative test or
  document, or an unrelated external-system use;
- all focused, offline, Ruff, compile, CLI, boundary, package, and diff gates pass;
- `.agent/TODO.md`, `.agent/TRACKER.md`, `.agent/LOG.md`, the active plan, and the removal
  acceptance evidence are reconciled.
