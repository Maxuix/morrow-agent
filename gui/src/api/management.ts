import type { EvaluationPage, WorkflowPolicyCandidate } from "./evaluation"
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
export interface KnowledgeRevision {
  knowledge_revision_id: string; statement: string; revision: number
  supersedes_revision_id: string | null; source_candidate_id: string | null
}
export interface ResolvedContext {
  workspace_id: string; task_run_id: string | null; agent_run_id: string | null
  available_runs: { agent_run_id: string }[]; status: 'resolved' | 'not_started'
  preferences: Preference[]; profile: Profile | null; source_revisions: { kind: string; revision: number }[]
  preference_digest: string | null; omitted_count: number; refresh_status: string
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
  orchestration?: { items: WorkflowPolicyCandidate[]; next_cursor: string | null }
  candidates: { expired?: boolean; candidate: { candidate_id: string; candidate_type: string; status: string
    row_version: number; semantic_key: string; proposed_scope: string; proposed_payload: Record<string, unknown> }
    evidence: { evidence_id: string; source_kind: string; excerpt_redacted: string | null }[]
    target: { statement: string | null; reason: string | null }; conflicts: { candidate_id: string }[] }[]
  proposals: { proposal_id: string; status: string; row_version: number; stale: boolean; stale_reason: string | null
    operation: { operation: string; scope: Scope; statement: string | null; preference_id: string | null }
    expected_document_revision: number; expected_target_revision: number | null
    evidence_excerpt: string | null; evidence_source_kind: string | null; evidence_id: string }[]
  next_cursor: string | null; limit: number
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
  "workflow-evaluation": EvaluationPage
  "workflow-policy-candidates": { items: WorkflowPolicyCandidate[]; next_cursor: string | null }
  context: ResolvedContext
  preferences: PreferencePage
  profile: { profile: Profile | null; revision: number; scope: 'workspace' }
  learning: LearningPage
  knowledge: { items: Knowledge[]; next_cursor: string | null; limit: number }
  skills: { skills: ManagedSkill[]; scope: Scope; binding_digest: string }
  'skill-drafts': { drafts: SkillDraft[]; limit: number; next_cursor: string | null }
}
export type ManagementCommand = 'preferences' | 'profile' | 'preference-decision' | 'learning-decision'
  | 'knowledge' | 'skill-binding' | 'skill-draft' | 'skill-draft-create'
  | 'workflow-feedback' | 'workflow-policy-decision' | 'workflow-evaluation'
