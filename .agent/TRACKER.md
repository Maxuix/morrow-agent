# Progress Tracker

## Current status

Stage 3 Local Tool Execution is closed on the claimed macOS platform under the
review-remediated implementation plan. All confirmed findings from
`docs/reviews/stage-3-implementation-plan-review.md` are resolved in the plan contract. Gate P0,
Subplans 29–34, final product/security acceptance, and package gates passed on 2026-08-18. The
later external implementation review's 12 findings were also verified and remediated, with
refreshed offline, host-level sandbox, quality, and wheel evidence. The final 2026-08-19
recheck also passed the persistent OpenCode Go Mimo v2.5 environment and user-facing Provider
error/feedback paths.

## Last completed task

Completed the post-acceptance implementation review remediation: protected symlink targets and
`.git`, all common PEM private-key headers, empty read windows, shell-wrapped Git writes,
sandbox-promotion ChangeSet recording, stable error mapping, mixed-newline fail-closed behavior,
cancellable snapshot phases, redacted command previews, Linux fail-closed probing, and the review
nit. Then rechecked Keychain sanitization, connection/first-token feedback, Mimo preset discovery,
the persistent wrapper, and final gates: 418 offline tests passed with 2 intentional skips, 2
host-level Seatbelt tests passed, and the rebuilt wheel smoke gate passed. The accepted evidence is in
`docs/acceptance/stage-3-local-code-agent-evidence.md`.

## Next action

Stage 3 has no remaining action. Stage 4 is now open for planning against
`docs/roadmap/stage-4-task-session-and-persistence.md`; define and authorize one scoped
implementation subplan before changing persistence or Full Access behavior.

## Active task

No active subplan. Subplan 34 completed with native macOS snapshot execution, fail-closed backend
selection, read-only Git inspection, real escape evidence, bounded sandbox Diff, approval-required
promotion, product stories, local metrics, package evidence, and final Mimo/provider acceptance.

## Blockers

None. Gate P0 passed on the current macOS host. The nested probe was blocked by the Codex
execution sandbox, but the approved host-level rerun proved the required Seatbelt and APFS
capabilities.

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
