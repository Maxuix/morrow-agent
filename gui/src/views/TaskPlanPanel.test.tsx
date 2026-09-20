import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type {
  SessionOwnershipWire,
  TaskPlanCandidateWire,
  TaskPlanFrozenPlanWire,
} from '../api/types'
import { ExecutionOwnershipCard, PlanAdjustmentCard } from './TaskPlanPanel'

const candidate: TaskPlanCandidateWire = {
  parent_run_id: 'wrun_1',
  base_revision_id: 'wrev_1',
  expected_parent_row_version: 4,
  past_node_ids: ['work'],
  added_node_ids: ['fix'],
  removed_node_ids: [],
  changed_node_ids: ['audit'],
  origin: 'replan',
  reason: '节点发现新证据，建议调整尚未开始的工作',
  review_blocking: false,
  source_hash: 'ab'.repeat(32),
  settings_digest: 'cd'.repeat(32),
  stable: true,
  digest: 'ef'.repeat(32),
}

describe('plan adjustment card', () => {
  it('shows reason, locked past, and pending confirmation without OCC or JSON', () => {
    const html = renderToStaticMarkup(<PlanAdjustmentCard candidate={candidate} />)
    expect(html).toContain('计划调整')
    expect(html).toContain('节点发现新证据')
    expect(html).toContain('已执行锁定 1 个')
    expect(html).toContain('新增 fix')
    expect(html).toContain('任务变更 audit')
    expect(html).toContain('待确认')
    expect(html).not.toContain('expected_parent_row_version')
    expect(html).not.toContain('处理待处理信号')
  })

  it('explains draining when the pause has not settled', () => {
    const html = renderToStaticMarkup(
      <PlanAdjustmentCard candidate={{ ...candidate, stable: false }} />,
    )
    expect(html).toContain('暂停完成后可应用修改')
  })
})

const ownership: SessionOwnershipWire = {
  role: 'node',
  issues: [],
  node_run_id: 'nrun_1',
  node_id: 'work',
  node_status: 'running',
  node_attempt: 1,
  workflow_run_id: 'wrun_abc12345',
  workflow_status: 'running',
  root_task_run_id: 'task_root',
  root_session_id: 'ses_root',
  plan: { mode: 'frozen', origin: 'initial', planning_binding_id: 'wplan_1', draft_id: 'wd_1', draft_version: 3 },
}

const frozenPlan: TaskPlanFrozenPlanWire = {
  mode: 'frozen',
  origin: 'initial',
  planning_binding_id: 'wplan_1',
  draft_id: 'wd_1',
  draft_version: 3,
  source: {
    workflow_definition_id: 'wf_1',
    name: '计算器交付',
    description: '',
    tags: [],
    origin: 'user',
    input_contract: { kind: 'TaskContract', version: 1 },
    required_outputs: [],
    edges: [],
    default_budget: {
      max_agent_generation_requests: 10,
      default_node_max_agent_generation_requests: 3,
      admission_timeout_seconds: 300,
      max_concurrency: 1,
    },
    nodes: [
      {
        node_id: 'work',
        agent_definition_ref: { definition_id: 'd', version_id: 'v1', content_hash: 'aa' },
        task_contract: { objective: '实现计算器并测试', scope: [], constraints: [], source_refs: [] },
        input_bindings: [],
        output_contracts: [],
        access_mode: 'read',
        conversation_scope: 'isolated',
        tool_requirements: null,
        max_agent_generation_requests: null,
      },
    ],
  },
  node_metadata: {},
}

describe('execution ownership card', () => {
  it('names the node, owning run and frozen plan, and offers the return entry', () => {
    const html = renderToStaticMarkup(
      <ExecutionOwnershipCard ownership={ownership} plan={frozenPlan} onReturnToRoot={() => {}} />,
    )
    expect(html).toContain('工作流执行节点 · work')
    expect(html).toContain('wrun_abc'.slice(-8))
    expect(html).toContain('执行依据：冻结计划 v3')
    expect(html).toContain('实现计算器并测试')
    expect(html).not.toContain('主对话后续的重规划不会改写本节点的执行依据')
    expect(html).toContain('返回主对话')
    expect(html).not.toContain('未生成计划')
  })

  it('explains direct published runs without claiming a missing plan', () => {
    const direct: TaskPlanFrozenPlanWire = { ...frozenPlan, mode: 'direct', source: null, draft_version: null }
    const html = renderToStaticMarkup(
      <ExecutionOwnershipCard ownership={ownership} plan={direct} onReturnToRoot={null} />,
    )
    expect(html).toContain('已发布工作流')
    expect(html).not.toContain('冻结计划')
    expect(html).not.toContain('返回主对话')
  })

  it('surfaces incomplete ownership as explicit hints', () => {
    const broken: SessionOwnershipWire = {
      ...ownership,
      issues: ['frozen_draft_missing'],
    }
    const html = renderToStaticMarkup(
      <ExecutionOwnershipCard ownership={broken} plan={frozenPlan} onReturnToRoot={null} />,
    )
    expect(html).toContain('归属提示')
    expect(html).toContain('冻结的计划草稿已不可读')
  })
})
