/** Honest empty state — never a fake surface, just the fact and a hint. */
export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 px-6 py-10 text-center">
      <p className="text-sm text-secondary">{title}</p>
      {hint !== undefined && <p className="text-xs text-secondary/80">{hint}</p>}
    </div>
  )
}
