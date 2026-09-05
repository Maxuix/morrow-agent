import type { WorkflowPolicyCandidate } from '../api/evaluation'
import { feedbackLabels } from '../api/evaluation'
import { Card, Facts, buttonClass } from './ContextDrawer'
import type { Mutate } from './ContextDrawer'

export function WorkflowPolicyReview({ candidate: c, mutate }: { candidate: WorkflowPolicyCandidate; mutate: Mutate }) {
  return <Card title={`编排策略候选 · ${feedbackLabels[c.feedback_kind] ?? c.feedback_kind}`}>
    <p className="text-sm">{c.task_type} · {c.evidence_ids.length} 份独立任务证据 · {c.status}</p>
    <p className="text-xs text-secondary">接受后影响此工作空间之后的同类任务；自动化仍需配对收益和用户策略授权。</p>
    <details open><summary className="text-sm">拟写入策略 · 文档修订 {c.expected_document_revision}</summary><Facts value={c.proposed_policy} /></details>
    <details><summary className="text-sm">反馈证据</summary><Facts value={c.evidence_ids} /></details>
    {c.stale && <p role="status">策略已变更；请拒绝此过期候选并检查当前策略。</p>}
    {c.status === 'applying' && <p role="status">接受尚未完成，可用原命令 {c.decision_command_id} 重试；若策略已有后续修改，请先检查策略。</p>}
    {c.status === 'applying' && c.decision_command_id && <button className={buttonClass} onClick={() => void mutate('workflow-policy-decision', { command_id: c.decision_command_id, action: 'accept', expected_row_version: c.row_version - 1 }, c.candidate_id)}>重试接受</button>}
    {c.status === 'proposed' && <div className="flex gap-2">
      <button className={buttonClass} disabled={c.stale} onClick={() => void mutate('workflow-policy-decision', { action: 'accept', expected_row_version: c.row_version }, c.candidate_id)}>接受编排策略</button>
      <button className={buttonClass} onClick={() => void mutate('workflow-policy-decision', { action: 'reject', expected_row_version: c.row_version }, c.candidate_id)}>拒绝候选</button>
    </div>}
  </Card>
}
