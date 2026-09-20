import { useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { WorkflowDefinitionViewWire } from '../api/types'

/**
 * The single Chat entry: an ordinary conversation plus workflow modes chosen
 * from the composer ＋ menu. 工作流编排 → the message becomes the task
 * objective for server-side task-plan generation (slice 5.2); the published
 * workflow picker runs an exact released revision. Both render as a compact
 * tag inside the composer, config-heavy surfaces stay in the side panel.
 */
export interface WorkflowChoice { workflow_definition_id: string; workflow_revision_id: string }

/** Anchored to the composer tag row; opens upward and closes on outside press. */
export function PublishedWorkflowPicker({client, disabled, label, onChoice, onClose}: {
  client: ApiClient; disabled: boolean; label: string
  onChoice: (choice: WorkflowChoice | null, label: string) => void
  onClose: () => void
}) {
  const anchor = useRef<HTMLSpanElement | null>(null)
  const [definitions, setDefinitions] = useState<WorkflowDefinitionViewWire[]>([])
  const [more, setMore] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    void client.listWorkflowDefinitions().then(page => {
      if (alive) {setDefinitions(page); setMore(page.length === 100)}
    }, (e: Error) => {if (alive) setError(e.message)})
    return () => {alive = false}
  }, [client])
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (anchor.current && event.target instanceof Node && !anchor.current.contains(event.target)) onClose()
    }
    const onKeyDown = (event: KeyboardEvent) => {if (event.key === 'Escape') onClose()}
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {document.removeEventListener('pointerdown', onPointerDown); document.removeEventListener('keydown', onKeyDown)}
  }, [onClose])
  const choose = (item: WorkflowDefinitionViewWire) => {
    onChoice(item.head ? {workflow_definition_id: item.workflow_definition_id, workflow_revision_id: item.head.workflow_revision_id} : null, item.source?.name ?? item.workflow_definition_id)
  }
  const available = definitions.filter(item => item.head?.enabled && !item.revoked)
  return <span className="composer-chip-wrap" ref={anchor}>
    <button className="composer-chip is-open" aria-expanded="true">{label || '选择已发布工作流'}</button>
    <div className="composer-popover composer-picker-popover" role="dialog" aria-label="选择已发布工作流">
      {available.map(item => <button key={item.workflow_definition_id} className="composer-menu-item" disabled={disabled}
        onClick={() => choose(item)}>
        <span>{item.source?.name ?? item.workflow_definition_id}</span>
        <span className="menu-note">修订 {item.head?.workflow_revision_id.slice(-8)}</span>
      </button>)}
      {!available.length && !error && <p className="text-xs text-secondary">正在载入已发布工作流…</p>}
      {!available.length && !error && <button className="editor-button" onClick={onClose}>取消</button>}
      {more && <button className="editor-button" disabled={disabled}
        onClick={() => void client.listWorkflowDefinitions(definitions.at(-1)?.workflow_definition_id).then(page => {
          setDefinitions(old => [...old, ...page]); setMore(page.length === 100)
        }, (e: Error) => setError(e.message))}>更多已发布工作流</button>}
      {error && <p role="alert" className="text-failed">{error}</p>}

    </div>
  </span>
}
