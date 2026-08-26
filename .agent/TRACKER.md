# Progress Tracker

## Current status

S7P-01 and S7P-02 are verified, fast-forward integrated into local `main`, and retired. S7P-03
Subplan 82 is active for the reusable Direct Coding prompt profile and bounded scoped project
instructions. The main worktree contains only three user-owned untracked research documents;
they are outside this plan and remain untouched.

## Active task

The root task has completed code inspection and written the S7P-03 executable plan. The next action
is to commit the activation baseline, create `codex/feat/s7p-03-direct-coding-prompt`, and open one
dedicated `gpt-5.6-luna` / `max` implementation task. That task must perform implementation and its
own read-only Luna Max subagent review before returning a clean verified branch.

## Located evidence

- A current Direct context probe emits two system messages but no inspect/edit/verify/stop protocol
  and no `AGENTS.md`, even when the task names `src/morrow/application/context.py`.
- `AgentRunSnapshot.model_fields` contains no prompt/project-instruction evidence. No resolver or
  instruction projection exists in `src/morrow/`; the mini-eval profile fields are placeholders.
- The fixed boundary, Skill, state, Preferences, Memory and checkpoint are assembled directly in
  `ContextBuilder`, with no reusable prompt-profile/role-prompt seam or scoped project rules.

## Next action

Activate the topic task from the committed main baseline. Implement only S7P-03, then run the
declared focused/full offline gates and same-task reviewer closure. S7P-04 remains unopened.

## Blockers

None.
