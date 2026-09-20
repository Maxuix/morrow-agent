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
  metadata?: { title: string; pinned: boolean; revision: number }
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

/** `node_execution_wire` — per-node execution projection (BUG-GUI-002, P02). */
export interface NodeExecutionWire {
  state: 'running' | 'pausing' | 'paused' | 'needs_recovery'
  segment_id: string | null
  control_generation: number | null
  reason: string | null
  settled_at: string | null
}

/** `node_view_wire` */
export interface NodeViewWire {
  node: NodeRunWire
  output_bindings: ArtifactBindingWire[]
  artifacts: ArtifactWire[]
  approval_pending: boolean
  agent_generation_request_count: number
  /** Absent on pre-P02 servers: the GUI falls back to business status only. */
  execution?: NodeExecutionWire | null
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
  feedback: string[]
  artifact_refs: unknown[]
  evidence_refs: unknown[]
  created_at: string
}

export type TaskArtifactAvailability = 'available' | 'staging' | 'missing' | 'corrupt' | 'unavailable'
export type TaskArtifactSource =
  | 'task_evidence'
  | 'registered_result'
  | 'workflow_output'
  | 'workflow_delivery'
  | 'command_output'
export type TaskArtifactEncoding = 'utf8' | 'binary' | 'unknown'

export interface TaskArtifactProvenanceWire {
  kind: string
  role: string
  reference_id: string
}

export interface TaskArtifactItemWire {
  artifact_id: string
  kind: string
  name: string
  path: string | null
  mime: string | null
  source: TaskArtifactSource
  availability: TaskArtifactAvailability
  byte_size: number
  excerpt: string
  diff: string | null
  diff_truncated: boolean
  content_complete: boolean | null
  content_encoding: TaskArtifactEncoding
  omission_reason: string | null
  retention: string
  row_version: number
  session_id: string | null
  task_run_id: string | null
  workflow_run_id: string | null
  node_run_id: string | null
  output_slot: string | null
  provenance: TaskArtifactProvenanceWire[]
  created_at: string
  updated_at: string
}

export interface TaskFileChangeWire {
  path: string
  operation: string
  status: string
  artifact_id: string | null
  source: TaskArtifactSource
  availability: TaskArtifactAvailability
  mime: string | null
  diff: string | null
  diff_truncated: boolean
  content_complete: boolean | null
  content_encoding: TaskArtifactEncoding
  omission_reason: string | null
  message: string | null
  updated_at: string | null
}

export interface CommandOutputWire {
  tool_execution_id: string
  tool_name: string
  command_class: string | null
  cwd: string | null
  ordinal: number
  state: string
  disposition: string
  started_at: string
  ended_at: string | null
  duration_ms: number | null
  exit_code: number | null
  signal: number | null
  output_artifact_id: string | null
  output_availability: 'available' | 'missing' | 'corrupt' | 'staging' | 'not_persisted'
  output_excerpt: string
  output_truncated: boolean
  output_source: 'artifact' | 'activity' | 'none'
  message: string | null
}

export interface TaskArtifactsWire {
  schema_version: 1
  session_id: string
  task_run_id: string | null
  title: string
  status: string | null
  purpose: string | null
  workflow_run_id: string | null
  workflow_run_ids: string[]
  node_run_id: string | null
  task: {
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
  } | null
  outcomes: TaskOutcomeWire[]
  artifacts: TaskArtifactItemWire[]
  files: TaskFileChangeWire[]
  command_outputs: CommandOutputWire[]
  availability: 'available' | 'partial' | 'missing' | 'empty'
  message: string | null
}

export type TaskArtifactPreview = "text" | "image" | "pdf" | "html" | "binary"

export interface TaskArtifactContentWire {
  /** Null when the bytes are not decodable text; download serves them raw. */
  content: string | null
  truncated: boolean
  byte_size: number
  encoding: TaskArtifactEncoding
  content_kind: "text" | "binary"
  /** Real file name from the verified delivery marker, else the Artifact id. */
  name: string
  path: string | null
  mime: string
  preview: TaskArtifactPreview
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
  planner?: PlannerMetadataWire | null
}

