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
  max_agent_generation_requests: number
  default_node_max_agent_generation_requests: number
  admission_timeout_seconds: number
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
  admission_deadline_at: string
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
}

/** `approval_wire` — bounded previews only, never full tool arguments. */
export interface ApprovalWire {
  approval_id: string
  tool_execution_id: string
  tool_name: string
  session_id: string
  task_run_id: string
  agent_run_id: string
  requested_scope: string
  preview: string[]
  resolution: ApprovalResolution
  created_at: string
  expires_at: string
  resolved_at: string | null
  row_version: number
}

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
