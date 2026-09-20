import type { WorkflowDraftDiagnosticWire } from '../../api/types'
import { diagnosticSummary, presentDiagnostic, uniqueDiagnostics } from './diagnostics'

export function WorkflowDiagnostics({
  diagnostics,
  onSelectNode,
  onSelectEdge,
}: {
  diagnostics: WorkflowDraftDiagnosticWire[]
  onSelectNode?: (nodeId: string) => void
  onSelectEdge?: (edgeId: string) => void
}) {
  const unique = uniqueDiagnostics(diagnostics)
  if (unique.length === 0) return null
  const ordered = [...unique].sort((left, right) => Number(right.severity === 'error') - Number(left.severity === 'error'))
  return (
    <section className="workflow-diagnostics-summary" aria-label="工作流校验结果">
      <div className="workflow-canvas-heading">
        <div>
          <h2>检查摘要</h2>

        </div>
        <span className="workflow-summary-hint" role="status" aria-live="polite">{diagnosticSummary(diagnostics)}</span>
      </div>
      <ul>
        {ordered.map((item, index) => {
          const view = presentDiagnostic(item)
          const key = `${item.code}:${item.node_id ?? ''}:${item.edge_id ?? ''}:${index}`
          return (
            <li key={key} className={item.severity === 'error' ? 'is-error' : 'is-warning'}>
              <div className="workflow-diagnostic-heading">
                <strong>{view.severityLabel} · {view.targetLabel}</strong>
                {item.edge_id !== null && onSelectEdge !== undefined && <button type="button" className="workflow-link-button" onClick={() => onSelectEdge(item.edge_id!)}>查看连接</button>}
                {item.edge_id === null && item.node_id !== null && onSelectNode !== undefined && <button type="button" className="workflow-link-button" onClick={() => onSelectNode(item.node_id!)}>查看步骤</button>}
              </div>
              <p>{view.title}</p>
              <p className="workflow-field-help">{view.guidance}</p>
              <details className="workflow-technical-details">
                <summary>Core 说明 · {item.code}</summary>
                <p>{view.detail}</p>
              </details>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
