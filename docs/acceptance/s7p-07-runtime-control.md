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

- [x] Queue entries are FIFO, text is at most 4096 characters, and a session has at most 32
  pending entries; overflow rejects the newest entry.
- [x] Every entry owns a fresh `cmsg` identifier and consumption is atomic with the matching Turn
  submission.
- [x] `FinishReason.STEERED` is legal only with closed tool pairing, requires no final assistant,
  and round-trips through replay, backup, restore, doctor and terminal rendering.
- [x] Steering is observed only at loop start, after an admitted batch, or before final STOP commit;
  it never interrupts a tool, model stream, or retry backoff.
- [x] Steering and follow-up are delivered one-at-a-time through ordinary durable user Turns in
  the same TaskRun and ConversationLog remains the only chat-history writer.
- [x] Cancel/error/host-stop do not auto-drain follow-ups. Pending steering survives cancel and is
  delivered at the next run start.
- [x] Crash/recovery neither loses nor duplicates pending entries and every terminal path preserves
  legal tool-call/tool-result pairing.
- [x] Scripted retry interaction proves cancel during backoff remains immediate and steering waits
  until the next safe point. Retry classification/backoff itself is S7P-06 acceptance evidence.
- [x] Terminal in-run input uses the exact pinned Pi TUI mapping; Ctrl+C cancellation is unchanged.
- [x] Focused gates, the full offline suite, Ruff, compileall, both CLI help commands and
  `git diff --check` pass.

## Deferred

Pi TUI extensions, images, extension-command expansion and the `all` queue mode are outside v1.
## Terminal mapping evidence

The pinned `interactive-mode.ts` maps ordinary Enter while `session.isStreaming` to
`streamingBehavior: "steer"` (lines 2877–2885). Its Alt+Enter handler maps streaming input to
`streamingBehavior: "followUp"` (lines 3760–3768). Morrow therefore uses the same mapping:

- Enter during a foreground run queues steering.
- Alt+Enter during a foreground run queues follow-up.
- Ctrl+C retains Morrow's existing foreground cancellation behavior.

Source: [Pi Agent 0.84.2 interactive mode](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/coding-agent/src/modes/interactive/interactive-mode.ts).

## Validation evidence

- Focused runtime-control and adjacent matrix: `166 passed`.
- Full offline suite: `1297 passed, 2 deselected` in `84.82s`.
- `uv sync`, Ruff format/check, compileall, `morrow --help`, `morrow run --help`, and
  `git diff --check` passed.
- The initial formal Luna Max review found three issues. Commit `dd2090e` now rejects a final
  assistant for STEERED, polls every queued steering Turn at loop top, and replays the durable
  STOP/STEERED/CANCELLED/ERROR terminal reason (including ERROR stop code) without model work.
- Follow-up review found two P2 crash/replay gaps. Commit `b4ec3e6` records terminal `turn_id` and
  ERROR stop code in the durable Conversation terminal, so a receipt never borrows a newer Turn's
  outcome when metrics are missing, and emits the same `status.changed: steered` terminal cue on
  closed replay.
- Final Herschel review (`gpt-5.6-luna`, reasoning `max`) returned
  `APPROVE — no confirmed P0-P3 findings` for `7b52f5f..b4ec3e6`; its targeted verification passed
  `7 passed`, including process-rebuild ERROR replay.
- No live Provider/model/Pi/MCP/network/credential test ran.
