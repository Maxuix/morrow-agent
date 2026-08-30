# Subplan 96 — Current Chain Documentation and Feasibility Audit

Status: completed, verified and integrated into local `main` at `3215909`.

## Objective

Reconcile current user documentation and architecture records after compatibility removal, then
exercise the shipped public CLI and conversational workflows through isolated, realistic user
journeys. Diagnose any blocked or failed formal path without using private APIs as acceptance
evidence.

## Ownership

- current README, architecture, roadmap and acceptance documentation
- public CLI/help surface inventory and documentation links
- isolated offline user-simulation evidence and feasibility report
- documentation-only fixes discovered by the audit

## Boundaries

- Preserve unrelated dirty secret-preview work and do not include it in this subplan.
- Use disposable state and projects; do not read or mutate real user state or credentials.
- Do not run Live Provider/network tests without explicit authorization and compatible credentials.
- Product defects discovered by simulation are reproduced and diagnosed; code fixes require a
  separate implementation request.

## Acceptance

- Current authoritative docs describe only reachable current-version behavior.
- Every discovered public capability is inventoried and reconciled to scenario coverage.
- At least three distinct supported complex journeys are exercised through public entry points.
- Failures and blocked lanes include reproducible evidence and cause analysis.
- Relevant offline/static/document-link gates pass.

## Result

- Current README, architecture, roadmap and Stage 5/6 acceptance records now describe the current
  format and link to an indexed current acceptance baseline.
- Four public-entry-point journeys passed: configuration/conversation, tool/task/learning,
  Preference/state governance, and Skill/MCP lifecycle.
- No compatibility-cleanup regression blocked the formal chain. Two non-blocking control-plane
  diagnostics are recorded for follow-up; the unauthorized Live Provider lane remains blocked.
- Evidence: `docs/acceptance/current-chain-feasibility.md`, committed in `8da196b`.

## Validation

- focused public-chain regressions: `123 passed in 11.00s`
- full offline suite: `1280 passed, 2 deselected in 103.15s`
- Ruff format/check, compileall, CLI help, current-doc link check and `git diff --check`: passed
