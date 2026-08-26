# Evidence-backed Test Report

Return a self-contained Markdown report. Match the user's language and keep raw sensitive data out.

## Required sections

### 1. Verdict

State overall confidence and whether the tested revision is fit for the exercised user goals. Give
counts for `PASS`, `FAIL`, `BLOCKED`, `NOT RUN`, and `INCONCLUSIVE`. List release-blocking findings
first. Do not call the product ready when a core journey failed or was blocked.

### 2. Test basis

Record revision, dirty-state caveat, date/time, platform, launch mode, state isolation, public
interfaces exercised, and relevant non-secret configuration. State whether Provider-backed testing
was authorized and actually executed.

### 3. User Surface Inventory

Use a compact table with:

| Surface / capability | Implementation evidence | Reachable states or modes | Scenario IDs | Coverage status |
|---|---|---|---|---|

Identify documentation conflicts, discovery uncertainty, and surfaces excluded as not implemented.

### 4. Scenario results

Use a table with:

| ID | Persona and real task | Preconditions | User actions | Expected | Actual and evidence | Status |
|---|---|---|---|---|---|---|

Keep the table scannable. Put long reproduction details beneath the table and refer to them by ID.
Each status must have evidence or a precise blocking reason.

### 5. Complex journeys

For each complex journey, explain the end-to-end goal, state carried between steps, complication,
recovery behavior, final outcome, and evidence. Show why it is distinct from the ordinary cases.

### 6. Findings

For each defect include:

- severity (`P0` critical, `P1` high, `P2` medium, `P3` low);
- affected user and outcome;
- shortest public-interface reproduction;
- expected and actual behavior;
- reproducibility and evidence;
- likely boundary or component only when supported by diagnosis;
- workaround, if one was observed without modifying the product.

Do not turn harness failures into product defects.

### 7. Coverage and gaps

Report at least:

- discovered public capabilities versus capabilities exercised;
- planned scenarios versus executed scenarios;
- meaningful state transitions covered;
- number of distinct complex journeys;
- Provider-backed scenarios executed, blocked, or omitted;
- untested combinations, roles, platforms, integrations, and reasons.

Percentages must show their numerator and denominator. If discovery may be incomplete, say so next
to the percentage.

### 8. Provider evidence

When used, record only non-secret adapter/model identifiers, call count, tested behaviors, outcome
summary, and available aggregate latency or cost. When not used, state whether the reason was scope,
policy, credentials, environment, or lack of a relevant implemented path.

### 9. Recommended next actions

Order actions by user impact and confidence. Separate product fixes, test-environment work, and
additional authorized coverage. Testing-only work should recommend changes, not apply them.

## Evidence quality

Prefer concise command/output excerpts, screenshots, artifact links, and public state observations.
Redact credentials, private inputs, full provider payloads, tracebacks containing secrets, and noisy
logs. Include enough detail for another tester to reproduce the outcome at the same revision.
