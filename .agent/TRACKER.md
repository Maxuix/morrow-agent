# Progress Tracker

## Current status

S7P-01 is active as Subplan 80 from verified local
`main@d8d2752752cf7d0d0b057af9db56b3029fd05120`. The complete checklist has been read, and the
current implementation was inspected across the OpenAI-compatible adapter, ContextBuilder,
AgentLoop, SessionPersistence, Operational Store, durable tool envelopes, application API and CLI.

The current worktree also contains three user-owned untracked research documents, including the
reliability checklist. They are not part of Subplan 80 and must remain untouched.

## Active task

The executable Subplan 80 is written and awaiting implementation in a separate Codex project
worktree task using `gpt-5.6-luna` with `max` reasoning. The same implementation task must spawn a
Luna Max review subagent, repair all confirmed findings, validate and commit before handoff.

## Located evidence

- `OpenAICompatibleProvider.stream()` uses `if not choices: continue` and returns on the first
  finish-bearing choice, so usage-only chunks—especially the standard trailing chunk—are lost.
- `ModelEvent` has text/completion/error only; `ModelCallOutcome` has no usage projection.
- `ContextPack` exposes request chars, cleared cycles and dropped records only in process; AgentLoop
  uses the messages but discards these counters.
- `DurableAgentRun` stores an immutable start snapshot. Operational schema v16 has no model-request
  ledger or terminal usage/context/stop metrics.
- `PydanticArgumentsValidator` already creates bounded `{path,type}` details and ToolExecutor returns
  them to the model; `_envelope_from_outcome()` persists only `{chars}` plus the error code.
- The CLI root composes `build_session_application()` and enters `run_repl()`. No one-shot Agent run
  or JSONL command exists, though `SessionOrchestrator.stream()` is already the correct shared seam.
- The public event lifecycle is strict but sufficient. A headless interface wrapper can serialize
  existing AgentEvents without changing their type or payload contracts.

## Next action

Commit this plan checkpoint on local `main`, create the requested worktree task from that commit,
and have Luna Max execute Subplan 80 through implementation, internal Luna Max review, review repair,
full validation and verified commits. The root task will then inspect and fast-forward integrate it.

## Blockers

None.
