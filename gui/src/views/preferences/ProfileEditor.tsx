import { useEffect, useRef, useState } from 'react'
import {
  PROFILE_NAME_MAX,
  PROFILE_SUMMARY_MAX,
  emptyProfile,
  useProfileDocument,
  validateProfile,
  type Profile,
  type ProfileField,
} from '../../state/preferenceProfile'
import { ProfileListField } from './ProfileListField'

type ProfileDocumentState = ReturnType<typeof useProfileDocument>

const STATUS_TONE: Record<string, 'status' | 'alert'> = {
  saved: 'status',
  unchanged: 'status',
  'refresh-failed': 'status',
  conflict: 'alert',
  error: 'alert',
}

/**
 * One complete draft of the workspace Profile. Nothing is written until the
 * single save button submits the whole snapshot; discard restores the baseline.
 */
export function ProfileEditor({ model, connected = true, expandReset = false }: {
  model: ProfileDocumentState
  workspaceName: string
  connected?: boolean
  expandReset?: boolean
}) {
  const { snapshot, queryError, draft, base, status, message, serverAhead, dirty } = model
  const { edit, discard, save, clear, reload, refresh } = model
  const [confirmClear, setConfirmClear] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<ReturnType<typeof validateProfile>['errors']>({})
  const nameRef = useRef<HTMLInputElement>(null)
  const summaryRef = useRef<HTMLTextAreaElement>(null)
  const listRefs = useRef<Partial<Record<ProfileField, HTMLDivElement | null>>>({})

  useEffect(() => {
    setFieldErrors({})
    setConfirmClear(false)
  }, [snapshot?.revision, base])

  if (queryError && snapshot === null) {
    return (
      <article className="pp-card">
        <h2 className="pp-card-title">项目画像</h2>
        <p className="pp-error" role="alert">{queryError}</p>
        <button type="button" className="pp-button" onClick={refresh}>重新读取</button>
      </article>
    )
  }
  if (snapshot === null) {
    return (
      <article className="pp-card">
        <h2 className="pp-card-title">项目画像</h2>
        <p className="pp-hint" role="status">正在读取项目画像…</p>
      </article>
    )
  }

  const value: Profile = draft ?? snapshot.profile ?? emptyProfile()
  const set = (patch: Partial<Profile>) => {
    edit({ ...value, ...patch })
    setFieldErrors({})
  }
  const focusField = (field: ProfileField) => {
    if (field === 'name') nameRef.current?.focus()
    else if (field === 'summary') summaryRef.current?.focus()
    else listRefs.current[field]?.querySelector<HTMLElement>('textarea, input')?.focus()
  }
  const onSave = async () => {
    const { errors, first } = validateProfile(value)
    setFieldErrors(errors)
    if (first !== null) {
      focusField(first)
      return
    }
    await save()
  }
  const tone = STATUS_TONE[status]
  return (
    <article className="pp-card">
      <header className="pp-card-head">
        <h2 className="pp-card-title">项目画像</h2>

      </header>

      <label className="pp-field">
        <span className="pp-field-title">项目名称</span>
        <input
          ref={nameRef}
          className="pp-input"
          aria-label="项目名称"
          value={value.name}
          maxLength={PROFILE_NAME_MAX}
          aria-invalid={fieldErrors.name !== undefined}
          onChange={event => set({ name: event.target.value })}
        />

        {fieldErrors.name && <span className="pp-error" role="alert">{fieldErrors.name}</span>}
      </label>

      <label className="pp-field">
        <span className="pp-field-title">项目概述（选填）</span>
        <textarea
          ref={summaryRef}
          className="pp-input"
          aria-label="项目概述"
          rows={2}
          value={value.summary ?? ''}
          maxLength={PROFILE_SUMMARY_MAX}
          aria-invalid={fieldErrors.summary !== undefined}
          onChange={event => set({ summary: event.target.value })}
        />

        {fieldErrors.summary && <span className="pp-error" role="alert">{fieldErrors.summary}</span>}
      </label>

      {(
        [
          ['tech_stack', '技术栈', false, '例如 Python 3.12', '添加技术栈'],
          ['goals', '项目目标', true, '每条一个目标', '添加目标'],
          ['constraints', '项目约束', true, '每条一个约束', '添加约束'],
          ['conventions', '项目约定', true, '每条一个约定', '添加约定'],
        ] as const
      ).map(([field, label, multiline, placeholder, addLabel]) => (
        <div key={field} ref={node => { listRefs.current[field] = node }}>
          <ProfileListField
            label={label}
            items={value[field]}
            error={fieldErrors[field]}
            multiline={multiline}
            placeholder={placeholder}
            addLabel={addLabel}
            onChange={items => set({ [field]: items } as Partial<Profile>)}
          />
        </div>
      ))}

      {serverAhead && (
        <p className="pp-notice" role="status">
          服务端有更新；你的草稿未被覆盖。可先重新读取比较最新值。
        </p>
      )}

      <footer className="pp-savebar">
        <span className={`pp-hint ${dirty ? 'is-dirty' : ''}`}>{dirty ? '有未保存修改' : ''}</span>
        <div className="pp-savebar-actions">
          <button type="button" className="pp-button" disabled={!dirty || status === 'saving'} onClick={discard}>
            放弃修改
          </button>
          <button type="button" className="pp-button pp-primary" disabled={!dirty || status === 'saving' || !connected} onClick={() => void onSave()}>
            保存项目画像
          </button>
        </div>
      </footer>
      {status === 'saving' && <p className="pp-hint" role="status">正在保存…</p>}
      {tone !== undefined && message !== '' && (
        <p className={tone === 'alert' ? 'pp-error' : 'pp-hint'} role={tone}>
          {message}
          {status === 'refresh-failed' && (
            <button type="button" className="pp-button" onClick={() => void reload()}>重新读取</button>
          )}
          {status === 'conflict' && (
            <button type="button" className="pp-button" onClick={() => void reload()}>采用最新值</button>
          )}
        </p>
      )}

      <details className="pp-more" open={expandReset || undefined} data-anchor="reset">
        <summary>更多操作</summary>
        {!confirmClear ? (
          <button type="button" className="pp-button" disabled={snapshot.profile === null || status === 'saving' || !connected} onClick={() => setConfirmClear(true)}>
            清空项目画像
          </button>
        ) : (
          <div className="pp-confirm">
            <p>清空当前工作区的项目画像？偏好规则会保留。</p>
            {dirty && <p className="pp-hint">这也会丢弃你当前的草稿。</p>}
            <button type="button" className="pp-button pp-danger" onClick={() => { setConfirmClear(false); void clear() }}>
              确认清空
            </button>
            <button type="button" className="pp-button" onClick={() => setConfirmClear(false)}>取消</button>
          </div>
        )}
      </details>
    </article>
  )
}
