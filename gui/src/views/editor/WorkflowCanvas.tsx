import { useCallback, useEffect, useMemo, useRef } from 'react'
import {
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import type { Connection, Edge, Node, NodeChange, NodeProps, Viewport } from '@xyflow/react'
import type {
  AgentDefinitionViewWire,
  WorkflowDefinitionSourceWire,
  WorkflowDraftDiagnosticWire,
} from '../../api/types'
import { layeredPositions } from '../lib/graph'
import { nodeIsLocked, preserveCanvasPositions } from '../lib/editor'
import { canvasScopeKey, readCanvasUiState, updateCanvasUiState } from './canvasState'
import { buildStepDisplayModels, type StepDisplayModel } from './display'

interface EditorNodeData extends Record<string, unknown> {
  model: StepDisplayModel
  error: boolean
  warning: boolean
  locked: boolean
  onSelect: () => void
}

type EditorFlowNode = Node<EditorNodeData, 'editorNode'>

function statusText(model: StepDisplayModel, error: boolean, warning: boolean, locked: boolean): string {
  if (error) return '有错误'
  if (warning) return '有提醒'
  if (locked) return '已锁定'
  if (model.agentAvailability !== 'available') return '助手不可用'
  return '可编辑'
}

function EditorNode({ data, selected }: NodeProps<EditorFlowNode>) {
  const status = statusText(data.model, data.error, data.warning, data.locked)
  return (
    <div
      className={`workflow-node-card ${data.error ? 'is-error' : data.warning ? 'is-warning' : ''} ${selected ? 'is-selected' : ''}`}
      role="button"
      tabIndex={0}
      aria-label={`${data.model.ordinal}. ${data.model.cardTitle}`}
      onClick={data.onSelect}
      onKeyDown={event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          data.onSelect()
        }
      }}
    >
      <Handle type="target" position={Position.Left} className="!bg-subtle" />
      <div className="workflow-node-card-topline">
        <span className="workflow-node-number">步骤 {data.model.ordinal}</span>
        <span className="workflow-node-status">{status}</span>
      </div>
      <strong className="workflow-node-title" title={data.model.fullTitle}>{data.model.cardTitle}</strong>
      <span className="workflow-node-agent">{data.model.agentName}</span>
      {data.model.agentAvailability !== 'available' && (
        <span className="workflow-node-warning">请检查助手版本</span>
      )}
      <Handle type="source" position={Position.Right} className="!bg-subtle" />
    </div>
  )
}

const nodeTypes = { editorNode: EditorNode }
const EDITOR_GRID_X = 200
const EDITOR_GRID_Y = 112

function positionFor(
  source: WorkflowDefinitionSourceWire,
  cached: ReturnType<typeof readCanvasUiState>,
): Record<string, { x: number; y: number }> {
  const layers = layeredPositions(
    source.nodes.map(node => node.node_id),
    source.edges,
  )
  return Object.fromEntries(source.nodes.map(node => {
    const saved = cached?.positions[node.node_id]
    const layer = layers.get(node.node_id) ?? { layer: 0, row: 0 }
    return [node.node_id, saved ?? { x: layer.layer * EDITOR_GRID_X, y: layer.row * EDITOR_GRID_Y }]
  }))
}

function diagnosticSets(diagnostics: WorkflowDraftDiagnosticWire[]) {
  return {
    errors: new Set(diagnostics.flatMap(item => item.severity === 'error' && item.node_id !== null ? [item.node_id] : [])),
    warnings: new Set(diagnostics.flatMap(item => item.severity === 'warning' && item.node_id !== null ? [item.node_id] : [])),
    edges: new Set(diagnostics.flatMap(item => item.edge_id === null ? [] : [item.edge_id])),
  }
}

