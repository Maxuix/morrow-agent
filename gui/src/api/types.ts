/**
 * Wire types for the Morrow Core API (`/v1`).
 *
 * Field names mirror the server projections in `src/morrow/server/projections.py`
 * exactly — snake_case, no client-side renaming — so the redaction boundary on
 * the server stays the single place where field allowlists live.
 */

export const WORKFLOW_STATUSES = [
  'queued',
  'running',
  'completed',
  'failed',
  'cancelled',
  'blocked',
  'draining',
  'paused',
  'superseded',
] as const

export type WorkflowStatus = (typeof WORKFLOW_STATUSES)[number]

export type SessionLifecycle = 'active' | 'archived' | 'deleted'
export type SessionHealth = 'ok' | 'needs_recovery' | 'quarantined' | 'read_only'
export type TaskRunStatus =
  | 'open'
  | 'ready_for_acceptance'
  | 'accepted'
  | 'cancelled'
  | 'failed'
  | 'abandoned'
export type TaskRunPurpose = 'user' | 'workflow_node'
export type ApprovalResolution = 'pending' | 'approved' | 'denied' | 'expired'
export type ArtifactState = 'staging' | 'available' | 'missing' | 'corrupt'

export interface ContractRefWire {
  kind: string
  version: number
}

export interface ArtifactBindingWire {
  name: string
  artifact_id: string
  contract: ContractRefWire
}

/** `session_wire` */
export interface SessionWire {
  session_id: string
  lifecycle: SessionLifecycle
  health: SessionHealth
  created_at: string
  updated_at: string
  current_task_run_id: string | null
  parent_session_id: string | null
}

/** `task_wire` */
export interface TaskRunWire {
  task_run_id: string
  session_id: string
  purpose: TaskRunPurpose
  status: TaskRunStatus
  attempt: number
  row_version: number
  created_at: string
  updated_at: string
  accepted_at: string | null
  closed_at: string | null
}

/** `WorkflowBudget.model_dump` */
export interface WorkflowBudgetWire {
  max_agent_generation_requests: number | null
  default_node_max_agent_generation_requests: number | null
  admission_timeout_seconds: number | null
  max_concurrency: number
}

/** `run_wire` */
export interface WorkflowRunWire {
  workflow_run_id: string
  workflow_revision_id: string
  root_task_run_id: string
  status: WorkflowStatus
  row_version: number
  started_at: string | null
  completed_at: string | null
  budget_snapshot: WorkflowBudgetWire
  admission_deadline_at: string | null
  input_artifacts: ArtifactBindingWire[]
  result_status: 'succeeded' | 'needs_revision' | null
  pending_terminal_intent: 'user_cancel' | null
  pause_requested: boolean
  run_relation: 'initial' | 'continuation' | 'rerun'
  lineage_budget_root_run_id: string | null
  parent_run_id: string | null
  superseded_reason: 'continued_by_patch' | null
}

/** `node_wire` */
export interface NodeRunWire {
  node_run_id: string
  workflow_run_id: string
  node_id: string
  status: WorkflowStatus
  attempt: number
  row_version: number
  started_at: string | null
  completed_at: string | null
  conversation_session_id: string | null
  leaf_task_run_id: string | null
  agent_run_id: string | null
  effective_node_generation_request_cap: number | null
}

/** `artifact_wire` */
export interface ArtifactWire {
  artifact_id: string
  kind: string
  contract: ContractRefWire | null
  output_slot: string | null
  state: ArtifactState
  sensitivity: string
  retention: string
  byte_size: number
  sha256: string
  excerpt: string
  session_id: string | null
  task_run_id: string | null
  producer_node_run_id: string | null
  created_at: string
  updated_at: string
  row_version: number
}

/** `node_view_wire` */
export interface NodeViewWire {
  node: NodeRunWire
  output_bindings: ArtifactBindingWire[]
  artifacts: ArtifactWire[]
  approval_pending: boolean
  agent_generation_request_count: number
}

