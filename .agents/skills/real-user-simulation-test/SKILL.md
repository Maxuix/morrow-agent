---
name: real-user-simulation-test
description: Dynamically discover a project's currently implemented user-facing capabilities, exercise them through realistic end-to-end user behavior, and return an evidence-backed simulation test report. Use for user simulation, exploratory acceptance, journey coverage, or release-readiness testing; do not use as a substitute for unit tests or for features that exist only in plans.
---

# Real User Simulation Test

Test the product as a real user would. Derive the test mission from the implementation that exists
at invocation time; never carry a fixed project feature list or prewritten test cases in this Skill.

## Non-negotiable rules

- Obey the repository's instructions and the current user's scope. Testing does not authorize fixes,
  production mutations, account creation, purchases, publication, or other external side effects.
- Treat a capability as implemented only when a current public entry point and implementation
  evidence agree. Roadmaps, TODOs, proposals, disabled code, and aspirational documentation are not
  sufficient.
- Exercise public user interfaces first: UI, CLI, conversational interface, public API, SDK, or
  documented workflow. Internal functions may diagnose a failure but cannot prove a user scenario
  passed.
- Use isolated, disposable state whenever possible. Preserve existing user data and unrelated
  workspace changes. Never expose credentials, private prompts, reasoning, raw provider payloads, or
  sensitive logs in evidence or reports.
- Do not repair defects during a testing-only request. Record and reproduce them. Change code only
  if the user separately asks for implementation.
- Never claim full coverage when any discovered surface is untested or blocked. Report the gap and
  its reason.

## Workflow

### 1. Establish the test basis

Read the applicable repository instructions before acting. Record the tested revision, dirty-state
warning, platform, relevant configuration mode, and available public launch paths. Inspect the
current implementation, CLI help or routes, user documentation, configuration examples, and tests
as supporting evidence.

Build a `User Surface Inventory` containing every presently reachable user capability, action,
state transition, role or mode, and externally observable failure path. For each entry, cite the
evidence that makes it part of the shipped surface. Resolve documentation/code conflicts in favor of
the public behavior that can actually be reached, and disclose the conflict.

### 2. Generate scenarios at runtime

Before creating the matrix, read
[scenario-generation.md](references/scenario-generation.md). Generate fresh, outcome-oriented user
tasks from the inventory; do not translate existing unit tests one-for-one and do not test future
work.

Cover every discovered capability with at least one realistic happy path. Add reachable validation,
permission, cancellation, recovery, empty-state, repeat-use, and boundary paths according to the
surface's actual states and risks. Use representative combination coverage when exhaustive Cartesian
coverage would be impractical, and list combinations not exercised.

Include at least three distinct complex journeys when the implemented product can support them. A
complex journey must be a credible multi-step goal spanning multiple actions, states, or surfaces,
with continuity, correction, interruption, recovery, or another realistic complication. If fewer
than three are possible, explain the product limitation rather than inventing features.

### 3. Prepare safe execution

Use the product's supported setup and launch path. Prefer temporary directories, fixture accounts,
local services, sandboxed data roots, reversible inputs, and unique test identifiers. Run the
smallest relevant baseline health check before scenarios; ordinary automated tests may support the
diagnosis but do not count as user simulation coverage.

For every scenario, define the persona and intent, preconditions, exact user-visible actions,
expected outcome, observable success oracle, cleanup, and risk before execution. Avoid prompts or
inputs optimized with knowledge a normal user would not have.

### 4. Execute like a user

Perform the documented actions through the public surface. Preserve authentic sequencing: users may
make imprecise requests, inspect output, revise input, cancel, retry, resume, or return with retained
state. Do not bypass a difficult step with direct database edits or private helpers.

Capture concise, sanitized evidence for each result: exact input or action, relevant output, status
or exit code, screenshot or artifact when useful, and enough state to reproduce it. Compare semantic
outcomes to the declared oracle; do not require exact wording from nondeterministic systems.

Continue after an individual failure when isolation and safety allow it. Reset only the affected
fixture, reproduce the failure once when practical, and distinguish product defects from harness,
environment, authorization, and dependency failures.

### 5. Provider-backed paths

Provider calls are an eligible test lane, not a requirement to fake. Use a real configured Provider
when the current request explicitly permits live/provider testing, a compatible credential already
exists, and repository policy allows it. The Skill itself grants no network, credential, cost, or
external-side-effect authority.

Use the configured Provider/Model rather than silently switching or falling back. Make only the
calls needed to cover distinct user behavior; repeat critical nondeterministic scenarios only when
cost and time are proportionate. Record the public adapter/model identifier, call count, observable
outcomes, and latency or cost metadata when available, but never secret values or raw sensitive
payloads. If live execution is unavailable, test the offline path and mark the live lane `BLOCKED`
or `NOT RUN` with the exact reason.

### 6. Reconcile coverage

Before reporting, reconcile the executed scenarios against the inventory. The run is complete only
when:

- every discovered public capability appears in the matrix;
- every executable scenario has an evidence-backed result;
- stateful surfaces cover their meaningful transitions and recovery behavior;
- at least three supported complex journeys were executed;
- all failures and blocked lanes have explicit impact and reproduction notes; and
- all omitted combinations and discovery uncertainties are visible.

Do not keep testing merely to inflate counts. Stop when additional cases duplicate the same behavior
and risk, or when further work requires new authorization. State the stopping reason.

### 7. Return the report

Before writing the result, read [report-format.md](references/report-format.md). Return one
self-contained Markdown report in the user's language. Lead with release confidence and material
failures, then provide the inventory, scenario results, complex journeys, defects, coverage, Provider
evidence, and remaining gaps.

Use only `PASS`, `FAIL`, `BLOCKED`, `NOT RUN`, or `INCONCLUSIVE` for scenario status. Link to any
persisted screenshots or artifacts. Persist the report only when the user requests a file or the
repository already defines a report destination; otherwise return it directly without adding
generated files to the project.
