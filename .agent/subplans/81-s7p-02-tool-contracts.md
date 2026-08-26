# Subplan 81 — S7P-02 Provider-visible Tool Contracts

> Status: completed
> Branch: `codex/fix/s7p-02-tool-contracts`
> Base: activation baseline `36f3be18d2d9ce813a827a704ea911aa4baa502a`
> Dependency: S7P-01 completed, integrated and retired

## Goal

Make every static Direct tool's final Provider-visible JSON Schema consistent with its executable
argument validator, recovery/capability declaration, handler preflight and bounded error envelope.
Repair the hidden command and file-write shape rules, add a fail-closed contract audit, and prove
one invalid call can be corrected by an Agent on the next model turn.

## Ownership

- `src/morrow/runtime/tool_arguments.py` and `src/morrow/runtime/tools.py` schema/validator/audit
  seams;
- static Direct argument models and factories in `src/morrow/application/local_tools.py`, with
  narrowly required configuration/preference/Skill compatibility edits;
- the OpenAI-compatible final tool wire and frozen tool-schema digest path;
- bounded invalid-argument error envelopes and S7P-01 durable diagnostic compatibility;
- focused schema, inventory, Provider-wire, Agent-repair, error-code and MCP regressions;
- `docs/acceptance/s7p-02-tool-contracts.md` and Subplan 81 execution state.

This subplan does not own prompts/project instructions, delete/move/rename, completion truthfulness,
budget/progress policy, retry/backoff, steering/follow-up, Workflow implementation, public events,
runtime-policy defaults, dependencies or the three user-owned research documents.

## Reproduced defects

### Hidden command XOR

The exact schema forwarded by `OpenAICompatibleProvider.serialize_tool()` contains nullable optional
`argv` and `shell` properties and no `oneOf`. A local evaluation of that schema accepts both `{}` and
`{"argv":["pwd"],"shell":"pwd"}`; `RunCommandArguments.model_validate_json(..., strict=True)`
rejects both. The current named XOR test checks only Chinese description text.

### Hidden write mode/revision relation and false size promise

The write-file schema requires path/content/mode but cannot show that replace requires
`expected_sha256` and create forbids it. It also promises 1 MiB content even though the common raw
validator rejects strings above 64 KiB and whole argument JSON above 128 KiB first.

### No complete contract audit

Pydantic schema generation, `ToolDefinition`, schema digests and Provider serialization are related
by convention. There is no normalized explicit schema override for representable cross-field
constraints and no fail-closed audit over the final static inventory. Dynamic MCP validates its
schema locally, but the same guarantee is not applied to Pydantic-backed tools.

### Weak recovery semantics

Value-free validation details are already safe and durable, but cross-field failures commonly
produce only `$ / value_error`. No scripted Agent test proves the model sees a concise expected
shape, corrects the next call and then receives the ordinary success envelope.

## Detailed implementation contract

### 1. One Provider-schema seam

Add an optional explicit schema to the Pydantic-backed validator/tool factory. Normalize and compile
it with the existing bounded local Draft 2020-12 subset. Validate raw JSON against this exact schema
before strict Pydantic construction. Default-only tools still use their generated schema, but all
definitions and frozen digests must reference the normalized validator schema.

Registration or ToolSet snapshot fails closed when:

- definition and validator schema canonical digests differ;
- the schema contains unsupported/unbounded/remote constructs;
- function, recovery declaration or tool names disagree;
- a production tool lacks the needed handler/resolver/recovery facts;
- serialization changes or introduces local policy/approval/capability data.

Audit output contains only names, safe flags and digests. It contains no arguments/results.

### 2. Static schema repairs

- `run_command`: exact XOR, non-null/non-empty branches, bounded argv items/total-safe collection,
  bounded nonblank shell, workspace-relative cwd, 90-second maximum and no extras.
- `write_file`: discriminated create/replace branches for revision presence, content bound aligned to
  the raw validator, workspace-relative path and no extras.
- common paths: one lexical schema/runtime helper; handler preflight continues to own existence,
  containment and symlink facts.
- `search_text`: Provider-visible `query`, while `find_files.pattern` remains discovery syntax.
- configuration/preferences/Skill Script: audit every actual inventory schema and encode only the
  cross-field shapes that the supported subset can prove. State-dependent facts remain typed
  preflight errors.

### 3. Recovery and error semantics

The error envelope retains `invalid_arguments` and bounded `{path,type}` details. For known schema
shape failures it may include one safe `expected` label/shape identifier. Persistence continues to
filter to `{path,type}`. Add an error matrix proving policy denial, invalid target, missing target,
search backend failure and handler execution failure retain distinct codes.

### 4. Final-wire and Agent correction evidence

Capture the actual kwargs passed to the OpenAI-compatible SDK stub and validate every schema in the
ordinary and auto-sandboxed Direct inventories. Tests submit positive/negative fixtures to both the
Provider schema validator and runtime validator. A scripted Agent first issues one invalid command,
reads the tool error, issues one valid corrected command, then stops normally; durable observation
contains no raw sentinel.

## Validation and acceptance

Use the commands in `.agent/PLAN.md`. Acceptance evidence records the before/after schema snippets,
inventory matrix, correction transcript summary, distinct error codes, safety scan, exact gate
results and residual empirical gate. Live repeated invalid-argument-rate measurement remains part of
the already ordered S7P-09 evaluation and is not replaced by synthetic percentages.

## Review and handoff

After coherent verified implementation commits, spawn a read-only `gpt-5.6-luna` / `max` reviewer
inside the same implementation task for the complete activation-base...HEAD diff. Repair all
confirmed findings, rerun focused/full offline gates, update documents/state and commit. Leave
merge, worktree/branch retirement and S7P-03 activation to the root task.
