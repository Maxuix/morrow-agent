import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import type { SessionWire } from '../api/types'
import { SidebarStore } from '../state/sidebar'
import { SessionRow, sessionTitle } from './Sidebar'

const store = new SidebarStore(new ApiClient({baseUrl: '', token: ''}), {storage: null})

const base: SessionWire = {
  session_id: 'ses_0f2a6c9b1d4e4877a3c5f0e9d2b84a17',
  lifecycle: 'active',
  health: 'ok',
  created_at: '2026-09-08T10:20:30Z',
  updated_at: '2026-09-08T10:20:30Z',
  current_task_run_id: null,
  parent_session_id: null,
}

describe('sidebar session rows', () => {
  it('renders one truncating line anchored by workspace and session id', () => {
    const html = renderToStaticMarkup(
      <SessionRow workspaceId="ws_a" session={{...base, metadata: {title: '重构侧栏工作区布局', pinned: false, revision: 2}}}
        current store={store} onOpen={() => {}}/>,
    )
    expect(html).toContain(`data-session-row="ws_a:${base.session_id}"`)
    expect(html).toContain('aria-current="true"')
    expect(html).toContain('is-current')
    expect(html).toContain('重构侧栏工作区布局')
    expect(html).toContain('sess-title')
  })

  it('marks pin, running and unhealthy states beyond color alone', () => {
    const html = renderToStaticMarkup(
      <SessionRow workspaceId="ws_a" session={{
        ...base,
        metadata: {title: '定位当前策略', pinned: true, revision: 1},
        current_task_run_id: 'task_1',
      }} current={false} store={store} onOpen={() => {}}/>,
    )
    expect(html).toContain('已置顶')
    expect(html).toContain('aria-label="运行中"')
    expect(html).toContain('dot-run')
  })

  it('dims archived rows and explains attention state in the tooltip', () => {
    const html = renderToStaticMarkup(
      <SessionRow workspaceId="ws_a" session={{...base, lifecycle: 'archived', health: 'needs_recovery'}}
        current={false} store={store} onOpen={() => {}}/>,
    )
    expect(html).toContain('is-archived')
    expect(html).toContain('已归档')
    expect(html).toContain('需要处理')
    expect(html).toContain('恢复对话')
  })

  it('uses a readable default for untitled sessions', () => {
    expect(sessionTitle(base)).toBe('新对话')
    expect(sessionTitle({...base, metadata: {title: '有名字', pinned: false, revision: 1}})).toBe('有名字')
  })
})
