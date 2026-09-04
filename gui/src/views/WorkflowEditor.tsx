import { useEffect, useMemo, useState } from 'react'
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import type { Connection, Edge, Node, NodeProps } from '@xyflow/react'
import type {
  AgentDefinitionViewWire,
  AgentNodeSourceWire,
  ArtifactContractCatalogWire,
  WorkflowDefinitionSourceWire,
  WorkflowDraftDiagnosticWire,
  WorkflowStatus,
} from '../api/types'
import { nodeIsLocked, preserveCanvasPositions } from './lib/editor'

interface EditorNodeData extends Record<string, unknown> {
  label: string
  error: boolean
  warning: boolean
  locked: boolean
}

type EditorFlowNode = Node<EditorNodeData, 'editorNode'>

function EditorNode({ data, selected }: NodeProps<EditorFlowNode>) {
  return (
    <div
      className={`w-52 rounded-[10px] border bg-raised px-3 py-2 ${
        data.error ? 'border-failed' : selected ? 'border-accent' : 'border-subtle'
      }`}
    >
      <Handle type="target" position={Position.Left} className="!bg-subtle" />
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs">{data.label}</span>
        {data.error && <span className="ml-auto text-[10px] text-failed">错误</span>}
        {!data.error && data.warning && (
          <span className="ml-auto text-[10px] text-blocked">警告</span>
        )}
        {data.locked && <span className="ml-auto text-[10px] text-secondary">已锁定</span>}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-subtle" />
    </div>
  )
}

const nodeTypes = { editorNode: EditorNode }
const EMPTY_NODE_STATUSES: Record<string, WorkflowStatus> = {}