export function WorkflowCanvas({
  source,
  diagnostics,
  agents,
  disabled,
  nodeStatuses,
  selectedNodeId,
  scope,
  arrangeSignal,
  onSelectNode,
  onSelectEdge,
  onConnect,
  onDeleteEdges,
}: {
  source: WorkflowDefinitionSourceWire
  diagnostics: WorkflowDraftDiagnosticWire[]
  agents: AgentDefinitionViewWire[]
  disabled: boolean
  nodeStatuses: Record<string, import('../../api/types').WorkflowStatus>
  selectedNodeId: string | null
  scope?: { workspaceId: string; draftId: string }
  arrangeSignal: number
  onSelectNode: (nodeId: string) => void
  onSelectEdge: (edgeId: string) => void
  onConnect: (connection: Connection) => void
  onDeleteEdges: (edges: Edge[]) => void
}) {
  const cacheKey = canvasScopeKey(scope)
  const cached = useMemo(() => readCanvasUiState(cacheKey), [cacheKey])
  const displayModels = useMemo(() => buildStepDisplayModels(source, agents), [source, agents])
  const displayById = useMemo(() => new Map(displayModels.map(model => [model.nodeId, model])), [displayModels])
  const sets = useMemo(() => diagnosticSets(diagnostics), [diagnostics])
  const initialPositions = useMemo(() => positionFor(source, cached), [source, cached])
  const flowNodes = useMemo<EditorFlowNode[]>(() => source.nodes.map(node => ({
    id: node.node_id,
    type: 'editorNode',
    position: initialPositions[node.node_id] ?? { x: 0, y: 0 },
    selected: node.node_id === selectedNodeId,
    data: {
      model: displayById.get(node.node_id)!,
      error: sets.errors.has(node.node_id),
      warning: sets.warnings.has(node.node_id),
      locked: disabled || nodeIsLocked(nodeStatuses[node.node_id]),
      onSelect: () => onSelectNode(node.node_id),
    },
  })), [disabled, displayById, initialPositions, nodeStatuses, onSelectNode, selectedNodeId, sets, source.nodes])
  const flowEdges = useMemo<Edge[]>(() => source.edges.map(edge => {
    const id = `${edge.from_node_id}->${edge.to_node_id}`
    return {
      id,
      source: edge.from_node_id,
      target: edge.to_node_id,
      label: '完成后继续',
      ariaLabel: `${edge.from_node_id} 到 ${edge.to_node_id} 的控制依赖`,
      className: sets.edges.has(id) ? 'workflow-edge is-error' : 'workflow-edge',
    }
  }), [sets.edges, source.edges])
  const [nodes, setNodes] = useNodesState<EditorFlowNode>(flowNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(flowEdges)
  const lastArrangeSignal = useRef(arrangeSignal)

  useEffect(() => {
    setNodes(current => preserveCanvasPositions(flowNodes, current))
  }, [flowNodes, setNodes])
  useEffect(() => setEdges(flowEdges), [flowEdges, setEdges])

  const savePositions = useCallback((next: EditorFlowNode[]) => {
    updateCanvasUiState(cacheKey, current => ({
      positions: Object.fromEntries(next.map(node => [node.id, node.position])),
      viewport: current?.viewport ?? null,
      selectedNodeId,
    }))
  }, [cacheKey, selectedNodeId])

  const handleNodesChange = useCallback((changes: NodeChange<EditorFlowNode>[]) => {
    setNodes(current => {
      const next = applyNodeChanges(changes, current) as EditorFlowNode[]
      if (changes.some(change => change.type === 'position' && change.dragging === false)) savePositions(next)
      return next
    })
  }, [savePositions, setNodes])

  const handleViewportChange = useCallback((viewport: Viewport) => {
    updateCanvasUiState(cacheKey, current => ({
      positions: current?.positions ?? {},
      viewport,
      selectedNodeId,
    }))
  }, [cacheKey, selectedNodeId])

  const arrange = useCallback(() => {
    const nextPositions = positionFor(source, null)
    setNodes(current => {
      const next = current.map(node => ({ ...node, position: nextPositions[node.id] ?? node.position }))
      savePositions(next)
      return next
    })
  }, [savePositions, setNodes, source])

  useEffect(() => {
    if (arrangeSignal === lastArrangeSignal.current) return
    lastArrangeSignal.current = arrangeSignal
    arrange()
  }, [arrange, arrangeSignal])

  return (
    <section className="workflow-canvas-panel" aria-label="流程画布">
      <div className="workflow-canvas-heading">
        <div>
          <h2>流程画布</h2>

        </div>
        <button type="button" className="editor-button" disabled={disabled || source.nodes.length === 0} onClick={arrange}>整理布局</button>
      </div>
      <div className="workflow-canvas-surface">
        {source.nodes.length === 0 ? (
          <div className="workflow-empty-canvas"><strong>还没有步骤</strong><span>从右侧添加一个已发布助手开始。</span></div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={handleNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onEdgesDelete={onDeleteEdges}
            onNodeClick={(_, node) => onSelectNode(node.id)}
            onEdgeClick={(_, edge) => onSelectEdge(edge.id)}
            onMoveEnd={(_, viewport) => handleViewportChange(viewport)}
            nodesConnectable={!disabled}
            edgesReconnectable={!disabled}
            deleteKeyCode={null}
            defaultViewport={cached?.viewport ?? undefined}
          >
            <Background color="var(--border-subtle)" gap={16} />
            <Controls showInteractive={false} />
          </ReactFlow>
        )}
      </div>
    </section>
  )
}
