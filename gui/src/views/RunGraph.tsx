import { useMemo } from 'react'
import type { GraphLayout } from './lib/graph'
import { WorkflowGraphCanvas } from './WorkflowGraphCanvas'

/**
 * Run-state rendering of a multi-node run on the shared canvas. The layout
 * comes from the frozen revision (never mutated here); node state joins by
 * node_id and stays overlaid on the frozen topology.
 */
export function RunGraph({
  layout,
  onSelectNode,
}: {
  layout: GraphLayout
  onSelectNode: (nodeId: string) => void
}) {
  const nodes = useMemo(() => layout.nodes, [layout])
  const edges = useMemo(() => layout.edges, [layout])

  return (
    <WorkflowGraphCanvas
      nodes={nodes}
      edges={edges}
      onSelectNode={onSelectNode}
      ariaLabel="运行流程图"
    />
  )
}