/** `pre_run_summary_wire` — §14.1 cost facts; `null` limits mean "no cap". */
export interface PreRunSummaryWire {
  node_count: number
  models: string[]
  providers: string[]
  max_agent_generation_requests: number | null
  default_node_max_agent_generation_requests: number | null
  admission_timeout_seconds: number | null
  max_concurrency: number
  writer_node_ids: string[]
}

/** `import_wire` */
export interface InheritedArtifactWire {
  workflow_run_id: string
  source_workflow_run_id: string
  source_node_run_id: string
  source_node_id: string
  output_slot: string
  artifact_id: string
  contract: ContractRefWire
  inherited_at: string
}

/** one entry of `run_view_wire["effective_outputs"]` */
export interface EffectiveOutputWire {
  node_id: string
  output_slot: string
  binding: ArtifactBindingWire
  artifact: ArtifactWire | null
  inherited: boolean
}

/** `outcome_wire` */
export interface TaskOutcomeWire {
  outcome_id: string
  task_run_id: string
  session_id: string
  version: number
  task_status: TaskRunStatus
  summary: string
  trigger: string
  completion_basis: string
  changed_paths: string[]
  side_effects: string[]
  unresolved_items: string[]
  feedback: string | null
  artifact_refs: unknown[]
  evidence_refs: unknown[]
  created_at: string
}

/** `run_view_wire` */
export interface RunViewWire {
  run: WorkflowRunWire
  /** Full WorkflowRevision model dump; the GUI treats it as opaque detail data. */
  revision: Record<string, unknown>
  nodes: NodeViewWire[]
  input_artifacts: ArtifactWire[]
  agent_generation_request_count: number
  lineage_agent_generation_request_count: number
  inherited_artifacts: InheritedArtifactWire[]
  effective_outputs: EffectiveOutputWire[]
  usage_availability: string
  terminal_outcome: TaskOutcomeWire | null
  actionable_status: string | null
  pre_run_summary: PreRunSummaryWire
}

/** `approval_wire` — bounded previews only, never full tool arguments. */
export interface ApprovalWire {
  approval_id: string
  tool_execution_id: string
  tool_name: string
  session_id: string
  task_run_id: string
  agent_run_id: string
  workflow_run_id: string | null
  node_run_id: string | null
  node_id: string | null
  agent_id: string | null
  effect_class: string
  risk_level: 'low' | 'medium' | 'high'
  session_scope_allowed: boolean
  affected_objects: string[]
  requested_scope: string
  granted_scope: string | null
  preview: string[]
  resolution: ApprovalResolution
  created_at: string
  expires_at: string
  resolved_at: string | null
  row_version: number
}

export type ApprovalDecisionWire = 'allow_once' | 'deny' | 'allow_session'


/** `model_usage` wire — absent values are explicit, never fabricated zeros. */
export interface ModelUsageWire {
  availability: 'available' | 'unavailable'
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
}

/** `model_cost` wire. */
export interface ModelCostWire {
  availability: 'available' | 'unavailable'
  amount_minor: number | null
  currency: string | null
  source: string | null
}

/** `agent_run_terminal_wire` — the subset the observer renders. */
export interface AgentRunTerminalWire {
  agent_run_id: string
  finish_reason: string | null
  stop_code: string | null
  model_attempts: number
  retry_count: number
  tool_rounds: number
  tool_calls: number
  usage: ModelUsageWire
  cost: ModelCostWire
  accounting_basis: string
  finalized_at: string
}

/** `agent_run_observation_wire` — the subset the observer renders. */
export interface AgentRunObservationWire {
  agent_run_id: string
  session_id: string
  task_run_id: string
  terminal_metrics: AgentRunTerminalWire | null
}

export interface AgentRunEnvelopeWire {
  observation: AgentRunObservationWire
}

// Editor contracts ----------------------------------------------------------

export interface ModelRefWire {
  provider_id: string
  model_id: string
}

export interface ToolRequirementWire {
  name: string
  requirement: 'required' | 'optional' | 'forbidden'
}

export interface AgentDefinitionSourceWire {
  definition_id: string
  name: string
  description: string
  role_prompt: string
  skill_version_ids: string[]
  tool_requirements: ToolRequirementWire[]
  access_mode_ceiling: 'read' | 'write'
  max_agent_generation_requests: number | null
  model_selection: ModelRefWire | 'invoking_active'
  derived_from_version_id: string | null
  derived_from_definition_id: string | null
  derived_from_source_hash: string | null
}

