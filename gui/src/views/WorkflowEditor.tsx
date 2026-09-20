import { useCallback, useEffect, useMemo, useState } from 'react'
import type { Connection, Edge } from '@xyflow/react'
import type {
  AgentDefinitionVersionWire,
  AgentDefinitionViewWire,
  AgentNodeSourceWire,
  ArtifactContractCatalogWire,
  WorkflowDefinitionSourceWire,
  WorkflowDraftDiagnosticWire,
  WorkflowStatus,
} from '../api/types'
import { nodeIsLocked } from './lib/editor'
import { canvasScopeKey, readCanvasUiState, updateCanvasUiState } from './editor/canvasState'
import { RunSettings } from './editor/RunSettings'
import { ConnectionEditor } from './editor/ConnectionEditor'
import { StepInspector } from './editor/StepInspector'
import { WorkflowCanvas } from './editor/WorkflowCanvas'
import { WorkflowToolbar } from './editor/WorkflowToolbar'
import { WorkflowDiagnostics } from './editor/WorkflowDiagnostics'
import {
  edgeId,
  edgeRemovalImpact,
  completionOutputs,
  nodeRemovalImpact,
  renameOutputReferences,
  type EdgeImpact,
} from './editor/relations'

const EMPTY_NODE_STATUSES: Record<string, WorkflowStatus> = {}

interface DeletePlan {
  nodeId: string
  affectedOutputs: Array<{ node_id: string; output_slot: string }>
  replacements: Array<{ node_id: string; output_slot: string }>
  selected: Record<string, string>
}

interface EdgeDeletePlan {
  fromNodeId: string
  toNodeId: string
  impact: EdgeImpact
  removeBindings: boolean
}

function outputKey(value: { node_id: string; output_slot: string }): string {
  return `${value.node_id}.${value.output_slot}`
}

