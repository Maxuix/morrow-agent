# Progress Tracker

## Current status

Stage 3 Local Tool Execution has a review-remediated implementation plan and six ordered
subplans. All confirmed findings from `docs/reviews/stage-3-implementation-plan-review.md` are
resolved in the plan contract. Implementation has not started, no subplan is active, and no
application or test code was changed as part of remediation.

## Last completed task

Verified the review against current code and remediated the plan: capability-derived system
boundary, RunPolicy-compatible semantic results, numeric Auto Safe limits, bounded parent creation,
Gate P0/CoW/toolchain/sandbox phase budgets, mutation Diff preview, production demo-tool removal,
current-run sandbox promotion, search ADR/budgets, Git metadata confinement, locked ToolFacts,
terminal summary/local metrics, exact capability guards, and configuration non-escalation tests.

## Next action

After explicit implementation authorization, re-check the repository baseline and run Gate P0 on
the current macOS host. If it passes, activate Subplan 29. If it fails, do not begin implementation
or silently downgrade Stage 3; return for an explicit scope/platform decision.

## Active task

None; this is a planning-only state.

## Blockers

None.

## Active boundary

- Stage 3 delivers `manual` and restricted `auto-safe`, then native-isolated
  `auto-sandboxed`; it does not deliver unrestricted Full Access.
- Full Access remains a separate Stage 4 scope and must not appear as a working Stage 3 mode.
- Permission scope and approval behavior are independent axes; the initial scope remains
  workspace-only.
- Native sandboxing requires current-host macOS Gate P0 plus definitive real escape evidence,
  never Docker or Host fallback. Linux is claimed only after a real runner passes; Windows is not
  a first-release Auto Sandboxed target.
- Network, loopback, workspace escape, home-directory access, credential/socket access,
  destructive commands, arbitrary Git writes, background jobs, browser, MCP, Skills, persistent
  chat history, and LLM summaries are outside the Stage 3 boundary unless the plan is explicitly
  revised and re-authorized.
- SensitiveResourcePolicy blocks protected credential/private-key content across file, search,
  mutation, Diff, snapshot, process-output, and Git paths; example/template exceptions are explicit.
- Approved Manual/Auto Safe Host commands are not OS-isolated and may attempt outside/network
  access; previews and acceptance must say so. Only Auto Sandboxed claims enforced confinement.
- The planned common production surface is configuration plus list/read/find/search,
  patch/write/show changes, command execution, and read-only Git status/diff. Supported sandbox
  composition adds only current-run, approval-required sandbox-change promotion; demo lookup/
  calculate tools become test-only.
- Read windows are capped at 400 lines/8 KiB and tightened to current result budgets; Auto Safe
  mutation has explicit per-call/cumulative thresholds and whole-file replace always prompts.
- Pi is the primary behavioral reference, Hermes is secondary evidence, and neither project is
  copied wholesale or treated as Morrow's architecture authority.
- Subplans execute serially in order 29–34; public event lifecycle or bundled policy-default
  changes are hold points that require separate user approval.