export type PlanningTaskType = 'implementation' | 'refactor' | 'research' | 'explanation' | 'diagnosis' | 'general'
export interface TaskFeaturesWire {
  task_type: PlanningTaskType
  expected_scope: string[]
  number_of_areas: number
  requires_code_write: boolean
  requires_research: boolean
  review_value: 'low' | 'medium' | 'high'
  parallelizable_read_work: boolean
  ambiguity: 'low' | 'medium' | 'high'
  risk_level: 'low' | 'medium' | 'high'
  expected_duration_class: 'short' | 'medium' | 'long'
  user_requested_roles: string[]
  user_excluded_roles: string[]
  workspace_constraints: string[]
}
export interface PlannerExplanationWire {
  mode: 'direct' | 'multi' | 'needs_input'
  reasons: string[]
  starting_point: 'direct' | 'grammar' | 'explore_implement_verify'
  node_count: number
  writing_nodes: string[]
  models: ModelRefWire[]
  budget: WorkflowBudgetWire
  concurrency: 1
  auto_run_eligible: boolean
  auto_run_reason: 'approval_only' | 'user_policy'
}
export interface PlannerMetadataWire {
  request_digest: string
  source_hash: string
  features: TaskFeaturesWire
  brief: { project_markers: string[]; observed_entries: number; truncated: boolean } | null
  policy_id: string
  policy_scope: 'global' | 'workspace'
  policy_revision: number
  classification: 'local' | 'model' | 'unavailable' | 'invalid'
  explanation: PlannerExplanationWire
  diagnostics: string[]
}
export interface GraphPlanningRequestWire {
  draft_id: string
  workflow_definition_id: string
  name: string
  task: TaskContractWire
  requested_roles: string[]
  excluded_roles: string[]
  budget: WorkflowBudgetWire | null
  use_model: boolean
  scout: boolean
}
export interface TaskGraphDraftWire {
  workflow_draft: WorkflowDraftViewWire | null
  metadata: PlannerMetadataWire | null
  explanation: PlannerExplanationWire
  diagnostics: string[]
}
export interface OrchestrationPolicyWire {
  policy_id: string
  scope: 'global' | 'workspace'
  task_matcher: PlanningTaskType | '*'
  preferred_template: 'direct' | 'explore_implement_verify' | null
  excluded_templates: Array<'direct' | 'explore_implement_verify'>
  required_roles: string[]
  excluded_roles: string[]
  model_preferences_by_role: Record<string, ModelRefWire>
  budget_limits: WorkflowBudgetWire | null
  review_requirement: 'adaptive' | 'required' | 'skip'
  multi_agent: boolean
  parallelism_limit: 1
  auto_run_mode: 'approval_only' | 'auto'
  auto_replan_mode: 'approval_only' | 'allow_low_risk'
  source: 'user' | 'builtin'
  evidence: string[]
  status: 'active' | 'disabled'
  revision: number
}
export interface OrchestrationPoliciesWire {
  global: { revision: number; policies: OrchestrationPolicyWire[] }
  workspace: { revision: number; policies: OrchestrationPolicyWire[] }
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

/**
 * `ServerCommands.workflow_recovery_eligibility` result: whether one failed
 * run may take the restricted "从中断处继续" historical recovery, with the
 * concrete blocking reasons when it may not.
 */
export interface RecoveryEligibilityWire {
  workflow_run_id: string
  eligible: boolean
  reasons: string[]
  failed_node_run_id: string | null
  stop_code: string | null
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

export interface WorkspaceFileRevisionWire {
  sha256: string
  size: number
  mtime_ns: number
}

export type WorkspaceFileKind = 'file' | 'directory' | 'symlink' | 'special'

export interface WorkspaceFileEntryWire {
  path: string
  kind: WorkspaceFileKind
  size: number
  protected: boolean
}

export interface WorkspaceFileTreeWire {
  schema_version: 1
  workspace_id: string
  path: string
  entries: WorkspaceFileEntryWire[]
  next_cursor: string | null
  truncated: boolean
}

export interface WorkspaceFileContentWire {
  path: string
  text: string
  revision: WorkspaceFileRevisionWire
  bom: boolean
  newline: 'none' | 'lf' | 'crlf' | 'cr' | 'mixed'
}

export interface WorkspaceFileReadWire {
  schema_version: 1
  workspace_id: string
  file: WorkspaceFileContentWire
}

export type WorkspaceFilePreview = "text" | "image" | "pdf" | "binary"

/** Narrow, non-leaking identity of one workspace file (`GET .../files/info`). */
export interface WorkspaceFileInfo {
  path: string
  kind: "file"
  byte_size: number
  text: boolean
  editable: boolean
  preview: WorkspaceFilePreview
}

export interface WorkspaceFileInfoWire {
  schema_version: 1
  workspace_id: string
  file: WorkspaceFileInfo
}

/** One declared or recorded result file, as projected from the TaskOutcome. */
export type TaskResultFileRole = "delivery" | "output" | "generated" | "changed" | "related"

export interface TaskResultFileWire {
  label: string
  kind: string
  role: TaskResultFileRole
  source: TaskArtifactSource
  artifact_id: string | null
  path: string | null
  name?: string | null
  mime: string | null
  byte_size: number | null
  availability: TaskArtifactAvailability
  inherited: boolean
  node_id: string | null
  output_slot: string | null
  resource_count?: number
  note: string | null
}

export interface TaskResultSectionWire {
  label: string
  kind: "lines" | "text"
  items: string[]
}

export interface TaskResultBodyWire {
  kind: "text"
  text: string
  truncated: boolean
  content_complete: boolean
}

export interface TaskResultBodyRefWire {
  record_id: string
  sha256: string
  content_complete: boolean
}

/** Readable projection of one durable TaskOutcome (`TimelineItem.result`). */
export interface TaskResultWire {
  outcome_id: string
  task_run_id: string
  workflow_run_id: string | null
  task_status: TaskRunStatus
  result_status: string | null
  trigger: string
  version: number
  summary: string
  sections: TaskResultSectionWire[]
  files: TaskResultFileWire[]
  body: TaskResultBodyWire | null
  body_ref: TaskResultBodyRefWire | null
  notes: string[]
  truncated: boolean
}

/** One bounded local HTML preview collection served from its own loopback origin. */
export interface PreviewFileWire {
  path: string
  media_type: string
  byte_size: number
}

export interface PreviewWire {
  schema_version: 1
  workspace_id: string
  preview_id: string
  url: string
  entry_path: string
  revision: string
  missing: string[]
  total_bytes: number
  files: PreviewFileWire[]
}

export interface WorkspaceFileWriteWire {
  schema_version: 1
  workspace_id: string
  file: WorkspaceFileContentWire
  mutation: Record<string, unknown> | null
  /** reconciled：内容早已按本次提交写盘（回执丢失后的重试），没有新的变更集。 */
  disposition: 'accepted' | 'replay' | 'reconciled'
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


export interface ReplanViewWire {
  proposal: {
    proposal_id: string
    patch: FutureGraphPatchWire
    signal_ids: string[]
    status: 'pending' | 'applied' | 'rejected' | 'conflict' | 'invalid'
    risk_level: 'low' | 'elevated'
    risk_reasons: string[]
    disposition_reason: string
    auto_applied: boolean
    child_run_id: string | null
    decided_by: string | null
    created_at: string
    decided_at: string | null
    row_version: number
    policy_id: string
    policy_revision: number
  }
  before: WorkflowDefinitionSourceWire
  after: WorkflowDefinitionSourceWire
  diff: { added_node_ids: string[]; removed_node_ids: string[]; changed_node_ids: string[];
    added_edges: string[]; removed_edges: string[]; required_outputs_changed: boolean; budget_changed: boolean }
}

// Task-plan planning contracts (server/planning.py wire) ----------------------

export interface PlanningContextRefWire {
  conversation_position: number
  attachments: unknown[]
  constraints: string[]
  settings_revision: number
  model: ModelRefWire
  generation: Record<string, unknown>
  settings_digest: string
}

export interface PlanningBindingWire {
  planning_binding_id: string
  workspace_id: string
  session_id: string
  origin_interaction_id: string
  mode: 'initial' | 'change' | 'repair'
  parent_run_id: string | null
  parent_revision_id: string | null
  current_draft_id: string | null
  context_ref: PlanningContextRefWire
  status: 'active' | 'closed' | 'superseded'
  row_version: number
  created_at: string
  updated_at: string
}

export type SelectionChoiceWire =
  | { mode: 'inherit' }
  | { mode: 'model_default' }
  | { mode: 'explicit'; value: string }

export interface NodePlanningMetadataWire {
  title: string
  responsibility: 'implementation' | 'research' | 'review' | 'synthesis' | 'general'
  agent_selection: string
  model_choice: SelectionChoiceWire | null
  generation_choice: SelectionChoiceWire | null
  x: number | null
  y: number | null
}

export interface DraftVersionWire {
  draft_id: string
  version: number
  node_metadata: Record<string, NodePlanningMetadataWire>
  execution_selections: Record<
    string,
    {
      model: ModelRefWire
      generation: string | null
      model_source: string
      generation_source: string
      agent_ref: AgentDefinitionRefWire
    }
  >
  created_from: 'llm_generate' | 'llm_revise' | 'manual_edit' | 'system_repair'
  command_id: string
  summary: string
  validation_digest: string
  created_at: string
}

export type PlanningOperationStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'expired'

export interface PlanningUsageWire {
  attempts: number
  total_tokens: number | null
  cost_amount_minor: number | null
  cost_currency: string | null
  availability: 'available' | 'unavailable'
}

export interface PlanningOperationWire {
  planning_operation_id: string
  workspace_id: string
  session_id: string
  planning_binding_id: string
  operation: 'generate' | 'revise' | 'validate' | 'repair'
  command_id: string
  base_draft_id: string | null
  base_draft_version: number
  status: PlanningOperationStatus
  result_draft_id: string | null
  result_draft_version: number | null
  error_code: 'invalid' | 'stale' | 'unavailable' | 'needs_recovery' | null
  diagnostics: string[]
  cancelled_at: string | null
  cancel_reason: 'user' | 'shutdown' | null
  row_version: number
  created_at: string
  updated_at: string
  usage?: PlanningUsageWire
}

export interface FrozenNodeSelectionWire {
  node_id: string
  version_id: string
  content_hash: string
  resolved_model: ModelRefWire
  resolved_generation: string | null
  model_source: string
  generation_source: string
}

export interface TaskPlanExecutionWire {
  allowed: boolean
  digest: string | null
  draft_id: string | null
  draft_version: number | null
  blockers: string[]
  frozen_selections: FrozenNodeSelectionWire[]
}

export interface TaskPlanRunWire {
  workflow_run_id: string
  status: WorkflowStatus
  result_status: 'succeeded' | 'needs_revision' | null
  pause_requested?: boolean
  row_version?: number
  workflow_revision_id?: string
  lineage_root_run_id: string | null
  origin: 'initial' | 'repair' | null
  inherited_node_ids?: string[]
  active_node_ids: string[]
  nodes: {
    node_id: string
    status: WorkflowStatus
    inherited?: boolean
    /** Same per-node projection as `NodeViewWire.execution` (P02). */
    execution?: NodeExecutionWire | null
  }[]
  agent_generation_request_count?: number
  lineage_agent_generation_request_count?: number
  outcome?: { summary: string; task_status: string; completion_basis: string } | null
}

export interface TaskPlanCandidateWire {
  parent_run_id: string
  base_revision_id: string | null
  expected_parent_row_version: number
  past_node_ids: string[]
  added_node_ids?: string[]
  removed_node_ids?: string[]
  changed_node_ids?: string[]
  origin?: 'replan' | 'user'
  reason?: string | null
  review_blocking?: boolean
  source_hash: string | null
  settings_digest: string
  stable: boolean
  digest: string | null
}

/** `GET .../task-plan` — admission view; the single server-owned planning truth. */
export interface TaskPlanViewWire {
  binding: PlanningBindingWire | null
  draft: WorkflowDraftViewWire | null
  version: DraftVersionWire | null
  operations: PlanningOperationWire[]
  execution: TaskPlanExecutionWire
  run: TaskPlanRunWire | null
  candidate: TaskPlanCandidateWire | null
  allowed_actions: string[]
  /**
   * The single server-side control contract (D01): state, allowed control
   * intents and the exact target identity. Header, composer and panel read this
   * instead of re-deriving their own state machine.
   */
  control: TaskPlanControlWire
  /** Durable ownership chain of the viewed session. */
  ownership: SessionOwnershipWire | null
  /** Frozen plan identity for execution-node sessions; `mode: 'direct'` runs never had a draft. */
  plan: TaskPlanFrozenPlanWire | null
}

/** One durable control acceptance receipt. */
export interface ControlReceiptWire {
  command_id: string
  client_message_id: string | null
  session_id: string
  text: string
  state: string | null
  intent: string | null
  status: 'accepted' | 'executed' | 'acknowledged' | 'answered' | 'unresolved' | 'needs_choice' | 'steer' | 'failed'
  disposition: string | null
  message: string | null
  result_kind: string | null
  result_id: string | null
  error_code: string | null
  outcome: Record<string, unknown>
  revision: number
  created_at: string
  updated_at: string
}

export type TaskPlanControlState =
  | 'none'
  | 'draft'
  | 'generating'
  | 'generate_failed'
  | 'running'
  | 'draining'
  | 'paused'
  | 'repair_ready'
  | 'repair_draft'
  | 'terminal'

export interface TaskPlanControlWire {
  state: TaskPlanControlState
  allowed_intents: string[]
  hint: string
  resume_available: boolean
  cancel_available: boolean
  start_available: boolean
  repair_available: boolean
  target: {
    workflow_run_id: string | null
    run_status: WorkflowStatus | null
    run_row_version: number | null
    pause_requested: boolean
    planning_operation_id: string | null
    binding_id: string | null
    binding_mode: string | null
    draft_id: string | null
    draft_version: number | null
    execution_digest: string | null
  }
  allowed_actions: string[]
}

/** `POST .../task-plan/control` — raw text control; the optional expected
 * run id lets the server refuse a command composed against a stale target. */
export interface TaskPlanControlRequestWire {
  command_id: string
  session_id: string
  text: string
  client_message_id?: string | null
  expected_workflow_run_id?: string
}

/**
 * Server-resolved ownership of the viewed session: an isolated workflow-node
 * conversation (`node`), the conversation that roots runs (`root`), or an
 * unrelated chat (`plain`). Missing relations are reported in `issues`; the
 * server never guesses a nearest workflow.
 */
export interface SessionOwnershipWire {
  role: 'root' | 'node' | 'plain'
  issues: string[]
  node_run_id: string | null
  node_id: string | null
  node_status: WorkflowStatus | null
  node_attempt: number | null
  workflow_run_id: string | null
  workflow_status: WorkflowStatus | null
  root_task_run_id: string | null
  root_session_id: string | null
  plan: {
    mode: 'frozen' | 'direct'
    origin: string | null
    planning_binding_id: string | null
    draft_id: string | null
    draft_version: number | null
  } | null
}

/** The frozen plan version a run was admitted with, or the direct-run marker. */
export interface TaskPlanFrozenPlanWire {
  mode: 'frozen' | 'direct'
  origin: string | null
  planning_binding_id: string | null
  draft_id: string | null
  draft_version: number | null
  source: WorkflowDefinitionSourceWire | null
  node_metadata: Record<string, unknown>
}

export interface PlanWorkflowRequestWire {
  command_id: string
  session_id: string
  origin_interaction_id: string
  task: TaskContractWire
  attachments?: unknown[]
  planning_binding_id?: string | null
  base_draft_version?: number
  operation?: 'generate' | 'revise' | 'repair'
}

export interface PlanNodeWire {
  node_id: string
  title: string
  task: string
  /** `preset:general|preset:explore|preset:review` or `custom:<definition_id>`. */
  agent: string
  responsibility: 'implementation' | 'research' | 'review' | 'synthesis' | 'general'
  depends_on: string[]
  completion: string[]
}

export interface PlanNodeEditWire {
  command_id: string
  binding_id: string
  expected_version: number
  action: 'add' | 'replace' | 'remove' | 'restore' | 'layout' | 'dependencies'
  node_id?: string | null
  node?: PlanNodeWire | null
  /** `dependencies` action only: the full parent set of `node_id`. */
  depends_on?: string[]
  restore_version?: number | null
  confirm_impact?: boolean
}

export interface StartWorkflowPlanRequestWire {
  command_id: string
  session_id: string
  draft_id: string
  draft_version: number
  execution_digest: string
  action_source: 'button' | 'chat_command'
  interaction_id: string
  root_task_run_id?: string | null
  expected_root_row_version?: number | null
}

export interface EditPlanRequestWire {
  command_id: string
  binding_id: string
  expected_version: number
  source: WorkflowDefinitionSourceWire
  metadata?: Record<string, NodePlanningMetadataWire>
  confirm_impact?: boolean
}

export interface AgentPresetRowWire {
  definition_id: string
  role: 'general' | 'explore' | 'review'
  name: string
  description: string
  access: 'read' | 'write'
  available: boolean
  preference: { model?: ModelRefWire | null; generation?: SelectionChoiceWire | null } | null
  preference_source: 'preset_preference' | 'inherit_session'
}

export interface AgentPresetCatalogWire {
  presets: AgentPresetRowWire[]
  revision: number
}
