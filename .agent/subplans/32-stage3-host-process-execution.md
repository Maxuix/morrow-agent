# Subplan 32 — Stage 3D Host Process Execution

> Status: completed; accepted 2026-08-18
> Depends on: Subplan 31 completed and accepted

## Goal

Implement bounded non-interactive Host command execution for Manual and Auto Safe, including
argument-aware risk preflight, approval, structured results, timeout, cancellation, output
limits, and complete process-tree cleanup. Do not automatically execute Host project code.

## Scope and expected ownership

- extend `core/local_tools.py` with command request/result models;
- add `services/process.py` for command classification and lifecycle semantics;
- add `adapters/local/process.py` for concrete async subprocess/process-group operations;
- extend `application/local_tools.py` with `run_command`;
- extend bootstrap/terminal previews and focused process/policy/AgentLoop/product tests.

No native sandbox activation, network permission, package installation, TTY/stdin, Git write,
public output-stream event, background process, or Full Access.

## Executable tasks

### S3.32.1 — Define strict command and result models

- Compare Pi `bash`/`bash-executor`/`exec` at the fixed commit and record borrowed lifecycle
  behavior plus Morrow's stricter authority, environment, and output-sanitization differences.
- Accept exactly one of non-empty `argv[]` or a bounded shell string.
- Accept only a validated workspace-relative `cwd` and bounded timeout below ToolExecutor's
  outer timeout; Host requests cap at 90 seconds and reserve cleanup time inside the existing
  120-second deadline. Reject model-supplied arbitrary env/stdin/TTY/sudo.
- Treat every explicit shell string as high risk/approval-required on Host; only argv avoids
  shell interpretation, and neither form gains automatic Host execution.
- Define `CommandResult` with status (`exited|signaled|timed_out|cancelled`), exit code/signal,
  separate stdout/stderr tails, original line/byte counts, truncation, duration, command
  class, sanitized working directory, and redaction flags/counts without matched values.
- Treat normal non-zero/signal/timeout as completed command results; reserve tool errors for
  policy/spawn/cleanup/infrastructure failures.

### S3.32.2 — Implement structural command preflight

- Parse structured argv directly and conservatively classify shell strings.
- Flag deletion/overwrite, chmod/chown/link, process control, package install, system paths,
  Git writes, redirection/pipes/substitution, network/download/upload/remote execution, and
  opaque interpreters/scripts.
- Deny Stage 3 forbidden capabilities before approval.
- Deny directly identifiable reads of SensitiveResourcePolicy-protected paths.
- Reject Git redirection flags such as `--git-dir`, `--work-tree`, absolute `-C`, and equivalent
  config/environment bypass forms before approval.
- Require approval for every Host project/test/build/script/opaque command in Manual and
  Auto Safe; command-name allowlists cannot grant automatic Host execution.
- Approval preview shows sanitized command class/cwd/timeout/risk and “非沙箱宿主进程；批准后
  项目代码可能以当前用户权限访问工作区外文件或网络”, not full environment or
  secret-bearing raw data.

### S3.32.3 — Implement HostProcessAdapter

- Build a minimal environment from an explicit allowlist; remove credential, key, proxy,
  agent/socket, and Morrow-sensitive variables.
- Spawn without shell for argv; use the platform shell only for the explicit shell variant.
- Create a separate process group/session where supported.
- Drain stdout and stderr concurrently with incremental UTF-8 decoding and bounded tail
  accumulators sized through ToolCallContext; invalid bytes are handled deterministically
  without traceback leakage and complete envelopes are semantically bounded before Executor.
- Sanitize output through a bounded SecretRedactor before any Provider/terminal/fact surface;
  seed exact redaction only from active CredentialStore/environment values, never by reading
  protected project files, and conservatively redact credential-token patterns.
- On timeout/cancel, terminate the complete process tree, wait a bounded grace, escalate once,
  and reap; cleanup failures are visible typed errors.
- Do not leave background children after a tool result or cancelled Agent turn.

### S3.32.4 — Register and integrate run_command

- Register `run_command` only for function-tool-capable Adapters.
- Preserve ToolExecutor's outer deadline and AgentLoop cancellation closure.
- Keep `tool.status` lifecycle/payload unchanged; final stdout/stderr arrives only in the
  bounded ToolMessage result.
- Update exact capability inventory and boundary tests atomically.
- Rebuild/snapshot the capability-derived system boundary for `run_command`, preserving the
  Host non-isolation warning and all still-forbidden capabilities.
- Keep auto-sandboxed unavailable until Subplan 33.

### S3.32.5 — Accept Host validation and recovery

- Test success, non-zero, signal, timeout, output overflow, invalid encoding, spawn failure,
  cancellation, descendant/grandchild cleanup, environment sentinels, cwd escape, and shell
  bypass attempts.
- Test exact known-secret and token-pattern redaction across chunk boundaries, and prove raw
  stdout/stderr never enters events, ConversationLog, terminal captures, YAML, or diagnostics.
- Use coordination files/pipes/events rather than sleep-based timing assertions.
- Fake Provider patches a Fixture, requests a test command, receives Manual approval,
  observes a first failure, fixes again, reruns, and reports exact final status.
- Exercise terminal approve/reject/EOF/Ctrl+C/timeout and a healthy subsequent turn.
- Assert a 90-second request maximum, cleanup reserve, semantic output truncation, and no
  successful result using Executor JSON-prefix truncation.

## Completion criteria

- Host command schemas, policy classification, minimal environment, structured result, and
  no-auto behavior are directly tested.
- Manual and Auto Safe require approval for all project code/opaque Host execution.
- No directly classified forbidden network/package/Git-write/system/destructive command reaches
  spawn; tests and documentation do not misrepresent this classifier as Host isolation.
- Timeout/cancel reliably cleans the tested process tree and never leaks secret environment.
- Capability-derived prompt, exact inventory, and result-envelope budgets remain aligned.
- Non-zero test failure remains a model-visible ordinary result, enabling correction.
- Public events and ConversationLog contracts remain unchanged and legal.
- Focused tests, full offline suite, quality gates, CLI help, and `git diff --check` pass.

## Delivered result

Morrow can validate edits on Host only with explicit informed approval and bounded process
semantics, while remaining unable to execute Host commands automatically.

## Acceptance evidence

- Fixed Pi comparison recorded in `docs/decisions/stage-3-process.md`.
- Strict argv/shell models, cwd/timeout bounds, minimal environment, `stdin=DEVNULL`, shell
  and privilege/network/destructive/Git/protected-path preflight, structured exit/signal/timeout
  results, output budgets and redaction are covered by `tests/test_process.py`.
- Manual and Auto Safe require approval; Auto Sandboxed rejects the Host tool even when a
  sandbox backend is reported available. Directly classified forbidden operations are denied
  before approval.
- Signal exit, timeout, cancellation, descendant process-group cleanup, invalid UTF-8, output
  overflow, spawn failure, environment sentinels, shell bypasses, secret/token redaction and
  Fake Provider failure recovery are covered. Existing terminal approval/cancellation/timeout
  tests cover the shared interaction port without changing public events or ConversationLog.
- Full offline suite: 384 passed, 1 deselected; quality gates passed: Ruff format/check,
  compileall, CLI help, and `git diff --check`. No Live or real-network test was run.
