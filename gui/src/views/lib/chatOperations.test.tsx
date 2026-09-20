import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiClient } from '../../api/client'
import { DiagnosticsSection } from '../settings/DiagnosticsSection'
import { chatOperation } from './chatOperations'

describe('chat operations', () => {
  it('routes current task commands without reopening an operations drawer', () => {
    expect(chatOperation('/task retry')).toBeUndefined()
    expect(chatOperation('/task show')?.kind).toBe('artifacts')
    expect(chatOperation('/recovery retry')).toBeUndefined()
  })

  it('keeps data-root scope, preview and confirmation in the settings page', () => {
    const html = renderToStaticMarkup(<DiagnosticsSection client={new ApiClient({ baseUrl: '', token: 'fixture' })} />)
    expect(html).toContain('整个数据根')
    expect(html).toContain('预览孤立文件清理')
    expect(html).toContain('创建完整备份')
    expect(html).not.toContain('确认维护操作')
  })

  it('preserves compaction instructions and scoped download authentication', async () => {
    const calls: { url: string; body: unknown; auth: string | null }[] = []
    const client = new ApiClient({ baseUrl: '', token: 'fixture', workspaceId: 'ws_a', fetchImpl: async (url, init) => {
      calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null, auth: new Headers(init?.headers).get('authorization') })
      return new Response('{}', { status: 200 })
    } })
    await client.chatCommand('ws_a', 'ses_a', 'compact', 'cmd_fixed', '保留工具结果和约束')
    expect(calls[0].body).toEqual({ action: 'compact', command_id: 'cmd_fixed', instructions: '保留工具结果和约束' })
    await client.stateDownload('backup.zip')
    expect(calls[1].url).toBe('/v1/workspaces/ws_a/state-downloads/backup.zip')
    expect(calls[1].auth).toBe('Bearer fixture')
  })
})
