/**
 * Source projection for workspace-wide learning candidates.
 * Package 5 filters the current session on the server; this helper only labels
 * whether a row already carries a known origin so that filter can be applied
 * without inventing session membership in the browser.
 */
export interface CandidateSourceFields {
  session_id?: string | null
  task_run_id?: string | null
  source_kind?: string | null
  source_id?: string | null
  origin_review_id?: string | null
  evidence?: { source_kind?: string | null; source_id?: string | null }[]
}

export function isCandidateSourceKnown(row: CandidateSourceFields): boolean {
  if (row.session_id || row.task_run_id || row.source_id) return true
  return Boolean(row.evidence?.some(item => item.source_id))
}

export function candidateSourceLabel(row: CandidateSourceFields): string {
  if (!isCandidateSourceKnown(row)) return '来源未知'
  const kind = row.source_kind || row.evidence?.find(item => item.source_kind)?.source_kind
  if (row.session_id) return kind ? `来源已知 · 会话 · ${kind}` : '来源已知 · 会话'
  if (row.task_run_id) return kind ? `来源已知 · 任务 · ${kind}` : '来源已知 · 任务'
  return kind ? `来源已知 · ${kind}` : '来源已知'
}
