// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type ApiClient } from '../api/client'
import type { WorkflowDefinitionSourceWire, WorkflowDraftViewWire } from '../api/types'
import { EditorShell } from './EditorShell'
import { fixtureDraft, THREE_STEP_SOURCE, WORKFLOW_EDITOR_FIXTURE } from './editor/fixtures'

type Guard = { isDirty: () => boolean; confirmLeave: () => Promise<boolean> }

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver

function mockClient(update?: (source: typeof THREE_STEP_SOURCE, row: number, command: string) => Promise<WorkflowDraftViewWire>) {
  const calls: Array<{ source: typeof THREE_STEP_SOURCE; row: number; command: string }> = []
  const client = {
    workspaceId: 'ws_fixture',
    listAgentDefinitions: async () => WORKFLOW_EDITOR_FIXTURE.agents,
    listWorkflowDefinitions: async () => [],
    listWorkflowDrafts: async () => [WORKFLOW_EDITOR_FIXTURE.drafts.normal],
    editorCatalogs: async () => ({ providers: [], active_model: null, skills: [], tools: [], contracts: WORKFLOW_EDITOR_FIXTURE.contracts }),
    updateWorkflowDraft: async (_draftId: string, source: typeof THREE_STEP_SOURCE, row: number, command: string) => {
      calls.push({ source, row, command })
      return update?.(source, row, command) ?? fixtureDraft(source, { row_version: row + 1, source_hash: 'b'.repeat(64) })
    },
    getWorkflowDraft: async () => WORKFLOW_EDITOR_FIXTURE.drafts.normal,
    createWorkflowDraft: async () => WORKFLOW_EDITOR_FIXTURE.drafts.normal,
    writeWorkflowSource: async () => ({}),
    freezeWorkflowDraft: async () => ({ workflow_draft: WORKFLOW_EDITOR_FIXTURE.drafts.frozen, workflow_revision: { workflow_revision_id: 'wrev_fixture' } }),
    orchestrationPolicies: async () => ({
      global: { revision: 0, policies: [] },
      workspace: { revision: 0, policies: [] },
    }),
    definitionAction: async () => ({}),
    planWorkflow: async () => ({ workflow_draft: null, metadata: null, explanation: { mode: 'direct', reasons: [], starting_point: 'direct', node_count: 1, writing_nodes: [], models: [], budget: { max_agent_generation_requests: null, default_node_max_agent_generation_requests: null, admission_timeout_seconds: null, max_concurrency: 1 }, concurrency: 1, auto_run_eligible: false, auto_run_reason: 'approval_only' }, diagnostics: [] }),
  } as unknown as ApiClient
  return { client, calls }
}

afterEach(() => { cleanup(); sessionStorage.clear(); vi.useRealTimers() })

