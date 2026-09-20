// @vitest-environment jsdom
import { useRef } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { InspectorStore, type InspectorScope } from '../../state/inspector'
import { ChatInspector } from './ChatInspector'

const scope: InspectorScope = { workspaceId: 'ws_1', sessionId: 'se_1' }

function Harness({ store }: { store: InspectorStore }) {
  const toggleRef = useRef<HTMLButtonElement>(null)
  return (
    <>
      <button ref={toggleRef} onClick={() => store.toggleVisible()}>面板显隐</button>
      <ChatInspector store={store} returnFocusRef={toggleRef} />
    </>
  )
}

describe('ChatInspector', () => {
  let container: HTMLDivElement
  let root: Root
  let store: InspectorStore

  beforeEach(() => {
    sessionStorage.clear()
    store = new InspectorStore()
    store.setScope(scope)
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => {
      root.render(<Harness store={store} />)
    })
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
  })

  const aside = () => container.querySelector('aside') as HTMLElement

  it('stays hidden by default in a new session and shows six cards when revealed', () => {
    expect(aside().hidden).toBe(true)
    act(() => store.setVisible(true))
    expect(aside().hidden).toBe(false)
    const cards = container.querySelectorAll('.inspector-empty-card')
    expect(cards).toHaveLength(6)
    expect(screen.getByRole('button', { name: /学习审阅/ })).toBeDefined()
  })

  it('opens a tab from an empty-state card and focuses it', async () => {
    const user = userEvent.setup()
    act(() => store.setVisible(true))
    await user.click(screen.getByRole('button', { name: /终端输出/ }))
    expect(store.getState().activeTab).toBe('terminal')
    expect(container.querySelector('[data-inspector-kind="terminal"]')).not.toBeNull()
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: '终端输出' }))
  })

  it('keeps every opened tab panel mounted with hidden/inert on the inactive ones', () => {
    act(() => {
      store.openInspectorTab('context')
      store.openInspectorTab('learning')
    })
    const panels = container.querySelectorAll('[role="tabpanel"]')
    expect(panels).toHaveLength(2)
    const contextPanel = container.querySelector('[data-inspector-kind="context"]')!.closest('[role="tabpanel"]') as HTMLElement
    const learningPanel = container.querySelector('[data-inspector-kind="learning"]')!.closest('[role="tabpanel"]') as HTMLElement
    expect(contextPanel.hidden).toBe(true)
    expect(contextPanel.getAttribute('inert')).not.toBeNull()
    expect(learningPanel.hidden).toBe(false)
    expect(learningPanel.getAttribute('inert')).toBeNull()

    act(() => store.activateTab('context'))
    expect(contextPanel.hidden).toBe(false)
    expect(learningPanel.hidden).toBe(true)
  })

  it('keeps the panel open with the empty state after the last tab closes', () => {
    act(() => {
      store.openInspectorTab('context')
      store.closeTab('context')
    })
    expect(aside().hidden).toBe(false)
    expect(container.querySelectorAll('.inspector-empty-card')).toHaveLength(6)
    expect(store.getState().activeTab).toBeNull()
  })

  it('returns focus to the trigger button when hidden from inside the panel', async () => {
    const user = userEvent.setup()
    act(() => store.openInspectorTab('context'))
    expect(aside().contains(document.activeElement)).toBe(true)
    const toggle = screen.getByRole('button', { name: '面板显隐' })
    await user.click(toggle)
    expect(aside().hidden).toBe(true)
    expect(document.activeElement).toBe(toggle)
  })

  it('surfaces a storage-corruption notice without blocking the shell', () => {
    sessionStorage.setItem('morrow.inspector.v1.ws_1.se_1', '{oops')
    const corrupt = new InspectorStore()
    act(() => {
      corrupt.setScope(scope)
      root.render(<Harness store={corrupt} />)
    })
    expect(screen.getByRole('status', { hidden: true }).textContent).toContain('损坏')
    act(() => corrupt.setVisible(true))
    expect(container.querySelectorAll('.inspector-empty-card')).toHaveLength(6)
  })
})
