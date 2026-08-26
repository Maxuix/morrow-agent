# Progress Tracker

## Current status

S7P-01 is active as Subplan 80 from verified local
`main@d204a6518d1c7f64193ddb42c33384c8fb320e3d`. The complete checklist has been read, and the
current implementation was inspected across the OpenAI-compatible adapter, ContextBuilder,
AgentLoop, SessionPersistence, Operational Store, durable tool envelopes, application API and CLI.

The current worktree also contains three user-owned untracked research documents, including the
reliability checklist. They are not part of Subplan 80 and must remain untouched.

## Active task

The implementation, read-only Luna Max review, all seven review repairs, and final offline quality
gates are complete on the dedicated topic branch using `gpt-5.6-luna` with `max` reasoning. The
reviewer found one P1, five P2, and one P3 finding, with no P0 or uncertain findings; all were
repaired and committed as `c9535d0` for handoff.

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

Hand the committed verified review-repaired state back to the root task. The delegating/root
task owns any later integration; this task must not merge into `main`, delete the topic
branch/worktree, or push.

## Blockers

None.
