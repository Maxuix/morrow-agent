// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../api/client'
import type { RecoveryStatus } from '../api/chat'
import { RecoveryBanner } from './RecoveryBanner'

const response = (value: unknown = {}) =>
  new Response(JSON.stringify(value), {status: 200, headers: {'content-type': 'application/json'}})

function client(calls: {url: string; body: unknown}[]) {
  return new ApiClient({
    baseUrl: '',
    token: '',
    fetchImpl: async (url, init) => {
      calls.push({url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null})
      return response()
    },
  })
}

const base = (overrides: Partial<RecoveryStatus>): RecoveryStatus => ({
  protocol_version: 1,
  display_state: 'resumable',
  owner: 'chat',
  safe_summary: '上一轮对话已中断；可以从已保存的检查点继续。',
  allowed_actions: ['resume', 'new_session'],
  opaque_target: 'arun_private',
  opaque_target_kind: 'agent_run',
  decision_required: false,
  revision: 7,
  checks: [],
  ...overrides,
})

afterEach(() => cleanup())

describe('RecoveryBanner', () => {
  it('routes chat resume once and keeps opaque targets out of the display', async () => {
    const calls: {url: string; body: unknown}[] = []
    const onChanged = vi.fn()
    render(
      <RecoveryBanner
        client={client(calls)}
        workspace="ws_a"
        session="ses_a"
        status={base({})}
        onChanged={onChanged}
        onNewSession={vi.fn()}
      />,
    )

    expect(screen.getByRole('status').textContent).toContain('上一轮对话已中断')
    expect(screen.queryByText('arun_private')).toBeNull()
    fireEvent.click(screen.getByRole('button', {name: '继续上次对话'}))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0]).toEqual(expect.objectContaining({
      url: '/v1/workspaces/ws_a/sessions/ses_a/recovery',
      body: expect.objectContaining({action: 'resume', target_agent_run_id: 'arun_private'}),
    }))
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it('keeps the old recovery view when creating a new session fails', async () => {
    const onNewSession = vi.fn().mockRejectedValue(new Error('创建失败'))
    render(
      <RecoveryBanner
        client={client([])}
        workspace="ws_a"
        session="ses_a"
        status={base({})}
        onChanged={vi.fn()}
        onNewSession={onNewSession}
      />,
    )

    fireEvent.click(screen.getByRole('button', {name: '新建对话'}))
    await waitFor(() => expect(onNewSession).toHaveBeenCalledOnce())
    expect(screen.getByRole('status').textContent).toContain('上一轮对话已中断')
    expect(screen.getByRole('alert').textContent).toContain('创建失败')
  })

  it('reuses one command identity after an uncertain recovery response', async () => {
    const calls: {url: string; body: unknown}[] = []
    let attempt = 0
    const recoveryClient = new ApiClient({
      baseUrl: '',
      token: '',
      fetchImpl: async (url, init) => {
        calls.push({url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null})
        attempt += 1
        return attempt === 1
          ? new Response(JSON.stringify({error: {code: 'busy', message: '结果待核对'}}), {
              status: 503,
              headers: {'content-type': 'application/json'},
            })
          : response()
      },
    })
    render(
      <RecoveryBanner
        client={recoveryClient}
        workspace="ws_a"
        session="ses_a"
        status={base({
          display_state: 'unknown_side_effect',
          safe_summary: '副作用结果未知，必须先核对。',
          allowed_actions: ['acknowledge'],
          opaque_target: 'rrp_private',
          opaque_target_kind: 'report',
          decision_required: true,
          checks: [{
            summary: '有一项工具执行需要处理：请根据保存的证据核对。',
            classification: 'outcome_unknown',
            allowed_actions: ['acknowledge'],
            opaque_target: 'rrp_private',
            opaque_item: 'rit_private',
          }],
        })}
        onChanged={vi.fn()}
        onNewSession={vi.fn()}
      />,
    )

    const acknowledge = () => fireEvent.click(screen.getByRole('button', {name: '确认这项记录'}))
    acknowledge()
    await waitFor(() => expect(calls).toHaveLength(1))
    acknowledge()
    await waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[0]?.body).toEqual(calls[1]?.body)
  })

  it('routes unknown side effects to a report decision without rendering report IDs', async () => {
    const calls: {url: string; body: unknown}[] = []
    render(
      <RecoveryBanner
        client={client(calls)}
        workspace="ws_a"
        session="ses_a"
        status={base({
          display_state: 'unknown_side_effect',
          safe_summary: '副作用结果未知，必须先核对。',
          allowed_actions: ['acknowledge', 'new_session'],
          opaque_target: 'rrp_private',
          decision_required: true,
          checks: [{
            summary: '有一项工具执行需要处理：请根据保存的证据核对。',
            classification: 'outcome_unknown',
            allowed_actions: ['acknowledge'],
            opaque_target: 'rrp_private',
            opaque_item: 'rit_private',
          }],
        })}
        onChanged={vi.fn()}
        onNewSession={vi.fn()}
      />,
    )

    expect(screen.queryByText('rrp_private')).toBeNull()
    expect(screen.queryByText('rit_private')).toBeNull()
    fireEvent.click(screen.getByRole('button', {name: '确认这项记录'}))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0]).toEqual(expect.objectContaining({
      url: '/v1/workspaces/ws_a/sessions/ses_a/recovery',
      body: expect.objectContaining({
        action: 'resolve',
        report_id: 'rrp_private',
        item_id: 'rit_private',
        resolution: 'acknowledge',
      }),
    }))
  })

  it('resolves a report-level resume once without sending a second resume', async () => {
    const calls: {url: string; body: unknown}[] = []
    const onChanged = vi.fn()
    render(
      <RecoveryBanner
        client={client(calls)}
        workspace="ws_a"
        session="ses_a"
        status={base({
          display_state: 'resumable',
          allowed_actions: ['resume'],
          opaque_target: 'rrp_private',
          opaque_target_kind: 'report',
          checks: [],
        })}
        onChanged={onChanged}
        onNewSession={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', {name: '继续上次对话'}))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0]).toEqual(expect.objectContaining({
      url: '/v1/workspaces/ws_a/sessions/ses_a/recovery',
      body: expect.objectContaining({
        action: 'resolve',
        report_id: 'rrp_private',
        resolution: 'resume',
      }),
    }))
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it('uses the planning and Workflow owner endpoints', async () => {
    const planningCalls: {url: string; body: unknown}[] = []
    const planning = render(
      <RecoveryBanner
        client={client(planningCalls)}
        workspace="ws_a"
        session="ses_a"
        status={base({owner: 'planning', allowed_actions: ['resume_generation'], opaque_target: 'wop_private'})}
        onChanged={vi.fn()}
        onNewSession={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', {name: '继续生成计划'}))
    await waitFor(() => expect(planningCalls).toHaveLength(1))
    expect(planningCalls[0].url).toBe('/v1/workspaces/ws_a/sessions/ses_a/task-plan/operations/wop_private/resume')

    planning.unmount()
    const workflowCalls: {url: string; body: unknown}[] = []
    render(
      <RecoveryBanner
        client={client(workflowCalls)}
        workspace="ws_a"
        session="ses_a"
        status={base({owner: 'workflow', allowed_actions: ['continue'], opaque_target: 'wrun_private', display_state: 'paused'})}
        onChanged={vi.fn()}
        onNewSession={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', {name: '继续执行'}))
    await waitFor(() => expect(workflowCalls).toHaveLength(1))
    expect(workflowCalls[0].url).toBe('/v1/workflow-runs/wrun_private/resume')
  })
})
