import type { ReactNode } from 'react'
import { buttonClass } from './styles'

export function Card({ title, children }: { title: string; children: ReactNode }) {
  return <article className="space-y-3 rounded-[10px] border border-subtle bg-raised p-4"><h3 className="font-medium">{title}</h3>{children}</article>
}

/** Restricted advanced diagnostics only. Ordinary management pages use FieldList. */
export function Facts({ value }: { value: unknown }) {
  return <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-[8px] bg-base p-3 text-xs text-secondary">{JSON.stringify(value, null, 2)}</pre>
}

export function Pager({ page, next, onChange }: { page: number; next: string | null; onChange: (page: number) => void }) {
  return <div className="flex items-center gap-3 text-xs">
    <button type="button" className={buttonClass} disabled={page === 0} onClick={() => onChange(page - 1)}>上一页</button>
    <span>第 {page + 1} 页</span>
    <button type="button" className={buttonClass} disabled={!next} onClick={() => onChange(page + 1)}>下一页</button>
  </div>
}

export function CursorPager({ hasPrev, hasNext, onPrev, onNext }: {
  hasPrev: boolean; hasNext: boolean; onPrev: () => void; onNext: () => void
}) {
  return <div className="flex items-center gap-3 text-xs">
    <button type="button" className={buttonClass} disabled={!hasPrev} onClick={onPrev}>上一页</button>
    <button type="button" className={buttonClass} disabled={!hasNext} onClick={onNext}>下一页</button>
  </div>
}

export function OriginBadge({ origin }: { origin: 'workspace' | 'global' }) {
  return origin === 'global'
    ? <span className="asset-origin" data-origin="global">来自全局</span>
    : <span className="asset-origin" data-origin="workspace">本项目</span>
}

export function FieldList({ items }: { items: { label: string; value: string | null | undefined }[] }) {
  const visible = items.filter(item => item.value)
  if (visible.length === 0) return null
  return <dl className="asset-fields">
    {visible.map(item => (
      <div className="asset-field" key={item.label}>
        <dt>{item.label}</dt>
        <dd>{item.value}</dd>
      </div>
    ))}
  </dl>
}
