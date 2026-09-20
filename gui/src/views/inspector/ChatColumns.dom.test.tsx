// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { InspectorStore, type InspectorScope } from '../../state/inspector'
import { ChatColumns } from './ChatColumns'

const scope: InspectorScope = { workspaceId: 'ws_1', sessionId: 'se_1' }

class MockResizeObserver {
  static instances: MockResizeObserver[] = []
  private readonly callback: ResizeObserverCallback
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
    MockResizeObserver.instances.push(this)
  }
  observe() {}
  unobserve() {}
  disconnect() {}
  trigger() {
    this.callback([], this as unknown as ResizeObserver)
  }
}

function setWidth(element: Element, width: number) {
  Object.defineProperty(element, 'clientWidth', { configurable: true, value: width })
  act(() => MockResizeObserver.instances.at(-1)?.trigger())
}

function Harness({ store }: { store: InspectorStore }) {
  return (
    <section className="chat-session-workspace">
      <ChatColumns store={store} center={() => <input aria-label="草稿" defaultValue="未发送的草稿" />} />
    </section>
  )
}

describe('ChatColumns compact mode', () => {
  let container: HTMLDivElement
  let root: Root
  let store: InspectorStore
  const originalRO = globalThis.ResizeObserver

  beforeEach(() => {
    sessionStorage.clear()
    MockResizeObserver.instances = []
    ;(globalThis as { ResizeObserver?: unknown }).ResizeObserver = MockResizeObserver
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
    act(() => root.unmount())
    container.remove()
    ;(globalThis as { ResizeObserver?: unknown }).ResizeObserver = originalRO
  })

  const section = () => container.querySelector('section') as HTMLElement
  const center = () => container.querySelector('.chat-center') as HTMLElement
  const aside = () => container.querySelector('aside') as HTMLElement

  it('keeps side-by-side layout at and above the 480+400+divider threshold', () => {
    act(() => store.openInspectorTab('context'))
    setWidth(section(), 881)
    expect(aside().dataset.layoutMode).toBe('regular')
    expect(center().hidden).toBe(false)
    setWidth(section(), 1440)
    expect(aside().dataset.layoutMode).toBe('regular')
  })

  it('replaces (not overlays) the chat body below the threshold, kept alive hidden/inert', () => {
    act(() => store.openInspectorTab('context'))
    setWidth(section(), 880)
    expect(aside().dataset.layoutMode).toBe('compact')
    // Replacement: the chat body leaves the layout but stays mounted.
    expect(center().hidden).toBe(true)
    expect(center().getAttribute('inert')).not.toBeNull()
    expect(screen.getByLabelText('草稿', { exact: true }).closest('.chat-center')).not.toBeNull()
    // No overlay element is introduced; the panel is a sibling, not a cover.
    expect(aside().parentElement).toBe(section())
    expect(document.fullscreenElement ?? null).toBeNull()
  })

  it('does not replace the chat body while the panel is hidden at compact widths', () => {
    setWidth(section(), 500)
    expect(aside().hidden).toBe(true)
    expect(center().hidden).toBe(false)
  })

  it('restores the chat body with its state via 返回对话', async () => {
    const user = userEvent.setup()
    act(() => store.openInspectorTab('context'))
    const draft = screen.getByLabelText('草稿') as HTMLInputElement
    await user.clear(draft)
    await user.type(draft, '改成新草稿')
    setWidth(section(), 700)
    expect(center().hidden).toBe(true)
    await user.click(screen.getByRole('button', { name: /返回对话/ }))
    expect(store.getState().visible).toBe(false)
    expect(center().hidden).toBe(false)
    expect(center().getAttribute('inert')).toBeNull()
    expect((screen.getByLabelText('草稿') as HTMLInputElement).value).toBe('改成新草稿')
    // Widening again keeps tabs and visibility state intact.
    setWidth(section(), 1440)
    expect(store.getState().tabs).toEqual(['context'])
  })

  it('clamps the expanded width instead of overflowing, without touching fullscreen', () => {
    act(() => {
      store.openInspectorTab('context')
      store.setSize('expanded')
    })
    setWidth(section(), 1000)
    expect(aside().dataset.layoutMode).toBe('regular')
    expect(aside().style.width).toBe('519px') // 1000 - 480 聊天 - 1 分隔线
    expect(document.fullscreenElement ?? null).toBeNull()
    setWidth(section(), 1440)
    expect(aside().style.width).toBe('620px')
  })

  it('renders divider resizer handle in regular mode and allows keyboard adjustments', async () => {
    const user = userEvent.setup()
    act(() => store.openInspectorTab('context'))
    setWidth(section(), 1200)

    const resizer = container.querySelector('.inspector-resizer') as HTMLElement
    expect(resizer).not.toBeNull()
    expect(resizer.getAttribute('role')).toBe('separator')
    expect(resizer.getAttribute('aria-orientation')).toBe('vertical')

    // Initial default width is 400px
    expect(aside().style.width).toBe('400px')

    // ArrowLeft widens the inspector by 10px
    resizer.focus()
    await user.keyboard('{ArrowLeft}')
    expect(store.getState().width).toBe(410)
    expect(aside().style.width).toBe('410px')

    // ArrowRight shrinks the inspector by 10px
    await user.keyboard('{ArrowRight}')
    expect(store.getState().width).toBe(400)
    expect(aside().style.width).toBe('400px')

    // Enter or double-click resets to 400px default
    await user.keyboard('{ArrowLeft}{ArrowLeft}')
    expect(store.getState().width).toBe(420)
    await user.keyboard('{Enter}')
    expect(store.getState().width).toBe(400)

    // Double click resets width
    act(() => store.setWidth(450))
    expect(aside().style.width).toBe('450px')
    await user.dblClick(resizer)
    expect(store.getState().width).toBe(400)
  })

  it('hides divider resizer in compact mode', () => {
    act(() => store.openInspectorTab('context'))
    setWidth(section(), 880) // compact
    expect(container.querySelector('.inspector-resizer')).toBeNull()
  })

  it('updates panel width via pointer dragging on resizer', () => {
    act(() => store.openInspectorTab('context'))
    setWidth(section(), 1200)

    const resizer = container.querySelector('.inspector-resizer') as HTMLElement
    expect(resizer).not.toBeNull()

    // Simulate pointerdown
    act(() => {
      resizer.dispatchEvent(
        new PointerEvent('pointerdown', {
          bubbles: true,
          button: 0,
          clientX: 800,
          pointerId: 1,
        }),
      )
    })

    // Drag left by 50px (clientX: 800 -> 750) => inspector widens by 50px
    act(() => {
      window.dispatchEvent(
        new PointerEvent('pointermove', {
          bubbles: true,
          clientX: 750,
          pointerId: 1,
        }),
      )
    })
    expect(store.getState().width).toBe(450)
    expect(aside().style.width).toBe('450px')

    // Release pointer
    act(() => {
      window.dispatchEvent(
        new PointerEvent('pointerup', {
          bubbles: true,
          clientX: 750,
          pointerId: 1,
        }),
      )
    })
    expect(store.getState().width).toBe(450)
  })
})
