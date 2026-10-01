import type { ModelRefWire } from './types'
export interface ProviderSettingsView {
  revision: number
  active_model: ModelRefWire | null
  adapters: {adapter_id: string; discovery: boolean; reasoning_efforts?: string[]}[]
  presets: {preset_id: string; provider_id: string; adapter: string; base_url: string; model_id: string; api_model_id: string}[]
  providers: {provider_id: string; adapter: string; base_url: string; credential_configured: boolean
    last_test: {ok: boolean; message: string | null} | null
    models: {model_id: string; api_model_id: string; capabilities: Record<string, unknown> | null; effective_capabilities?: {reasoning_efforts: string[]; input_types: string[]; tool_protocol: string}}[]}[]
}
export interface ChatSettings {
  model: ModelRefWire | null
  generation: {reasoning_effort?: string | null} | null
  permission: 'manual' | 'auto-safe' | 'auto-sandboxed' | 'full-access-manual' | null
}
export interface ChatSettingsView {
  permission_presets: {preset: NonNullable<ChatSettings['permission']>; label: string; available: boolean; reason: string | null}[]
  documents: Record<'session' | 'workspace' | 'global', {revision: number; settings: ChatSettings}>
  effective: ChatSettings
  sources: Record<'model' | 'generation' | 'permission', {scope: string; revision: number}>
}
export interface DesktopRuntimeStatus {
  scope: 'local_host'
  state: 'not_activated' | 'idle' | 'active' | 'quarantined' | 'stopping' | 'closed' | 'unknown'
  native_pending: boolean | null
  unknown_actions: number
}
export interface ChatPermissionsView {
  desktop_runtime?: DesktopRuntimeStatus
  run_ids: string[]; next_run_cursor: string | null
  snapshot: {agent_run_id: string; model: ModelRefWire; generation: {reasoning_effort?: string | null}; sources: Record<string, {scope: string; revision: number}>; permission_preset: string | null
    permission: {access_scope: string; approval_mode: string; process_isolation: string; workspace_read_only: boolean; grant_id: string | null} | null} | null
  grants: {computer_use?: ComputerPermissionSummary; grant_id: string; row_version: number; capabilities: string[]; expires_at: string; status: 'active' | 'revoked' | 'expired'}[]
  next_grant_cursor: string | null
  session_scopes: {items: {scope: string; revision: number; approval_id: string}[]; next_cursor: string | null}
}

export interface ComputerUseSettings {
  enabled: boolean
  mode: 'semantic' | 'hybrid'
  max_operations: number
  max_run_seconds: number
  max_call_seconds: number
  max_observation_bytes: number
  image_long_edge_px: number
}
export interface ComputerUseSettingsView {
  revision: number
  settings: ComputerUseSettings
  host: {status: 'unavailable'; reason: string}
  model: ModelRefWire | null
  model_capabilities: {function_tools: boolean; images: boolean}
  model_error: 'model_unavailable' | 'function_tools_required' | 'images_not_supported' | null
  required_permission: 'full-access-manual'
  configuration_scope: 'global'
  applies_to: 'future_runs'
}

export interface ComputerWindowCandidate {
  candidate_id: string
  app: {bundle_id: string}
  display_label: string | null
}
export interface ComputerWindowCandidates {
  candidates: ComputerWindowCandidate[]
  expires_at: string
}
export interface ComputerWindowSelectionRequest {
  candidate_ids: string[]
  allow_action: boolean
  share_images: boolean
  delivery: 'foreground' | 'background'
}
export interface ComputerWindowSelection {
  selection_id: string
  expires_at: string
  windows: ComputerWindowCandidate[]
  operations: ('observe' | 'action')[]
  delivery: 'foreground' | 'background'
  image_share: 'none' | 'controlled_window'
  applies_to: 'one_future_run'
}

export interface ComputerPermissionSummary {
  apps: string[]
  window_scope: 'selected_windows' | 'legacy_app_windows'
  window_count: number | null
  operations: ('observe' | 'action')[]
  delivery: 'foreground' | 'background'
  image_share: 'none' | 'controlled_window'
}
