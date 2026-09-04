import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { ApiClient } from '../api/client'
import type { ReplanViewWire } from '../api/types'
import { ReplanReview } from './ReplanPanel'

const view = {
  proposal: { proposal_id: 'rprop_one', status: 'pending', risk_level: 'elevated',
    risk_reasons: ['cap_or_deadline_relaxed'], disposition_reason: 'approval_required',
    auto_applied: false, child_run_id: null, decided_at: null, row_version: 1,
    policy_id: 'default', policy_revision: 2 },
  before: { name: 'Original' }, after: { name: 'Changed' },
  diff: { changed_node_ids: ['review'], added_node_ids: [], removed_node_ids: [] },
} as unknown as ReplanViewWire

describe('Replan proposal review', () => {
  it('shows exact changes, escalation reasons and disabled approval during recovery', () => {
    const html = renderToStaticMarkup(<ReplanReview view={view} paused={false} busy={false} decide={() => {}} />)
    expect(html).toContain('Original')
    expect(html).toContain('Changed')
    expect(html).toContain('cap_or_deadline_relaxed')
    expect(html).toContain('须完成暂停与恢复')
    expect(html).toMatch(/disabled=""[^>]*>批准并应用/)
    expect(html).toContain('拒绝')
  })
  it('keeps automatic decisions visible with child lineage and no decision buttons', () => {
    const applied = { ...view, proposal: { ...view.proposal, status: 'applied' as const, risk_level: 'low' as const,
      auto_applied: true, child_run_id: 'wrun_child', disposition_reason: 'low_risk_policy' } }
    const html = renderToStaticMarkup(<ReplanReview view={applied} paused={false} busy={false} decide={() => {}} />)
    expect(html).toContain('已自动应用')
    expect(html).toContain('wrun_child')
    expect(html).not.toContain('批准并应用')
  })
  it('sends exact proposal OCC and stable command identity through authenticated API', async () => {
    const calls: { url: string; body: unknown; auth: string | null }[] = []
    const client = new ApiClient({ baseUrl: '', token: 'test', fetchImpl: async (url, init) => {
      calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null, auth: new Headers(init?.headers).get('authorization') })
      return new Response(JSON.stringify(init?.method === 'GET' ? { proposals: [view] } : { result: view }), { status: 200 })
    } })
    expect(await client.listReplans('wrun_one')).toEqual([view])
    await client.decideReplan('rprop_one', false, 7, 'cmd_same')
    expect(calls[1]).toEqual({ url: '/v1/replans/rprop_one/decide', body: { approved: false, expected_row_version: 7, command_id: 'cmd_same' }, auth: 'Bearer test' })
  })
})
