# Stage 6 Skills and Extensions Implementation Plan

> Status: final design and executable plan complete; implementation not started
> Active subplan: 63 — dependency and contract spike
> Baseline: local `main` at `479270b`
> Design baseline: `docs/reviews/stage-6-skills-and-extensions-final-proposal.md`
> Roadmap authority: `docs/roadmap/stage-6-skills-and-extensions.md`

## 1. Objective

Deliver a governed extension layer without moving extension semantics into AgentLoop or weakening
the existing permission, approval, recovery, ConversationLog, and storage boundaries:

```text
versioned Skills + controlled Drafts + per-run Skill context
+ isolated Skill scripts
+ MCP stdio tools through the existing ToolExecutor safety path
+ complete Provider/Model control and per-run capabilities
+ reproducible AgentRun evidence, doctor, backup v2 and acceptance
```

Stage 6 is complete only when one handwritten Skill, one generated Draft, one Fake MCP Server, and a
second Fake/real-compatible Provider adapter pass the declared offline acceptance, with optional live
paths kept separate.

## 2. Authority and implementation rules

1. Current user decisions and later explicit scope changes.
2. Current code and validation just run.
3. This plan and the one active child subplan.
4. The Stage 6 roadmap.
5. The final proposal, reviews and research as decision history, not parallel specifications.

Implementation follows these rules:

- one child subplan is active at a time and starts from latest verified `main`;
- new dependencies are not added until Subplan 63 finishes and the user explicitly approves the
  exact dependency change;
- no Provider, MCP, credential, live network, or user-state test runs unless separately authorized;
- code and tests supersede stale plan wording; update the plan before changing scope;
- every cross-store mutation uses prepare/apply/finalize with idempotent receipts or remains a
  single-authority write;
- new production modules normally remain below about 300 lines; split by responsibility when they
  grow beyond that point;
- do not add a generic ExtensionManager, Plugin base class, dependency graph, event bus, daemon,
  automatic retry engine, or learned router.

## 3. Locked architecture

### 3.1 Per-AgentRun preparation

`AgentRunPreparationService` is the only cross-domain composition boundary. It returns:

- `PreparedAgentRunSpec`: immutable sanitized Provider runtime snapshot, model/capability/run-policy,
  Skill/MCP evidence refs, ToolSet digest and source revisions;
- `PreparedAgentRunRuntime`: Provider, ContextBuilder, ToolExecutor, optional lazy MCP pool and
  cleanup handles.

`TurnSubmissionCoordinator.probe()` must classify replay/recovery before `prepare_new()` performs
current configuration reads or external work. `rehydrate()` uses stored evidence only. The existing
transaction rechecks the receipt to resolve concurrent duplicates. AgentLoop consumes a prepared run
but never reads YAML, SQLite, Skill directories, or MCP configuration.

The Provider runtime snapshot includes adapter/provider/model/API-model IDs, sanitized endpoint,
CredentialRef name/version, capabilities and config revision/digest—never a credential value—so
rehydration cannot silently fall back to the current active model.

### 3.2 Skills

Keep four concepts separate:

- `SkillDefinition`: stable identity and provenance;
- `SkillVersion`: immutable managed package content;
- `SkillBinding`: global/workspace enabled and pinned desired state;
- `SkillSelection`: exact version chosen for one AgentRun.

Effective Trust is computed from local provenance and lifecycle evidence. Manifest Trust is only a
hint. Managed paths use Morrow-assigned `skv_` IDs and canonical tree digests. Selection freezes
version/tree digests; every later resource/script read revalidates the frozen package.

Full Skill context lives in a bounded `agent_run_skill_contexts` table. AgentRun's 64 KiB snapshot
contains references/digests/counts only. Scripts run through `SkillScriptExecutionService` with a
read-only Skill root and isolated output, not through the workspace-rooted ProcessExecutionService.

### 3.3 Dynamic tools and MCP

`RegisteredTool` gains generic arguments validation and a frozen recovery declaration. Local tools
use the Pydantic implementation; MCP tools use a selected JSON Schema dialect. ToolExecutor and
recovery do not branch on `mcp.*` names.

MCP v1 is stdio and per-AgentRun lazy. Every call evaluates a launch Intent and a tool semantic
Intent with the same CapabilityPolicy, then combines decisions as
`DENY > REQUIRE_APPROVAL > ALLOW`. MCP snapshots are evidence, not a second permission authority.
Calls never auto-retry. Result loss after handler entry is recovery-visible.

