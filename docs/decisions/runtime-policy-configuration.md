# Runtime Policy Configuration Boundary

Status: implemented by Stage 6 Subplan 76 and Stage 7 S7P-06

## Decision

Morrow has two runtime-configuration authorities and no environment-variable or Agent-tool fallback:

1. `morrow/resources/runtime-policy.toml` is the versioned default shipped with the application.
2. The user's global `config.yaml` may contain a partial `runtime_policy` overlay.

Bootstrap validates the packaged file, validates the user document, merges only declared fields,
revalidates all cross-field combinations, and injects the effective immutable policy into AgentRun,
Learning Review and Preference Review composition. A process restart is required after manually
changing `config.yaml`.

Example optional overlay:

```yaml
runtime_policy:
  agent_run:
    max_run_seconds: 2400
    tool_timeout_seconds: 180
  reviews:
    learning_timeout_seconds: 90
    learning_lease_seconds: 180
```

The block may be omitted. Unknown fields, wrong types, non-finite numbers, unsafe combinations and
values above code-owned ceilings make the global document unavailable; Morrow does not partially
apply the remaining keys. Runtime policy is not writable through `update_configuration`,
`manage_preferences`, Learning promotion, Skills, MCP, or a Provider response.

S7P-06 splits AgentRun behavior into two explicit policy shapes. Historical v1 snapshots keep their
bounded rounds/attempts/calls/deadline and character compatibility fields. A new v2 long-horizon run
has no cumulative model-request, tool-round, tool-call, repetition or task-time stop; it requires an
exact configured model context window and uses token accounting, compaction and bounded per-request
retry/truncation settings instead. The default composition selects v2 only when the active configured
model exposes that exact capability. An explicit v2 request without it fails closed; it never guesses a
small character window. Legacy/injected runtimes without a persisted ProviderConfig remain v1-compatible.

Optional v2 overlay fields use the same `runtime_policy.agent_run` block:

```yaml
runtime_policy:
  agent_run:
    compaction_enabled: true
    reserve_tokens: 16384
    keep_recent_tokens: 20000
    retry_enabled: true
    max_retries: 3
    retry_base_delay_seconds: 2.0
    max_provider_retry_delay_seconds: 60.0
    truncation_max_bytes: 51200
    truncation_max_lines: 2000
    grep_max_line_chars: 500
```

These settings tune model-context projection and transient provider recovery only. They do not
reintroduce a task lifetime ceiling, and exact model capability, payload, path, permission and
storage limits remain code-owned.

## Hardcoding inventory and disposition

| Category | Examples found | Disposition |
|---|---|---|
| Process-wide runtime tuning | v1 AgentRun rounds/attempts/calls/deadlines/context/result budgets; v2 compaction/retry/truncation tuning; Learning and Preference Review timeout/lease; Preference retry backoff | v1 defaults remain packaged and readable for compatibility; v2 settings use the same validated partial overlay |
| Per-entity or per-request tuning | MCP Server `timeout_ms`; `run_command` and Skill script request timeouts | Remains on the typed entity/request and is already user-visible; global policy must not become a competing authority |
| Defensive local I/O deadlines | search subprocess deadline, Git inspection deadline, sandbox prepare/collect/cleanup deadlines | Remains code-owned because it bounds local cleanup, termination and resource exposure rather than product behavior |
| Permission and isolation invariants | deny-first capability composition, approval requirements, no-network Skill sandbox, MCP destructive/outside-workspace denials | Remains code-owned and cannot be overridden |
| Secret and path invariants | credential separation, secret filters, protected paths, traversal/symlink/hardlink refusal, file modes | Remains code-owned and cannot be overridden |
| Protocol/persistence budgets | event, snapshot, schema, arguments, result, Artifact, package, backup and diagnostic size/count/depth limits | Remains code-owned so untrusted YAML cannot widen memory/disk/parser exposure |
| Schema and compatibility constants | SQLite/YAML schema versions, migration checksums, ID prefixes, enum values, protocol names | Remains code-owned; these describe data/protocol identity rather than tuning |
| Retry correctness limits | Learning/Preference maximum attempts, no automatic MCP retry, Tool recovery declarations, SQLite write retry bounds | Remains code-owned because changing them alters recovery semantics or persisted constraints |

## Safety model

The packaged and user values are defaults/tuning only. Independent Pydantic field ceilings and
combination validators stay in `morrow.core.runtime_policy`; those ceilings are intentionally not
represented in either configuration file. In particular, users cannot override:

- `loop_detection_enabled`;
- the verified exact-model `model_safe_request_chars` table;
- permission, approval or isolation behavior;
- secret/path/schema/payload/storage limits;
- maximum Review attempts or retryable failure classes.

Effective AgentRun policy shape and values continue to be frozen into each AgentRun snapshot. v1
resume uses its historical bounded values; v2 records explicit `None` for retired cumulative controls
and the exact context/retry settings. Review rows continue to record model/policy/evidence identity,
while timeout and lease behavior comes from the effective bootstrap policy rather than constructor
literals.
