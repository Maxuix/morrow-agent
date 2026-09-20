import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { AgentDefinitionVersionWire, WorkflowDraftDiagnosticWire, WorkflowStatus } from '../api/types'
import { WorkflowEditor } from './WorkflowEditor'
import { newSingleNodeWorkflow } from './lib/editor'

const source = newSingleNodeWorkflow('repair', 'Repair', {
  source: { definition_id: 'helper', access_mode_ceiling: 'read' },
  version_id: 'adev_helper', content_hash: 'a'.repeat(64),
} as AgentDefinitionVersionWire)
source.required_outputs = [{ node_id: 'deleted', output_slot: 'result' }]
source.edges = [{ from_node_id: 'agent', to_node_id: 'deleted' }]
const diagnostics: WorkflowDraftDiagnosticWire[] = [
  { severity: 'error', code: 'edge_endpoint_invalid', node_id: null, edge_id: 'agent->deleted', message: 'Missing edge endpoint' },
  { severity: 'error', code: 'structure_invalid', node_id: 'deleted', edge_id: null, message: 'Missing required output' },
]
const render = (disabled = false, nodeStatuses: Record<string, WorkflowStatus> = {}) => renderToStaticMarkup(
  <WorkflowEditor source={source} diagnostics={diagnostics} agents={[]} contracts={[]} disabled={disabled} nodeStatuses={nodeStatuses} onChange={() => {}} />,
)

describe('Workflow draft repair', () => {
  it('shows global and deleted-node diagnostics without selecting a missing canvas node', () => {
    const html = render()
    expect(html).toContain('Missing edge endpoint')
    expect(html).toContain('Missing required output')
    expect(html).toContain('节点已不存在')
    expect(html).toContain('aria-label="删除连接 agent → deleted"')
  })

  it('keeps the invalid final output selected until the user explicitly repairs it', () => {
    const html = render()
    expect(html).toContain('aria-invalid="true"')
    expect(html).toMatch(/<option value="deleted.result" disabled="" selected="">无效输出/)
    expect(html).toContain('<option value="agent.result">agent.result</option>')
    expect(source.required_outputs).toEqual([{ node_id: 'deleted', output_slot: 'result' }])
  })

  it('prevents connection deletion while disconnected or touching an admitted node', () => {
    for (const html of [render(true), render(false, { agent: 'running' }), render(false, { deleted: 'completed' })]) {
      expect(html).toMatch(/aria-label="删除连接 agent → deleted" disabled=""/)
    }
  })
})
