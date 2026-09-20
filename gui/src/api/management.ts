/** User-facing projections; no backing store or package paths. */
export type Scope = 'global' | 'workspace'
export type ManagedStatus = 'active' | 'disabled' | 'deleted' | 'disputed'
export interface Preference {
  preference_id: string; statement: string; scope: Scope | 'session'; status?: ManagedStatus
  revision: number; evidence_ids?: string[]; updated_at: string
}
export interface Profile {
  name: string; summary: string | null; goals: string[]; tech_stack: string[]
  constraints: string[]; conventions: string[]
}
export interface ContextRunLabel {
  agent_run_id: string
  label?: string
  task_title?: string
  node_id?: string | null
  node_title?: string | null
  created_at?: string
}
export interface PromptConstraintSource {
  path: string
  scope: string
  byte_count: number
}
export interface PromptConstraintSection {
  kind: 'profile' | 'role' | 'project_instructions' | string
  label: string
  available: boolean
  summary: string
  sources?: PromptConstraintSource[]
}
export interface PromptConstraints {
  availability: 'available' | 'missing' | string
  message: string | null
  sections: PromptConstraintSection[]
}
export interface KnowledgeRevision {
  knowledge_revision_id: string; statement: string; revision: number
  supersedes_revision_id: string | null; source_candidate_id: string | null
}
export interface ResolvedContext {
  workspace_id: string; session_id?: string | null; task_run_id: string | null; agent_run_id: string | null
  available_runs: ContextRunLabel[]; status: 'resolved' | 'not_started'
  preferences: Preference[]; profile: Profile | null; source_revisions: { kind: string; revision: number }[]
  preference_digest: string | null; omitted_count: number; refresh_status: string
  prompt_constraints?: PromptConstraints
  knowledge: { selection: { record_id: string; revision: number }; revision: KnowledgeRevision | null }[]
  memory_selection_id: string | null; pending_learning_count: number; convention_count: number
  language: string | null; verbosity: string | null
}
export interface PreferencePage {
  document: { scope: Scope; revision: number; entries: Preference[] }
  history: { batch_id: string; status: string; expected_document_revision: number; created_at: string
    before: Preference[]; after: Preference[]
    operations: { operation: string; statement: string | null; preference_id: string | null }[] }[]
}
export interface Knowledge {
  head: { knowledge_id: string; semantic_key: string; category: string; status: ManagedStatus; row_version: number }
  revision: KnowledgeRevision | null; timeline: KnowledgeRevision[]
  evidence: { evidence_id: string; source_kind: string; excerpt_redacted: string | null }[]
}
export interface LearningPage {
  candidates: { expired?: boolean; candidate: { candidate_id: string; candidate_type: string; status: string
    row_version: number; semantic_key: string; proposed_scope: string; proposed_payload: Record<string, unknown> }
    evidence: { evidence_id: string; source_kind: string; excerpt_redacted: string | null }[]
    target: { statement: string | null; reason: string | null }; conflicts: { candidate_id: string }[]
    source?: { kind: 'task' | 'unknown' | string; known: boolean; session_id?: string; task_run_id?: string; review_id?: string } }[]
  proposals: { proposal_id: string; status: string; row_version: number; stale: boolean; stale_reason: string | null
    operation: { operation: string; scope: Scope; statement: string | null; preference_id: string | null }
    expected_document_revision: number; expected_target_revision: number | null
    evidence_excerpt: string | null; evidence_source_kind: string | null; evidence_id: string
    source?: { kind: 'task' | 'unknown' | string; known: boolean; session_id?: string; task_run_id?: string; review_id?: string } }[]
  next_cursor: string | null; limit: number
  candidate_count?: number; proposal_count?: number; total_count?: number; unknown_source_count?: number
  source_scope?: 'workspace' | 'session' | 'task' | string
}
export interface ManagedSkill {
  status: { skill_id: string; name: string; source_kind: string; scope_id: string | null
    availability: string; effective_trust: string; requested_trust: string | null
    binding: { pinned_version_id: string | null } | null }
  enabled: boolean
  versions: { version: { version_id: string; display_version: string | null; tree_digest: string; source_kind: string }
    summary: string | null; manifest: { required_tools: string[]; required_mcp_servers: string[]; requested_permissions: string[] } | null
    scripts: string[]; inspection_error: string | null }[]
  usage: { usage_id: string; version_id: string; terminal_status: string }[]
}
export interface SkillDraft {
  draft: { draft_id: string; name: string; status: string; row_version: number; revision: number
    tree_digest: string; evidence_refs: string[]; accepted_version_id: string | null; parent_draft_id: string | null }
  validation: { valid: boolean; findings: { code: string; message: string }[] } | null
  skill_md?: string | null; text_diff?: string | null; editable?: boolean; inspection_error?: string | null
  diff: { added: string[]; removed: string[]; changed: string[] } | null
}
export interface ManagementQueries {
  context: ResolvedContext
  preferences: PreferencePage
  profile: { profile: Profile | null; revision: number; scope: 'workspace' }
  learning: LearningPage
  knowledge: { items: Knowledge[]; next_cursor: string | null; limit: number }
  skills: { skills: ManagedSkill[]; scope: Scope; binding_digest: string; next_cursor?:string|null }
  'skill-drafts': { drafts: SkillDraft[]; limit: number; next_cursor: string | null }
}
/** One complete Profile snapshot; the server owns scope and validation. */
export interface ProfileSaveResult {
  status: 'applied' | 'unchanged'; scope: 'workspace'; target: 'profile'; revision: number
}
export interface ProfileSaveBody {
  expected_revision: number; profile: Profile
}
export type ManagementCommand = 'preferences' | 'profile' | 'profile-save' | 'preference-decision'
  | 'learning-decision' | 'knowledge' | 'skill-binding' | 'skill-draft' | 'skill-draft-create'
