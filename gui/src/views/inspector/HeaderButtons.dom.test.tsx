// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createRef, type RefObject } from 'react'
import { InspectorStore, type InspectorScope } from '../../state/inspector'
import { InspectorHeaderButtons } from './HeaderButtons'

const scope: InspectorScope = { workspaceId: 'ws_1', sessionId: 'se_1' }

describe('InspectorHeaderButtons (chat header icon actions)', () => {
  let container: HTMLDivElement
  let root: Root
  let store: InspectorStore
  let toggleRef: RefObject<HTMLButtonElement | null>

  beforeEach(() => {
    sessionStorage.clear()
    store = new InspectorStore()
    store.setScope(scope)
    toggleRef = createRef<HTMLButtonElement>()
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => {
      root.render(<InspectorHeaderButtons store={store} toggleRef={toggleRef} layout={null} />)
    })
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  it('renders the single panel toggle icon button and no expand button', () => {
    const buttons = screen.getAllByRole('button')
    expect(buttons).toHaveLength(1)
    const button = buttons[0]
    expect(button.className).toContain('icon-button')
    expect(button.getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: '显示执行面板' })).toBeDefined()
    expect(screen.queryByRole('button', { name: '加宽执行面板' })).toBeNull()
    expect(screen.getByRole('button', { name: '显示执行面板' }).title).toContain('显示/隐藏执行面板')
  })

  it('toggles panel visibility through the store and reflects it in aria state', async () => {
    const user = userEvent.setup()
    const toggle = screen.getByRole('button', { name: '显示执行面板' })
    expect(toggleRef.current).toBe(toggle)
    await user.click(toggle)
    expect(store.getState().visible).toBe(true)
    const pressed = screen.getByRole('button', { name: '隐藏执行面板' })
    expect(pressed.getAttribute('aria-pressed')).toBe('true')
    await user.click(pressed)
    expect(store.getState().visible).toBe(false)
    expect(screen.getByRole('button', { name: '显示执行面板' }).getAttribute('aria-pressed')).toBe('false')
  })
})
