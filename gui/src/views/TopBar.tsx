import type { ConnectionState } from '../state/sync'
import { CONNECTION_LABELS } from './lib/labels'

export type Theme = 'light' | 'dark' | 'system'

const THEME_LABELS: Record<Theme, string> = {
  system: '跟随系统',
  light: '浅色',
  dark: '深色',
}

const NEXT_THEME: Record<Theme, Theme> = {
  system: 'light',
  light: 'dark',
  dark: 'system',
}

/**
 * Top bar: product name, workspace id, connection state, pending approval
 * count, theme toggle. Only real data — preference/context summaries land in
 * a later subplan when those endpoints exist.
 */
export function TopBar({
  workspaceId,
  connection,
  pendingApprovals,
  theme,
  onThemeChange,
  activeView,
  onViewChange,
}: {
  workspaceId: string | null
  connection: ConnectionState
  pendingApprovals: number
  theme: Theme
  onThemeChange: (theme: Theme) => void
  activeView: 'observe' | 'edit' | 'evaluate'
  onViewChange: (view: 'observe' | 'edit' | 'evaluate') => void
}) {
  return (
    <header className="flex items-center gap-4 border-b border-subtle bg-raised px-4 py-2.5">
      <h1 className="font-serif text-lg font-semibold">Morrow</h1>
      <span className="font-mono text-xs text-secondary">
        {workspaceId ?? '…'}
      </span>
      <nav className="flex rounded-[8px] border border-subtle bg-base p-0.5" aria-label="工作区视图">
        <button type="button" className={`rounded-[6px] px-2.5 py-1 text-xs ${activeView === 'observe' ? 'bg-raised text-accent' : 'text-secondary'}`} onClick={() => onViewChange('observe')}>观察</button>
        <button type="button" className={`rounded-[6px] px-2.5 py-1 text-xs ${activeView === 'edit' ? 'bg-raised text-accent' : 'text-secondary'}`} onClick={() => onViewChange('edit')}>编辑器</button>
        <button type="button" className={`rounded-[6px] px-2.5 py-1 text-xs ${activeView === 'evaluate' ? 'bg-raised text-accent' : 'text-secondary'}`} onClick={() => onViewChange('evaluate')}>反馈与评估</button>
      </nav>
      <span className="ml-auto flex items-center gap-4 text-sm">
        <span className="inline-flex items-center gap-1.5 text-xs text-secondary">
          <span
            aria-hidden="true"
            className={`inline-block size-2 rounded-full ${
              connection === 'live'
                ? 'bg-completed'
                : connection === 'offline' || connection === 'unauthorized'
                  ? 'bg-failed'
                  : 'bg-blocked'
            }`}
          />
          {CONNECTION_LABELS[connection]}
        </span>
        <span aria-live="polite" className="text-xs text-secondary">
          待审批 {pendingApprovals}
        </span>
        <button
          type="button"
          onClick={() => onThemeChange(NEXT_THEME[theme])}
          className="rounded-[8px] border border-subtle bg-base px-2.5 py-1 text-xs text-primary transition-colors duration-150 hover:border-accent"
        >
          主题：{THEME_LABELS[theme]}
        </button>
      </span>
    </header>
  )
}
