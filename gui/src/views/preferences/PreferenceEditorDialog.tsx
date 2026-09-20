import { useEffect, useRef, useState } from 'react'
import type { Scope } from '../../api/management'
import { PREFERENCE_STATEMENT_MAX, charCount, statementError } from '../../state/preferences'

export interface EditorSubmit {
  scope: Scope
  statement: string
}

/**
 * One form for add and edit. Edit keeps the row's own scope; add picks the
 * scope explicitly and says who it affects. Nothing is written before submit.
 */
export function PreferenceEditorDialog({
  mode,
  scope,
  initialStatement = '',
  busy = false,
  error = '',
  onSubmit,
  onCancel,
}: {
  mode: 'add' | 'edit'
  scope: Scope
  initialStatement?: string
  busy?: boolean
  error?: string
  onSubmit: (value: EditorSubmit) => void
  onCancel: () => void
}) {
  const [statement, setStatement] = useState(initialStatement)
  const [target, setTarget] = useState<Scope>(scope)
  const [localError, setLocalError] = useState('')
  const input = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    input.current?.focus()
  }, [])

  const count = charCount(statement)
  const submit = () => {
    const problem = statementError(statement)
    setLocalError(problem)
    if (problem) {
      input.current?.focus()
      return
    }
    onSubmit({ scope: target, statement: statement.trim() })
  }
  const shown = localError || error
  return (
    <div className="pp-modal" role="presentation">
      <dialog
        className="pp-dialog"
        open
        aria-labelledby="pp-editor-title"
        onCancel={event => { event.preventDefault(); onCancel() }}
        onKeyDown={event => { if (event.key === 'Escape') onCancel() }}
      >
        <h2 id="pp-editor-title">{mode === 'add' ? '新增偏好' : '编辑偏好'}</h2>
        <label className="pp-field">
          <span className="pp-field-title">规则正文</span>
          <textarea
            ref={input}
            className="pp-input"
            aria-label="规则正文"
            placeholder="例如：先给结论，再说明理由。"
            value={statement}
            maxLength={PREFERENCE_STATEMENT_MAX * 2}
            onChange={event => { setStatement(event.target.value); setLocalError('') }}
          />
          <span className="pp-hint" role="status">
            {count} / {PREFERENCE_STATEMENT_MAX} 字符
          </span>
        </label>
        {mode === 'add' ? (
          <fieldset className="pp-scope">
            <legend className="pp-field-title">作用范围</legend>
            {(['workspace', 'global'] as const).map(value => (
              <label key={value} className="pp-radio">
                <input
                  type="radio"
                  name="preference-scope"
                  value={value}
                  checked={target === value}
                  onChange={() => setTarget(value)}
                />
                {value === 'workspace' ? '当前工作区' : '所有工作区（全局）'}
              </label>
            ))}

          </fieldset>
        ) : (
          <p className="pp-hint">
            作用范围：{scope === 'global' ? '所有工作区（全局）' : '当前工作区'}
          </p>
        )}
        {shown && <p className="pp-error" role="alert">{shown}</p>}
        <div className="pp-dialog-actions">
          <button type="button" className="pp-button pp-primary" disabled={busy} onClick={submit}>
            {mode === 'add' ? '添加偏好' : '保存修改'}
          </button>
          <button type="button" className="pp-button" onClick={onCancel}>取消</button>
        </div>
      </dialog>
    </div>
  )
}