export function WorkflowEditor({
  source,
  diagnostics,
  agents,
  contracts,
  disabled,
  onChange,
  nodeStatuses = EMPTY_NODE_STATUSES,
}: {
  source: WorkflowDefinitionSourceWire
  diagnostics: WorkflowDraftDiagnosticWire[]
  agents: AgentDefinitionViewWire[]
  contracts: ArtifactContractCatalogWire[]
  disabled: boolean
  onChange: (source: WorkflowDefinitionSourceWire) => void
  nodeStatuses?: Record<string, WorkflowStatus>
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(
    source.nodes[0]?.node_id ?? null,
  )
  const [newAgentId, setNewAgentId] = useState('')
  const warningNodes = useMemo(
    () =>
      new Set(
        diagnostics.flatMap((item) =>
          item.severity === 'warning' && item.node_id !== null ? [item.node_id] : [],
        ),
      ),
    [diagnostics],
  )
  const diagnosticEdges = useMemo(
    () => new Set(diagnostics.flatMap((item) => (item.edge_id === null ? [] : [item.edge_id]))),
    [diagnostics],
  )
  const flowNodes = useMemo<EditorFlowNode[]>(
    () =>
      source.nodes.map((node, index) => ({
        id: node.node_id,
        type: 'editorNode',
        position: { x: (index % 3) * 240, y: Math.floor(index / 3) * 100 },
        data: {
          label: node.node_id,
          error: diagnostics.some(
            (item) => item.severity === 'error' && item.node_id === node.node_id,
          ),
          warning: warningNodes.has(node.node_id),
          locked: disabled || nodeIsLocked(nodeStatuses[node.node_id]),
        },
      })),
    [diagnostics, disabled, nodeStatuses, source.nodes, warningNodes],
  )
  const flowEdges = useMemo<Edge[]>(
    () =>
      source.edges.map((edge) => {
        const id = `${edge.from_node_id}->${edge.to_node_id}`
        return {
          id,
          source: edge.from_node_id,
          target: edge.to_node_id,
          style: { stroke: diagnosticEdges.has(id) ? 'var(--status-failed)' : 'var(--border-subtle)' },
        }
      }),
    [diagnosticEdges, source.edges],
  )
  const [nodes, setNodes, onNodesChange] = useNodesState<EditorFlowNode>(flowNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(flowEdges)

  useEffect(
    () => setNodes((current) => preserveCanvasPositions(flowNodes, current)),
    [flowNodes, setNodes],
  )
  useEffect(() => setEdges(flowEdges), [flowEdges, setEdges])

  const selected = source.nodes.find((node) => node.node_id === selectedNodeId) ?? null
  const selectedAgent = agents.find(
    (item) => item.definition_id === selected?.agent_definition_ref.definition_id,
  )
  const availableAgents = agents.filter((item) => item.published_version !== null && !item.revoked)
  const outputKinds = contracts
    .map((item) => item.kind)
    .filter((kind) => !['TaskContract', 'ChangeCapture'].includes(kind))

  function updateNode(nodeId: string, update: (node: AgentNodeSourceWire) => AgentNodeSourceWire) {
    if (disabled || nodeIsLocked(nodeStatuses[nodeId])) return
    onChange({
      ...source,
      nodes: source.nodes.map((node) => (node.node_id === nodeId ? update(node) : node)),
    })
  }

  function connect(connection: Connection) {
    if (
      disabled ||
      connection.source === null ||
      connection.target === null ||
      connection.source === connection.target
    ) return
    if (nodeIsLocked(nodeStatuses[connection.source]) || nodeIsLocked(nodeStatuses[connection.target])) return
    const id = `${connection.source}->${connection.target}`
    if (source.edges.some((edge) => `${edge.from_node_id}->${edge.to_node_id}` === id)) return
    setEdges((current) => addEdge(connection, current))
    onChange({
      ...source,
      edges: [
        ...source.edges,
        { from_node_id: connection.source, to_node_id: connection.target },
      ],
    })
  }

  function deleteEdges(removed: Edge[]) {
    if (disabled) return
    const mutable = removed.filter(
      (edge) =>
        !nodeIsLocked(nodeStatuses[edge.source]) && !nodeIsLocked(nodeStatuses[edge.target]),
    )
    const ids = new Set(mutable.map((edge) => edge.id))
    onChange({
      ...source,
      edges: source.edges.filter(
        (edge) => !ids.has(`${edge.from_node_id}->${edge.to_node_id}`),
      ),
    })
  }

  function addNode() {
    const agent = availableAgents.find((item) => item.definition_id === newAgentId)
    const version = agent?.published_version
    if (version === null || version === undefined || disabled) return
    let ordinal = source.nodes.length + 1
    let nodeId = `agent_${ordinal}`
    while (source.nodes.some((node) => node.node_id === nodeId)) {
      ordinal += 1
      nodeId = `agent_${ordinal}`
    }
    const node: AgentNodeSourceWire = {
      node_id: nodeId,
      agent_definition_ref: {
        definition_id: version.source.definition_id,
        version_id: version.version_id,
        content_hash: version.content_hash,
      },
      task_contract: { objective: 'Describe this node task.', scope: [], constraints: [], source_refs: [] },
      input_bindings: [],
      output_contracts: [
        { kind: 'TextResult', version: 1, slot: 'result', required_for_node_completion: true },
      ],
      access_mode: version.source.access_mode_ceiling,
      conversation_scope: 'isolated',
      tool_requirements: null,
      max_agent_generation_requests: null,
    }
    onChange({ ...source, nodes: [...source.nodes, node] })
    setSelectedNodeId(nodeId)
  }

  function deleteSelected() {
    if (
      selected === null ||
      source.nodes.length === 1 ||
      disabled ||
      nodeIsLocked(nodeStatuses[selected.node_id])
    ) return
    onChange({ ...source, nodes: source.nodes.filter((node) => node.node_id !== selected.node_id) })
    setSelectedNodeId(source.nodes.find((node) => node.node_id !== selected.node_id)?.node_id ?? null)
  }

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_340px]">
      <section className="flex min-h-0 flex-col border-r border-subtle p-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="名称">
            <input
              value={source.name}
              disabled={disabled}
              onChange={(event) => onChange({ ...source, name: event.target.value })}
              className="editor-input"
            />
          </Field>
          <Field label="最终输出">
            <select
              className="editor-input"
              disabled={disabled}
              value={`${source.required_outputs[0]?.node_id ?? ''}.${source.required_outputs[0]?.output_slot ?? ''}`}
              onChange={(event) => {
                const [node_id, output_slot] = event.target.value.split('.')
                if (node_id && output_slot) onChange({ ...source, required_outputs: [{ node_id, output_slot }] })
              }}
            >
              {source.nodes.flatMap((node) =>
                node.output_contracts.map((output) => (
                  <option key={`${node.node_id}.${output.slot}`} value={`${node.node_id}.${output.slot}`}>
                    {node.node_id}.{output.slot}
                  </option>
                )),
              )}
            </select>
          </Field>
        </div>
        <div className="mt-3 grid grid-cols-4 gap-2">
          <BudgetField
            label="Workflow 请求上限"
            value={source.default_budget.max_agent_generation_requests}
            disabled={disabled}
            onChange={(value) => onChange({
              ...source,
              default_budget: { ...source.default_budget, max_agent_generation_requests: value },
            })}
          />
          <BudgetField
            label="默认节点请求上限"
            value={source.default_budget.default_node_max_agent_generation_requests}
            disabled={disabled}
            onChange={(value) => onChange({
              ...source,
              default_budget: {
                ...source.default_budget,
                default_node_max_agent_generation_requests: value,
              },
            })}
          />
          <BudgetField
            label="准入超时（秒）"
            value={source.default_budget.admission_timeout_seconds}
            disabled={disabled}
            onChange={(value) => onChange({
              ...source,
              default_budget: { ...source.default_budget, admission_timeout_seconds: value },
            })}
          />
          <Field label="并发声明">
            <input
              className="editor-input"
              type="number"
              min={1}
              disabled={disabled}
              value={source.default_budget.max_concurrency}
              onChange={(event) => onChange({
                ...source,
                default_budget: {
                  ...source.default_budget,
                  max_concurrency: Number(event.target.value),
                },
              })}
            />
          </Field>
        </div>
        <div className="mt-3 flex items-end gap-2">
          <Field label="添加 Agent 节点" className="min-w-56">
            <select className="editor-input" value={newAgentId} onChange={(e) => setNewAgentId(e.target.value)}>
              <option value="">选择已发布 Agent</option>
              {availableAgents.map((agent) => (
                <option key={agent.definition_id} value={agent.definition_id}>{agent.source?.name ?? agent.definition_id}</option>
              ))}
            </select>
          </Field>
          <button className="editor-button" type="button" disabled={disabled || newAgentId === ''} onClick={addNode}>添加</button>
          <span className="ml-auto text-xs text-secondary">拖动仅调整画布；连接/字段变更才校验。</span>
        </div>
        <div className="mt-3 min-h-80 flex-1 rounded-[10px] border border-subtle bg-base">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={connect}
            onEdgesDelete={deleteEdges}
            onNodeClick={(_, node) => setSelectedNodeId(node.id)}
            nodesConnectable={!disabled}
            edgesReconnectable={!disabled}
            deleteKeyCode={null}
            fitView
          >
            <Background color="var(--border-subtle)" gap={16} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      </section>
      <aside className="min-h-0 overflow-y-auto p-4" aria-label="节点 Inspector">
        {selected === null ? (
          <p className="text-sm text-secondary">选择节点以编辑。</p>
        ) : (
          <NodeInspector
            node={selected}
            agent={selectedAgent}
            agents={availableAgents}
            source={source}
            outputKinds={outputKinds}
            disabled={disabled || nodeIsLocked(nodeStatuses[selected.node_id])}
            diagnostics={diagnostics.filter((item) => item.node_id === selected.node_id)}
            onUpdate={(update) => updateNode(selected.node_id, update)}
            onDelete={deleteSelected}
          />
        )}
      </aside>
    </div>
  )
}

