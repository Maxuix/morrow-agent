# Progress Tracker

## Current status

S7P-01 and S7P-02 are verified, fast-forward integrated into local `main`, and retired. S7P-03
Subplan 82 implementation, focused/full offline validation, same-task Luna Max review, and all
confirmed finding repairs are complete on `codex/feat/s7p-03-direct-coding-prompt`. The topic
branch is clean and awaits root-task fast-forward merge; S7P-04 remains unopened. User-owned
research documents were not touched.

## Active task

Implementation task is complete through reviewer closure. The next action belongs to the root task:
verify ancestry/cleanliness and fast-forward merge `fb9e4dc` + `288bba6` (including acceptance/state
documentation) into local `main`. Do not merge here, push, retire the branch, or open S7P-04.

## Located evidence

- A current Direct context probe emits two system messages but no inspect/edit/verify/stop protocol
  and no `AGENTS.md`, even when the task names `src/morrow/application/context.py`.
- `AgentRunSnapshot.model_fields` contains no prompt/project-instruction evidence. No resolver or
  instruction projection exists in `src/morrow/`; the mini-eval profile fields are placeholders.
- The fixed boundary, Skill, state, Preferences, Memory and checkpoint are assembled directly in
  `ContextBuilder`, with no reusable prompt-profile/role-prompt seam or scoped project rules.
- `fb9e4dc` adds the Direct Coding profile, bounded read-only resolver, reference-only prompt
  evidence, fresh/recovery plumbing, production composition tests and acceptance implementation.
- `288bba6` closes all eight confirmed reviewer findings: role/provenance binding, no-rehydrator
  fail-closed behavior, safe-open/TOCTOU checks, durable ordering, URL/target bounds and wire/
  quarantine evidence.
- Final offline gate: 1193 passed, 2 skipped, 2 deselected; focused gates and quality checks are
  recorded in `docs/acceptance/s7p-03-direct-coding-prompt.md`.

## Next action

Root task should inspect `git status --branch`, verify the two topic commits and clean worktree,
then fast-forward merge into local `main`. S7P-04 remains unopened.

## Blockers

None.
