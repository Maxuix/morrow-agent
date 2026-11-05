import type { TaskArtifactSelectors } from './client'

export interface FileManifest {
  exists: boolean
  sha256: string | null
  byte_size: number
  mode: number | null
  snapshot_id: string | null
}
export interface WorkspaceChange {
  change_id: string
  task_run_id: string
  tool_execution_id: string | null
  workspace_sequence: number
  path: string
  added_lines?: number
  removed_lines?: number
  operation: string
  origin: 'tool' | 'restore'
  related_change_ids: string[]
  before: FileManifest
  after: FileManifest
  effect_state: string
  coverage: string
  omission_reason: string | null
  review_state: 'unreviewed' | 'reviewed'
  row_version: number
  restore_support: string
  restore_reason: string | null
}
export interface ChangePage {
  items: WorkspaceChange[]
  next_cursor: string | null
  view_digest: string
  uncovered_commands: number
  coverage_message: string
  legacy_message: string | null
}
export interface ChangeDiff {
  before?: FileManifest
  after?: FileManifest
  chain_ids?: string[]
  change: WorkspaceChange
  diff: string
  complete: boolean
  next_offset: number | null
  restore_support: string
  restore_reason: string | null
  version_digest: string
}
export interface RestoreItem {
  change_ids: string[]
  change_id: string | null
  path: string
  expected: FileManifest
  desired: FileManifest
  capture_name: string
  temporary_name: string
  state: string
  reason: string | null
}
export interface RestoreOperation {
  operation_id: string
  session_id: string
  preview_digest: string
  state: string
  items: RestoreItem[]
  expires_at: string
  apply_command_id: string | null
}
export interface ChangeSelection extends TaskArtifactSelectors {
  command_id: string
  change_ids: string[]
}
