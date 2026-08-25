# Subplan 77 — Skill Script Diagnostics and Callable Context

> Status: completed locally
> Branch: `fix/stage6-script-diagnostics`
> Baseline: `main@aba2785`

## Objective

Close the two remaining Stage 6 Skill Script usability findings: preserve reviewed diagnostics when
a Script domain failure reaches the Agent boundary, and expose each frozen Skill `selection_id` in
the model-visible low-authority context so `run_skill_script` can be called with existing evidence.

## Boundaries

- do not expose raw exceptions, tracebacks, host paths, credentials or arbitrary handler text;
- do not change public event keys, lifecycle ordering or ConversationLog ownership;
- do not make Skill text authoritative or let context grant tools, permissions or approval;
- do not weaken exact selection/package matching, sandboxing, output budgets or recovery behavior.

## Completed work

1. Added a bounded `PublicDiagnosticError` contract with stable code syntax, single-line message
   limits and secret-material refusal.
2. Declared `SkillScriptExecutionError` as public-safe and preserved its specific code/message in
   normal ToolExecutor error envelopes.
3. Let AgentLoop retain only explicitly declared public diagnostics while keeping unknown failures
   on the existing generic internal-error message.
4. Rendered Morrow-owned `selection_id` together with the frozen Skill identity; package body text
   remains explicitly low authority.
5. Added regressions for tool envelopes, Agent fallback, secret/control-line refusal and context
   projection.

## Validation

- direct diagnostic/context matrix: `34 passed in 3.30s`;
- expanded Skill/Agent/context/architecture matrix: `161 passed in 4.66s`;
- full non-live suite: `1077 passed, 2 skipped, 2 deselected in 42.77s`;
- Ruff format/check, compileall, CLI help and `git diff --check`: passed;
- no live Provider, network, credential, user-state or remote-push path was used.