A matching local-interface `McpReviewEvidence` may only convert MCP network/credential/loopback/
external-effect hard denials into per-call `REQUIRE_APPROVAL`; it never auto-allows them. Destructive,
outside-workspace, privilege escalation and git write remain denied in MCP v1, and bundled profile
defaults do not change.

MCP text/image/audio/resource/embedded/structured results normalize into a bounded DTO; binary and
embedded payloads enter ArtifactStore, not conversation or the AgentRun snapshot.

### 3.4 Provider and Model

Adapter and exact Model capability snapshots cover streaming, tool protocol, multi-tool calls,
structured output, request/context limits, input modalities, and cost metadata provenance/time.
`active_model` remains the only ordinary default. No silent fallback or automatic routing.

### 3.5 Persistence and backup

| Authority | Data |
|---|---|
| versioned YAML | Provider/Model, SkillBinding, MCP desired state |
| CredentialStore | secret values |
| managed filesystem | immutable Skill packages and envelopes |
| SQLite | catalogs, operations, Draft/Usage, run contexts and MCP snapshots |
| existing Tool Journal | tool execution, approval and recovery |

Operational migrations are split by responsibility: v14 Skills/run selection, v15 Draft/Usage, v16
MCP. Backup v1 remains stable; Stage 6 adds bundle v2 for SQLite, Artifacts, Extension YAML and
referenced managed Skill versions, never credentials.

## 4. Module ownership

| Boundary | Target modules | Constraints |
|---|---|---|
| Run preparation | `application/agent_runs/` | composition only; no domain parsing or direct SQL |
| Skill contracts | `core/skills/` | no filesystem, YAML, SQLite or CLI |
| Skill packages | `adapters/skills/` | package IO and validation only |
| Skill application | `application/skills/` | small catalog/lifecycle/selection/draft/script/query services |
| Skill persistence | `adapters/state/skill_journal.py`, v14/v15 migrations | no MCP tables |
| MCP contracts | `core/mcp/` | no SDK objects or process IO |
| MCP adapter | `adapters/mcp/` | SDK/stdio and result conversion only |
| MCP application | `application/mcp/` | definition/catalog/run bridge/query split |
| MCP persistence | `adapters/state/mcp_journal.py`, v16 migration | no Skill package writes |
| Provider control | `application/providers/`, `adapters/registry.py` | no AgentLoop/Session branches |
| User interfaces | focused `*_cli.py` modules | application calls only |
| Backup/doctor | focused Stage 6 verifier modules + thin composition | preserve v1 semantics |

Large existing files (`bootstrap.py`, `runtime/agent.py`, `runtime/tools.py`,
`application/turn_lifecycle.py`, `adapters/state/journal.py`, `adapters/state/migrations.py`) receive
only thin protocol/composition edits. If a change would add a second responsibility, extract the
existing seam first inside the active subplan.

## 5. Sequential subplans

| Order | Subplan | Result |
|---|---|---|
| 63 | Dependency and contract spike | ADRs, selected MCP/JSON Schema approach, frozen budgets and no production dependency change |
| 64 | Per-AgentRun runtime preparation | replay-first admission, prepare/rehydrate, per-run Provider/Model/RunPolicy/ToolSet |
| 65 | Skill package and Catalog foundation | core contracts, managed package validator, v14 storage and deterministic conflicts |
| 66 | Skill lifecycle and Binding control | install/enable/disable/pin/rollback/remove with YAML authority and recovery |
| 67 | Skill selection, context and resources | AgentRun selection/context evidence, bounded injection and frozen reads |
| 68 | Generated Draft and Usage | Candidate→Draft review flow, validation reports, Usage and v15 storage |
| 69 | Skill Script execution | dedicated sandboxed/approved script service and Artifact outputs |
| 70 | Provider/Model control plane | complete capabilities and provider/model CLI without core branching |
| 71 | Dynamic Tool contracts | JSON Schema validator seam and declaration-owned recovery |
| 72 | MCP control plane and Catalog | stdio definitions, config, discovery, namespacing and v16 evidence |
| 73 | MCP runtime and security adapter | compound policy, lazy lifecycle, no retry, normalized results and recovery |
| 74 | Backup v2 and doctor | cross-store backup/restore verification and Stage 6 integrity checks |
| 75 | Integrated acceptance and closeout | fixtures/examples, full gates, docs and truthful architecture/roadmap sync |

Each child plan in `.agent/subplans/63-*.md` through `75-*.md` owns exact tasks, files, focused tests
and exit evidence. Later schemas and interfaces cannot be implemented early.

