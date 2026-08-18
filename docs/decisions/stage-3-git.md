# Stage 3 read-only Git inspection decision

## Fixed reference

Subplan 34 uses Pi coding-agent `0.84.2` at commit
`209bc7b9a89b01c8fd05861cf5bbdda3e300037a` as a behavioral reference for status and Diff
ergonomics only. Morrow does not expose Pi's general command runner as a Git interface.

## Borrowed behavior

- Keep separate status and Diff tools with bounded, model-readable results.
- Report branch/HEAD and staged, unstaged, untracked, renamed and conflicted worktree facts.
- Permit optional relative path filters while preserving ordinary Git unified Diff shape.

## Deliberately strengthened for Morrow

- Resolve the worktree root, git directory and common directory before inspection; the worktree
  root must equal the frozen workspace and all Git metadata must remain inside it. Linked
  worktrees or external object stores return `external_git_metadata` rather than widening reads.
- Invoke only fixed, read-only Git argv through a scrubbed environment with no prompt, credential,
  pager, optional-lock, global/system-config, fsmonitor, external-diff or textconv authority.
- Parse porcelain-v2 NUL status locally and bound entries, output bytes and path filters. Diff hunks
  for protected credential/private-key paths or protected content are replaced by a bounded marker;
  no protected hunk reaches a ToolMessage or Provider request.
- Git inspection emits a bounded `GitToolFact` and does not merge pre-existing Git changes into
  Morrow's current-run mutation ChangeSet. Git writes remain absent from the Provider surface.

## Rejected behavior

- No general Git argv, commit/push/pull/reset/clean/checkout/merge/rebase/config-write or hook
  execution tool.
- No external metadata read, arbitrary config/environment inheritance, interactive credential
  prompt, external diff/textconv, fsmonitor daemon or network access.
- No second task state machine or public event lifecycle; the existing AgentLoop, ToolCycle,
  `ConversationLog` ownership and generic `ToolExecutor` remain authoritative.
