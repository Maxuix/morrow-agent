# S7P-07 Runtime Control Acceptance

## Scope

S7P-07 adds bounded durable steering and follow-up delivery. Provider retry, compaction,
tool-output truncation, long-horizon defaults and policy versioning remain owned by S7P-06.
Validation uses scripted Providers and fake clocks only; no live Provider, Pi, MCP, network or
credential test is permitted.

## Pinned reference

Reference: Pi Agent 0.84.2 at commit
`209bc7b9a89b01c8fd05861cf5bbdda3e300037a`.

| Contract | Pi behavior | Morrow adaptation |
|---|---|---|
| Steering drain | Poll at loop start and after a completed turn, before the next model call | Close the current durable turn as `steered`, then submit the queued text through the normal user-turn path |
| Tool batch | An admitted batch completes before steering is polled | Never interrupt or skip an admitted call for steering |
| Follow-up drain | Drain only when the agent would otherwise stop | After normal `stop`, submit one queued follow-up as the next durable turn |
| Queue mode | FIFO; default one-at-a-time | FIFO and one entry per delivery point |
| Abort | Pending steering survives abort and drains before follow-up | Preserve pending rows across cancel/crash; consume only with durable turn admission |
| Persistence | In-memory | Harden to the Operational Store without loss or duplication |

## Acceptance checklist

- [ ] Queue entries are FIFO, text is at most 4096 characters, and a session has at most 32
  pending entries; overflow rejects the newest entry.
- [ ] Every entry owns a fresh `cmsg` identifier and consumption is atomic with the matching Turn
  submission.
- [ ] `FinishReason.STEERED` is legal only with closed tool pairing, requires no final assistant,
  and round-trips through replay, backup, restore, doctor and terminal rendering.
- [ ] Steering is observed only at loop start, after an admitted batch, or before final STOP commit;
  it never interrupts a tool, model stream, or retry backoff.
- [ ] Steering and follow-up are delivered one-at-a-time through ordinary durable user Turns in
  the same TaskRun and ConversationLog remains the only chat-history writer.
- [ ] Cancel/error/host-stop do not auto-drain follow-ups. Pending steering survives cancel and is
  delivered at the next run start.
- [ ] Crash/recovery neither loses nor duplicates pending entries and every terminal path preserves
  legal tool-call/tool-result pairing.
- [ ] Scripted retry interaction proves cancel during backoff remains immediate and steering waits
  until the next safe point. Retry classification/backoff itself is S7P-06 acceptance evidence.
- [ ] Terminal in-run input uses the exact pinned Pi TUI mapping; Ctrl+C cancellation is unchanged.
- [ ] Focused gates, the full offline suite, Ruff, compileall, both CLI help commands and
  `git diff --check` pass.

## Deferred

Pi TUI extensions, images, extension-command expansion and the `all` queue mode are outside v1.
The exact terminal key/prefix mapping is recorded here when Phase 2 pins it from the same Pi commit.
