# Handoff Reference Classification

> Audit date: 2026-08-17
> Purpose: account for every retained repository reference after product removal.

## Classification rules

| Category | Owning action | Rationale |
|---|---|---|
| Current product/source/test | Remove | A supported command, type, state API, runtime path, or product promise would be false. |
| Historical Stage 1/2 record | Retain and label | Completed plans, reviews, evidence, and logs must remain truthful about behavior at that time. |
| Legacy-data non-destruction | Retain | Exact filename/command sentinels prove removal, ignored files, and ordinary unknown-command behavior. |
| Unrelated external/research use | Retain unchanged | Framework or external-system “handoff” terminology does not name the removed Morrow feature. |

## Current authority

| Paths | Category | Result |
|---|---|---|
| `src/morrow/**` | Current product/source | Zero Handoff implementation or command references; removed. |
| `README.md` | Legacy-data documentation | Retains only the ignored `handoff.yaml(.bak)` policy; current commands and promises are removed. |
| `docs/ARCHITECTURE.md` | Legacy-data documentation | Retains only the fact that legacy files are outside the current state API. |
| `docs/ROADMAP.md` | Historical/future boundary | Labels the Stage 1 experiment as removed; the 2026-08-18 route now separates Stage 4 runtime persistence from Stage 5 learning. |
| `docs/roadmap/stage-4-sessions-context-and-memory.md` | Compatibility entry | The old filename now redirects readers to the new Stage 4 persistence and Stage 5 learning documents. |
| `tests/test_preferences_and_orchestration.py` | Legacy/negative test | Unknown-command and byte-identical ignored-file sentinels only. |
| `tests/test_stage2_product_acceptance.py` | Negative product test | `/handoff` and `/continue` must render ordinary unknown-command results. |
| `tests/test_stage_boundary.py` | Negative boundary | Asserts removed public symbols and store methods are absent. |
| `tests/test_context_projections.py` | Negative context sentinel | Asserts the structured system-state projection has no `handoff` key. |
| `tests/test_context_runtime.py` | Negative context sentinel | Asserts supported runtime context contains no `handoff` key or `current_goal`. |

## Historical records retained and labeled

| Paths | Category | Rationale |
|---|---|---|
| `docs/roadmap/stage-1-direction-and-prototype.md` | Historical Stage 1 | Records the original experiment and acceptance scope. |
| `docs/roadmap/stage-2-agent-core.md` | Historical Stage 2 | Records the accepted Stage 2 architecture before removal. |
| `docs/acceptance/stage-1a-evidence.md`, `stage-1b-evidence.md`, `stage-2-evidence.md` | Historical evidence | Results are not rewritten as if the feature never existed. |
| `docs/reviews/stage-1-direction-and-prototype-review.md` | Historical review | Preserves original product criticism and decisions. |
| `docs/reviews/stage-2-agent-core-final-proposal.md` and its two review files | Historical proposal/review | Preserves decision history; each document has a historical marker. |
| Git commit `cbc3d6d` 中的 `.agent/PLAN.md`, `.agent/TODO.md`, `.agent/TRACKER.md`, `.agent/subplans/21-24-*`，以及当前 `.agent/LOG.md` | Historical removal record | Living plan files later moved on; the committed snapshot and append-only log retain the exact removal evidence. |

## Unrelated occurrences

Repository-wide review found external framework/research uses in historical review material (for example,
OpenAI Agents SDK terminology). They remain inside already labeled historical review documents and do not
describe a Morrow command, model, or persistence authority.

## Audit conclusion

No unclassified current-product occurrence remains. Any future new occurrence must be classified here or
removed before acceptance.
