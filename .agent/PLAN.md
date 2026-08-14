# Stage 1 Implementation and Review Remediation Plan

> Status: completed after remediation and final-tree offline, Live, and manual acceptance; Stage 2 is unblocked but has not started.

## Overall goal

Implement and validate Morrow Stage 1 as a terminal-based, workspace-aware conversational prototype that can preserve a reliable project handoff without reading project content or executing project tools.

Stage 1 is complete only when both Stage 1A and Stage 1B gates pass:

- **Stage 1A — vertical loop:** start in a workspace, configure one model connection, converse with streaming output, explicitly load an existing handoff, and save a valid new handoff with a deterministic fallback.
- **Stage 1B — stable boundaries:** complete preference/configuration flows, safe session switching, provider-local management, concurrency/recovery behavior, documentation, and all Stage 1 acceptance gates.

The plan is ordered by dependency and validation value. It intentionally contains no calendar or duration estimates. Progress is determined only by completion criteria.

## Governing decisions

- Work on one active subplan at a time.
- Do not begin Stage 2 implementation until all Stage 1B gates pass.
- Stage 1A passing permits Stage 2 design discussion and real-project product feedback, not Stage 2 implementation.
- Use gated natural-language configuration only after a conservative local intent gate; ordinary chat must not incur a configuration extraction call.
- Keep `AgentRuntime.run_turn()` a single model turn. A later task loop must use a separate orchestration entry point.
- Keep state under the Morrow data directory; never write Morrow state into the selected project workspace.
- Default tests are offline and isolated from the user's home directory, real keychain, clock, randomness, and network.
- Do not create empty modules for tools, memory, Skills, automation, or future clients.

## Ordered subplans

| Order | Subplan | Stage | Status | Depends on |
|---|---|---|---|---|
| 01 | [Engineering baseline and terminal spike](subplans/01-engineering-baseline-and-terminal-spike.md) | 1A | completed | — |
| 02 | [Core contracts and test doubles](subplans/02-core-contracts-and-test-doubles.md) | 1A | completed | 01 |
| 03 | [Workspace identity and state storage](subplans/03-workspace-identity-and-state-storage.md) | 1A | completed | 02 |
| 04 | [Provider and onboarding](subplans/04-provider-and-onboarding.md) | 1A | completed | 02, 03 |
| 05 | [Context and turn runtime](subplans/05-context-and-turn-runtime.md) | 1A | completed | 02, 03, 04 |
| 06 | [REPL and orchestration](subplans/06-repl-and-orchestration.md) | 1A | completed | 03, 04, 05 |
| 07 | [Handoff and Stage 1A gate](subplans/07-handoff-and-stage-1a-gate.md) | 1A | completed | 03–06 |
| 08 | [Preferences and ConfigPatch](subplans/08-preferences-and-config-patch.md) | 1B | completed | 07 |
| 09 | [Session and state commands](subplans/09-session-and-state-commands.md) | 1B | completed | 08 |
| 10 | [Provider, concurrency, and recovery](subplans/10-provider-concurrency-and-recovery.md) | 1B | completed | 09 |
| 11 | [Stage 1B acceptance and delivery](subplans/11-stage-1b-acceptance-and-delivery.md) | 1B | completed | 10 |
| 12 | [Workspace identity and durable state remediation](subplans/12-workspace-identity-and-durable-state-remediation.md) | 1B remediation | completed | 11, independent review |
| 13 | [Runtime, event, terminal, and read-only remediation](subplans/13-runtime-terminal-and-read-only-remediation.md) | 1B remediation | completed | 12 |
| 14 | [Configuration, Provider, and structured-completion remediation](subplans/14-configuration-provider-and-structured-remediation.md) | 1B remediation | completed | 13 |
| 15 | [Context and domain-invariant remediation](subplans/15-context-and-domain-invariant-remediation.md) | 1B remediation | completed | 14 |
| 16 | [Stage 1 remediation acceptance and truth reconciliation](subplans/16-stage-1-remediation-acceptance.md) | 1B remediation | completed | 12–15 |

## Review remediation traceability

| Confirmed finding | Owning subplan |
|---|---|
| Repeated/stale candidate confirmation publishes duplicate workspace IDs | 12 |
| Relink permits two IDs at one effective Git root | 12 |
| Clear resets the persisted revision and admits stale revision 0 writes | 12 |
| Clear bypasses the validated temporary-file/`fsync` publication path | 12 |
| Duplicate event IDs, repeated `ses_1`, and broken injected workspace IDs | 13 |
| Provider exceptions can omit terminal lifecycle events | 13 |
| Non-normal/missing Provider finish is accepted as `stop` and enters history | 13 |
| Dirty independent EOF can busy-loop; closed switch prompts can escape | 13 |
| Degraded workspace sessions can still `/continue` | 13 |
| Ordinary Provider/security discussion is rejected by the config gate | 14 |
| Inconsistent `ConfigExtractionResult` shapes silently fall through to chat | 14 |
| Config previews omit the mutation and deterministic edits write before confirmation | 14 |
| Environment credentials are ignored by active Provider construction | 14 |
| Base-URL-only Provider configure forces secret re-entry | 14 |
| Structured repair loses its task/schema and can consume twice the total timeout | 14 |
| Provider tests lose typed error codes and failed CLI tests exit zero | 14 |
| Provider show reports references rather than resolvable credentials | 14 |
| Context pruning can admit an assistant without its user | 15 |
| Handoff accepts an empty/whitespace `current_goal` and persistence invariants are weak | 15 |
| Ten-turn, multiprocess, Live, terminal, CLI, and secret-surface evidence is overstated | 16 |
| README, ROADMAP, acceptance, PLAN, and TRACKER status/behavior drift | 16 |

