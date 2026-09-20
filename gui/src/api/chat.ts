import type { AttachmentRef, AttachmentLimits } from './attachments'
import type { ActivityItem, ActivityStreamCapability } from './activity'
import type { ChatSettingsView } from './settings'
import type { SessionWire, TaskResultWire } from './types'
export interface TaskWorkflowCapability {
  schema: 1
  planning: boolean
  start: boolean
  control: boolean
  pause: boolean
  change: boolean
  apply_change: boolean
  resume: boolean
  repair: boolean
}
export interface ChatCapabilities {
  attachment_limits?: AttachmentLimits
  interaction_protocol_version: number
  protocol_version?: number
  workspace_id: string
  features: Record<string, { available: boolean; reason?: string }>
  execution_ready: boolean
  limits: Record<string, number>
  effective_settings?: { model: string | null; permission: string }
  /** Advertised by `/v1/capabilities` when task-plan controls are available. */
  task_workflow?: TaskWorkflowCapability
  /** Activity-mode negotiation (activity_schema=1); missing key keeps v1 stage/reply frames. */
  activity_stream?: ActivityStreamCapability
}

/** New Chat `/workflow` entry is on only when Core advertises planning. Rollback sets this false. */
export function taskWorkflowAvailable(caps: ChatCapabilities | null | undefined): boolean {
  return caps?.task_workflow?.planning === true
}
export interface TimelineItemActions {
  /** Server-projected edit-to-new-chat permission for this item's record. */
  edit_fork?: { available: boolean; reason_code: string | null }
}
export interface TimelineItem {
  item_id: string; kind: string; workspace_id: string; session_id: string
  order_key: [number, number, number, string]; revision: number
  source: Record<string, string | null>
  content: string | { tool_name?: string; status?: string; disposition?: string; approval_id?: string
    finish_reason?: string; stop_code?: string; task_outcome?: { task_status: string } } | null
  attachments?: AttachmentRef[]
  content_ref: string | null
  actions?: TimelineItemActions
  /**
   * Readable projection of the durable TaskOutcome behind a `result` item.
   * Absent when Core predates the projection; the renderer then keeps the
   * existing task/artifact entry point.
   */
  result?: TaskResultWire | null
}
export interface TimelinePage { items: TimelineItem[]; next_cursor: string | null; has_more: boolean; bytes: number }
export interface InteractionInput {
  client_message_id: string; text: string; intent: 'send' | 'steer' | 'follow_up' | 'explicit_workflow'
  workflow?: {workflow_definition_id:string;workflow_revision_id:string}
  attachments?: AttachmentRef[]
  target_agent_run_id?: string
  allow_unconfined_host?: boolean
}
export interface Receipt {
  client_message_id: string; interaction_id: string; status: string; revision: number
  queue_position: number | null; agent_run_id: string | null; turn_id: string | null
  reason?: string; run_status?: string
}
export interface QueueItem extends Receipt { text: string; intent: string }
export interface ChatQueue { revision: number; paused: boolean; active_agent_run_id: string | null; items: QueueItem[] }
export type ExecutionOwner = 'chat' | 'planning' | 'workflow'
export type ExecutionState =
  | 'idle' | 'queued' | 'running' | 'stopping' | 'pausing' | 'paused'
  | 'waiting_approval' | 'needs_recovery'
/**
 * One execution owning the Session right now. Durable facts own the state;
 * `needs_recovery` marks durable running evidence without a live driver.
 */
export interface ExecutionItem {
  owner: ExecutionOwner | null
  state: ExecutionState
  phase: string | null
  agent_run_id: string | null
  workflow_run_id: string | null
  root_session_id: string | null
  leaf: boolean
  node_run_ids: string[]
  planning_operation_id: string | null
  allowed_actions: string[]
  accepts_chat_input: boolean
}
export interface ExecutionView extends ExecutionItem {
  protocol_version: number
  executions: ExecutionItem[]
  revision: number
  updated_at: string
}
export interface ReplyDraft { message_id: string; text: string; revision: number }
/** One content-free execution stage of a workflow node, keyed by node_run_id. */
export interface StageFrame {
  workflow_run_id: string
  node_run_id: string
  node_id?: string
  agent_run_id?: string | null
  session_id?: string
  stage: string
  tool?: string | null
  ordinal?: number | null
  total?: number | null
  ok?: boolean | null
  retry_delay_seconds?: number | null
  attempt_ordinal?: number | null
  ts?: string | null
}
export interface ChatSnapshot {
  stream_epoch: string; sequence: number; timeline: TimelinePage; queue: ChatQueue
  draft: ReplyDraft | null; event_cursor: number
  settings?: ChatSettingsView
  session?: SessionWire
  /** Unified execution projection for the current chat turn. */
  execution?: ExecutionView | null
  /** Latest per-node stage frames for the current chat turn. */
  stages?: StageFrame[]
}
/** Targeted node-steer command receipt (P6.5). */
export interface NodeSteerReceipt {
  disposition: 'accepted' | 'replay'
  leaf_session_id: string
  node_run_id: string
  workflow_run_id: string
  client_message_id: string
  entry_status: string
}
/** Activity-mode snapshot (`?activity_schema=1`): self-consistent activity cursor. */
export interface ActivitySnapshot extends ChatSnapshot {
  activities: ActivityItem[]
  activity_epoch: string
  activity_sequence: number
}
export interface ChatFrame {
  type: string; workspace_id: string; session_id: string; stream_epoch: string; sequence: number
  message_id: string | null; payload: { text?: string; status?: string; discard_message_id?: string } & Partial<StageFrame>
}
export interface RecoveryView {
  pending_resume?: boolean; target_agent_run_id?: string
  reports: { report_id: string; status: string; items: { item_id: string; classification: string; allowed_resolutions: string[] }[] }[]
}
export type RecoveryDisplayState =
  | 'idle' | 'checking' | 'reconnecting' | 'paused' | 'resumable'
  | 'needs_review' | 'unknown_side_effect' | 'quarantined' | 'unsupported'
export type RecoveryOwner = 'chat' | 'planning' | 'workflow'
export interface RecoveryCheck {
  summary: string
  classification: string
  allowed_actions: string[]
  /** Action handle only; never render this value in the UI. */
  opaque_target?: string | null
  /** Action handle only; never render this value in the UI. */
  opaque_item?: string | null
}
export interface RecoveryStatus {
  protocol_version: number
  display_state: RecoveryDisplayState
  owner: RecoveryOwner | null
  safe_summary: string
  allowed_actions: string[]
  /** Action handle only; never render this value in the UI. */
  opaque_target: string | null
  opaque_target_kind: 'agent_run' | 'report' | 'workflow_run' | 'planning_operation' | null
  decision_required: boolean
  revision: number
  checks: RecoveryCheck[]
}
export interface ChatSessionEnvelope { session: SessionWire; driving: boolean }
export const chatPath = (workspace: string, session?: string) =>
  `/v1/workspaces/${encodeURIComponent(workspace)}/sessions${session ? `/${encodeURIComponent(session)}` : ''}`
