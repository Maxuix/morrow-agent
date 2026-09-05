import type { OrchestrationPolicyWire } from './types'

export interface WorkflowPolicyCandidate {
  candidate_id: string; task_type: string; feedback_kind: string; evidence_ids: string[]
  proposed_policy: OrchestrationPolicyWire; expected_document_revision: number
  status: 'proposed' | 'applying' | 'accepted' | 'rejected'; row_version: number
  decision_command_id: string | null; stale: boolean
}
export interface EvaluationRun {
  workflow_run_id: string; root_task_run_id: string; lineage_root_run_id: string
  mode: 'direct' | 'multi'; task_type: string; status: string; result_status: string | null
  node_count: number; requests: number; lineage_requests: number; usage_availability: string
  user_modified: boolean; dead_node_ids: string[]; verification_results: string[]
  outcome: { outcome_id: string; summary: string; task_status: string } | null
  reviewer_findings: { node_id: string; artifact_id: string; findings: string[]; verdict: string | null; status: string }[]
  nodes: { node_id: string; status: string; requests: number }[]
}
export interface EvaluationPage {
  runs: EvaluationRun[]; next_cursor: string | null
  metrics: { root_task_count: number; edited_task_count: number; edit_frequency: number | null
    draft_edit_count: number; run_edit_count: number; reviewer_rated_tasks: number
    reviewer_useful_tasks: number; reviewer_value: number | null }
  feedback: { feedback_id: string; kind: string; subject_id: string; task_type: string; created_at: string }[]
  evaluations: { evaluation_id: string; kind: 'paired' | 'estimate'; task_type: string
    multi_run_id: string; direct_run_id: string | null; direct_requests: number; multi_requests: number
    direct_quality: number | null; multi_quality: number | null; benefit: boolean }[]
  promotion: { task_type: string; paired_count: number; beneficial_count: number; promoted: boolean
    reason: string; auto_run_eligible: boolean; task_class_replan_eligible: boolean; evidence_ids: string[] }[]
}
export const feedbackLabels: Record<string, string> = {
  graph_edit: '编辑工作流', removed_planner: '删除 Planner', model_changed: '替换模型', added_reviewer: '增加 Reviewer',
  too_complex: '流程太复杂', missing_exploration: '缺少探索', reviewer_useful: 'Reviewer 有价值',
  reviewer_not_useful: 'Reviewer 无价值', model_expensive: '模型太贵', prefer_template: '此类任务使用该模板',
  avoid_template: '此类任务不用该模板',
}
