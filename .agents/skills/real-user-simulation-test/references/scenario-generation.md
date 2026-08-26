# Runtime Scenario Generation

Use this method only after discovering the current project's public user surface. The output must be
derived anew for the tested revision; none of the categories below are project-specific test cases.

## 1. Build the surface model

For each public capability, record:

- entry surface and user-visible action;
- personas or permission levels that can actually reach it;
- preconditions and external dependencies;
- starting, success, empty, invalid, denied, cancelled, interrupted, and recoverable states that
  truly exist;
- persistent or cross-session effects;
- observable success and failure signals;
- implementation and user-documentation evidence.

Deduplicate aliases that reach the same behavior, but keep distinct interfaces when their user
experience or failure modes differ.

## 2. Convert the model into user goals

Write tasks as outcomes a plausible user wants, not as implementation checks. Each task must be
possible using only information available from the product or its user documentation. Give each one
a stable runtime ID, persona, intent, preconditions, actions, oracle, cleanup, and risk.

Generate cases in this order:

1. one ordinary success journey for every capability;
2. each meaningful public state transition;
3. errors a user can trigger through normal or mistaken use;
4. permission, configuration, dependency, cancellation, retry, and recovery behavior that exists;
5. repeated use, retained state, restart/resume, and cross-surface continuity where implemented;
6. boundaries supported by documented limits or implementation evidence;
7. interactions among features whose shared state or sequencing can change the outcome.

Do not manufacture exotic inputs only to reach private branches. Boundary and adversarial cases
must still represent plausible user behavior unless the user explicitly requests fuzzing or
security testing.

## 3. Construct complex journeys

A complex journey should combine a meaningful subset of the following:

- multiple public capabilities or interfaces;
- state that must survive across steps, turns, or process restarts;
- an interruption, correction, partial failure, denial, or retry;
- ambiguous but reasonable input requiring clarification;
- a real external or Provider-backed dependency;
- verification that the final user goal, not merely an intermediate command, was achieved.

Choose at least three journeys with different risks and state shapes. Do not create three cosmetic
variations of the same flow. If the product exposes fewer independent workflows, execute all valid
combinations and document why three distinct journeys cannot exist.

## 4. Control combinations

When roles, modes, inputs, configurations, and states create too many combinations, cover:

- every value at least once;
- every high-risk interaction directly;
- representative pairwise interactions for lower-risk dimensions;
- all previously failing combinations known from current evidence;
- both first-use and repeat-use behavior when state persists.

List the omitted combinations and rationale. Never present sampled combinations as exhaustive.

## 5. Define honest oracles

Prefer user-observable oracles: rendered state, returned data, exit status, public event, persisted
artifact, restored session, or externally visible side effect. For generative output, use a short
semantic rubric tied to the user's goal and constraints. A plausible-looking response is not enough
when the requested effect should persist or invoke a tool.

Classify results consistently:

- `PASS`: the complete declared user outcome was observed.
- `FAIL`: the surface was executable but behavior violated its oracle.
- `BLOCKED`: execution could not begin or finish because of environment, permission, credential, or
  dependency constraints outside the behavior under test.
- `NOT RUN`: intentionally omitted after prioritization or because current authorization excludes it.
- `INCONCLUSIVE`: evidence was insufficient or nondeterminism prevented a defensible verdict.
