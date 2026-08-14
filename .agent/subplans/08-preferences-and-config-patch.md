# Subplan 08 — Preferences and ConfigPatch

> Stage: 1B  
> Status: completed
> Parent: [Stage 1 implementation plan](../PLAN.md)

## Objective

Implement the three-layer collaboration preference model and one safe typed write path shared by deterministic commands and explicitly gated natural-language configuration.

## Prerequisites

- Subplan 07 and the Stage 1A gate are complete.
- Real-project feedback does not require a roadmap-level boundary change; if it does, update the roadmap and active plan first.

## Tasks

1. Implement global, workspace, and session Preferences for language, response detail, and instruction lists. Interface text remains Simplified Chinese; `language` controls model replies only.
2. Implement scalar precedence `session > workspace > global > default`, scoped `unset`, and instruction merging in exact global → workspace → session order. Normalize duplicates so only the highest-priority source remains, placed later in the resulting list.
3. Treat a missing `preferences.yaml` as an empty workspace layer. Create it only when the first validated workspace Preferences patch commits successfully; reads and failed patches must not create it.
4. Define typed `ConfigPatch` results: `no_change`, `clarification_required`, or `config_patch`.
5. Enforce the allowed scope/target matrix, field whitelist, and `set`/`unset`/`append`/`remove` operation constraints.
6. Apply each patch as one validated transaction with expected revision and zero partial side effects. Global Preferences updates must use Subplan 03's `GlobalConfigStore` whole-document update and preserve Provider/`active_model` fields.
7. Extend `CommandService` with `/config`, `/config edit [scope]`, and `/config reset <scope>` through the same patch application service; Orchestrator remains a dispatcher.
8. Implement a local, conservative, independently tested `ConfigIntentGate`. It may match only when the entire input is a standalone persistence request and contains both an explicit persistence action and an explicit scope, target, or recognized configuration field. A single word/phrase such as “记住”, “以后”, “这个项目”, “这次”, or “请” can never trigger it.
9. Add mandatory gate fixtures. Must-trigger examples include “请记住这个项目以后用中文回复” and “把这条约束写进项目档案”. Must-not-trigger examples include “这个项目用什么框架？”, “以后再改”, “记住刚才的报错”, “这次先这样”, and “请帮我修复这个问题”, plus ordinary development dialogue.
10. Integrate the gate after slash-command dispatch. Only a matched standalone request may call Subplan 05's `ContextBuilder`-backed structured helper to extract a patch; ordinary messages proceed directly to streaming chat.
11. Detect mixed task/configuration input locally, make zero extraction calls, and require the user to separate the requests rather than dropping or rewriting the task portion.
12. Treat `clarification_required` as one bounded question, not a hidden configuration mode. If the next input is not a direct answer to that question, return immediately to normal dispatch.
13. Reject all natural-language attempts to change credentials, Provider/Model connection data, workspace identity/index, schema/revision, permissions, or system safety boundaries.
14. Replace the relevant typed context snapshot after a successful edit so the next model turn observes it.

## Verification

- All scalar precedence, scoped unset, list order, deduplication, and removal cases pass.
- Ordinary chat and every must-not-trigger fixture perform zero `complete()` calls for configuration extraction.
- Every must-trigger fixture enters extraction, while mixed inputs produce a local split request with zero `complete()` calls.
- Explicit intent produces each of the three result types under scripted tests.
- Sensitive/forbidden field requests result in zero writes.
- Invalid, ambiguous, or failed repair output produces zero writes and at most one clarification.
- Successful command and natural-language edits use the same patch validator and affect the next turn.
- A non-answer after a clarification is handled as ordinary input, not as implicit configuration continuation.
- Missing workspace Preferences read as an empty layer; only a successful first workspace patch creates `preferences.yaml`.
- Interleaved global Preferences and Provider updates retain both domains and advance the shared global revision.

## Completion criteria

- `S1B-01` and `S1B-02` pass.
- There is one authoritative configuration write path.
- Natural-language configuration remains a gated feature, never a universal pre-chat model call.

## Deliverables

- Preferences merge engine.
- Typed ConfigPatch validation/application service.
- Deterministic config commands and conservative intent/extraction path.
