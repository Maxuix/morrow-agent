# Stage 5 Offline Safety Evaluation

> Date: 2026-08-21
> Status: deterministic offline evidence for Subplan 54.4
> Scope: synthetic Reviewer output and Learning safety/promotion boundaries

This report measures bounded deterministic behavior. It does not claim that a real model will
classify natural language with the same precision. The live model evaluation remains a separate
hold point requiring explicit authorization and a compatible credential.

## Dataset and evaluator

- Dataset: `src/morrow/resources/stage5-learning-evaluation.json`
- Version: `stage5-offline-v1`
- Cases: 26
- Families: explicit Preference/Profile, deterministic Project Knowledge, one-shot, negation,
  quotation, hypothesis, Assistant-only behavior, prompt injection, synthetic secret, prohibited
  personal data, hidden Unicode, capability authorization, inferred identity, duplicate,
  suppression, cross-workspace evidence, future candidate-only types, malformed Reviewer output,
  Selection budgets, and same-Run Selection reuse.
- Evaluator: `morrow.application.learning.evaluation`; it performs no Provider call and writes no
  journal, YAML, workspace, Tool, capability, or runtime state.

## Result

The deterministic report returned:

| Gate | Result |
|---|---:|
| Cases passed | 26 / 26 |
| Safety-negative cases | 5 |
| Safety-negative Active writes | 0 |
| Maximum accepted Reviewer candidate count | 1 (policy maximum remains 3) |
| Invented/cross-workspace evidence | rejected |
| Future Skill/Workflow/Orchestration activation | candidate-only or rejected |
| Selection budget overflow | rejected |
| Same-Run selection reuse | accepted only when explicitly reused |

The fixture includes only synthetic values. The report contains case IDs, dispositions, reason
codes, safety codes, and counts; it does not contain source text, raw Reviewer output, or the
synthetic credential-shaped value.

## Direct command evidence

```text
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run pytest -q tests/test_stage5_learning_evaluation.py
  -> 10 passed
```

The same test module also drives the real Review runner with scripted typed output for positive,
one-shot, negative, quoted, and hypothetical user evidence. Only the positive explicit durable
case produces a Preference Candidate. The other cases complete without a Candidate.

These results establish the deterministic product boundary only. They do not close the optional
live-model quality target or the remaining Stage 5 doctor, backup, documentation, and end-to-end
acceptance work.
