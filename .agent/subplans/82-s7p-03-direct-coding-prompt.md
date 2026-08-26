# Subplan 82 — S7P-03 Direct Coding Prompt and Project Instructions

> Status: completed and integrated locally
> Branch: `codex/feat/s7p-03-direct-coding-prompt`
> Base: `main@e75c3b2`

## Goal

Supply every new Direct coding AgentRun with a reusable, versioned and hash-frozen coding protocol
plus bounded workspace project instructions, while keeping fixed safety, ToolSet, permissions and
sandbox authority above all user-editable prompt layers.

## Deliverables

1. A typed Direct Coding profile/prompt assembler with stable ID/version/digest, an optional bounded
   future role-prompt seam and deterministic system-message order.
2. A filesystem-only `AGENTS.md` resolver with explicit compatible-name configuration, bounded
   target extraction, root-to-leaf scope precedence, sibling isolation and fail-closed diagnostics.
3. Reference-only AgentRun metadata and exact fresh/recovery projections; no instruction content or
   task text in the durable snapshot.
4. Final fake-Provider/request capture, no-permission-expansion, no-auto-Shell and no-unrequested-
   artifact regressions plus acceptance evidence.

## Owned areas

- Prompt/context core and application assembly under `src/morrow/application/` and
  `src/morrow/core/context.py`.
- Reference-only `AgentRunSnapshot` / prepared-spec metadata and turn admission/recovery plumbing.
- Bootstrap injection for the current Direct profile and workspace-root resolver.
- Focused tests for prompt assembly, project instructions, preparation/recovery and mini-eval
  runtime evidence; S7P-03 acceptance document and `.agent` execution state.

## Non-goals

- No AgentDefinition/Workflow persistence, delete/move/rename tools, completion checker, retry,
  steering, budget or public-event work.
- No execution of project document contents, dependency changes, runtime-policy default changes,
  ToolSet changes, permission/sandbox widening or live tests.
- No S7P-04 work and no edits to user-owned research documents.

## Acceptance gates

- Root `AGENTS.md` appears in the final Direct request and nested rules are scope-labelled with
  deterministic root-to-leaf precedence and sibling isolation.
- Invalid/oversized/outside/symlinked/non-NFC/changed instruction evidence fails closed with bounded
  value-free diagnostics; recovery never silently adopts current file content.
- AgentRun freezes prompt profile and ordered project-source metadata/hash/version only.
- Fixed safety remains first; project/role/Skill/Memory/Preference text cannot add tools or widen
  capability, approval, sandbox or recovery authority.
- Focused, full offline, Ruff, compileall, both CLI help and diff checks pass after reviewer repair.

## Review protocol

After the first verified implementation commits, the same Luna Max implementation task spawns one
read-only `gpt-5.6-luna` / `max` subagent to review the entire activation-base...HEAD diff. The
implementation task verifies and repairs every confirmed finding, reruns the gates, commits final
state and leaves merge/retirement to the root task.
