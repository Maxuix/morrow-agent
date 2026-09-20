// @vitest-environment jsdom
import { useReducer } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  createInspectorState,
  inspectorReducer,
  type InspectorAction,
  type InspectorKind,
} from '../../state/inspector'
import { InspectorTabBar } from './InspectorTabBar'

const INITIAL: InspectorKind[] = ['context', 'workflow', 'terminal']

function Harness() {
  const [state, dispatch] = useReducer(
    inspectorReducer,
    INITIAL,
    kinds => kinds.reduce((s, kind) => inspectorReducer(s, { type: 'open', kind }), createInspectorState()),
  )
  return (
    <InspectorTabBar
      tabs={state.tabs}
      activeTab={state.activeTab}
      onActivate={kind => dispatch({ type: 'activate', kind } as InspectorAction)}
      onClose={kind => dispatch({ type: 'close', kind } as InspectorAction)}
      onAdd={kind => dispatch({ type: 'open', kind } as InspectorAction)}
    />
  )
}

describe('InspectorTabBar', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => {
      root.render(<Harness />)
    })
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
  })

  it('uses tablist/tab semantics with roving tabindex', () => {
    expect(screen.getByRole('tablist', { name: '执行工具' })).toBeTruthy()
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map(tab => tab.getAttribute('aria-selected'))).toEqual(['false', 'false', 'true'])
    expect(tabs.map(tab => tab.tabIndex)).toEqual([-1, -1, 0])
    // 关闭按钮独立于 tab 内容（不能 button 嵌套 button）。
    const close = screen.getByRole('button', { name: '关闭终端输出' })
    expect(close.closest('[role="tab"]')?.tagName).toBe('DIV')
    expect(close.closest('button')).toBe(close)
  })

  it('switches tabs with arrows and Home/End, moving focus with activation', async () => {
    const user = userEvent.setup()
    const terminal = screen.getByRole('tab', { name: '终端输出' })
    terminal.focus()
    await user.keyboard('{ArrowLeft}')
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: '计划与工作流' }))
    expect(screen.getByRole('tab', { name: '计划与工作流' }).getAttribute('aria-selected')).toBe('true')

    await user.keyboard('{ArrowRight}{ArrowRight}')
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: '上下文' }))

    await user.keyboard('{End}')
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: '终端输出' }))
    await user.keyboard('{Home}')
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: '上下文' }))
    expect(screen.getByRole('tab', { name: '上下文' }).getAttribute('aria-selected')).toBe('true')
  })

  it('activates a tab on click', async () => {
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: '上下文' }))
    expect(screen.getByRole('tab', { name: '上下文' }).getAttribute('aria-selected')).toBe('true')
  })

  it('Delete closes the focused active tab and focuses the right neighbor', async () => {
    const user = userEvent.setup()
    screen.getByRole('tab', { name: '计划与工作流' }).focus()
    await user.keyboard('{ArrowLeft}') // focus + activate 上下文（最左）
    await user.keyboard('{Delete}')
    expect(screen.queryByRole('tab', { name: '上下文' })).toBeNull()
    const replacement = screen.getByRole('tab', { name: '计划与工作流' })
    expect(replacement.getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(replacement)
  })

  it('closing the last tab moves focus to the add button', async () => {
    const user = userEvent.setup()
    for (const name of ['上下文', '计划与工作流', '终端输出']) {
      screen.getByRole('tab', { name }).focus()
      await user.keyboard('{Delete}')
    }
    expect(screen.queryAllByRole('tab')).toEqual([])
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '添加执行工具' }))
  })

  it('closing an inactive tab via its close button keeps the active tab', async () => {
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '关闭上下文' }))
    expect(screen.queryByRole('tab', { name: '上下文' })).toBeNull()
    expect(screen.getByRole('tab', { name: '终端输出' }).getAttribute('aria-selected')).toBe('true')
  })
})
