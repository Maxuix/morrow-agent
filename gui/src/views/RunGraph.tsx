import { useMemo } from 'react'
import { Background, Handle, Position, ReactFlow } from '@xyflow/react'
import type { Edge, Node, NodeProps } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { StatusDot } from '../components/StatusDot'
import type { GraphLayout, GraphNodeModel } from './lib/graph'

type MorrowFlowNode = Node<GraphNodeModel, 'morrowNode'>

/**
 * Custom node: white raised card, left status dot + text label (never color
 * alone), mono node_id, model in secondary text, approval badge, attempt
 * indicator. Colors come from the same Warm Paper tokens via Tailwind.
 */
function MorrowNode({ data, selected }: NodeProps<MorrowFlowNode>) {
  return (
    <div
      className={`w-52 rounded-[10px] border bg-raised px-3 py-2 shadow-none transition-colors duration-150 ${
        selected ? 'border-accent' : 'border-subtle'
      }`}
    >
      <Handle type="target" position={Position.Left} className="!bg-subtle" />
      <div className="flex items-center gap-2">
        <StatusDot status={data.status} className="text-xs" />
        {data.approval_pending && (
          <span className="rounded-[8px] border border-blocked px-1 py-0.5 text-[10px] text-blocked">
            待审批
          </span>
        )}
        {data.attempt > 1 && (
          <span className="ml-auto font-mono text-[10px] text-secondary">
            尝试 {data.attempt}
          </span>
        )}
      </div>
      <div className="mt-1 font-mono text-xs text-primary">{data.node_id}</div>
      {data.model !== null && (
        <div className="mt-0.5 truncate text-[11px] text-secondary">{data.model}</div>
      )}
      <Handle type="source" position={Position.Right} className="!bg-subtle" />
    </div>
  )
}

const nodeTypes = { morrowNode: MorrowNode }

/**
 * Read-only React Flow rendering of a multi-node run. Nodes/edges come from
 * the frozen revision (never mutated here); node state joins by node_id.
 * Edge/background colors reference the token CSS variables so both themes
 * stay consistent without duplicating values.
 */
export function RunGraph({
  layout,
  selectedNodeId,
  onSelectNode,
}: {
  layout: GraphLayout
  selectedNodeId: string | null
  onSelectNode: (nodeId: string) => void
}) {
  const nodes = useMemo<MorrowFlowNode[]>(
    () =>
      layout.nodes.map((node) => ({
        id: node.id,
        type: 'morrowNode',
        position: { x: node.x, y: node.y },
        data: node.data,
        selected: node.id === selectedNodeId,
      })),
    [layout, selectedNodeId],
  )
  const edges = useMemo<Edge[]>(
    () =>
      layout.edges.map((edge) => ({
        ...edge,
        style: { stroke: 'var(--border-subtle)' },
      })),
    [layout],
  )

  return (
    <div className="h-72 rounded-[10px] border border-subtle bg-base">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
        fitView
        minZoom={0.4}
        maxZoom={1.5}
        onNodeClick={(_, node) => onSelectNode(node.id)}
        onSelectionChange={({ nodes: selected }) => {
          const first = selected[0]
          if (first !== undefined) onSelectNode(first.id)
        }}
      >
        <Background color="var(--border-subtle)" gap={16} />
      </ReactFlow>
    </div>
  )
}
