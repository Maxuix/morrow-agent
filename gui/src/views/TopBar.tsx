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
}: {
  workspaceId: string | null
  connection: ConnectionState
  pendingApprovals: number
  theme: Theme
  onThemeChange: (theme: Theme) => void
}) {
  return (
    <header className="flex items-center gap-4 border-b border-subtle bg-raised px-4 py-2.5">
      <h1 className="font-serif text-lg font-semibold">Morrow</h1>
      <span className="font-mono text-xs text-secondary">
        {workspaceId ?? '…'}
      </span>
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
