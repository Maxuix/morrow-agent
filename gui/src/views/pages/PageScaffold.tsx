import type { ReactNode } from 'react'

export interface PageTab {
  id: string
  label: string
  active: boolean
  onSelect: () => void
}

/**
 * Shared main-area page frame: a 返回对话 affordance (the navigation store's
 * returnTo target) plus optional section tabs. Every non-chat main view uses
 * it so deep pages always offer a way back to the originating chat.
 */
export function PageScaffold({ title, onBack, tabs, children }: {
  title: string
  onBack: () => void
  tabs?: PageTab[]
  children: ReactNode
}) {
  return (
    <div className="page-scaffold" role="main" aria-label={title}>
      <header className="page-scaffold-header">
        <button type="button" className="editor-button page-back" onClick={onBack}>← 返回对话</button>
        <h1 className="page-scaffold-title">{title}</h1>
        {tabs && tabs.length > 0 && (
          <nav className="page-scaffold-tabs" aria-label={`${title}分区`}>
            {tabs.map(tab => (
              <button key={tab.id} type="button" className="editor-button"
                aria-pressed={tab.active} aria-current={tab.active ? 'page' : undefined}
                onClick={tab.onSelect}>{tab.label}</button>
            ))}
          </nav>
        )}
      </header>
      <div className="page-scaffold-body">{children}</div>
    </div>
  )
}

/** Honest placeholder for a section a later package delivers. */
export function PendingSection({ packageLabel, summary, currentEntry }: {
  packageLabel: string
  summary: string
  currentEntry?: string
}) {
  return (
    <section className="page-pending" aria-label="尚未迁移的分区">
      <p className="page-pending-tag">{packageLabel}</p>
      <p>{summary}</p>
      {currentEntry && <p className="text-sm text-secondary">{currentEntry}</p>}
    </section>
  )
}
