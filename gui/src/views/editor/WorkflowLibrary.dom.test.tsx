// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowDefinitionViewWire } from '../../api/types'
import { WorkflowLibrary } from './WorkflowLibrary'

afterEach(cleanup)

function workflow(overrides: Partial<WorkflowDefinitionViewWire> = {}): WorkflowDefinitionViewWire {
  return {
    workflow_definition_id: 'workflow_1',
    origin: 'user',
    source_revision: 3,
    revoked: false,
    desired_ahead_of_published: false,
    source: {
      workflow_definition_id: 'workflow_1', name: '用户流程', description: '', tags: [], origin: 'user',
      input_contract: { kind: 'TaskContract', version: 1 }, required_outputs: [], edges: [],
      default_budget: { max_agent_generation_requests: null, default_node_max_agent_generation_requests: null, admission_timeout_seconds: null, max_concurrency: 1 },
      nodes: [],
    },
    head: { workflow_revision_id: 'wrev_1', row_version: 3, enabled: true },
    published_revision: { created_at: '2026-09-12T00:00:00Z' },
    ...overrides,
  }
}

describe('WorkflowLibrary semantic DOM', () => {
  it('distinguishes disabled and revoked definitions from published ones', () => {
    render(
      <WorkflowLibrary
        open
        drafts={[]}
        workflows={[workflow(), workflow({ workflow_definition_id: 'workflow_2', source: { ...workflow().source!, workflow_definition_id: 'workflow_2', name: '停用流程' }, head: { workflow_revision_id: 'wrev_2', row_version: 4, enabled: false } }), workflow({ workflow_definition_id: 'workflow_3', source: { ...workflow().source!, workflow_definition_id: 'workflow_3', name: '撤销流程' }, revoked: true })]}
        selectedDraftId={null}
        selectedWorkflowId={null}
        more={{ agents: false, workflows: false, drafts: false }}
        onToggle={vi.fn()}
        onNew={vi.fn()}
        onDraft={vi.fn()}
        onWorkflow={vi.fn()}
        onLoadMore={vi.fn()}
      />,
    )

    expect(screen.getByText(/自定义 · 已发布/)).toBeTruthy()
    expect(screen.getByText(/自定义 · 已停用/)).toBeTruthy()
    expect(screen.getByText(/自定义 · 已撤销 · 只读/)).toBeTruthy()
  })
})
