// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import type { AppLocation } from '../state/navigation'
import { SidebarStore } from '../state/sidebar'
import { WorkspaceSidebar } from './Sidebar'

/** Empty-workspace backend; loadAll settles without further calls. */
function makeStore() {
  const fetchImpl: typeof fetch = async () =>
    new Response(JSON.stringify({ items: [], revision: 0 }), { status: 200 })
  return new SidebarStore(new ApiClient({ baseUrl: '', token: '', fetchImpl }), { storage: null })
}

describe('WorkspaceSidebar main navigation', () => {
  let container: HTMLDivElement
  let root: Root
  let navigated: AppLocation[]

  const render = async (location: AppLocation = { kind: 'chat' }) => {
    navigated = []
    const store = makeStore()
    await act(async () => {
      root.render(
        <WorkspaceSidebar
          store={store}
          activeWorkspaceId={null}
          activeSessionId={null}
          cursor={0}
          connection="live"
          pendingApprovals={0}
          location={location}
          onNavigate={target => navigated.push(target)}
          onSelectSession={() => {}}
          onCreateSession={() => Promise.resolve()}
          onCollapse={() => {}}
          onSwitchWorkspace={() => {}}
        />,
      )
    })
  }

  beforeEach(() => {
    // jsdom lacks the dialog API used by WorkspaceManagerDialog.
    if (typeof HTMLDialogElement.prototype.showModal !== 'function') {
      HTMLDialogElement.prototype.showModal = function showModal() {}
      HTMLDialogElement.prototype.close = function close() {}
    }
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  it('renders the flat workbench navigation buttons above the workspace tree', async () => {
    await render()
    expect(screen.getByRole('navigation', { name: '快捷导航' })).toBeDefined()
    expect(screen.getByRole('button', { name: '新对话' })).toBeDefined()
    expect(screen.getByRole('button', { name: '项目知识与偏好' })).toBeDefined()
    expect(screen.getByRole('button', { name: '技能与 MCP 工具' })).toBeDefined()
    expect(screen.getByRole('button', { name: '工作流定义' })).toBeDefined()
    expect(screen.queryByRole('button', { name: '文件编辑器' })).toBeNull()
    expect(screen.queryByRole('button', { name: '运行观察' })).toBeNull()
    // Sub-items are no longer in the sidebar.
    expect(screen.queryByRole('button', { name: '项目画像' })).toBeNull()
    expect(screen.queryByRole('button', { name: '行为偏好' })).toBeNull()
    expect(screen.queryByRole('button', { name: '知识库' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Skills' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'MCP 服务' })).toBeNull()
    expect(screen.queryByRole('button', { name: '项目文件' })).toBeNull()
    // Tree stays below the nav.
    expect(screen.getByRole('navigation', { name: '工作区与会话' })).toBeDefined()
  })

  it('routes every entry through onNavigate with the target location', async () => {
    const user = userEvent.setup()
    await render()
    const cases: [string, AppLocation][] = [
      ['项目知识与偏好', { kind: 'knowledge', section: 'profile' }],
      ['技能与 MCP 工具', { kind: 'tools', section: 'skills' }],
      ['工作流定义', { kind: 'editor', target: 'workflows' }],
      ['设置', { kind: 'settings', section: 'providers' }],
    ]
    for (const [name, expected] of cases) {
      await user.click(screen.getByRole('button', { name }))
      expect(navigated.at(-1)).toEqual(expected)
    }
    expect(navigated).toHaveLength(cases.length)
  })

  it('marks the current section with aria-current', async () => {
    await render({ kind: 'knowledge', section: 'preferences' })
    expect(screen.getByRole('button', { name: '项目知识与偏好' }).getAttribute('aria-current')).toBe('page')
    expect(screen.getByRole('button', { name: '技能与 MCP 工具' }).getAttribute('aria-current')).toBeNull()
    expect(screen.getByRole('button', { name: '工作流定义' }).getAttribute('aria-current')).toBeNull()
  })

  it('keeps drafts in the overflow popover and no longer opens theme or maintenance', async () => {
    const user = userEvent.setup()
    navigated = []
    const store = makeStore()
    await act(async () => {
      root.render(
        <WorkspaceSidebar
          store={store}
          activeWorkspaceId={null}
          activeSessionId={null}
          cursor={0}
          connection="live"
          pendingApprovals={0}
          location={{ kind: 'chat' }}
          onNavigate={target => navigated.push(target)}
          onSelectSession={() => {}}
          onCreateSession={() => Promise.resolve()}
          onCollapse={() => {}}
          onSwitchWorkspace={() => {}}
        />,
      )
    })
    await user.click(screen.getByTitle('更多选项（草稿管理）'))
    expect(screen.queryByRole('group', { name: '外观主题' })).toBeNull()
    expect(screen.queryByRole('button', { name: '状态与维护' })).toBeNull()
    expect(screen.queryByRole('button', { name: '模型设置' })).toBeNull()
    expect(screen.getByRole('button', { name: '草稿管理' })).toBeDefined()
  })
})