## 6. Dependency gate

Subplan 63 may inspect SDK packages in a temporary environment and write an ADR. It must not edit
`pyproject.toml` or `uv.lock`. Before Subplan 72 adds any recommended dependency, stop and ask the
user to approve the exact packages and rationale. If approval is denied:

- complete Skills, AgentRun preparation and Provider/Model work normally;
- mark MCP implementation subplans blocked;
- do not hand-roll a partial MCP protocol implementation and do not claim Stage 6 complete.

## 7. Validation strategy

Every implementation subplan runs its focused tests, then:

```bash
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

Run `uv run pytest -m 'not live'` at the risk gates declared by the child plan and for final
integration. No wall-clock sleeps: use scripted providers, Fake MCP stdio processes, injected clocks,
events and cancellation tokens.

Final gate:

```bash
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow skill --help
uv run morrow mcp --help
uv run morrow provider --help
uv run morrow model --help
git diff --check
```

Required deterministic acceptance evidence:

- next-new-run model/Skill/MCP changes, same-run freeze, closed replay without current reads, and
  recovery rehydration from historical evidence;
- Skill source/scope isolation, identity conflicts, Trust spoof rejection, tree/path/TOCTOU checks,
  context budget/omission, pin/rollback and referenced-version retention;
- Draft cannot activate itself, user/builtin/imported cannot be silently changed, and Usage cannot
  authorize an update;
- Script cannot escape Skill root/output, write project state, use network/credentials or run shell
  without an allowed/approved frozen intent;
- dynamic Schema dialect and recovery declarations work without static MCP name branches;
- MCP launch/tool decision matrix, cancellation, timeout, crash, no retry, result types, truncation,
  Artifact import and unrelated-tool isolation;
- complete Provider/Model capabilities and second Adapter contract without AgentLoop/Session/Task
  branches;
- v13→v16 migration, future-version refusal, doctor tamper, backup v1 compatibility and isolated v2
  restore.

## 8. Git and execution discipline

- Create each subplan branch from latest verified `main`, using `codex/feat/stage6-<slice>` unless a
  repair branch is more appropriate.
- Commit small verified checkpoints; never leave `wip:` in final history.
- Before closing a subplan, commit changes, run declared gates, update `.agent` state, fast-forward
  merge, verify the topic has no commits absent from `main`, and remove the clean branch/worktree.
- Do not push unless an authorized remote is configured and the user has placed remote publication
  in scope. Record the blocker rather than silently claiming remote completion.

## 9. Cross-cutting invariants

1. ConversationLog remains the only chat-history writer.
2. Ordinary chat remains on `AgentLoop.run_task()`; `run_turn()` stays a thin delegate.
3. Replay and recovery do not consult current extension desired state.
4. Provider calls, MCP IO, process execution and user approval never occur inside SQLite/YAML
   transactions.
5. Skill/MCP data is lower authority than system, developer, capability and tool policy.
6. Manifest annotations, Trust claims and MCP annotations never authorize behavior.
7. Every runtime tool goes through one ToolExecutor, approval, budget, cancellation and audit path.
8. MCP calls and Skill scripts never auto-retry after handler entry.
9. Workspace A cannot discover, bind, select, execute or inspect Workspace B extensions/evidence.
10. No secret, raw provider/MCP payload, SDK object, traceback, reasoning, full script output or
    credential enters events, YAML, reports, model context or terminal diagnostics.
11. Existing immutable AgentRun/Tool/backup evidence stays decodable and is never rewritten.
12. Configuration changes affect the next new AgentRun only; the current run stays frozen.

## 10. Non-goals

- Marketplace, remote auto-update, daemonized MCP, package-runner auto-download, shell-string Skill
  scripts, cross-Skill dependency resolution or recursive Skills.
- Learned Skill routing, autonomous Draft acceptance, automatic version promotion/rollback, model
  routing/fallback, cost optimizer or Stage 7 workflow behavior.
- Public event lifecycle redesign, bundled policy default relaxation, CredentialStore backup, or a
  generic plugin framework.

## 11. Completion condition

Stage 6 implementation is complete only when Subplans 63–75 are merged, every declared offline gate
passes, dependency decisions are recorded, backup/restore and doctor evidence is complete, examples
pass in isolated state, `docs/ARCHITECTURE.md` describes only the now-implemented structure, the
Stage 6 roadmap is reconciled, and the working tree contains no unexpected changes.
