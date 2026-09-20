// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../api/client'
import type { ApprovalResolveResultWire, ApprovalWire } from '../../api/types'
import { InlineApprovalCard } from './InlineApprovalCard'

const approval = (overrides: Partial<ApprovalWire> = {}): ApprovalWire => ({
  approval_id: 'approval_secret_123', tool_execution_id: 'execution_secret_123', tool_name: 'read_file',
  session_id: 'session_1', task_run_id: 'task_1', agent_run_id: 'agent_1', workflow_run_id: null,
  node_run_id: null, node_id: null, agent_id: null, effect_class: 'workspace_read', risk_level: 'medium',
  session_scope_allowed: true, affected_objects: ['/workspace/README.md'], requested_scope: 'workspace_read',
  granted_scope: null, preview: ['读取 /workspace/README.md'], resolution: 'pending',
  created_at: '2026-09-11T00:00:00Z', expires_at: '2099-01-01T00:00:00Z', resolved_at: null,
  row_version: 1, ...overrides,
})

function resultFor(source: ApprovalWire, decision: 'allow_once' | 'deny' | 'allow_session'): ApprovalResolveResultWire {
  return {
    approval: {...source, resolution: decision === 'deny' ? 'denied' : 'approved',
      granted_scope: decision === 'allow_session' ? 'session:workspace_read' : null},
    delivery: 'live', executed: decision !== 'deny',
  }
}

function fakeClient(overrides: Partial<Record<keyof ApiClient, unknown>> = {}) {
  return {
    resolveApproval: vi.fn(),
    listApprovals: vi.fn(async () => []),
    chatPermissions: vi.fn(async () => ({
      run_ids: [], next_run_cursor: null, snapshot: null, grants: [], next_grant_cursor: null,
      session_scopes: {items: [], next_cursor: null},
    })),
    revokeChatPermission: vi.fn(async () => ({disposition: 'applied'})),
    ...overrides,
  } as unknown as ApiClient & {
    resolveApproval: ReturnType<typeof vi.fn>
    listApprovals: ReturnType<typeof vi.fn>
    chatPermissions: ReturnType<typeof vi.fn>
    revokeChatPermission: ReturnType<typeof vi.fn>
  }
}

describe('InlineApprovalCard', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  it('shows a bounded safe preview and submits only one command on a double click', async () => {
    const user = userEvent.setup()
    const source = approval()
    let resolve!: (value: ApprovalResolveResultWire) => void
    const client = fakeClient()
    client.resolveApproval.mockImplementation(() => new Promise<ApprovalResolveResultWire>(done => {resolve = done}))
    await act(async () => {
      root.render(<InlineApprovalCard approval={source} client={client} workspace="ws" />)
    })
    expect(screen.getByText('读取工作区')).toBeDefined()
    expect(screen.getByText('查看安全预览')).toBeDefined()
    expect(container.textContent).not.toContain(source.approval_id)
    expect(container.textContent).not.toContain(source.tool_execution_id)
    await user.dblClick(screen.getByRole('button', {name: '允许本次执行'}))
    expect(client.resolveApproval).toHaveBeenCalledTimes(1)
    resolve(resultFor(source, 'allow_once'))
    await waitFor(() => expect(screen.getByText('已允许本次执行，等待工具结果；决定不等于工具已成功。')).toBeDefined())
  })

  it('preserves the command id after an uncertain response and allows the same decision to retry', async () => {
    const user = userEvent.setup()
    const source = approval()
    const client = fakeClient()
    client.resolveApproval.mockRejectedValueOnce(new Error('network lost'))
      .mockResolvedValueOnce(resultFor(source, 'allow_once'))
    await act(async () => {
      root.render(<InlineApprovalCard approval={source} client={client} workspace="ws" />)
    })
    await user.click(screen.getByRole('button', {name: '允许本次执行'}))
    await waitFor(() => expect(screen.getByRole('button', {name: '重试相同决定'})).toBeDefined())
    await user.click(screen.getByRole('button', {name: '重试相同决定'}))
    await waitFor(() => expect(screen.getByText('已允许本次执行，等待工具结果；决定不等于工具已成功。')).toBeDefined())
    expect(client.resolveApproval).toHaveBeenCalledTimes(2)
    expect(client.resolveApproval.mock.calls[0][2]).toBe(client.resolveApproval.mock.calls[1][2])
  })

  it('keeps the session and deny decisions inline with the same authority', async () => {
    const user = userEvent.setup()
    const source = approval()
    const client = fakeClient()
    client.resolveApproval.mockResolvedValue(resultFor(source, 'allow_session'))
    await act(async () => {
      root.render(<InlineApprovalCard approval={source} client={client} workspace="ws" />)
    })
    expect(screen.getByRole('button', {name: '本会话同范围免批'})).toBeDefined()
    expect(screen.getByRole('button', {name: '拒绝'})).toBeDefined()
    await user.click(screen.getByRole('button', {name: '本会话同范围免批'}))
    await waitFor(() => expect(screen.getByRole('button', {name: '撤销此授权'})).toBeDefined())
    expect(client.resolveApproval).toHaveBeenCalledWith(source.approval_id, 'allow_session', expect.any(String))
  })

  it('does not offer a session exemption for a high-risk approval', async () => {
    await act(async () => {
      root.render(<InlineApprovalCard approval={approval({risk_level: 'high', session_scope_allowed: true})} client={fakeClient()} workspace="ws" />)
    })
    expect(screen.queryByRole('button', {name: '本会话同范围免批'})).toBeNull()
    expect(screen.getByRole('button', {name: '允许本次执行'})).toBeDefined()
  })

  it('reconciles a decision made in another page and can revoke a session scope once', async () => {
    const user = userEvent.setup()
    const source = approval()
    const client = fakeClient()
    client.listApprovals.mockResolvedValue([approval({resolution: 'approved'})])
    await act(async () => {
      root.render(<InlineApprovalCard approval={source} client={client} workspace="ws" />)
    })
    window.dispatchEvent(new CustomEvent('morrow:approval-sync', {
      detail: {approval_id: source.approval_id, resolution: 'approved', source: 'other-page'},
    }))
    await waitFor(() => expect(screen.getByText('此请求已在其他页面处理，当前显示的是最新状态。')).toBeDefined())
    client.chatPermissions.mockResolvedValue({
      run_ids: [], next_run_cursor: null, snapshot: null, grants: [], next_grant_cursor: null,
      session_scopes: {items: [{scope: 'session:workspace_read', revision: 4, approval_id: source.approval_id}], next_cursor: null},
    })
    // A fresh approved wire carries the session grant that the other page created.
    await act(async () => {
      root.render(<InlineApprovalCard approval={approval({resolution: 'approved', granted_scope: 'session:workspace_read'})} client={client} workspace="ws" />)
    })
    await user.click(screen.getByRole('button', {name: '撤销此授权'}))
    await waitFor(() => expect(client.revokeChatPermission).toHaveBeenCalledTimes(1))
    expect(client.revokeChatPermission.mock.calls[0][2]).toMatchObject({kind: 'session_scope', subject_id: source.approval_id, expected_revision: 4})
    expect(container.textContent).toContain('已撤销此授权')
  })

  it('shows an expired request without sending it', async () => {
    const client = fakeClient()
    await act(async () => {
      root.render(<InlineApprovalCard approval={approval({expires_at: '2020-01-01T00:00:00Z'})} client={client} workspace="ws" />)
    })
    expect(screen.getByText('此请求已过期，工具不会执行。')).toBeDefined()
    expect(client.resolveApproval).not.toHaveBeenCalled()
  })
})
