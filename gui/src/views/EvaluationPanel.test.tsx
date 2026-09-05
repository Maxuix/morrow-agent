import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { EvaluationPage, EvaluationRun, WorkflowPolicyCandidate } from '../api/evaluation'
import { EvaluationMetrics, ratio, RunEvaluation } from './EvaluationPanel'
import { WorkflowPolicyReview } from './WorkflowPolicyReview'
import { plannerApprovalText } from './GraphPlanner'

const run: EvaluationRun = {
  workflow_run_id: 'wrun_multi', root_task_run_id: 'task_multi', lineage_root_run_id: 'wrun_multi',
  mode: 'multi', task_type: 'implementation', status: 'completed', result_status: 'succeeded',
  node_count: 2, requests: 3, lineage_requests: 4, usage_availability: 'unavailable',
  user_modified: true, dead_node_ids: ['unused'], verification_results: ['test:passed'],
  outcome: { outcome_id: 'tout_one', summary: 'Verified outcome', task_status: 'ready_for_acceptance' },
  reviewer_findings: [{ node_id: 'reviewer', artifact_id: 'art_review', verdict: null, status: 'available', findings: ['<script>private code</script>'] }],
  nodes: [],
}
const data = { runs: [run], next_cursor: null, metrics: { root_task_count: 2, edited_task_count: 1,
  edit_frequency: 0.5, draft_edit_count: 2, run_edit_count: 0, reviewer_rated_tasks: 0, reviewer_useful_tasks: 0,
  reviewer_value: null }, promotion: [{ task_type: 'implementation', paired_count: 1, beneficial_count: 1,
  promoted: false, reason: 'paired_evidence_missing', auto_run_eligible: false, task_class_replan_eligible: false,
  evidence_ids: ['weval_1'] }], evaluations: [], feedback: [] } satisfies EvaluationPage

describe('feedback and evaluation projections', () => {
  it('distinguishes unrated from zero and estimates from paired promotion', () => {
    expect(ratio(null)).toBe('暂无评价')
    expect(ratio(0)).toBe('0%')
    const html = renderToStaticMarkup(<EvaluationMetrics data={data} />)
    expect(html).toContain('50%')
    expect(html).toContain('暂无评价')
    expect(html).toContain('证据不足')
    expect(html).toContain('估算不计入')
    expect(html).toContain('手工运行')
  })
  it('shows real run evidence and safely escapes Reviewer findings', () => {
    const html = renderToStaticMarkup(<RunEvaluation run={run} mutate={async () => true} />)
    expect(html).toContain('Verified outcome')
    expect(html).toContain('本次 3 次请求')
    expect(html).toContain('用户修改过工作流')
    expect(html).toContain('unused')
    expect(html).toContain('usage 不可用')
    expect(html).toContain('&lt;script&gt;')
    expect(html).not.toContain('<script>')
  })
  it('only offers post-run feedback for settled runs', () => {
    const html = renderToStaticMarkup(<RunEvaluation run={{ ...run, status: 'running' }} mutate={async () => true} />)
    expect(html).not.toContain('记录反馈')
    expect(html).not.toContain('运行反馈')
  })
  it('disables stale acceptance while keeping rejection and evidence review', () => {
    const candidate = { candidate_id: 'wpc_test', task_type: 'implementation', feedback_kind: 'removed_planner',
      evidence_ids: ['wfb_1', 'wfb_2'], proposed_policy: {}, expected_document_revision: 0,
      status: 'proposed', row_version: 1, decision_command_id: null, stale: true } as WorkflowPolicyCandidate
    const html = renderToStaticMarkup(<WorkflowPolicyReview candidate={candidate} mutate={async () => true} />)
    expect(html).toContain('wfb_1')
    expect(html).toContain('策略已变更')
    expect(html).toMatch(/disabled=""[^>]*>接受编排策略/)
    expect(html).toContain('拒绝候选')
  })
  it('labels promotion as a generation-time eligibility fact', () => {
    expect(plannerApprovalText('paired_benefit')).toContain('生成时')
    expect(plannerApprovalText('paired_benefit')).toContain('策略变更后需重新评估')
  })
})
