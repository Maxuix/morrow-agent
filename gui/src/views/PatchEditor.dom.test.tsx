// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../api/client'
import type {
  NodeViewWire,
  PatchApplyResultWire,
  PatchValidationWire,
  RunViewWire,
  WorkflowRunWire,
} from '../api/types'
import { PatchEditor } from './PatchEditor'
import { WORKFLOW_EDITOR_FIXTURE, THREE_STEP_SOURCE } from './editor/fixtures'

afterEach(cleanup)

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver

function makeRun(overrides: Partial<WorkflowRunWire> = {}): WorkflowRunWire {
  return {
    workflow_run_id: 'wrun_parent',
    workflow_revision_id: 'wrev_parent',
    root_task_run_id: 'task_parent',
    status: 'paused',
    row_version: 7,
    started_at: null,
    completed_at: null,
    budget_snapshot: THREE_STEP_SOURCE.default_budget,
    admission_deadline_at: null,
    input_artifacts: [],
    result_status: null,
    pending_terminal_intent: null,
    pause_requested: true,
    run_relation: 'initial',
    lineage_budget_root_run_id: null,
    parent_run_id: null,
    superseded_reason: null,
    ...overrides,
  }
}

function makeNodeView(nodeId: string, status: NodeViewWire['node']['status']): NodeViewWire {
  return {
    node: {
      node_run_id: `nrun_${nodeId}`,
      workflow_run_id: 'wrun_parent',
      node_id: nodeId,
      status,
      attempt: 1,
      row_version: 1,
      started_at: null,
      completed_at: null,
      conversation_session_id: null,
      leaf_task_run_id: null,
      agent_run_id: null,
      effective_node_generation_request_cap: null,
    },
    output_bindings: [],
    artifacts: [],
    approval_pending: false,
    agent_generation_request_count: 0,
  }
}

function makeView(statuses: NodeViewWire['node']['status'][] = ['completed', 'queued', 'queued']): RunViewWire {
  const revision = {
    ...THREE_STEP_SOURCE,
    budget: THREE_STEP_SOURCE.default_budget,
  } as Record<string, unknown>
  return {
    run: makeRun(),
    revision,
    nodes: THREE_STEP_SOURCE.nodes.map((node, index) => makeNodeView(node.node_id, statuses[index] ?? 'queued')),
    input_artifacts: [],
    agent_generation_request_count: 0,
    lineage_agent_generation_request_count: 0,
    inherited_artifacts: [],
    effective_outputs: [],
    usage_availability: 'available',
    terminal_outcome: null,
    actionable_status: null,
    pre_run_summary: {
      node_count: THREE_STEP_SOURCE.nodes.length,
      models: ['fixture/deterministic'],
      providers: ['fixture'],
      max_agent_generation_requests: null,
      default_node_max_agent_generation_requests: null,
      admission_timeout_seconds: null,
      max_concurrency: 1,
      writer_node_ids: ['implement'],
    },
  }
}

function clientWith(
  validatePatch: (patch: unknown) => Promise<PatchValidationWire>,
  applyPatch: (patch: unknown) => Promise<PatchApplyResultWire>,
) {
  return {
    listAgentDefinitions: vi.fn(async () => WORKFLOW_EDITOR_FIXTURE.agents),
    editorCatalogs: vi.fn(async () => ({
      providers: [], active_model: null, skills: [], tools: [], contracts: WORKFLOW_EDITOR_FIXTURE.contracts,
    })),
    validatePatch: vi.fn(validatePatch),
    applyPatch: vi.fn(applyPatch),
  } as unknown as ApiClient
}

const validPreview: PatchValidationWire = {
  valid: true,
  diagnostics: [],
  past_node_ids: ['research'],
  execution_node_ids: ['implement', 'review'],
  diff: {
    added_node_ids: [], removed_node_ids: [], changed_node_ids: ['implement'],
    added_edges: [], removed_edges: [], required_outputs_changed: false, budget_changed: false,
  },
  risk: { level: 'elevated', reasons: ['writer_order_changed'] },
}

const applied: PatchApplyResultWire = {
  workflow_patch_id: 'wpatch_1',
  workflow_revision_id: 'wrev_child',
  parent_run: makeRun({ status: 'superseded', superseded_reason: 'continued_by_patch' }),
  child_run: makeRun({ workflow_run_id: 'wrun_child', status: 'running' }),
  driving: true,
}

describe('PatchEditor semantic DOM', () => {
  it('keeps admitted history locked while queued nodes remain editable', async () => {
    const client = clientWith(vi.fn(async () => validPreview), vi.fn(async () => applied))
    render(<PatchEditor client={client} run={makeRun()} view={makeView()} workspaceId="ws_fixture" onClose={vi.fn()} />)

    expect(screen.getByText('父运行已暂停')).toBeTruthy()
    expect((screen.getByLabelText('任务目标') as HTMLTextAreaElement).disabled).toBe(true)
    fireEvent.click(screen.getByTestId('rf__node-implement').querySelector('.workflow-node-card')!)
    expect((screen.getByLabelText('任务目标') as HTMLTextAreaElement).disabled).toBe(false)
  })

  it('requires preview risk acknowledgement before applying the same patch identity', async () => {
    const validate = vi.fn(async (_patch: unknown) => validPreview)
    const apply = vi.fn(async (_patch: unknown) => applied)
    const client = clientWith(validate, apply)
    render(<PatchEditor client={client} run={makeRun()} view={makeView()} workspaceId="ws_fixture" onClose={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: '预览补丁' }))
    await screen.findByText('需确认的风险')
    const applyButton = screen.getByRole('button', { name: '确认应用补丁' }) as HTMLButtonElement
    expect(applyButton.disabled).toBe(true)
    fireEvent.click(screen.getByRole('checkbox', { name: '我已知晓上述风险' }))
    expect(applyButton.disabled).toBe(false)
    fireEvent.click(applyButton)
    await screen.findByRole('button', { name: '返回观察' })

    expect(validate).toHaveBeenCalledTimes(1)
    expect(apply).toHaveBeenCalledTimes(1)
    expect((validate.mock.calls[0]?.[0] as { workflow_patch_id: string }).workflow_patch_id)
      .toBe((apply.mock.calls[0]?.[0] as { workflow_patch_id: string }).workflow_patch_id)
  })

  it('maps patch diagnostics safely instead of exposing raw exception text', async () => {
    const invalid: PatchValidationWire = {
      ...validPreview,
      valid: false,
      diagnostics: [{
        severity: 'error',
        code: 'future_code',
        message: 'token=sk-live Traceback (most recent call last):\nsecret details',
        node_id: 'implement',
        edge_id: null,
      }],
    }
    const client = clientWith(vi.fn(async () => invalid), vi.fn(async () => applied))
    render(<PatchEditor client={client} run={makeRun()} view={makeView()} workspaceId="ws_fixture" onClose={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: '预览补丁' }))
    expect((await screen.findAllByText('未识别的检查项')).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/token=已隐藏/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/Traceback|secret details/)).toBeNull()
  })
})
