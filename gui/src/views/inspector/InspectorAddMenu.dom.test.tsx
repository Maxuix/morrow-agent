// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { InspectorKind } from '../../state/inspector'
import { InspectorAddMenu } from './InspectorAddMenu'

describe('InspectorAddMenu', () => {
  let container: HTMLDivElement
  let root: Root
  let onSelect: ReturnType<typeof vi.fn<(kind: InspectorKind) => void>>

  beforeEach(() => {
    onSelect = vi.fn<(kind: InspectorKind) => void>()
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => {
      root.render(<InspectorAddMenu openKinds={['context', 'workflow']} onSelect={onSelect} />)
    })
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
  })

  const addButton = () => screen.getByRole('button', { name: '添加执行工具' })

  it('opens on Enter/Space, focuses the first enabled item and skips opened kinds', async () => {
    const user = userEvent.setup()
    addButton().focus()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('menu', { name: '添加执行工具' })).toBeTruthy()
    const items = screen.getAllByRole('menuitem')
    expect(items).toHaveLength(6)
    expect((items[0] as HTMLButtonElement).disabled).toBe(true) // 上下文已打开
    expect(document.activeElement).toBe(items[2]) // 首个可用：任务与产物

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).toBeNull()

    addButton().focus()
    await user.keyboard(' ')
    expect(screen.getByRole('menu')).toBeTruthy()
  })

  it('moves with arrow keys across enabled items and selects with Enter', async () => {
    const user = userEvent.setup()
    await user.click(addButton())
    const items = screen.getAllByRole('menuitem')
    await user.keyboard('{ArrowDown}')
    expect(document.activeElement).toBe(items[3]) // 终端输出
    await user.keyboard('{ArrowDown}')
    expect(document.activeElement).toBe(items[4]) // 学习审阅
    await user.keyboard('{ArrowDown}')
    expect(document.activeElement).toBe(items[5]) // 文件
    await user.keyboard('{ArrowDown}') // 环绕回首个可用
    expect(document.activeElement).toBe(items[2])
    await user.keyboard('{ArrowUp}')
    expect(document.activeElement).toBe(items[5])

    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('file')
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('Escape closes the menu and returns focus to the add button', async () => {
    const user = userEvent.setup()
    await user.click(addButton())
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).toBeNull()
    expect(document.activeElement).toBe(addButton())
  })

  it('closes on outside pointer press but not on inside clicks', async () => {
    const user = userEvent.setup()
    await user.click(addButton())
    expect(screen.getByRole('menu')).toBeTruthy()
    await user.click(document.body)
    expect(screen.queryByRole('menu')).toBeNull()

    await user.click(addButton())
    await user.click(screen.getAllByRole('menuitem')[2])
    expect(onSelect).toHaveBeenCalledWith('artifacts')
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('keeps the add button operable when all six kinds are open', async () => {
    act(() => {
      root.render(<InspectorAddMenu
        openKinds={['context', 'workflow', 'artifacts', 'terminal', 'learning', 'file']}
        onSelect={onSelect}
      />)
    })
    const user = userEvent.setup()
    await user.click(addButton())
    expect(screen.getByRole('menu')).toBeTruthy()
    for (const item of screen.getAllByRole('menuitem')) {
      expect((item as HTMLButtonElement).disabled).toBe(true)
    }
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).toBeNull()
  })
})
