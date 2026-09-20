/**
 * A local conversation draft has no Core Session yet.  Keeping a distinct
 * prefix makes it impossible to accidentally send the browser-only id to a
 * Session endpoint (durable ids use the `ses_` prefix).
 */
export const LOCAL_DRAFT_PREFIX = 'draft_'

export function isLocalDraft(sessionId: string | null | undefined): boolean {
  return sessionId?.startsWith(LOCAL_DRAFT_PREFIX) === true
}

/** Generate an id used only to scope the draft while it lives in the UI. */
export function newLocalDraftId(): string {
  const uuid = globalThis.crypto?.randomUUID?.()
  if (uuid) return `${LOCAL_DRAFT_PREFIX}${uuid}`
  return `${LOCAL_DRAFT_PREFIX}${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`
}