describe('EditorShell draft wiring', () => {
  it('edits the rendered draft through the controller and registers one leave guard', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const registered: { current: Guard | null } = { current: null }
    const { client, calls } = mockClient()
    render(<EditorShell client={client} registerGuard={guard => { registered.current = guard; return () => { registered.current = null } }} />)

    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    fireEvent.click(within(screen.getByRole('complementary', { name: '工作流目录' })).getByRole('button', { name: /三步交付流程/ }))
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() })
    expect(screen.getByRole('region', { name: '步骤详情' })).toBeTruthy()
    const objective = screen.getByLabelText('任务目标')
    fireEvent.change(objective, { target: { value: '更新后的任务目标' } })
    expect(registered.current?.isDirty()).toBe(true)

    await act(async () => {
      vi.advanceTimersByTime(400)
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(calls).toHaveLength(1)
    expect(calls[0]?.row).toBe(1)
    expect(calls[0]?.source.nodes.find(node => node.node_id === 'research')?.task_contract.objective).toBe('更新后的任务目标')
    expect(registered.current?.isDirty()).toBe(false)
  })

  it('remembers the selected draft across an editor refresh without storing Source', async () => {
    const { client } = mockClient()
    render(<EditorShell client={client} />)
    await screen.findByText('三步交付流程')
    fireEvent.click(within(screen.getByRole('complementary', { name: '工作流目录' })).getByRole('button', { name: /三步交付流程/ }))
    await screen.findByRole('region', { name: '步骤详情' })
    expect(sessionStorage.getItem('morrow.editor.draft.ws_fixture')).toBe('wdraft_three_step')
    expect(sessionStorage.getItem('morrow.editor.source.ws_fixture')).toBeNull()
  })

  it('uses the visible leave dialog and keeps editing when navigation is cancelled', async () => {
    const { client } = mockClient()
    const registered: { current: Guard | null } = { current: null }
    render(<EditorShell client={client} registerGuard={guard => { registered.current = guard; return () => {} }} />)
    await screen.findByText('三步交付流程')
    fireEvent.click(within(screen.getByRole('complementary', { name: '工作流目录' })).getByRole('button', { name: /三步交付流程/ }))
    await screen.findByRole('region', { name: '步骤详情' })
    const objective = screen.getByLabelText('任务目标')
    await userEvent.setup().type(objective, '未保存')
    expect(registered.current?.isDirty()).toBe(true)
    const leave = registered.current?.confirmLeave()
    if (leave === undefined) throw new Error('editor leave guard was not registered')
    expect(await screen.findByText('工作流草稿有未保存修改')).toBeTruthy()
    await userEvent.setup().click(screen.getByRole('button', { name: '继续编辑' }))
    await expect(leave).resolves.toBe(false)
    expect((objective as HTMLTextAreaElement).value).toContain('未保存')
  })

  it('saves a dirty draft before freezing and never starts a run', async () => {
    const { client } = mockClient()
    const events: string[] = []
    const update = client.updateWorkflowDraft
    client.updateWorkflowDraft = async (...args) => {
      events.push('save')
      return update(...args)
    }
    client.freezeWorkflowDraft = async () => {
      events.push('freeze')
      return {
        workflow_draft: WORKFLOW_EDITOR_FIXTURE.drafts.frozen,
        workflow_revision: { workflow_revision_id: 'wrev_fixture' },
      }
    }
    render(<EditorShell client={client} />)
    await screen.findByText('三步交付流程')
    fireEvent.click(within(screen.getByRole('complementary', { name: '工作流目录' })).getByRole('button', { name: /三步交付流程/ }))
    await screen.findByRole('region', { name: '步骤详情' })
    fireEvent.change(screen.getByLabelText('任务目标'), { target: { value: '发布前保存的目标' } })
    const publish = screen.getByRole('button', { name: '发布版本' }) as HTMLButtonElement
    expect(publish.disabled).toBe(false)
    fireEvent.click(publish)
    fireEvent.click(publish)
    await waitFor(() => expect(events).toEqual(['save', 'freeze']))
    await screen.findByText('已发布')
    expect(screen.getByRole('region', { name: '发布审核' }).textContent).not.toContain('未启动运行')
    expect((screen.getByLabelText('任务目标') as HTMLTextAreaElement).disabled).toBe(true)
  })

  it('keeps local input and skips freeze when the pre-publish save fails', async () => {
    const { client } = mockClient()
    const freeze = vi.fn()
    client.updateWorkflowDraft = async () => {
      throw new ApiError(409, 'stale', '服务器版本已变化')
    }
    client.freezeWorkflowDraft = freeze
    render(<EditorShell client={client} />)
    await screen.findByText('三步交付流程')
    fireEvent.click(within(screen.getByRole('complementary', { name: '工作流目录' })).getByRole('button', { name: /三步交付流程/ }))
    await screen.findByRole('region', { name: '步骤详情' })
    const objective = screen.getByLabelText('任务目标') as HTMLTextAreaElement
    fireEvent.change(objective, { target: { value: '冲突时仍保留的目标' } })
    fireEvent.click(screen.getByRole('button', { name: '发布版本' }))
    await screen.findByText('发布前必须先保存最新修改；本地内容仍保留。')
    expect(freeze).not.toHaveBeenCalled()
    expect(objective.value).toBe('冲突时仍保留的目标')
  })

  it('creates an editable new draft after a frozen draft without changing the old one', async () => {
    const frozen = structuredClone(WORKFLOW_EDITOR_FIXTURE.drafts.frozen)
    const created = fixtureDraft(frozen.draft.source, {
      draft_id: 'wdraft_clone_fixture',
      row_version: 1,
    })
    const { client } = mockClient()
    client.listWorkflowDrafts = async () => [frozen]
    client.getWorkflowDraft = async () => frozen
    const requests: Array<{ source: WorkflowDefinitionSourceWire; revision: number; command: string; draftId: string }> = []
    client.createWorkflowDraft = async (source, revision, command, draftId) => {
      requests.push({ source, revision, command, draftId })
      return created
    }
    render(<EditorShell client={client} />)
    await screen.findByText('三步交付流程')
    fireEvent.click(within(screen.getByRole('complementary', { name: '工作流目录' })).getByRole('button', { name: /三步交付流程/ }))
    await screen.findByRole('button', { name: '创建新草稿继续编辑' })
    fireEvent.click(screen.getByRole('button', { name: '创建新草稿继续编辑' }))
    await screen.findByText('新草稿已创建。')
    expect(requests[0]?.draftId).toMatch(/^wdraft_/)
    expect(requests[0]?.source.workflow_definition_id).toBe('three_step')
    expect((screen.getByLabelText('任务目标') as HTMLTextAreaElement).disabled).toBe(false)
    expect(screen.getByRole('button', { name: '发布版本' })).toBeTruthy()
  })

  it('reads back the exact draft identity once when new-draft creation has an unknown result', async () => {
    const frozen = structuredClone(WORKFLOW_EDITOR_FIXTURE.drafts.frozen)
    const { client } = mockClient()
    client.listWorkflowDrafts = async () => [frozen]
    let cloneId = ''
    const readback = fixtureDraft(frozen.draft.source, { draft_id: 'wdraft_readback' })
    client.createWorkflowDraft = async (_source, _revision, _command, draftId) => {
      cloneId = draftId
      throw new ApiError(0, 'network_unknown', '网络结果未知')
    }
    const reads: string[] = []
    client.getWorkflowDraft = async draftId => {
      reads.push(draftId)
      return draftId === cloneId ? { ...readback, draft: { ...readback.draft, draft_id: draftId } } : frozen
    }
    render(<EditorShell client={client} />)
    await screen.findByText('三步交付流程')
    fireEvent.click(within(screen.getByRole('complementary', { name: '工作流目录' })).getByRole('button', { name: /三步交付流程/ }))
    await screen.findByRole('button', { name: '创建新草稿继续编辑' })
    fireEvent.click(screen.getByRole('button', { name: '创建新草稿继续编辑' }))
    await screen.findByText('新 Draft 创建结果已核对；原发布版本仍保持只读。')
    expect(cloneId).toMatch(/^wdraft_/)
    expect(reads).toContain(cloneId)
  })
})
