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
    tool_timeout_seconds: 180
    reserve_tokens: 20000
  reviews:
    preference_timeout_seconds: 90
    preference_lease_seconds: 180
```

The block may be omitted. Unknown fields, wrong types, non-finite numbers, unsafe combinations and
values above code-owned ceilings make the global document unavailable; Morrow does not partially
apply the remaining keys. Runtime policy is not writable through `update_configuration`,
`manage_preferences`, Learning promotion, Skills, MCP, or a Provider response.

AgentRun has one policy shape: v2 long-horizon. It has no cumulative model-request, tool-round,
tool-call, repetition or task-time stop. Configured and injected Providers use the same resolver.
Historical v1 snapshots and retired cumulative override fields fail strict validation instead of
silently selecting another execution path. With an exact configured model context window, v2 uses
the Pi token threshold and expands the reserve to include a known maximum output capability;
without an exact window it uses the 256-KiB conservative character request boundary to trigger the
same compaction path and leaves token-window telemetry absent.

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
| Process-wide runtime tuning | AgentRun context/result/tool-timeout, compaction/retry/truncation tuning; Learning and Preference Review timeout/lease; Preference retry backoff | Uses one validated partial overlay and one v2 AgentRun resolver |
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

- the verified exact-model `model_safe_request_chars` table;
- permission, approval or isolation behavior;
- secret/path/schema/payload/storage limits;
- maximum Review attempts or retryable failure classes.

Effective AgentRun policy shape and values continue to be frozen into each AgentRun snapshot. Only
v2 snapshots are accepted, and they record the exact context/retry settings without retired
cumulative controls. Retry progress is stored separately as bounded mutable
AgentRun evidence so resume can restore retry transitions without rewriting the immutable snapshot;
v20 rows remain readable before the v21 migration. Review rows continue to record model/policy/evidence
identity, while timeout and lease behavior comes from the effective bootstrap policy rather than
constructor literals.
