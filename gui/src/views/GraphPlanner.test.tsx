import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import type { GraphPlanningRequestWire, PlannerMetadataWire } from '../api/types'
import { optionalRequestCap, plannerApprovalText, PlannerExplanation } from './GraphPlanner'
import { defaultOrchestrationPolicy } from './OrchestrationSettings'

const budget = { max_agent_generation_requests: null, default_node_max_agent_generation_requests: null, admission_timeout_seconds: null, max_concurrency: 1 }

describe('GraphPlanner review contract', () => {
  it('keeps absent request limits absent and rejects invalid explicit caps', () => {
    expect(optionalRequestCap('')).toBeNull()
    expect(optionalRequestCap('3')).toBe(3)
    for (const value of ['0', '-1', '1.5', 'Infinity', 'bad']) expect(() => optionalRequestCap(value)).toThrow()
  })

  it('defaults to approval-first and tells users manual runs remain available', () => {
    const policy = defaultOrchestrationPolicy('workspace', 'research')
    expect(policy.scope).toBe('workspace')
    expect(policy.task_matcher).toBe('research')
    expect(policy.auto_run_mode).toBe('approval_only')
    expect(policy.auto_replan_mode).toBe('approval_only')
    expect(policy.budget_limits).toBeNull()
    expect(plannerApprovalText('paired_evidence_missing')).toContain('手工运行')
  })

  it('renders structured explanation, original-draft label and the evidence gate', () => {
    const metadata: PlannerMetadataWire = {
      request_digest: 'a'.repeat(64), source_hash: 'b'.repeat(64),
      features: { task_type: 'research', expected_scope: ['API', 'storage'], number_of_areas: 2, requires_code_write: false, requires_research: true, review_value: 'low', parallelizable_read_work: true, ambiguity: 'low', risk_level: 'low', expected_duration_class: 'short', user_requested_roles: [], user_excluded_roles: [], workspace_constraints: [] },
      brief: null, policy_id: 'research', policy_scope: 'workspace', policy_revision: 2,
      classification: 'model', diagnostics: ['graph_cycle: repaired'],
      explanation: { mode: 'multi', reasons: ['Independent evidence collection'], starting_point: 'grammar', node_count: 3, writing_nodes: [], models: [{ provider_id: 'configured', model_id: 'model' }], budget, concurrency: 1, auto_run_eligible: false, auto_run_reason: 'paired_evidence_missing' },
    }
    const html = renderToStaticMarkup(<PlannerExplanation metadata={metadata} edited />)
    expect(html).toContain('图已编辑')
    expect(html).toContain('无上限')
    expect(html).toContain('configured/model')
    expect(html).toContain('graph_cycle: repaired')
    expect(html).toContain('对照收益证据')
  })

  it('submits stable draft/command identity and explicit controls through the authenticated API', async () => {
    const calls: Array<{ url: string; body: unknown; method: string | undefined; auth: string | null }> = []
    const client = new ApiClient({ baseUrl: '', token: 'test', fetchImpl: async (url, init) => {
      calls.push({ url: String(url), body: JSON.parse(String(init?.body)), method: init?.method, auth: new Headers(init?.headers).get('authorization') })
      return new Response(JSON.stringify({ result: { workflow_draft: null, diagnostics: ['catalog_missing'] } }), { status: 200 })
    } })
    const planning: GraphPlanningRequestWire = { draft_id: 'wdraft_same', workflow_definition_id: 'planned', name: 'Plan', task: { objective: 'Investigate', scope: [], constraints: [], source_refs: [] }, requested_roles: [], excluded_roles: ['reviewer'], budget, use_model: false, scout: true }
    const result = await client.planWorkflow(planning, 'cmd_same')
    await client.planWorkflow(planning, 'cmd_same')
    expect(result.workflow_draft).toBeNull()
    expect(calls[0]).toEqual(calls[1])
    expect(calls[0]).toEqual({ url: '/v1/workflow-planner', body: { command_id: 'cmd_same', planning }, method: 'POST', auth: 'Bearer test' })
    const policy = defaultOrchestrationPolicy('global', '*')
    await client.putOrchestrationPolicy(policy, 7, 'cmd_policy')
    expect(calls[2].method).toBe('PUT')
    expect(calls[2].body).toEqual({ policy, expected_revision: 7, command_id: 'cmd_policy' })
  })
})