## Cross-cutting contracts

These contracts apply to every subplan and cannot be postponed to final hardening:

1. **Dependency direction:** interfaces → application → runtime/services → core; adapters implement core ports; bootstrap is the only composition root.
2. **Secret boundary:** credentials never enter YAML, logs, public events, model context, handoffs, tracebacks shown to users, or terminal echo.
3. **Workspace boundary:** only path metadata and `.git` directory/gitfile existence may be inspected; no source reads, project writes, subprocesses, Shell, or Git commands.
4. **State safety:** typed validation precedes publication; writes use revision checks, same-directory temporary files, `fsync`, atomic replacement, and one valid backup.
5. **Context authority:** `ContextBuilder` is the only path for Profile, Preferences, Handoff, and in-process messages to enter model context, including purpose-specific structured-completion contexts.
6. **Explicit continuity:** discovering or displaying a Handoff does not load it; only an explicit continuation loads a specific revision.
7. **Event lifecycle:** every accepted turn emits exactly one start and one completion; cancellation is a completion reason, not an error.
8. **Deterministic recovery:** whenever a Handoff save is required, failure to generate a model-authored Handoff must still produce a minimal valid fallback unless the storage operation itself fails; an independent-session exit that was not explicitly saved must not write a Handoff.
9. **Forward compatibility:** consumers ignore unknown event fields/types; unknown future state schemas are never silently downgraded or overwritten.
10. **No hidden network:** only explicit provider setup/test and normal user-approved model turns may access the network.
11. **Application ownership:** `SessionOrchestrator` dispatches inputs and transitions; `CommandService` owns command use cases; neither `AgentRuntime` nor terminal rendering reads or writes application state directly.
12. **Global aggregate:** global Preferences, Providers, and `active_model` share one `config.yaml` schema, revision, and lock; every update is a whole-document read-modify-validate-write through `GlobalConfigStore` that preserves unrelated fields.
13. **Handoff invariants:** every generated, repaired, fallback, edited, or loaded Handoff uses the same initial schema, including normalized uniqueness of decision text.

## Stage gates

### Stage 1A gate

Subplan 07 historically demonstrated the initial `S1A-01` through `S1A-08` gate. After remediation, Subplan 16 must rerun every Stage 1A requirement against the final post-remediation tree, including the real-provider and real-terminal checks. A failure reopens the owning remediation subplan rather than relying on the earlier evidence.

After the gate, record real-project feedback about handoff usefulness, latency, fallback frequency, and terminology. Feedback may adjust presentation and defaults, but changing a locked contract requires updating the roadmap and execution plan first.

### Stage 1B gate

Subplan 11 is historical evidence and is not authoritative after the confirmed review defects. Subplan 16 must demonstrate all `S1B-01` through `S1B-06` requirements, all offline quality gates, the complete manual/Live checklist, documentation completeness, and absence of Stage 2 capabilities on the final post-remediation tree.

## Explicit non-goals

- Tool calling, file access, Shell, Git execution, or project modification.
- Persistent chat history, session database, long-term memory, embeddings, or retrieval.
- Skills, MCP, plugins, model routing, automatic fallback, or multi-model selection.
- Provider removal, model use/add/sync/remove, `/undo`, workspace forget, or complete data-erasure flows.
- Web, desktop, messaging clients, background execution, or scheduling.
- A general-purpose PTY regression framework or future tool-event taxonomy.

## Plan maintenance

- `.agent/TODO.md` contains executable tasks for the active subplan only.
- `.agent/TRACKER.md` identifies the current state and next concrete action.
- Completing a subplan requires its listed verification to pass, after which update this index, the tracker, and the execution log before activating the next subplan.
- If implementation evidence conflicts with this plan, update the plan and relevant roadmap/architecture decision before continuing.

## Currently selected subplan

Subplan 16 rebuilt and passed the final-tree offline evidence, the explicit OpenCode Go Live test, the real-stream boundary inspection, and the complete isolated real-terminal/manual checklist. Stage 1 remediation is complete and Stage 2 is unblocked, but Stage 2 work has not started; its scope and plan remain a separate next step.
