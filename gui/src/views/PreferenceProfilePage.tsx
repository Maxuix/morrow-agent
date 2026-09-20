import { useCallback, useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { DirtyGuard, KnowledgeFocus } from '../state/navigation'
import { useProfileDocument } from '../state/preferenceProfile'
import { usePreferenceDocuments } from '../state/preferences'
import { LeaveGuardDialog, useLeaveGuard, useLeavePrompt } from './management/LeaveGuard'
import { useWorkspaceName } from './management/hooks'
import { buttonClass } from './management/styles'
import { PreferenceList } from './preferences/PreferenceList'
import { ProfileEditor } from './preferences/ProfileEditor'

export type PreferenceFocus = 'profile' | 'preferences'

export function PreferenceProfilePage({
  client,
  workspaceId,
  focus = 'profile',
  anchor,
  embedded = false,
  connected = true,
  registerGuard,
}: {
  client: ApiClient
  workspaceId: string
  focus?: PreferenceFocus
  /** In-section landing from slash commands (`edit`/`reset`/`add`/…). */
  anchor?: KnowledgeFocus
  /** When true, the page scaffold already provides the title; render one column. */
  embedded?: boolean
  /** Read-only while disconnected: browsing and local editing stay possible. */
  connected?: boolean
  registerGuard?: (guard: DirtyGuard) => () => void
}) {
  const workspaceName = useWorkspaceName(client, workspaceId)
  const profile = useProfileDocument(client, workspaceId)
  const preferences = usePreferenceDocuments(client, workspaceId)
  const [collapsed, setCollapsed] = useState(false)
  const dirty = profile.dirty
  const { open: guardOpen, confirmLeave, settle } = useLeavePrompt()

  // Entering from a preference command collapses an untouched Profile so the
  // right column gets the first screen; a draft always stays visible. The
  // matching column then takes focus so the command lands where it promised.
  const landed = useRef<PreferenceFocus | null>(null)
  useEffect(() => {
    setCollapsed(focus === 'preferences' && !dirty)
  }, [focus, dirty])
  useEffect(() => {
    if (landed.current === focus) return
    // The target column may still be loading; land once it exists.
    const target = document.querySelector<HTMLElement>(
      focus === 'preferences' ? '[aria-label="搜索规则"]' : '[aria-label="项目名称"]',
    )
    if (target === null) return
    landed.current = focus
    target.focus()
  })

  const promptLeave = useCallback(() => {
    if (!dirty) return Promise.resolve(true)
    return confirmLeave()
  }, [confirmLeave, dirty])
  useLeaveGuard(registerGuard, dirty, promptLeave)

  const profileColumn = (
    <section className="pp-column" aria-label="项目画像">
      {!embedded && (
        <button
          type="button"
          className="pp-collapse"
          aria-expanded={!collapsed}
          onClick={() => setCollapsed(value => !value)}
        >
          {collapsed ? '展开项目画像' : '收起项目画像'}{dirty ? '（有未保存修改）' : ''}
        </button>
      )}
      {(embedded || !collapsed || dirty) && <ProfileEditor model={profile} workspaceName={workspaceName} connected={connected} expandReset={anchor === 'reset'} />}
    </section>
  )
  const preferenceColumn = (
    <section className="pp-column" aria-label="行为偏好">
      <div className="pp-card">
        <PreferenceList
          client={client}
          documents={preferences.documents}
          rows={preferences.rows}
          write={preferences.write}
          mutate={preferences.mutate}
          connected={connected}
          onRefresh={preferences.refresh}
          anchor={anchor}
          registerGuard={registerGuard}
        />
      </div>
    </section>
  )

  return (
    <div className="pp-page" aria-labelledby={embedded ? undefined : 'pp-title'}>
      {!embedded && (
        <header className="pp-header">
          <div>
            <h1 id="pp-title" className="pp-title">偏好与项目画像</h1>

          </div>
          <div className="pp-header-side">
            <span className="pp-hint">当前工作区：{workspaceName}</span>
            <button type="button" className={buttonClass} onClick={() => { profile.refresh(); preferences.refresh() }}>
              刷新
            </button>
          </div>
        </header>
      )}

      <div className="pp-content">
        {embedded && (
          <div className="pp-header-side" style={{ marginBottom: 8 }}>
            <span className="pp-hint">当前工作区：{workspaceName}</span>
            <button type="button" className={buttonClass} onClick={() => { profile.refresh(); preferences.refresh() }}>
              刷新
            </button>
          </div>
        )}
        {!connected && <p className="pp-notice" role="status">连接中断：可以继续查看和整理草稿，提交已禁用；恢复后先刷新再提交。</p>}

        {embedded ? (
          <div className="pp-grid" style={{ gridTemplateColumns: 'minmax(0, 1fr)' }}>
            {focus === 'preferences' ? preferenceColumn : profileColumn}
          </div>
        ) : (
          <div className="pp-grid">
            {profileColumn}
            {preferenceColumn}
          </div>
        )}
      </div>

      <LeaveGuardDialog
        open={guardOpen}
        title="项目画像有未保存修改"
        description="离开前可以保存、放弃，或留在此页继续编辑。"
        onSave={() => { void profile.save().then(ok => settle(ok)) }}
        onDiscard={() => { profile.discard(); settle(true) }}
        onStay={() => settle(false)}
      />
    </div>
  )
}