export function WorkflowEditor({
  source,
  diagnostics,
  agents,
  contracts,
  disabled,
  onChange,
  nodeStatuses = EMPTY_NODE_STATUSES,
  scope,
  libraryOpen: libraryOpenProp,
  onToggleLibrary,
}: {
  source: WorkflowDefinitionSourceWire
  diagnostics: WorkflowDraftDiagnosticWire[]
  agents: AgentDefinitionViewWire[]
  contracts: ArtifactContractCatalogWire[]
  disabled: boolean
  onChange: (source: WorkflowDefinitionSourceWire) => void
  nodeStatuses?: Record<string, WorkflowStatus>
  scope?: { workspaceId: string; draftId: string }
  libraryOpen?: boolean
  onToggleLibrary?: () => void
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(source.nodes[0]?.node_id ?? null)
  const [newAgentId, setNewAgentId] = useState('')
  const [inspectorOpen, setInspectorOpen] = useState(true)
  const [internalLibraryOpen, setInternalLibraryOpen] = useState(true)
  const [arrangeRevision, setArrangeRevision] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
  const [pendingAdd, setPendingAdd] = useState<AgentDefinitionVersionWire | null>(null)
  const [deletePlan, setDeletePlan] = useState<DeletePlan | null>(null)
  const [edgeDeletePlan, setEdgeDeletePlan] = useState<EdgeDeletePlan | null>(null)
  const [connectionRequest, setConnectionRequest] = useState<{ fromNodeId: string | null; toNodeId: string | null }>({ fromNodeId: null, toNodeId: null })
  const currentScopeKey = canvasScopeKey(scope)
  const libraryOpen = libraryOpenProp ?? internalLibraryOpen
  const toggleLibrary = onToggleLibrary ?? (() => setInternalLibraryOpen(value => !value))

  useEffect(() => {
    const cached = readCanvasUiState(currentScopeKey)
    const cachedNode = cached?.selectedNodeId !== null && cached?.selectedNodeId !== undefined && source.nodes.some(node => node.node_id === cached.selectedNodeId)
      ? cached.selectedNodeId
      : source.nodes[0]?.node_id ?? null
    setSelectedNodeId(cachedNode)
  }, [currentScopeKey])

  useEffect(() => {
    if (selectedNodeId !== null && source.nodes.some(node => node.node_id === selectedNodeId)) return
    setSelectedNodeId(source.nodes[0]?.node_id ?? null)
  }, [selectedNodeId, source.nodes])

  useEffect(() => {
    setInspectorOpen(true)
  }, [currentScopeKey])

  const selected = source.nodes.find(node => node.node_id === selectedNodeId) ?? null
  const availableAgents = agents.filter(agent => agent.published_version !== null && !agent.revoked)
  const outputChoices = useMemo(
    () => completionOutputs(source),
    [source.nodes],
  )

  const selectNode = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId)
    setConnectionRequest({ fromNodeId: null, toNodeId: null })
    updateCanvasUiState(currentScopeKey, current => ({
      positions: current?.positions ?? {},
      viewport: current?.viewport ?? null,
      selectedNodeId: nodeId,
    }))
    setInspectorOpen(true)
  }, [currentScopeKey])

  function changeSource(next: WorkflowDefinitionSourceWire) {
    setNotice(null)
    onChange(next)
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
    if (source.edges.some(edge => `${edge.from_node_id}->${edge.to_node_id}` === id)) return
    setConnectionRequest({ fromNodeId: connection.source, toNodeId: connection.target })
    setInspectorOpen(true)
  }

  function requestEdgeRemoval(removed: Edge[]) {
    if (disabled) return
    const edge = removed.find(item => !nodeIsLocked(nodeStatuses[item.source]) && !nodeIsLocked(nodeStatuses[item.target]))
    if (edge === undefined) return
    if (!source.edges.some(item => item.from_node_id === edge.source && item.to_node_id === edge.target)) {
      setNotice('这条连接已不存在；请重新读取当前 Draft 后再操作。')
      return
    }
    setEdgeDeletePlan({
      fromNodeId: edge.source,
      toNodeId: edge.target,
      impact: edgeRemovalImpact(source, edge.source, edge.target),
      removeBindings: false,
    })
  }

  function confirmEdgeRemoval() {
    if (edgeDeletePlan === null) return
    const { fromNodeId, toNodeId, removeBindings } = edgeDeletePlan
    if (
      !source.edges.some(edge => edge.from_node_id === fromNodeId && edge.to_node_id === toNodeId) ||
      nodeIsLocked(nodeStatuses[fromNodeId]) ||
      nodeIsLocked(nodeStatuses[toNodeId])
    ) {
      setEdgeDeletePlan(null)
      setNotice('连接或其端点状态已变化；未提交删除，请重新检查当前流程。')
      return
    }
    changeSource({
      ...source,
      edges: source.edges.filter(edge => !(edge.from_node_id === fromNodeId && edge.to_node_id === toNodeId)),
      nodes: removeBindings ? source.nodes.map(node => ({
        ...node,
        input_bindings: node.input_bindings.filter(binding => binding.source !== 'node_output' || node.node_id !== toNodeId || binding.node_output.node_id !== fromNodeId),
      })) : source.nodes,
    })
    setEdgeDeletePlan(null)
    setConnectionRequest({ fromNodeId: null, toNodeId: null })
  }

  function makeNode(version: AgentDefinitionVersionWire, nodeId: string) {
    return {
      node_id: nodeId,
      agent_definition_ref: {
        definition_id: version.source.definition_id,
        version_id: version.version_id,
        content_hash: version.content_hash,
      },
      task_contract: { objective: '描述这一步要完成的任务。', scope: [], constraints: [], source_refs: [] },
      input_bindings: [],
      output_contracts: [{ kind: 'TextResult', version: 1, slot: 'result', required_for_node_completion: true }],
      access_mode: version.source.access_mode_ceiling,
      conversation_scope: 'isolated' as const,
      tool_requirements: null,
      max_agent_generation_requests: null,
    }
  }

  function appendNode(version: AgentDefinitionVersionWire, convertInvokingSession: boolean) {
    let ordinal = source.nodes.length + 1
    let nodeId = `agent_${ordinal}`
    while (source.nodes.some(node => node.node_id === nodeId)) {
      ordinal += 1
      nodeId = `agent_${ordinal}`
    }
    const nodes = convertInvokingSession
      ? source.nodes.map(node => node.conversation_scope === 'invoking_session' ? { ...node, conversation_scope: 'isolated' as const } : node)
      : source.nodes
    const next = { ...source, nodes: [...nodes, makeNode(version, nodeId)] }
    changeSource(next)
    setSelectedNodeId(nodeId)
    setNewAgentId('')
    setPendingAdd(null)
  }

  function addNode() {
    const agent = availableAgents.find(item => item.definition_id === newAgentId)?.published_version
    if (agent === null || agent === undefined || disabled) return
    if (source.nodes.length === 1 && source.nodes[0]?.conversation_scope === 'invoking_session') {
      setPendingAdd(agent)
      return
    }
    appendNode(agent, false)
  }

  function deleteSelected() {
    if (selected === null || source.nodes.length <= 1 || disabled || nodeIsLocked(nodeStatuses[selected.node_id])) return
    const lockedConsumer = source.nodes.some(node => nodeIsLocked(nodeStatuses[node.node_id]) && node.input_bindings.some(binding => binding.source === 'node_output' && binding.node_output.node_id === selected.node_id))
    if (lockedConsumer) {
      setNotice('此步骤的结果已被锁定历史步骤使用，不能部分删除；请从运行修补流程处理。')
      return
    }
    const nodeImpact = nodeRemovalImpact(source, selected.node_id)
    const lockedRelatedNode = nodeImpact.edgeIds.some(id => {
      const [fromNodeId, toNodeId] = id.split('->')
      return nodeIsLocked(nodeStatuses[fromNodeId]) || nodeIsLocked(nodeStatuses[toNodeId])
    })
    if (lockedRelatedNode) {
      setNotice('该步骤与已接纳的历史步骤存在控制依赖，不能部分删除；请从运行修补流程处理。')
      return
    }
    const affectedOutputs = source.required_outputs.filter(output => output.node_id === selected.node_id)
    const replacements = outputChoices.filter(output => output.node_id !== selected.node_id)
    if (affectedOutputs.length > 0 && replacements.length === 0) {
      setNotice('删除会移除最后一个交付结果，请先添加并选择替代结果。')
      return
    }
    setDeletePlan({
      nodeId: selected.node_id,
      affectedOutputs,
      replacements,
      selected: Object.fromEntries(affectedOutputs.map(output => [outputKey(output), ''])),
    })
  }

  function applyDelete(nodeId: string, requiredOutputs: Array<{ node_id: string; output_slot: string }>) {
    const nextNodes = source.nodes
      .filter(node => node.node_id !== nodeId)
      .map(node => ({
        ...node,
        input_bindings: node.input_bindings.filter(binding => binding.source !== 'node_output' || binding.node_output.node_id !== nodeId),
      }))
    changeSource({
      ...source,
      nodes: nextNodes,
      edges: source.edges.filter(edge => edge.from_node_id !== nodeId && edge.to_node_id !== nodeId),
      required_outputs: requiredOutputs,
    })
    setSelectedNodeId(nextNodes[0]?.node_id ?? null)
    setDeletePlan(null)
  }

  function confirmDelete() {
    if (deletePlan === null) return
    const target = source.nodes.find(node => node.node_id === deletePlan.nodeId)
    if (
      target === undefined ||
      source.nodes.length <= 1 ||
      nodeIsLocked(nodeStatuses[deletePlan.nodeId]) ||
      source.nodes.some(node => nodeIsLocked(nodeStatuses[node.node_id]) && node.input_bindings.some(binding => binding.source === 'node_output' && binding.node_output.node_id === deletePlan.nodeId))
    ) {
      setDeletePlan(null)
      setNotice('步骤或其消费者状态已变化；未提交删除，请重新检查当前流程。')
      return
    }
    const currentImpact = nodeRemovalImpact(source, deletePlan.nodeId)
    const currentAffectedOutputs = source.required_outputs.filter(output => output.node_id === deletePlan.nodeId)
    if (currentImpact.edgeIds.some(id => {
      const [fromNodeId, toNodeId] = id.split('->')
      return nodeIsLocked(nodeStatuses[fromNodeId]) || nodeIsLocked(nodeStatuses[toNodeId])
    })) {
      setDeletePlan(null)
      setNotice('步骤与已接纳的历史步骤存在控制依赖；未提交删除。')
      return
    }
    if (currentAffectedOutputs.length !== deletePlan.affectedOutputs.length) {
      setDeletePlan(null)
      setNotice('交付结果已变化；未提交删除，请重新打开删除确认。')
      return
    }
    const replacements = currentAffectedOutputs.map(output => deletePlan.selected[outputKey(output)]).filter(Boolean)
    const currentCandidates = completionOutputs(source).filter(output => output.node_id !== deletePlan.nodeId)
    if (currentAffectedOutputs.length > 0 && currentCandidates.length === 0) {
      setDeletePlan(null)
      setNotice('删除会移除最后一个交付结果，请先添加并选择替代结果。')
      return
    }
    if (replacements.length !== currentAffectedOutputs.length || new Set(replacements).size !== replacements.length || replacements.some(value => !currentCandidates.some(candidate => outputKey(candidate) === value))) {
      setNotice('请为每个受影响的交付结果选择不同的替代结果，或取消删除。')
      return
    }
    const replacementsByKey = new Map(currentAffectedOutputs.map((output, index) => {
      const replacement = currentCandidates.find(candidate => outputKey(candidate) === replacements[index])
      return [outputKey(output), replacement === undefined ? undefined : {
        node_id: replacement.node_id,
        output_slot: replacement.output_slot,
      }]
    }))
    applyDelete(deletePlan.nodeId, source.required_outputs.map(output => replacementsByKey.get(outputKey(output)) ?? output))
  }

  function updateSelectedNode(nextNode: AgentNodeSourceWire) {
    if (disabled || nodeIsLocked(nodeStatuses[nextNode.node_id])) return
    const previous = source.nodes.find(node => node.node_id === nextNode.node_id)
    if (previous === undefined) return
    let nextSource: WorkflowDefinitionSourceWire = {
      ...source,
      nodes: source.nodes.map(node => node.node_id === nextNode.node_id ? nextNode : node),
    }
    for (let index = 0; index < Math.min(previous.output_contracts.length, nextNode.output_contracts.length); index += 1) {
      const oldSlot = previous.output_contracts[index]?.slot
      const newSlot = nextNode.output_contracts[index]?.slot
      if (oldSlot !== undefined && newSlot !== undefined && oldSlot !== newSlot) {
        nextSource = renameOutputReferences(nextSource, nextNode.node_id, oldSlot, newSlot)
      }
    }
    const slots = nextNode.output_contracts.map(output => output.slot.trim())
    if (slots.some(slot => slot === '') || new Set(slots).size !== slots.length) {
      setNotice('结果名称必须是非空且互不重复的 slot；相关引用会在确认有效名称后保留。')
      return
    }
    changeSource(nextSource)
  }

  function selectFinalOutput(index: number, value: string) {
    if (disabled || value === '') return
    const [nodeId, ...slotParts] = value.split('.')
    const outputSlot = slotParts.join('.')
    if (nodeId === undefined || outputSlot === '') return
    if (!outputChoices.some(output => output.node_id === nodeId && output.output_slot === outputSlot)) {
      setNotice('请选择一个真实且完成所需的输出；无效旧引用需要从列表中修复。')
      return
    }
    const duplicate = source.required_outputs.some((output, outputIndex) => outputIndex !== index && output.node_id === nodeId && output.output_slot === outputSlot)
    if (duplicate) {
      setNotice(`交付结果 ${nodeId}.${outputSlot} 已在列表中。`)
      return
    }
    changeSource({ ...source, required_outputs: source.required_outputs.map((output, outputIndex) => outputIndex === index ? { node_id: nodeId, output_slot: outputSlot } : output) })
  }

  function outputLabel(output: { node_id: string; output_slot: string }): string {
    return `${output.node_id}.${output.output_slot}`
  }

  const [newFinalOutput, setNewFinalOutput] = useState('')

  function addFinalOutput() {
    if (disabled || newFinalOutput === '') return
    const [nodeId, ...slotParts] = newFinalOutput.split('.')
    const outputSlot = slotParts.join('.')
    if (nodeId === undefined || outputSlot === '') return
    if (!outputChoices.some(output => output.node_id === nodeId && output.output_slot === outputSlot)) {
      setNotice('请选择一个真实且完成所需的输出。')
      return
    }
    if (source.required_outputs.some(output => outputLabel(output) === newFinalOutput)) {
      setNotice(`交付结果 ${newFinalOutput} 已在列表中。`)
      return
    }
    changeSource({ ...source, required_outputs: [...source.required_outputs, { node_id: nodeId, output_slot: outputSlot }] })
    setNewFinalOutput('')
  }

  function removeFinalOutput(index: number) {
    if (disabled || source.required_outputs.length <= 1) return
    changeSource({ ...source, required_outputs: source.required_outputs.filter((_, outputIndex) => outputIndex !== index) })
  }

  return (
    <div className="workflow-editor-layout" data-workflow-scope={currentScopeKey}>
      <WorkflowToolbar
        name={source.name}
        libraryOpen={libraryOpen}
        inspectorOpen={inspectorOpen}
        disabled={disabled}
        onNameChange={name => changeSource({ ...source, name })}
        onToggleLibrary={toggleLibrary}
        onToggleInspector={() => setInspectorOpen(value => !value)}
        onArrange={() => setArrangeRevision(value => value + 1)}
      />
      <div className={`workflow-editor-panels ${inspectorOpen ? 'is-inspector-open' : ''}`}>
        <div className="workflow-canvas-column">
          <section className="workflow-add-step" aria-label="添加步骤">
            <div>
              <strong>添加步骤</strong>

            </div>
            <select aria-label="添加步骤助手" className="editor-input" value={newAgentId} disabled={disabled} onChange={event => setNewAgentId(event.target.value)}>
              <option value="">选择已发布助手</option>
              {availableAgents.map(agent => <option key={agent.definition_id} value={agent.definition_id}>{agent.source?.name ?? agent.published_version?.source.name ?? agent.definition_id}</option>)}
            </select>
            <button type="button" className="editor-button" disabled={disabled || newAgentId === ''} onClick={addNode}>添加步骤</button>
            {availableAgents.length === 0 && <span className="workflow-field-warning">没有可用的已发布助手，请先到资源管理 · Agent。</span>}
          </section>
          <WorkflowCanvas
            source={source}
            diagnostics={diagnostics}
            agents={agents}
            disabled={disabled}
            nodeStatuses={nodeStatuses}
            selectedNodeId={selectedNodeId}
            scope={scope}
            arrangeSignal={arrangeRevision}
            onSelectNode={selectNode}
            onSelectEdge={edge => {
              const [fromNodeId, toNodeId] = edge.split('->')
              if (fromNodeId !== undefined && toNodeId !== undefined) {
                setConnectionRequest({ fromNodeId, toNodeId })
                setInspectorOpen(true)
              }
            }}
            onConnect={connect}
            onDeleteEdges={requestEdgeRemoval}
          />
          <section className="workflow-delivery-summary" aria-label="交付给用户的结果">
            <div className="workflow-canvas-heading">
              <div><h2>交付给用户的结果</h2></div>
              <span className="workflow-summary-hint">{source.required_outputs.length} 项</span>
            </div>
            <ul>
              {source.required_outputs.map((output, index) => {
                const node = source.nodes.find(item => item.node_id === output.node_id)
                const contract = node?.output_contracts.find(item => item.slot === output.output_slot)
                const value = outputLabel(output)
                const valid = contract?.required_for_node_completion === true
                return <li key={`${value}:${index}`} className={!valid ? 'is-invalid' : ''}>
                  <label className="workflow-field workflow-final-output-field">
                    <span>{source.required_outputs.length === 1 ? '最终输出' : `交付结果 ${index + 1}`}</span>
                    <select aria-label={source.required_outputs.length === 1 ? '最终输出' : `最终输出 ${index + 1}`} aria-invalid={!valid} className="editor-input" value={value} disabled={disabled} onChange={event => selectFinalOutput(index, event.target.value)}>
                      {!valid && <option value={value} disabled>无效输出 · {value}</option>}
                      {outputChoices.map(candidate => <option key={outputLabel(candidate)} value={outputLabel(candidate)}>{outputLabel(candidate)}</option>)}
                    </select>
                  </label>
                  <span>{contract === undefined ? '请修复无效结果' : `${contract.kind} v${contract.version}`}</span>
                  {source.required_outputs.length > 1 && <button type="button" className="workflow-link-button" disabled={disabled || source.required_outputs.length <= 1} onClick={() => removeFinalOutput(index)}>移除</button>}
                </li>
              })}
            </ul>
            <div className="workflow-final-output-add">
              <select aria-label="添加交付结果" className="editor-input" value={newFinalOutput} disabled={disabled} onChange={event => setNewFinalOutput(event.target.value)}>
                <option value="">添加另一个完成所需结果</option>
                {outputChoices.filter(output => !source.required_outputs.some(current => outputLabel(current) === outputLabel(output))).map(output => <option key={outputLabel(output)} value={outputLabel(output)}>{outputLabel(output)}</option>)}
              </select>
              <button type="button" className="editor-button" disabled={disabled || newFinalOutput === ''} onClick={addFinalOutput}>添加交付结果</button>
            </div>
          </section>
          <WorkflowDiagnostics
            diagnostics={diagnostics}
            onSelectNode={nodeId => {
              if (!source.nodes.some(node => node.node_id === nodeId)) {
                setNotice(`步骤 ${nodeId} 已不存在；请删除或修复相关引用。`)
                return
              }
              selectNode(nodeId)
            }}
            onSelectEdge={edge => {
              const [fromNodeId, toNodeId] = edge.split('->')
              if (fromNodeId === undefined || toNodeId === undefined) return
              if (!source.nodes.some(node => node.node_id === fromNodeId) || !source.nodes.some(node => node.node_id === toNodeId)) {
                setNotice(`连接 ${edge} 含有不存在的端点；可删除该连接，但不能编辑缺失节点。`)
                return
              }
              setConnectionRequest({ fromNodeId, toNodeId })
              setInspectorOpen(true)
            }}
          />
          {source.edges.length > 0 && <section className="workflow-connections-compact" aria-label="工作流连接">
            <h2>控制依赖</h2>
            <p>当前边表示“完成后继续”；数据传递会在连接详情中单独选择。</p>
            {source.edges.map(edge => {
              const id = `${edge.from_node_id}->${edge.to_node_id}`
              const missing = !source.nodes.some(node => node.node_id === edge.from_node_id) || !source.nodes.some(node => node.node_id === edge.to_node_id)
              return <div key={id} className="workflow-connection-row"><span>{edge.from_node_id} → {edge.to_node_id}{missing && <strong className="workflow-field-warning">节点已不存在</strong>}</span><div className="workflow-binding-actions"><button type="button" className="workflow-link-button" aria-label={`编辑连接 ${edge.from_node_id} → ${edge.to_node_id}`} disabled={disabled || missing || nodeIsLocked(nodeStatuses[edge.from_node_id]) || nodeIsLocked(nodeStatuses[edge.to_node_id])} onClick={() => { setConnectionRequest({ fromNodeId: edge.from_node_id, toNodeId: edge.to_node_id }); setInspectorOpen(true) }}>编辑连接</button><button type="button" className="workflow-link-button" aria-label={`删除连接 ${edge.from_node_id} → ${edge.to_node_id}`} disabled={disabled || nodeIsLocked(nodeStatuses[edge.from_node_id]) || nodeIsLocked(nodeStatuses[edge.to_node_id])} onClick={() => requestEdgeRemoval([{ id, source: edge.from_node_id, target: edge.to_node_id }])}>删除连接</button></div></div>
            })}
          </section>}
          {notice !== null && <p role="alert" className="workflow-editor-notice">{notice}</p>}
        </div>
        <aside className="workflow-inspector-column" aria-label="步骤与流程设置">
          <RunSettings budget={source.default_budget} disabled={disabled} onChange={default_budget => changeSource({ ...source, default_budget })} />
          {connectionRequest.fromNodeId !== null || connectionRequest.toNodeId !== null ? <ConnectionEditor source={source} initialFromNodeId={connectionRequest.fromNodeId} initialToNodeId={connectionRequest.toNodeId} disabled={disabled} nodeStatuses={nodeStatuses} onConfirm={next => { changeSource(next); setConnectionRequest({ fromNodeId: null, toNodeId: null }) }} onCancel={() => setConnectionRequest({ fromNodeId: null, toNodeId: null })} /> : selected === null ? <section className="workflow-overview"><h2>流程摘要</h2><dl><dt>步骤</dt><dd>{source.nodes.length}</dd><dt>控制依赖</dt><dd>{source.edges.length}</dd><dt>交付结果</dt><dd>{source.required_outputs.length}</dd></dl></section> : <StepInspector node={selected} source={source} agents={agents} contracts={contracts} diagnostics={diagnostics.filter(item => item.node_id === selected.node_id)} disabled={disabled} nodeStatuses={nodeStatuses} onUpdate={updateSelectedNode} onOpenConnection={(fromNodeId, toNodeId) => { setConnectionRequest({ fromNodeId, toNodeId }); setInspectorOpen(true) }} onDelete={deleteSelected} />}
        </aside>
      </div>
      {pendingAdd !== null && <dialog open className="workflow-confirm-dialog" aria-labelledby="workflow-add-confirm-title">
        <h2 id="workflow-add-confirm-title">需要转换会话范围</h2>
        <p>添加第二个步骤将把现有步骤改为独立对话。</p>
        <div className="workflow-dialog-actions"><button type="button" className="editor-button" onClick={() => setPendingAdd(null)}>取消</button><button type="button" className="editor-button border-accent text-accent" onClick={() => appendNode(pendingAdd, true)}>转为独立对话并添加</button></div>
      </dialog>}
      {edgeDeletePlan !== null && <dialog open className="workflow-confirm-dialog" aria-labelledby="workflow-edge-delete-confirm-title">
        <h2 id="workflow-edge-delete-confirm-title">确认删除控制依赖</h2>
        <p>删除 {edgeId(edgeDeletePlan.fromNodeId, edgeDeletePlan.toNodeId)} 不会自动重连。{edgeDeletePlan.impact.hasAlternativePath ? '仍有其他可达路径，但现有数据传递仍需显式核对。' : '这可能使下游步骤失去直接依赖。'}</p>
        {edgeDeletePlan.impact.bindings.length > 0 && <>
          <p className="workflow-field-warning">受影响的数据传递：{edgeDeletePlan.impact.bindings.map(binding => `${binding.node_id}.${binding.input_name} ← ${edgeDeletePlan.fromNodeId}.${binding.output_slot}`).join('、')}</p>
          <label className="workflow-checkbox-field"><input type="checkbox" checked={edgeDeletePlan.removeBindings} onChange={event => setEdgeDeletePlan(current => current === null ? current : { ...current, removeBindings: event.target.checked })} />同时移除这些数据传递</label>
          <p className="workflow-field-help">不勾选会保留原引用，之后可在连接详情中补回控制依赖或移除传递。</p>
        </>}
        <div className="workflow-dialog-actions"><button type="button" className="editor-button" onClick={() => setEdgeDeletePlan(null)}>取消</button><button type="button" className="editor-button border-failed text-failed" onClick={confirmEdgeRemoval}>确认删除依赖</button></div>
      </dialog>}
      {deletePlan !== null && <dialog open className="workflow-confirm-dialog" aria-labelledby="workflow-delete-confirm-title">
        <h2 id="workflow-delete-confirm-title">确认删除步骤</h2>
        <p>删除不会自动重连。它会移除相关控制依赖和未锁定的输入引用，并需要为受影响的交付结果选择替代输出。</p>
        {(() => { const impact = nodeRemovalImpact(source, deletePlan.nodeId); return <div className="workflow-impact-list"><span>受影响的控制依赖：{impact.edgeIds.join('、') || '无'}</span><span>受影响的输入传递：{impact.bindings.map(binding => `${binding.node_id}.${binding.input_name}`).join('、') || '无'}</span></div> })()}
        <div className="workflow-impact-list"><strong>受影响的交付结果</strong>{deletePlan.affectedOutputs.map(output => <label key={outputKey(output)}>{outputKey(output)}<select aria-label={`替代结果 ${outputKey(output)}`} className="editor-input" value={deletePlan.selected[outputKey(output)]} onChange={event => setDeletePlan(current => current === null ? current : { ...current, selected: { ...current.selected, [outputKey(output)]: event.target.value } })}><option value="">选择替代结果</option>{deletePlan.replacements.map(candidate => <option key={outputKey(candidate)} value={outputKey(candidate)}>{outputKey(candidate)}</option>)}</select></label>)}</div>
        <div className="workflow-dialog-actions"><button type="button" className="editor-button" onClick={() => setDeletePlan(null)}>取消</button><button type="button" className="editor-button border-failed text-failed" onClick={confirmDelete}>确认删除</button></div>
      </dialog>}
    </div>
  )
}
