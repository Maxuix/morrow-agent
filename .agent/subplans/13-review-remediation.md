# Subplan 13 — Subplans 8–12 Review Remediation

Status: in progress; explicitly authorized by the user on 2026-09-05.
Branch: `fix/stage8-subplans-8-12-review`, based on verified local main `429d812`.
Review: `docs/reviews/stage-8-subplans-8-12-code-review.md`.

Fix all seven findings without changing bundled runtime policy or the public event lifecycle:

1. Require approval for removed/replaced input evidence, including generic TextResult reports.
2. Separate controlled frontier cancellation from actual driver loss and close leaf lifecycles.
3. Preserve signal/pause semantics in serial fallback.
4. Reload Profile constraints for planning and reconcile changes across classification awaits.
5. Share the frozen canonical node-role convention between planning and feedback, retaining
   definition-origin fallback for non-role node IDs and preserving existing Revision hashes.
6. Merge Learning pagination across all candidate types.
7. Render promotion/authorization state from current service projections.

Work one logical task at a time; add regression coverage for runtime and service behavior.
Validate touched suites and the full offline gate, Ruff, compileall, CLI help, GUI typecheck/tests/
budgeted build and whitespace. Commit verified progress, fast-forward local main, verify ancestry
and retire the topic branch. Remote push and Live tests remain unauthorized.