function NodeInspector({
  node,
  agent,
  agents,
  source,
  outputKinds,
  disabled,
  diagnostics,
  onUpdate,
  onDelete,
}: {
  node: AgentNodeSourceWire
  agent: AgentDefinitionViewWire | undefined
  agents: AgentDefinitionViewWire[]
  source: WorkflowDefinitionSourceWire
  outputKinds: string[]
  disabled: boolean
  diagnostics: WorkflowDraftDiagnosticWire[]
  onUpdate: (update: (node: AgentNodeSourceWire) => AgentNodeSourceWire) => void
  onDelete: () => void
}) {
  const definition = agent?.published_version?.source
  const overlays = new Map((node.tool_requirements ?? []).map((item) => [item.name, item.requirement]))
  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex items-center gap-2">
        <h3 className="font-serif text-lg font-semibold">{node.node_id}</h3>
        <span className="rounded-[8px] border border-subtle px-1.5 py-0.5 text-[10px] text-secondary">Pending Draft</span>
      </div>
      <Field label="AgentDefinition">
        <select
          className="editor-input"
          value={node.agent_definition_ref.definition_id}
          disabled={disabled}
          onChange={(event) => {
            const next = agents.find((item) => item.definition_id === event.target.value)?.published_version
            if (next === null || next === undefined) return
            onUpdate((value) => ({
              ...value,
              agent_definition_ref: {
                definition_id: next.source.definition_id,
                version_id: next.version_id,
                content_hash: next.content_hash,
              },
              access_mode: next.source.access_mode_ceiling === 'read' ? 'read' : value.access_mode,
            }))
          }}
        >
          {agents.map((item) => <option key={item.definition_id}>{item.definition_id}</option>)}
        </select>
      </Field>
      <Field label="任务目标">
        <textarea
          className="editor-input min-h-24 resize-y"
          value={node.task_contract.objective}
          disabled={disabled}
          onChange={(event) => onUpdate((value) => ({
            ...value,
            task_contract: { ...value.task_contract, objective: event.target.value },
          }))}
        />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="访问模式">
          <select className="editor-input" value={node.access_mode} disabled={disabled} onChange={(event) => onUpdate((value) => ({ ...value, access_mode: event.target.value as 'read' | 'write' }))}>
            <option value="read">只读</option>
            <option value="write" disabled={definition?.access_mode_ceiling === 'read'}>Writer</option>
          </select>
        </Field>
        <Field label="请求上限（可空）">
          <input className="editor-input" type="number" min={1} disabled={disabled} value={node.max_agent_generation_requests ?? ''} onChange={(event) => onUpdate((value) => ({ ...value, max_agent_generation_requests: event.target.value === '' ? null : Number(event.target.value) }))} />
        </Field>
      </div>
      <Field label="会话范围">
        <select className="editor-input" value={node.conversation_scope} disabled={disabled} onChange={(event) => onUpdate((value) => ({ ...value, conversation_scope: event.target.value as 'isolated' | 'invoking_session' }))}>
          <option value="isolated">isolated</option>
          <option value="invoking_session">invoking_session（仅单节点）</option>
        </select>
      </Field>
      <section className="rounded-[8px] border border-subtle p-3">
        <h4 className="text-xs font-medium text-secondary">输入绑定</h4>
        {node.input_bindings.map((binding) => (
          <div key={binding.input_name} className="mt-2 flex items-center gap-2 font-mono text-xs">
            <span>{binding.input_name} ← {binding.source === 'workflow_input' ? 'task' : `${binding.node_output.node_id}.${binding.node_output.output_slot}`}</span>
            <button type="button" disabled={disabled} className="ml-auto text-failed" onClick={() => onUpdate((value) => ({ ...value, input_bindings: value.input_bindings.filter((item) => item.input_name !== binding.input_name) }))}>删除</button>
          </div>
        ))}
        <button
          type="button"
          className="editor-button mt-2"
          disabled={disabled || source.nodes.length < 2}
          onClick={() => {
            const producer = source.nodes.find((item) => item.node_id !== node.node_id)
            const output = producer?.output_contracts[0]
            if (producer === undefined || output === undefined) return
            let ordinal = node.input_bindings.length + 1
            let name = `input_${ordinal}`
            while (node.input_bindings.some((item) => item.input_name === name)) { ordinal += 1; name = `input_${ordinal}` }
            onUpdate((value) => ({ ...value, input_bindings: [...value.input_bindings, { source: 'node_output', input_name: name, accepts: { kind: output.kind, version: 1 }, node_output: { node_id: producer.node_id, output_slot: output.slot } }] }))
          }}
        >添加节点输出绑定</button>
      </section>
      <section className="rounded-[8px] border border-subtle p-3">
        <h4 className="text-xs font-medium text-secondary">输出合同</h4>
        {node.output_contracts.map((output) => (
          <div key={output.slot} className="mt-2 grid grid-cols-2 gap-2">
            <input className="editor-input" disabled={disabled} value={output.slot} onChange={(event) => onUpdate((value) => ({ ...value, output_contracts: value.output_contracts.map((item) => item.slot === output.slot ? { ...item, slot: event.target.value } : item) }))} />
            <select className="editor-input" disabled={disabled} value={output.kind} onChange={(event) => onUpdate((value) => ({ ...value, output_contracts: value.output_contracts.map((item) => item.slot === output.slot ? { ...item, kind: event.target.value } : item) }))}>
              {outputKinds.map((kind) => <option key={kind}>{kind}</option>)}
            </select>
          </div>
        ))}
      </section>
      {definition !== undefined && definition.tool_requirements.length > 0 && (
        <section className="rounded-[8px] border border-subtle p-3">
          <h4 className="text-xs font-medium text-secondary">工具策略 · 来源</h4>
          {definition.tool_requirements.map((tool) => (
            <label key={tool.name} className="mt-2 grid grid-cols-[1fr_120px] items-center gap-2 text-xs">
              <span>{tool.name} <span className="text-secondary">Definition: {tool.requirement}</span></span>
              <select className="editor-input" disabled={disabled} value={overlays.get(tool.name) ?? 'inherit'} onChange={(event) => onUpdate((value) => {
                const next = (value.tool_requirements ?? []).filter((item) => item.name !== tool.name)
                if (event.target.value !== 'inherit') next.push({ name: tool.name, requirement: event.target.value as 'required' | 'optional' | 'forbidden' })
                return { ...value, tool_requirements: next.length === 0 ? null : next }
              })}>
                <option value="inherit">继承</option><option value="optional">optional</option><option value="forbidden">forbidden</option><option value="required">required</option>
              </select>
            </label>
          ))}
        </section>
      )}
      {diagnostics.map((item) => <p key={`${item.code}:${item.edge_id}`} className={item.severity === 'error' ? 'text-xs text-failed' : 'text-xs text-blocked'}>{item.message}</p>)}
      <button type="button" className="editor-button border-failed text-failed" disabled={disabled || source.nodes.length === 1} onClick={onDelete}>删除节点（不自动重连）</button>
    </div>
  )
}

function Field({ label, children, className = '' }: { label: string; children: React.ReactNode; className?: string }) {
  return <label className={`flex flex-col gap-1 text-xs text-secondary ${className}`}><span>{label}</span>{children}</label>
}

function BudgetField({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string
  value: number | null
  disabled: boolean
  onChange: (value: number | null) => void
}) {
  return (
    <Field label={`${label}（可空）`}>
      <input
        className="editor-input"
        type="number"
        min={1}
        disabled={disabled}
        value={value ?? ''}
        onChange={(event) => onChange(event.target.value === '' ? null : Number(event.target.value))}
      />
    </Field>
  )
}
