# Runtime Policy Configuration Boundary

Status: implemented by Stage 6 Subplan 76

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

## Hardcoding inventory and disposition

| Category | Examples found | Disposition |
|---|---|---|
| Process-wide runtime tuning | AgentRun rounds/attempts/calls/deadlines/context/result budgets; Learning and Preference Review timeout/lease; Preference retry backoff | Moved to packaged `runtime-policy.toml`; declared partial user overlay allowed |
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

Effective AgentRun limits continue to be frozen into each AgentRun snapshot. Review rows continue to
record model/policy/evidence identity, while timeout and lease behavior comes from the effective
bootstrap policy rather than constructor literals.
