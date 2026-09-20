/**
 * Typed activity-stream wire contract (activity_schema=1).
 *
 * Mirrors `src/morrow/core/activity.py`: closed discriminated payload unions,
 * conditional identity and bounded text. Transport fields live on the frame
 * envelope, not on the item. Unknown frame types must be dropped whole by the
 * activity-mode consumer — never partially filtered across a sequence cursor.
 */

export const ACTIVITY_SCHEMA_VERSION = 1
/** Proposal 4.6 first-version display budgets; server mirrors enforce the same. */
export const ACTIVITY_METADATA_LIMIT = 256
export const ACTIVITY_PREVIEW_TOTAL_BYTES = 512 * 1024
export const ACTIVITY_PREVIEW_ITEM_BYTES = 32 * 1024
export const ACTIVITY_FRAME_TEXT_BYTES = 4 * 1024

export type ActivityKind =
  | 'model'
  | 'tool'
  | 'approval'
  | 'retry'
  | 'compaction'
  | 'node_output'
  | 'control'
export type ActivityState =
  | 'preparing'
  | 'waiting'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'skipped'
  | 'unknown'
export type ActivityOrigin =
  | 'agent_loop'
  | 'tool_executor'
  | 'scheduler'
  | 'workflow_bridge'
  | 'planning_service'
  | 'control_service'
export type ThinkingCapability = 'none' | 'activity_only' | 'visible_text' | 'summary'
export type ContentAvailability = 'live' | 'evicted' | 'unsaved' | 'none' | 'committed'

export interface ActivityIdentity {
  workspace_id: string
  root_session_id: string
  source_session_id: string
  interaction_id?: string | null
  planning_operation_id?: string | null
  workflow_run_id?: string | null
  node_run_id?: string | null
  node_id?: string | null
  agent_run_id?: string | null
  turn_id?: string | null
  model_request_id?: string | null
  attempt_id?: string | null
  tool_execution_id?: string | null
  call_id?: string | null
}

export interface ModelActivityPayload {
  kind: 'model'
  stage: 'awaiting_model' | 'thinking' | 'responding' | 'tool_preparing'
  attempt_ordinal?: number | null
  reasoning_capability?: ThinkingCapability | null
}
export interface ToolActivityPayload {
  kind: 'tool'
  tool_name: string
  call_id?: string | null
  tool_execution_id?: string | null
  ordinal?: number | null
  total?: number | null
  exit_code?: number | null
  /** Value-free failure diagnostics; absent on success or records without one. */
  error_code?: string | null
  validation_reason?: string | null
  validation_path?: string | null
}
export interface ApprovalActivityPayload {
  kind: 'approval'
  approval_id: string
  decision?: 'approved' | 'denied' | null
}
export interface RetryActivityPayload {
  kind: 'retry'
  attempt_ordinal: number
  retry_delay_seconds?: number | null
  reason_code?: string | null
}
export interface CompactionActivityPayload {
  kind: 'compaction'
  direction: 'compacting' | 'compacted'
}
export interface NodeOutputActivityPayload {
  kind: 'node_output'
  output_ref?: string | null
}
export interface ControlActivityPayload {
  kind: 'control'
  command: 'steer' | 'cancel' | 'pause' | 'resume'
  receipt?: 'accepted' | 'pending' | 'applied' | 'expired' | 'rejected' | null
}
export type ActivityPayload =
  | ModelActivityPayload
  | ToolActivityPayload
  | ApprovalActivityPayload
  | RetryActivityPayload
  | CompactionActivityPayload
  | NodeOutputActivityPayload
  | ControlActivityPayload

export interface ActivityItem {
  schema_version: 1
  activity_id: string
  revision: number
  kind: ActivityKind
  state: ActivityState
  origin: ActivityOrigin
  identity: ActivityIdentity
  payload: ActivityPayload
  started_at: string
  updated_at: string
  ended_at?: string | null
  last_activity_at?: string | null
  safe_title: string
  safe_summary?: string | null
  /** Binary artifact read path (fetchBlob + ?raw=1); asset cards only. */
  preview_ref?: string | null
  /** Durable JSON content read path (activity-content endpoint); text only. */
  content_ref?: string | null
  truncated: boolean
  availability: ContentAvailability
}

/** Shared envelope fields of activity-mode stream frames (proposal 4.6). */
export interface ActivityFrameBase {
  workspace_id: string
  session_id: string
  stream_epoch: string
  sequence: number
}
export interface ActivityUpsertFrame extends ActivityFrameBase {
  type: 'activity_upsert'
  payload: { item: ActivityItem }
}
export interface ActivityDeltaFrame extends ActivityFrameBase {
  type: 'activity_delta'
  payload: { activity_id: string; revision: number; delta: string; availability?: ContentAvailability }
}
export interface ActivityContentResetFrame extends ActivityFrameBase {
  type: 'activity_content_reset'
  payload: { activity_id: string; reason: string }
}
/** Admission rekey: the prepared act_call_* item becomes act_tool_* (stable). */
export interface ActivityRekeyFrame extends ActivityFrameBase {
  type: 'activity_rekey'
  payload: { from_activity_id: string; to_activity_id: string }
}
export type ActivityFrame =
  | ActivityUpsertFrame
  | ActivityDeltaFrame
  | ActivityContentResetFrame
  | ActivityRekeyFrame

/** Advertised under ChatCapabilities.features-independent `activity_stream` key. */
export interface ActivityStreamCapability {
  schema: 1
}
/** Durable tool-activity recovery (`GET .../activities`): skeletons only. */
export interface ActivityRecovery {
  items: ActivityItem[]
  truncated: boolean
}
