// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../api/client'
import { WorkspaceKnowledgePage } from '../knowledge/WorkspaceKnowledgePage'
import { WorkspaceToolsPage } from '../tools/WorkspaceToolsPage'
import { WorkspaceEditorPage } from '../editor/WorkspaceEditorPage'
import { SettingsPage } from '../settings/SettingsPage'

/** Management queries never settle: managers stay in their loading state. */
function pendingClient() {
  const fetchImpl: typeof fetch = () => new Promise<Response>(() => {})
  return new ApiClient({ baseUrl: '', token: '', fetchImpl })
}

describe('main-area page containers', () => {
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

  it('knowledge page shows the knowledge library and wires tabs/back', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    const onBack = vi.fn()
    await act(async () => {
      root.render(
        <WorkspaceKnowledgePage client={pendingClient()} workspaceId="ws_1" section="knowledge"
          connected onNavigate={onNavigate} onBack={onBack} />,
      )
    })
    expect(screen.getByRole('main', { name: '项目知识与偏好' })).toBeDefined()
    expect(screen.getByRole('region', { name: '已入库知识' })).toBeDefined()
    expect(screen.getByRole('button', { name: '知识库' }).getAttribute('aria-current')).toBe('page')
    await user.click(screen.getByRole('button', { name: '行为偏好' }))
    expect(onNavigate).toHaveBeenCalledWith('preferences')
    await user.click(screen.getByRole('button', { name: /返回对话/ }))
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('tools page renders the skills manager shell and switches sections', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    await act(async () => {
      root.render(
        <WorkspaceToolsPage client={pendingClient()} section="skills" connected
          onNavigate={onNavigate} onBack={() => {}} />,
      )
    })
    expect(screen.getByRole('main', { name: '技能与 MCP 工具' })).toBeDefined()
    expect(screen.getByLabelText('查看范围')).toBeDefined()
    expect(screen.getByRole('button', { name: '全局安装' })).toBeDefined()
    await user.click(screen.getByRole('button', { name: 'MCP 服务' }))
    expect(onNavigate).toHaveBeenCalledWith('mcp')
  })

  it('tools page renders the MCP manager for the mcp section', async () => {
    await act(async () => {
      root.render(
        <WorkspaceToolsPage client={pendingClient()} section="mcp" connected
          onNavigate={() => {}} onBack={() => {}} />,
      )
    })
    expect(screen.getByRole('button', { name: 'MCP 服务' }).getAttribute('aria-current')).toBe('page')
    expect(screen.queryByText(/打开此页不会启动进程/)).toBeNull()
    expect(screen.queryByLabelText('MCP查询ID')).toBeNull()
  })

  it('editor page shows workflow definition editor without project files tab or source tree', async () => {
    await act(async () => {
      root.render(
        <WorkspaceEditorPage client={pendingClient()} onBack={() => {}} />,
      )
    })
    expect(screen.getByRole('main', { name: '工作流定义' })).toBeDefined()
    expect(screen.getByRole('heading', { level: 1, name: '工作流定义' })).toBeDefined()
    expect(screen.queryByRole('button', { name: '项目文件' })).toBeNull()
    expect(screen.queryByText('项目源码')).toBeNull()
  })

  it('settings page renders four tabs and ProviderSettings without package-4 placeholders', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    const onBack = vi.fn()
    await act(async () => {
      root.render(
        <SettingsPage client={pendingClient()} section="appearance" onNavigate={onNavigate} onBack={onBack}
          theme="system" onThemeChange={() => {}} />,
      )
    })
    expect(screen.queryByText('此分区由包 4 承接')).toBeNull()
    expect(screen.getByRole('region', { name: '外观' })).toBeDefined()
    await user.click(screen.getByRole('button', { name: '诊断与维护' }))
    expect(onNavigate).toHaveBeenCalledWith('diagnostics')
    await act(async () => {
      root.render(
        <SettingsPage client={pendingClient()} section="providers" onNavigate={onNavigate} onBack={onBack}
          theme="system" onThemeChange={() => {}} />,
      )
    })
    expect(screen.getByRole('region', { name: '模型设置' })).toBeDefined()
    await user.click(screen.getByRole('button', { name: /返回对话/ }))
    expect(onBack).toHaveBeenCalled()
  })
})