export interface AgentDefinitionVersionWire {
  version_id: string
  workspace_id: string
  version: number
  source: AgentDefinitionSourceWire
  content_hash: string
  origin: 'user' | 'builtin'
  source_revision: number
  created_at: string
}

export interface AgentDefinitionHeadWire {
  workspace_id: string
  definition_id: string
  version_id: string
  source_revision: number
  source_hash: string
  enabled: boolean
  row_version: number
}

export interface AgentDefinitionViewWire {
  definition_id: string
  origin: 'user' | 'builtin'
  source_revision: number | null
  source_hash: string | null
  revoked: boolean
  desired_ahead_of_published: boolean
  source: AgentDefinitionSourceWire | null
  head: AgentDefinitionHeadWire | null
  published_version: AgentDefinitionVersionWire | null
}

export interface TaskContractWire {
  objective: string
  scope: string[]
  constraints: string[]
  source_refs: unknown[]
}

export interface NodeOutputRefWire {
  node_id: string
  output_slot: string
}

export interface WorkflowInputBindingWire {
  source: 'workflow_input'
  input_name: string
  accepts: { kind: 'TaskContract'; version: 1 }
  workflow_input: 'task'
}

export interface NodeOutputBindingWire {
  source: 'node_output'
  input_name: string
  accepts: ContractRefWire
  node_output: NodeOutputRefWire
}

export type InputBindingWire = WorkflowInputBindingWire | NodeOutputBindingWire

export interface OutputContractWire extends ContractRefWire {
  slot: string
  required_for_node_completion: boolean
}

export interface AgentDefinitionRefWire {
  definition_id: string
  version_id: string
  content_hash: string
}

export interface AgentNodeSourceWire {
  node_id: string
  agent_definition_ref: AgentDefinitionRefWire
  task_contract: TaskContractWire
  input_bindings: InputBindingWire[]
  output_contracts: OutputContractWire[]
  access_mode: 'read' | 'write'
  conversation_scope: 'isolated' | 'invoking_session'
  tool_requirements: ToolRequirementWire[] | null
  max_agent_generation_requests: number | null
}

export interface WorkflowEdgeWire {
  from_node_id: string
  to_node_id: string
}

export interface WorkflowDefinitionSourceWire {
  workflow_definition_id: string
  name: string
  description: string
  tags: string[]
  origin: 'user' | 'builtin'
  input_contract: { kind: 'TaskContract'; version: 1 }
  required_outputs: NodeOutputRefWire[]
  edges: WorkflowEdgeWire[]
  default_budget: WorkflowBudgetWire
  nodes: AgentNodeSourceWire[]
}

export interface WorkflowDefinitionViewWire {
  workflow_definition_id: string
  origin: 'user' | 'builtin'
  source_revision: number | null
  revoked: boolean
  desired_ahead_of_published: boolean
  source: WorkflowDefinitionSourceWire | null
  head: { workflow_revision_id: string; row_version: number; enabled: boolean } | null
  published_revision: Record<string, unknown> | null
}

export interface WorkflowDraftDiagnosticWire {
  severity: 'error' | 'warning'
  code: string
  message: string
  node_id: string | null
  edge_id: string | null
}

export interface WorkflowDraftWire {
  draft_id: string
  workspace_id: string
  source: WorkflowDefinitionSourceWire
  source_hash: string
  base_workflow_revision_id: string | null
  base_head_row_version: number
  base_source_revision: number
  base_definition_source_hash: string | null
  status: 'draft' | 'validating' | 'valid' | 'invalid' | 'rejected' | 'frozen'
  diagnostics: WorkflowDraftDiagnosticWire[]
  frozen_workflow_revision_id: string | null
  row_version: number
  created_at: string
  updated_at: string
}

export interface WorkflowDraftViewWire {
  draft: WorkflowDraftWire
  stale_reasons: string[]
}

export interface ProviderCatalogWire {
  provider_id: string
  credential_configured: boolean
  models: { model_id: string; active: boolean }[]
}

