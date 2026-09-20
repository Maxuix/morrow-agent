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
export interface ChatPermissionsView {
  run_ids: string[]; next_run_cursor: string | null
  snapshot: {agent_run_id: string; model: ModelRefWire; generation: {reasoning_effort?: string | null}; sources: Record<string, {scope: string; revision: number}>; permission_preset: string | null
    permission: {access_scope: string; approval_mode: string; process_isolation: string; workspace_read_only: boolean; grant_id: string | null} | null} | null
  grants: {grant_id: string; row_version: number; capabilities: string[]; expires_at: string; status: 'active' | 'revoked' | 'expired'}[]
  next_grant_cursor: string | null
  session_scopes: {items: {scope: string; revision: number; approval_id: string}[]; next_cursor: string | null}
}
