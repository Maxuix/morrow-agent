# Stage 3 Host process adapter decision

## Fixed reference

Subplan 32 uses Pi coding-agent `0.84.2` at commit
`209bc7b9a89b01c8fd05861cf5bbdda3e300037a` as a behavioral reference for `bash`,
`bash-executor`, and `exec`. This note records an independent Morrow implementation; Pi is
not a runtime or dependency.

## Borrowed behavior

- Keep a small command surface with a replaceable process adapter so lifecycle tests do not
  require a model or terminal.
- Drain stdout and stderr concurrently, retain bounded tails, and preserve original output
  size/line metadata.
- Treat non-zero exit and signal termination as ordinary command results; reserve infrastructure
  errors for spawn and cleanup failures.
- Use a process-group/session boundary and bounded terminate-then-escalate cleanup for timeout
  and cancellation.

## Deliberately strengthened for Morrow

- Admit exactly one bounded `argv` or explicit shell form; do not expose model-controlled env,
  stdin, TTY, password prompts, or privilege escalation.
- Launch with an explicit minimal environment and `stdin=DEVNULL`; active credentials and
  sensitive environment values never enter the child.
- Resolve `cwd` through the frozen workspace resolver, classify known network, destructive,
  protected-resource, Git-write, outside-workspace, and privilege-escalation risks before
  approval. Shell and `sh -c` forms are also scanned for wrapped Git commands. Every approval
  preview includes a bounded, single-line, redacted argv/shell rendering plus the explicit
  non-isolated Host warning; command text remains absent from results, events, and persistence.
- Redact active credentials and conservative token patterns before any ToolMessage or local
  fact; invalid UTF-8 and output-budget truncation are deterministic and typed.
- Keep Host commands approval-required in Manual and Auto Safe, and deny the Host tool in
  Auto Sandboxed even when a native backend is later available; sandbox execution belongs to
  the separate NativeSandbox adapter.

## Rejected behavior

- No inherited full Host environment, shell-by-default execution, raw streaming events,
  background process API, arbitrary network/package/Git-write capability, or automatic Host
  execution of project code.
- A workspace `cwd` is not treated as an operating-system sandbox. Approved Host code may still
  access resources that structural preflight cannot identify; only Auto Sandboxed can claim
  enforced confinement.
- No second task state machine or public event lifecycle; existing AgentLoop, ToolCycle,
  ConversationLog ownership, and generic ToolExecutor remain authoritative.
