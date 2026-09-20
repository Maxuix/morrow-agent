import { useEffect, useMemo, useRef } from 'react'
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useStoreApi,
  type Connection,
  type Edge,
  type EdgeMouseHandler,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/base.css'
import { StatusDot } from '../components/StatusDot'
import { NodeExecutionBadge } from '../components/NodeExecutionBadge'
import type { GraphNodeModel, PositionedNode } from './lib/graph'

type MorrowFlowNode = Node<GraphNodeModel, 'morrowNode'>

/**
 * Shared node card for run and plan-review graphs: raised card, status dot +
 * text label (never color alone), title (plan) / mono node_id (run), model in
 * secondary text, delivery/approval/error badges. Colors come from the Warm
 * Paper tokens via Tailwind.
 */
function MorrowNode({ data, selected }: NodeProps<MorrowFlowNode>) {
  return (
    <div
      className={`w-52 rounded-[10px] border bg-raised px-3 py-2 shadow-none transition-colors duration-150 ${
        selected ? 'border-accent' : (data.errorCount ?? 0) > 0 ? 'border-failed' : 'border-subtle'
      }`}
    >
      <Handle type="target" position={Position.Left} className="!bg-subtle" />
      <div className="flex items-center gap-2">
        <StatusDot status={data.status} className="text-xs" />
        <NodeExecutionBadge execution={data.execution} />
        {data.approval_pending && (
          <span className="rounded-[8px] border border-blocked px-1 py-0.5 text-[10px] text-blocked">
            待审批
          </span>
        )}
        {(data.errorCount ?? 0) > 0 && (
          <span className="rounded-[8px] border border-failed px-1 py-0.5 text-[10px] text-failed">
            校验错误 {data.errorCount}
          </span>
        )}
        {data.finalDelivery === true && (
          <span className="rounded-[8px] border border-accent px-1 py-0.5 text-[10px] text-accent">
            最终交付
          </span>
        )}
        {data.attempt > 1 && (
          <span className="ml-auto font-mono text-[10px] text-secondary">尝试 {data.attempt}</span>
        )}
      </div>
      {data.title ? (
        <div className="mt-1 truncate text-xs font-medium text-primary">{data.title}</div>
      ) : null}
      <div className="mt-1 font-mono text-xs text-primary">{data.node_id}</div>
      {data.agentLabel !== undefined && data.agentLabel !== null && (
        <div className="mt-0.5 truncate text-[11px] text-secondary">{data.agentLabel}</div>
      )}
      {data.model !== null && (
        <div className="mt-0.5 truncate text-[11px] text-secondary">{data.model}</div>
      )}
      {data.objective !== null && (
        <div className="mt-1 line-clamp-2 whitespace-pre-wrap text-[11px] text-secondary">
          {data.objective}
        </div>
      )}
      <Handle type="source" position={Position.Right} className="!bg-subtle" />
    </div>
  )
}

const nodeTypes = { morrowNode: MorrowNode }

/**
 * Edge rendering needs measured handle bounds. Whenever the caller passes a
 * fresh node-object identity (e.g. after a server refresh), React Flow
 * re-initializes the nodes and measurement only resumes via ResizeObserver,
 * which can be starved (occluded windows, mid-generation mounts) and then
 * silently drops edges. A synchronous internals update per node-identity
 * change keeps edges deterministic without relying on animation frames.
 */
function NodeInternalsSync({ nodes }: { nodes: PositionedNode[] }) {
  const store = useStoreApi()
  const nodesRef = useRef(nodes)
  nodesRef.current = nodes
  useEffect(() => {
    const sync = () => {
      const { domNode, updateNodeInternals } = store.getState()
      const updates = new Map<string, { id: string; nodeElement: HTMLDivElement; force: true }>()
      for (const { id } of nodesRef.current) {
        const nodeElement = domNode?.querySelector<HTMLDivElement>(`.react-flow__node[data-id="${id}"]`)
        if (nodeElement) updates.set(id, { id, nodeElement, force: true })
      }
      if (updates.size > 0) updateNodeInternals(updates)
    }
    sync()
    const timer = setTimeout(sync, 150)
    return () => clearTimeout(timer)
  }, [nodes, store])
  return null
}

export interface CanvasEdgeRef {
  id: string
  source: string
  target: string
}

/**
 * Read-only by default; the canvas never fetches anything. Passing
 * `onConnectEdge` makes handles connectable (graph-driven dependency edits);
 * passing `onSelectEdge` enables edge selection for removal. Node positions
 * come pre-computed from the layered layout and are not draggable, so the
 * layout stays a pure function of the topology. `nodes` identity must stay
 * stable across unrelated re-renders (React Flow re-initializes measurement
 * for fresh node objects, which silently drops edges).
 */
export function WorkflowGraphCanvas({
  nodes,
  edges,
  onSelectNode,
  selectedEdgeId = null,
  onSelectEdge,
  onConnectEdge,
  fullscreen = false,
  ariaLabel = '流程图',
}: {
  nodes: PositionedNode[]
  edges: CanvasEdgeRef[]
  onSelectNode: (nodeId: string) => void
  selectedEdgeId?: string | null
  onSelectEdge?: (edgeId: string | null) => void
  /** Presence unlocks handle-to-handle dependency drawing (source → target). */
  onConnectEdge?: (source: string, target: string) => void
  fullscreen?: boolean
  ariaLabel?: string
}) {
  const flowNodes = useMemo<MorrowFlowNode[]>(
    () =>
      nodes.map((node) => ({
        id: node.id,
        type: 'morrowNode',
        position: { x: node.x, y: node.y },
        data: node.data,
      })),
    [nodes],
  )
  const flowEdges = useMemo<Edge[]>(
    () =>
      edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        selected: edge.id === selectedEdgeId,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: edge.id === selectedEdgeId ? 'var(--accent)' : 'var(--border-subtle)',
          width: 16,
          height: 16,
        },
        style: {
          stroke: edge.id === selectedEdgeId ? 'var(--accent)' : 'var(--border-subtle)',
          strokeWidth: edge.id === selectedEdgeId ? 2 : 1.5,
        },
      })),
    [edges, selectedEdgeId],
  )

  const handleConnect = onConnectEdge
    ? (connection: Connection) => {
        if (connection.source && connection.target) onConnectEdge(connection.source, connection.target)
      }
    : undefined
  const handleEdgeClick: EdgeMouseHandler | undefined = onSelectEdge
    ? (_, edge) => onSelectEdge(edge.id)
    : undefined
  const handlePaneClick = onSelectEdge ? () => onSelectEdge(null) : undefined

  return (
    <div
      className={`rounded-[10px] border border-subtle bg-base ${fullscreen ? 'h-full w-full' : 'h-80'}`}
      data-testid="workflow-graph-canvas"
      aria-label={ariaLabel}
    >
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        nodesDraggable={false}
        nodesConnectable={onConnectEdge !== undefined}
        deleteKeyCode={null}
        fitView
        minZoom={0.3}
        maxZoom={1.5}
        onNodeClick={(_, node) => onSelectNode(node.id)}
        onConnect={handleConnect}
        onEdgeClick={handleEdgeClick}
        onPaneClick={handlePaneClick}
        onSelectionChange={({ nodes: selected }) => {
          const first = selected[0]
          if (first !== undefined) onSelectNode(first.id)
        }}
      >
        <Background color="var(--border-subtle)" gap={16} />
        <Controls showInteractive={false} position="bottom-right" />
        <NodeInternalsSync nodes={nodes} />
      </ReactFlow>
    </div>
  )
}
