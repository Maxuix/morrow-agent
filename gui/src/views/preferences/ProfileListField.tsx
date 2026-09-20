import { useState } from 'react'
import { PROFILE_ITEM_MAX, addItem, removeItem, updateItem } from '../../state/preferenceProfile'

export interface AddKeyEvent {
  key: string
  shiftKey?: boolean
  keyCode?: number
  nativeEvent: { isComposing?: boolean }
}

/** Enter adds one item; an IME composition (or Shift+Enter) never submits. */
export function shouldAddItem(event: AddKeyEvent): boolean {
  if (event.key !== 'Enter' || event.shiftKey === true) return false
  return event.nativeEvent.isComposing !== true && event.keyCode !== 229
}

export function ProfileListField({
  label,
  items,
  onChange,
  error = '',
  multiline = false,
  placeholder = '',
  addLabel,
}: {
  label: string
  items: string[]
  onChange: (items: string[]) => void
  error?: string
  multiline?: boolean
  placeholder?: string
  addLabel?: string
}) {
  const [pending, setPending] = useState('')
  const [localError, setLocalError] = useState('')
  const [editing, setEditing] = useState<{ index: number; value: string } | null>(null)

  const add = () => {
    const result = addItem(items, pending)
    setLocalError(result.error)
    if (result.error) return
    if (result.items.length !== items.length) onChange(result.items)
    setPending('')
  }
  const commitEdit = () => {
    if (editing === null) return
    const result = updateItem(items, editing.index, editing.value)
    setLocalError(result.error)
    if (result.error) return
    onChange(result.items)
    setEditing(null)
  }
  const shown = error || localError
  return (
    <section className="pp-field">
      <h3 className="pp-field-title">{label}</h3>
      {items.length > 0 && (
        <ul className="pp-list">
          {items.map((item, index) =>
            editing !== null && editing.index === index ? (
              <li key={`${index}:edit`} className="pp-list-row pp-list-row-editing">
                {multiline ? (
                  <textarea
                    className="pp-input"
                    aria-label={`编辑${label} ${index + 1}`}
                    rows={2}
                    value={editing.value}
                    maxLength={PROFILE_ITEM_MAX}
                    onChange={event => setEditing({ index, value: event.target.value })}
                  />
                ) : (
                  <input
                    className="pp-input"
                    aria-label={`编辑${label} ${index + 1}`}
                    value={editing.value}
                    maxLength={PROFILE_ITEM_MAX}
                    onChange={event => setEditing({ index, value: event.target.value })}
                  />
                )}
                <div className="pp-list-actions">
                  <button type="button" className="pp-button pp-primary" onClick={commitEdit}>保存条目</button>
                  <button type="button" className="pp-button" onClick={() => { setEditing(null); setLocalError('') }}>取消</button>
                </div>
              </li>
            ) : (
              <li key={`${index}:${item}`} className="pp-list-row">
                <span className="pp-list-text">{item}</span>
                <div className="pp-list-actions">
                  <button
                    type="button"
                    className="pp-button"
                    aria-label={`编辑${label} ${item}`}
                    onClick={() => { setEditing({ index, value: item }); setLocalError('') }}
                  >
                    编辑
                  </button>
                  <button
                    type="button"
                    className="pp-button"
                    aria-label={`移除${label} ${item}`}
                    onClick={() => { onChange(removeItem(items, index)); setLocalError('') }}
                  >
                    移除
                  </button>
                </div>
              </li>
            ),
          )}
        </ul>
      )}

      <div className={`pp-add-row ${multiline ? 'pp-add-multiline' : ''}`}>
        {multiline ? (
          <textarea
            className="pp-input"
            aria-label={`新增${label}`}
            placeholder={placeholder}
            rows={2}
            value={pending}
            maxLength={PROFILE_ITEM_MAX}
            onChange={event => setPending(event.target.value)}
          />
        ) : (
          <input
            className="pp-input"
            aria-label={`新增${label}`}
            placeholder={placeholder}
            value={pending}
            maxLength={PROFILE_ITEM_MAX}
            onChange={event => setPending(event.target.value)}
            onKeyDown={event => {
              if (!shouldAddItem(event)) return
              event.preventDefault()
              add()
            }}
          />
        )}
        <button type="button" className="pp-button" disabled={!pending.trim()} onClick={add}>
          {addLabel ?? '添加'}
        </button>
      </div>
      {shown && <p className="pp-error" role="alert">{shown}</p>}
    </section>
  )
}