export interface SkillCatalogWire {
  skill_id: string
  name: string
  availability: string
  versions: { version_id: string; display_version: string }[]
  binding: { enabled: boolean; pinned_version_id: string | null } | null
}

export interface ToolCatalogWire {
  name: string
  description: string
}

export interface ArtifactContractCatalogWire {
  kind: string
  version: number
  schema: Record<string, unknown>
}

/** `EventWire` from `src/morrow/server/protocol.py` */
export interface EventWire {
  cursor: number
  event_id: string
  event_type: string
  aggregate_kind: string
  aggregate_id: string
  payload: Record<string, unknown>
  created_at: string
}

// Run-control command payloads ------------------------------------------------

/** `FutureGraphPatch` on the wire. */
export interface FutureGraphPatchWire {
  workflow_patch_id: string
  workspace_id: string
  parent_run_id: string
  base_workflow_revision_id: string
  expected_parent_row_version: number
  source: WorkflowDefinitionSourceWire
  requested_by: string
  request_kind?: 'user_exact' | 'approved_proposal'
}

/** `_patch_diff_wire` */
export interface PatchDiffWire {
  added_node_ids: string[]
  removed_node_ids: string[]
  changed_node_ids: string[]
  added_edges: string[]
  removed_edges: string[]
  required_outputs_changed: boolean
  budget_changed: boolean
}

/** `_patch_risk_wire` — `elevated` means a C8 dimension fired. */
export interface PatchRiskWire {
  level: 'low' | 'elevated'
  reasons: string[]
}

/** `ServerCommands.patch_validate` result */
export interface PatchValidationWire {
  valid: boolean
  diagnostics: WorkflowDraftDiagnosticWire[]
  past_node_ids: string[]
  execution_node_ids: string[]
  diff: PatchDiffWire | null
  risk: PatchRiskWire | null
}

/** run-control command result: `{run, driving?}` */
export interface RunControlResultWire {
  run: WorkflowRunWire
  driving?: boolean
}

/** `ServerCommands.workflow_rerun` result */
export interface RerunResultWire {
  parent: WorkflowRunWire
  child: WorkflowRunWire
  full: boolean
  inherited_node_ids: string[]
  execution_node_ids: string[]
  driving: boolean
}

/** `ServerCommands.patch_apply` result */
export interface PatchApplyResultWire {
  workflow_patch_id: string
  workflow_revision_id: string
  parent_run: WorkflowRunWire
  child_run: WorkflowRunWire | null
  driving: boolean
}

/** `ServerCommands.approval_resolve` result */
export interface ApprovalResolveResultWire {
  approval: ApprovalWire
  delivery: 'live' | 'durable' | 'replay'
  executed: boolean
}

/** `EventsPageWire` */
export interface EventsPageWire {
  events: EventWire[]
  latest_cursor: number
  has_more: boolean
}

/** `MetaWire` */
export interface MetaWire {
  protocol_version: number
  workspace_id: string
  latest_cursor: number
}

/** `ServerCommands.snapshot` */
export interface SnapshotWire {
  cursor: number
  workflow_runs: WorkflowRunWire[]
  pending_approvals: ApprovalWire[]
}

// Response envelopes ---------------------------------------------------------

export interface SessionsPageWire {
  sessions: SessionWire[]
  next_cursor: string | null
}

export interface TasksPageWire {
  tasks: TaskRunWire[]
  next_cursor: string | null
}

export interface ApprovalsListWire {
  approvals: ApprovalWire[]
}

export interface ArtifactsPageWire {
  artifacts: ArtifactWire[]
  next_cursor: string | null
}

export interface SessionEnvelopeWire {
  session: SessionWire
}

export interface TaskEnvelopeWire {
  task: TaskRunWire
}

export interface RunViewEnvelopeWire {
  view: RunViewWire
}

export interface NodeViewEnvelopeWire {
  view: NodeViewWire
}

/** WebSocket hint message from `/v1/events/stream` — never carries facts. */
export interface StreamHintWire {
  type: 'hello' | 'cursor' | 'ping'
  latest_cursor: number
}
