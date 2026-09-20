// @vitest-environment jsdom
function editorTextOf(container: HTMLElement): string {
  const dom = container.querySelector('[data-file-editor-input]')
  const view = dom === null ? null : EditorView.findFromDOM(dom as HTMLElement)
  return view?.state.doc.toString() ?? ''
}
import { useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EditorView } from '@codemirror/view'
import { SourceEditor } from './SourceEditor'

function Harness({
  initial,
  path = 'src/app.ts',
  readOnly = false,
  onSave,
}: {
  initial: string
  path?: string
  readOnly?: boolean
  onSave?: () => void
}) {
  const [value, setValue] = useState(initial)
  return (
    <SourceEditor value={value} path={path} readOnly={readOnly} onChange={setValue} onSave={onSave} />
  )
}

describe('SourceEditor', () => {
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

  const input = () => container.querySelector('[data-file-editor-input]') as HTMLElement
  const render = async (ui: React.ReactElement) => {
    await act(async () => {
      root.render(ui)
    })
    await waitFor(() => expect(input()).toBeTruthy())
  }

  it('shows the source, its language and a line-number gutter', async () => {
    await render(<Harness initial={'const a = 1\nconst b = 2\n'} />)
    expect(input().textContent).toContain('const a = 1')
    expect(input().textContent).toContain('const b = 2')
    const gutter = container.querySelector('.cm-gutters')?.textContent ?? ''
    expect(gutter).toContain('1')
    expect(gutter).toContain('3')
    // The TypeScript parser is wired: keywords carry the syntax-highlight class.
    expect(container.querySelector('[class*="ͼ"]')).toBeTruthy()
  })

  it('reports every document change through onChange', async () => {
    await render(<Harness initial="abc" />)
    // jsdom performs no contenteditable default action, so the change is
    // dispatched through the live CodeMirror view; IME and typing fidelity
    // stay a browser-level check.
    const view = EditorView.findFromDOM(input())
    expect(view).toBeTruthy()
    act(() => {
      view!.dispatch({ changes: { from: 0, insert: 'x' } })
    })
    await waitFor(() => expect(input().textContent).toContain('xabc'))
  })

  it('saves on Ctrl+S and Cmd+S without touching the text', async () => {
    const onSave = vi.fn()
    await render(<Harness initial="abc" onSave={onSave} />)
    fireEvent.keyDown(input(), { key: 's', ctrlKey: true })
    fireEvent.keyDown(input(), { key: 's', metaKey: true })
    expect(onSave).toHaveBeenCalledTimes(2)
    expect(input().textContent).toContain('abc')
  })

it('applies an external value change as a new document', async () => {
    function Swap() {
      const [value, setValue] = useState('one')
      return (
        <>
          <button type="button" onClick={() => setValue('two')}>
            外部替换
          </button>
          <SourceEditor value={value} path="src/app.ts" onChange={setValue} />
        </>
      )
    }
    await render(<Swap />)
    expect(editorTextOf(container)).toBe('one')
    await (await import('@testing-library/user-event')).default.setup().click(screen.getByRole('button', { name: '外部替换' }))
    await waitFor(() => expect(editorTextOf(container)).toBe('two'))
  })

  it('blocks editing and saving when read-only', async () => {
    const onSave = vi.fn()
    await render(<Harness initial="abc" readOnly onSave={onSave} />)
    expect(input().getAttribute('contenteditable')).toBe('false')
    fireEvent.keyDown(input(), { key: 's', ctrlKey: true })
    expect(onSave).not.toHaveBeenCalled()
  })
})
