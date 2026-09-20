import { useEffect, useMemo, useRef, useState } from 'react'
import CodeMirror, { type ReactCodeMirrorRef } from '@uiw/react-codemirror'
import { EditorView } from '@codemirror/view'
import { languageForPath } from '../lib/markdownHighlight'

/**
 * Read-only CodeMirror view for one historical or current text body.
 *
 * Editing is never possible here: history stays immutable even under paste,
 * shortcut or extension input. Line numbers, search and selection come from
 * the same CodeMirror 6 packages the editor uses, so a past snapshot and the
 * current file share one renderer instead of two.
 */
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

export function SourceView({
  value,
  path,
  line,
  label,
}: {
  value: string
  path?: string | null
  line?: number
  label?: string
}) {
  const ref = useRef<ReactCodeMirrorRef>(null)
  const [ready, setReady] = useState(false)
  const extensions = useMemo(() => {
    const language = path === undefined || path === null ? null : languageForPath(path)
    return [
      morrowEditorTheme,
      ...(language === null ? [] : [language]),
    ]
  }, [path])
  useEffect(() => {
    const target = line ?? label === undefined ? line : undefined
    if (target === undefined) return
    const view = ref.current?.view
    if (view === undefined) return
    const index = Math.min(Math.max(target, 1), view.state.doc.lines)
    const position = view.state.doc.line(index).from
    view.dispatch({
      selection: { anchor: position },
      effects: EditorView.scrollIntoView(position, { y: 'center' }),
    })
  }, [line, label, value, ready])
  return (
    <div className="source-view min-h-0 min-w-0 flex-1 overflow-hidden" data-source-view>
      <CodeMirror
        ref={ref}
        onCreateEditor={() => setReady(true)}
        className="h-full"
        height="100%"
        value={value}
        editable={false}
        readOnly
        basicSetup={{
          lineNumbers: true,
          foldGutter: false,
          highlightActiveLine: false,
          autocompletion: false,
          allowMultipleSelections: false,
        }}
        extensions={extensions}
      />
    </div>
  )
}
