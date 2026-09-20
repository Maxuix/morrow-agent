/**
 * Frozen cross-lane protocol contracts (P01 freeze, contract version 1.0.0).
 *
 * Mirror of src/morrow/core/contracts.py; both sides are validated against
 * tests/fixtures/parallel_contracts/wire-fixtures.json. This module is coordinator
 * owned: adding or changing a field requires a contract-version bump by the
 * coordinator, never a lane-local edit.
 */

export const CONTRACT_VERSION = '1.1.0'

export type SegmentStatus = 'active' | 'interrupted' | 'completed'
export type PauseReason = 'user_interrupt' | 'node_boundary' | 'provider_failure' | 'process_interrupt'
export type PauseLifecycle = 'requested' | 'quiescing' | 'suspended' | 'resumed'
export type ContentAvailability = 'committed' | 'pending' | 'unsaved'
export type PlanningOutcomeLayer =
  | 'model_request'
  | 'candidate_validation'
  | 'planning_operation'

export const TIMELINE_ENTRY_KINDS = [
  'user_message',
  'assistant_message',
  'turn_status',
  'interruption',
  'tool_activity',
  'planning_input',
  'plan_version',
  'control_input',
  'node_progress',
  'result',
  'artifact_ref',
] as const
export type TimelineEntryKind = (typeof TIMELINE_ENTRY_KINDS)[number]

export const MODEL_REQUEST_OUTCOMES = ['succeeded', 'failed', 'interrupted', 'cancelled'] as const
export const CANDIDATE_VALIDATION_OUTCOMES = ['valid', 'invalid', 'not_produced'] as const
export const PLANNING_OPERATION_OUTCOMES = [
  'running',
  'paused',
  'succeeded',
  'failed',
  'cancelled',
  'expired',
] as const

/** Typed terminal result of interrupting the current turn; never "cancelled". */
export interface TurnInterruptOutcome {
  turn_id: string
  agent_run_id: string | null
  finish_reason: 'interrupted'
  stop_code: 'user_pause' | 'process_interrupted' | 'provider_auth' | 'provider_network'
    | 'provider_rate_limit' | 'provider_timeout' | 'invalid_response' | 'context_budget' | 'internal'
  committed_position: number | null
  partial_text: string | null
  workflow_run_id: string | null
  node_run_id: string | null
  segment_id: string | null
}

/** Durably accepted pause intent; cancel is a separate fact, never this. */
export interface PauseIntentFact {
  control_generation: number
  command_id: string
  owner: 'workflow_run' | 'planning_operation' | 'chat_turn'
  owner_id: string
  reason: PauseReason
  lifecycle: PauseLifecycle
  requested_at: string
  suspended_at: string | null
  resumed_at: string | null
}

/** One append-only node execution segment; at most one active per node. */
export interface ExecutionSegmentIdentity {
  segment_id: string
  workflow_run_id: string
  node_run_id: string
  ordinal: number
  leaf_session_id: string | null
  leaf_task_run_id: string | null
  agent_run_id: string | null
  turn_id: string | null
  status: SegmentStatus
  pause_reason: PauseReason | null
  previous_segment_id: string | null
  accepted_command_id: string | null
}

/** Layered planning result so "model returned" is never "plan valid". */
export interface PlanningRequestOutcome {
  operation_id: string
  attempt: number
  request_sequence: number
  layer: PlanningOutcomeLayer
  outcome: string
  started_at: string
  ended_at: string | null
  reason: string | null
  revision: number
}

/** Cross-lane identity of one unified display entry (references the source). */
export interface TimelineEntryIdentity {
  workspace_id: string
  root_session_id: string
  item_id: string
  kind: TimelineEntryKind
  source_kind: string
  source_id: string
  source_session_id: string
  source_position: number | null
  planning_binding_id: string | null
  planning_operation_id: string | null
  workflow_run_id: string | null
  node_run_id: string | null
  segment_id: string | null
  parent_item_id: string | null
  revision: number
  occurred_at: string
  content_ref: string | null
  availability: ContentAvailability
}

/** A stored entry: `timeline_position` is assigned by the index owner (C). */
export interface TimelineSinkEntry extends TimelineEntryIdentity {
  timeline_position: number
}

/** Bounded safe-content reference; clients only ever see committed offsets. */
export interface SafeContentRef {
  content_id: string
  kind: 'inline' | 'blob'
  committed_offset: number
  total_length: number | null
  availability: ContentAvailability
}

/** Versioned cursor; snapshot and subscribe share one high-water contract. */
export interface TimelineCursor {
  schema_version: number
  workspace_id: string
  root_session_id: string
  high_water: number
  before_position: number | null
}

/** Self-consistent snapshot page; `reset` demands a full re-fetch. */
export interface TimelineSnapshotPage {
  entries: TimelineSinkEntry[]
  cursor: TimelineCursor
  revision: number
  reset: boolean
}

/** Body of the frozen chat control endpoint. */
export interface GuiControlRequest {
  command_id: string
  session_id: string
  text: string
  client_message_id: string | null
}

/** Body of the frozen pause entry; cancel stays a separate action. */
export interface GuiPauseRequest {
  command_id: string
  session_id: string
  expected_run_row_version: number | null
}
