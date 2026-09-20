import { useEffect, useMemo, useRef, useState } from 'react'
import CodeMirror, { type ReactCodeMirrorRef } from '@uiw/react-codemirror'
import { indentWithTab } from '@codemirror/commands'
import { EditorView, keymap } from '@codemirror/view'
import { languageForPath } from '../lib/markdownHighlight'

/**
 * The one editable source view: CodeMirror 6 through its React component.
 *
 * Search, indentation, undo/redo, line numbers and selection come from the
 * library. Saving still belongs to the caller (FileBufferStore + DirtyGuard):
 * Ctrl/Cmd+S only asks for the existing save path, and read-only content can
 * never be written through paste, shortcuts or extensions.
 */

export interface SourceEditorProps {
  value: string
  path: string
  readOnly?: boolean
  onChange: (value: string) => void
  /** Ctrl/Cmd+S：显式保存；只读时不触发。 */
  onSave?: () => void
  /** 打开后定位到 1 起算的行号。 */
  line?: number
}

const morrowEditorTheme = EditorView.theme({
  '&': {
    fontFamily: 'var(--font-mono)',
    fontSize: '12px',
    backgroundColor: 'var(--bg-base)',
    color: 'var(--text-primary)',
  },
  '.cm-scroller': {
    fontFamily: 'var(--font-mono)',
    lineHeight: '1.6',
    maxHeight: '28rem',
    overflow: 'auto',
  },
  '.cm-gutters': {
    backgroundColor: 'var(--bg-raised)',
    color: 'var(--text-secondary)',
    borderRight: '1px solid var(--border-subtle)',
  },
  '.cm-activeLine': {
    backgroundColor: 'color-mix(in srgb, var(--text-primary) 4%, transparent)',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'color-mix(in srgb, var(--text-primary) 6%, transparent)',
    color: 'var(--text-primary)',
  },
})

export function SourceEditor({ value, path, readOnly = false, onChange, onSave, line }: SourceEditorProps) {
  const ref = useRef<ReactCodeMirrorRef>(null)
  const [ready, setReady] = useState(false)
  const save = useRef(onSave)
  save.current = onSave
  const change = useRef(onChange)
  change.current = onChange
  // The last value this editor emitted. A parent that resets the body (discard,
  // reload, conflict resolution) is applied immediately; our own echo is not
  // re-dispatched, so fast typing never gets rewound by a lagging prop.
  const emitted = useRef(value)
  const extensions = useMemo(() => {
    const language = languageForPath(path)
    return [
      morrowEditorTheme,
      ...(language === null ? [] : [language]),
      // No soft wrap by default: code keeps its own horizontal scroll.
      EditorView.contentAttributes.of({ 'data-file-editor-input': '' }),
      keymap.of([
        // Both bindings are explicit: jsdom and some Linux browsers report no
        // platform, and the requirement is Ctrl+S and Cmd+S either way.
        {
          key: 'Ctrl-s',
          preventDefault: true,
          run: () => {
            if (!readOnly) save.current?.()
            return true
          },
        },
        {
          key: 'Cmd-s',
          preventDefault: true,
          run: () => {
            if (!readOnly) save.current?.()
            return true
          },
        },
        ...(readOnly ? [] : [indentWithTab]),
      ]),
    ]
  }, [path, readOnly])
  useEffect(() => {
    const view = ref.current?.view
    if (view === undefined) return
    const current = view.state.doc.toString()
    if (value === current || value === emitted.current) return
    view.dispatch({ changes: { from: 0, to: current.length, insert: value } })
    emitted.current = value
  }, [value, ready])
  useEffect(() => {
    if (line === undefined) return
    const view = ref.current?.view
    if (view === undefined) return
    const index = Math.min(Math.max(line, 1), view.state.doc.lines)
    const position = view.state.doc.line(index).from
    view.dispatch({
      selection: { anchor: position },
      effects: EditorView.scrollIntoView(position, { y: 'center' }),
    })
  }, [line, value, ready])
  return (
    <div className="source-editor rounded-[8px] border border-subtle bg-base overflow-hidden" data-source-editor>
      <CodeMirror
        ref={ref}
        value={value}
        editable={!readOnly}
        readOnly={readOnly}
        basicSetup={{
          lineNumbers: true,
          foldGutter: false,
          highlightActiveLine: !readOnly,
          autocompletion: false,
        }}
        extensions={extensions}
        onCreateEditor={() => setReady(true)}
        onChange={next => {
          emitted.current = next
          change.current(next)
        }}
      />
    </div>
  )
}
