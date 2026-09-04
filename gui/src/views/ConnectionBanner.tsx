import type { ConnectionState } from '../state/sync'
import { CONNECTION_LABELS } from './lib/labels'

/**
 * Connection honesty: `reconnecting` / `offline` are visible and textual,
 * never just a color. The banner is a polite live region so screen readers
 * announce transitions.
 */
export function ConnectionBanner({
  connection,
  onRetry,
}: {
  connection: ConnectionState
  onRetry: () => void
}) {
  return (
    <div aria-live="polite" role="status">
      {connection === 'reconnecting' && (
        <div className="border-b border-subtle bg-raised px-4 py-2 text-sm text-secondary">
          {CONNECTION_LABELS.reconnecting}
        </div>
      )}
      {connection === 'offline' && (
        <div className="flex items-center gap-3 border-b border-subtle bg-raised px-4 py-2 text-sm">
          <span className="text-failed">{CONNECTION_LABELS.offline}</span>
          <span className="text-secondary">与服务端的连接已断开，显示的数据可能不是最新。</span>
          <button
            type="button"
            onClick={onRetry}
            className="rounded-[8px] border border-subtle bg-base px-3 py-1 text-sm text-primary transition-colors duration-150 hover:border-accent"
          >
            重试
          </button>
        </div>
      )}
      {connection === 'unauthorized' && (
        <div className="border-b border-failed bg-raised px-4 py-2 text-sm" role="alert">
          <span className="font-medium text-failed">会话令牌无效或已过期。</span>{' '}
          <span className="text-secondary">
            请在终端重新运行 <span className="font-mono text-primary">morrow gui</span>{' '}
            并使用新打开的页面。
          </span>
        </div>
      )}
    </div>
  )
}
