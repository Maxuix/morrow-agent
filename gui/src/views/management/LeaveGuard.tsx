import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import type { DirtyGuard } from '../../state/navigation'

/**
 * Shared leave-protection dialog. Forms that can persist call onSave; forms
 * that only have in-memory edits omit it and show 放弃修改 / 留在此页.
 * `onKeepDraft` is for pages that already stash a local draft.
 */
export function LeaveGuardDialog({
  open,
  title,
  description,
  saveLabel = '保存后离开',
  discardLabel = '放弃修改',
  stayLabel = '留在此页',
  keepDraftLabel,
  onSave,
  onDiscard,
  onStay,
  onKeepDraft,
}: {
  open: boolean
  title: string
  description: string
  saveLabel?: string
  discardLabel?: string
  stayLabel?: string
  keepDraftLabel?: string
  onSave?: () => void
  onDiscard: () => void
  onStay: () => void
  onKeepDraft?: () => void
}): ReactNode {
  if (!open) return null
  return (
    <dialog className="pp-guard" open aria-labelledby="leave-guard-title">
      <h2 id="leave-guard-title">{title}</h2>
      <p>{description}</p>
      <div className="pp-guard-actions">
        {onSave && (
          <button type="button" className="pp-button pp-primary" onClick={onSave}>{saveLabel}</button>
        )}
        {onKeepDraft && (
          <button type="button" className="pp-button" onClick={onKeepDraft}>{keepDraftLabel ?? '保留草稿并离开'}</button>
        )}
        <button type="button" className="pp-button pp-danger" onClick={onDiscard}>{discardLabel}</button>
        <button type="button" className="pp-button" onClick={onStay}>{stayLabel}</button>
      </div>
    </dialog>
  )
}

export function useLeaveGuard(
  registerGuard: ((guard: DirtyGuard) => () => void) | undefined,
  isDirty: boolean,
  confirmLeave: () => Promise<boolean>,
): void {
  const dirtyRef = useRef(isDirty)
  const confirmRef = useRef(confirmLeave)
  useEffect(() => { dirtyRef.current = isDirty }, [isDirty])
  useEffect(() => { confirmRef.current = confirmLeave }, [confirmLeave])
  useEffect(() => {
    if (!registerGuard) return
    return registerGuard({
      isDirty: () => dirtyRef.current,
      confirmLeave: () => dirtyRef.current ? confirmRef.current() : Promise.resolve(true),
    })
  }, [registerGuard])
}

/** Promise-based confirmLeave that opens the shared dialog. */
export function useLeavePrompt(): {
  open: boolean
  confirmLeave: () => Promise<boolean>
  settle: (ok: boolean) => void
} {
  const [open, setOpen] = useState(false)
  const resolver = useRef<((ok: boolean) => void) | null>(null)
  const confirmLeave = useCallback(() => {
    setOpen(true)
    return new Promise<boolean>(resolve => { resolver.current = resolve })
  }, [])
  const settle = useCallback((ok: boolean) => {
    setOpen(false)
    const resolve = resolver.current
    resolver.current = null
    resolve?.(ok)
  }, [])
  return { open, confirmLeave, settle }
}
