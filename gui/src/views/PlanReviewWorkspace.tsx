import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { TaskPlanState, TaskPlanStore } from '../state/taskPlan'
import type { WorkflowDraftDiagnosticWire } from '../api/types'
import {
  blockerText,
  canStartPlan,
  modelSourceLabel,
  type TaskPlanPhase,
} from './lib/taskPlan'
import {
  buildPlanGraph,
  buildPlanLayout,
  downstreamOf,
  nodeCompletionCriteria,
  planNodeFromTask,
  validateDependencyEdge,
  type DependencyValidation,
  type PlanGraph,
  type PlanNodeInfo,
} from './lib/planGraph'
import { WorkflowGraphCanvas } from './WorkflowGraphCanvas'
import {
  bufferDiffersFromServer,
  EXECUTOR_OPTIONS,
  PlanNodeInspector,
  type NodeEditBuffer,
  type NodeServerValues,
} from './PlanNodeInspector'

const VALIDATION_MESSAGES: Record<DependencyValidation, string> = {
  ok: '',
  self: '节点不能依赖自己。',
  duplicate: '这条依赖连线已存在。',
  cycle: '这条依赖会形成环路；已阻止。',
  missing_source: '连线的起点节点不存在。',
  missing_target: '连线的终点节点不存在。',
}

const DRAFT_STATUS_CHIP: Record<string, { label: string; failed: boolean }> = {
  draft: { label: '待校验', failed: false },
  validating: { label: '校验中', failed: false },
  valid: { label: '校验通过', failed: false },
  invalid: { label: '校验错误', failed: true },
  rejected: { label: '已拒绝', failed: true },
  frozen: { label: '已冻结', failed: false },
}

/**
 * The planning-phase review surface: the current draft plan as a default-open
 * graph plus one shared editor for the selected node. Graph and inspector read
 * the same server projection and share one plan-level edit-buffer state, so a
 * save anywhere refreshes both together. The same instance renders inline in
 * the side panel and fullscreen (portal) — state survives the switch.
 */
export function PlanReviewWorkspace({
  store, state, connection, phase, fullscreen, onToggleFullscreen,
}: {
  store: TaskPlanStore
  state: TaskPlanState
  connection: string
  phase: TaskPlanPhase
  fullscreen: boolean
  onToggleFullscreen: () => void
}) {
  const view = state.view
  const draft = view?.draft?.draft ?? null
  const source = draft?.source ?? null
  const version = view?.version ?? null
  const diagnostics: WorkflowDraftDiagnosticWire[] = draft?.diagnostics ?? []

  const planGraph = useMemo<PlanGraph | null>(
    () => (source === null ? null : buildPlanGraph(source, version, diagnostics)),
    [source, version, diagnostics],
  )
  const layout = useMemo(
    () => (planGraph === null ? null : buildPlanLayout(planGraph)),
    [planGraph],
  )

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [expandedIds, setExpandedIds] = useState<ReadonlySet<string>>(new Set())
  const [buffers, setBuffers] = useState<Record<string, NodeEditBuffer>>({})
  const [removeArmedId, setRemoveArmedId] = useState<string | null>(null)
  const [localNotice, setLocalNotice] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [newTask, setNewTask] = useState('')
  const [newAgent, setNewAgent] = useState('preset:general')
  const [newDepends, setNewDepends] = useState<string[]>([])
  // React Flow re-emits the current selection after unrelated store updates
  // (poll refreshes recreate node identities); only a genuine node CHANGE may
  // reset the armed-delete / edge-selection state.
  const selectedRef = useRef<string | null>(null)
  const handleSelectNode = (nodeId: string) => {
    if (selectedRef.current === nodeId) return
    selectedRef.current = nodeId
    setSelectedNodeId(nodeId)
    setSelectedEdgeId(null)
    setRemoveArmedId(null)
    setExpandedIds((current) => (current.has(nodeId) ? current : new Set(current).add(nodeId)))
  }

  // A selected node that no longer exists (deleted / regenerated away) must
  // not keep a dangling editor open.
  useEffect(() => {
    if (selectedNodeId !== null && planGraph !== null && !planGraph.nodes.some((node) => node.node_id === selectedNodeId)) {
      setSelectedNodeId(null)
      setExpandedIds((current) => {
        const next = new Set(current)
        next.delete(selectedNodeId)
        return next
      })
    }
  }, [selectedNodeId, planGraph])

  const serverValuesOf = (info: PlanNodeInfo): NodeServerValues => ({
    task: info.objective,
    agent: info.agent,
    parents: info.parents,
    completion: source === null ? '' : nodeCompletionCriteria(source, info.node_id).join('\n'),
  })
  const bufferFor = (info: PlanNodeInfo): NodeEditBuffer => {
    const stored = buffers[info.node_id]
    if (stored !== undefined) return stored
    const server = serverValuesOf(info)
    return { task: server.task, agent: server.agent, depends: server.parents, completion: server.completion, baseVersion: draft?.row_version ?? 1 }
  }
  const unsavedIds = useMemo(
    () =>
      Object.entries(buffers)
        .filter(([nodeId, buffer]) => {
          const info = planGraph?.nodes.find((node) => node.node_id === nodeId)
          return info !== undefined && bufferDiffersFromServer(buffer, serverValuesOf(info))
        })
        .map(([nodeId]) => nodeId),
    [buffers, planGraph], // eslint-disable-line react-hooks/exhaustive-deps
  )
  // Plan-level unsaved registry: the chat start guard reads the same truth.
  useEffect(() => {
    store.setUnsavedNodes(unsavedIds)
    return () => store.setUnsavedNodes([])
  }, [store, unsavedIds])
  // Clean buffers carry no information; prune them whenever the server
  // advances the draft so conflicts only appear for real divergence.
  useEffect(() => {
    setBuffers((current) => {
      let changed = false
      const next: Record<string, NodeEditBuffer> = {}
      for (const [nodeId, buffer] of Object.entries(current)) {
        const info = planGraph?.nodes.find((node) => node.node_id === nodeId)
        const keep = info !== undefined && bufferDiffersFromServer(buffer, serverValuesOf(info))
        if (keep) next[nodeId] = buffer
        else changed = true
      }
      return changed ? next : current
    })
  }, [draft?.row_version, planGraph]) // eslint-disable-line react-hooks/exhaustive-deps
  // Esc leaves the fullscreen review; the inline instance keeps all state.
  useEffect(() => {
    if (!fullscreen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onToggleFullscreen()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fullscreen, onToggleFullscreen])

  if (view === null || draft === null || source === null || planGraph === null || layout === null) return null

  const draftVersion = draft.row_version
  const changing = view.binding?.mode === 'change' || view.binding?.mode === 'repair'
  const locked = phase === 'generating' || state.busy || (view.run !== null && !changing)
  const past = new Set(view.candidate?.past_node_ids ?? [])
  const nodeIds = new Set(planGraph.nodes.map((node) => node.node_id))
  const metadata = version?.node_metadata ?? {}
  const selections = version?.execution_selections ?? {}

  const patchBuffer = (nodeId: string, patch: Partial<NodeEditBuffer>, server: NodeServerValues) => {
    setBuffers((current) => {
      const base = current[nodeId] ?? {
        task: server.task,
        agent: server.agent,
        depends: server.parents,
        completion: server.completion,
        baseVersion: draftVersion,
      }
      return { ...current, [nodeId]: { ...base, ...patch } }
    })
    setLocalNotice(null)
  }
  const discardBuffer = (nodeId: string) => {
    setBuffers((current) => {
      const next = { ...current }
      delete next[nodeId]
      return next
    })
  }

  const saveNode = async (info: PlanNodeInfo) => {
    const server = serverValuesOf(info)
    const value = bufferFor(info)
    const completions = value.completion.split('\n').map((line) => line.trim()).filter(Boolean)
    if (!value.task.trim() || completions.length === 0) return
    const depsOnly =
      value.task === server.task &&
      value.agent === server.agent &&
      value.completion === server.completion
    if (depsOnly) {
      // Dependency-only edits use the dedicated action: the node is never
      // re-lowered server-side, so model choice and constraints survive.
      await store.editNode('dependencies', { nodeId: info.node_id, dependsOn: value.depends })
    } else {
      const option = EXECUTOR_OPTIONS.find((item) => item.value === value.agent)
      const responsibility = option?.responsibility ?? (metadata[info.node_id]?.responsibility === 'synthesis' ? 'synthesis' : 'implementation')
      await store.editNode('replace', {
        nodeId: info.node_id,
        node: {
          node_id: info.node_id,
          title: info.title || info.node_id,
          task: value.task.trim(),
          agent: value.agent,
          responsibility,
          depends_on: value.depends,
          completion: completions,
        },
        confirmImpact: false,
      })
    }
    if (store.getState().error === null) discardBuffer(info.node_id)
  }

  /** Graph-driven dependency edits go through the same typed command chain.
   *  The handler itself guards `locked`: the canvas keeps a constant
   *  connectable flag so React Flow measures handles once and edges never
   *  disappear when the panel mounts during generation. */
  const connectEdge = (from: string, to: string) => {
    if (locked) return
    const validation = validateDependencyEdge(planGraph.edges, nodeIds, from, to)
    if (validation !== 'ok') {
      setLocalNotice(VALIDATION_MESSAGES[validation])
      return
    }
    const target = planGraph.nodes.find((node) => node.node_id === to)
    if (target === undefined) return
    void store.editNode('dependencies', {
      nodeId: to,
      dependsOn: [...new Set([...target.parents, from])],
    })
  }
  const removeEdge = (edgeId: string) => {
    const [from, to] = edgeId.split('->')
    const target = planGraph.nodes.find((node) => node.node_id === to)
    if (from === undefined || target === undefined || !target.parents.includes(from)) return
    void store.editNode('dependencies', {
      nodeId: to,
      dependsOn: target.parents.filter((parent) => parent !== from),
    })
    setSelectedEdgeId(null)
  }

  const removeNode = (info: PlanNodeInfo) => {
    void store.editNode('remove', { nodeId: info.node_id, confirmImpact: true })
    discardBuffer(info.node_id)
    setRemoveArmedId(null)
    if (selectedNodeId === info.node_id) setSelectedNodeId(null)
  }

  const submitAdd = async () => {
    if (!newTask.trim()) return
    await store.editNode('add', {
      node: planNodeFromTask(newTask, newAgent, newDepends, [...nodeIds]),
    })
    if (store.getState().error === null) {
      setNewTask(''); setNewAgent('preset:general'); setNewDepends([]); setAdding(false)
    }
  }

  const resultDelivery = source.required_outputs.find((output) => output.output_slot === 'result')
  const reviewDeliveries = source.required_outputs.filter((output) => output.output_slot !== 'result')
  const deliveryOptions = source.nodes.flatMap((node) =>
    node.output_contracts.filter((output) => output.required_for_node_completion).map((output) => `${node.node_id}.${output.slot}`),
  )
  const deliveryValue = resultDelivery ? `${resultDelivery.node_id}.${resultDelivery.output_slot}` : ''
  const setDelivery = (value: string) => {
    const [node_id, output_slot] = value.split('.')
    if (!node_id || !output_slot) return
    void store.editDeliveries([...reviewDeliveries, { node_id, output_slot }])
  }

  const selectedInfo = selectedNodeId === null ? null : planGraph.nodes.find((node) => node.node_id === selectedNodeId) ?? null
  const selectedEdge = selectedEdgeId === null ? null : planGraph.edges.find((edge) => `${edge.from_node_id}->${edge.to_node_id}` === selectedEdgeId) ?? null
  const statusChip = DRAFT_STATUS_CHIP[draft.status] ?? { label: draft.status, failed: false }

  const content = (
    <section className={fullscreen ? 'flex h-full flex-col gap-3 p-4 text-sm' : 'flex flex-col gap-3 text-sm'} aria-label="当前计划审阅" data-testid="plan-review-workspace">
      <div className={fullscreen ? 'flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto' : 'flex flex-col gap-3'}>
        <header className="rounded-[10px] border border-subtle bg-raised p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-serif text-base font-medium text-primary">{planGraph.name || '未命名计划'}</span>
            <span className="rounded-[8px] border border-subtle px-1.5 py-0.5 text-xs text-secondary">版本 v{draftVersion}</span>
            <span className={`rounded-[8px] border px-1.5 py-0.5 text-xs ${statusChip.failed ? 'border-failed text-failed' : 'border-subtle text-secondary'}`}>
              {statusChip.label}
            </span>
            {planGraph.nodes.length > 0 && (
              <span className="text-xs text-secondary">{planGraph.nodes.length} 节点</span>
            )}
            <button type="button" className="ml-auto editor-button" aria-pressed={fullscreen} onClick={onToggleFullscreen}>
              {fullscreen ? '退出全屏（Esc）' : '全屏审阅'}
            </button>
          </div>
          {planGraph.danglingEdgeCount > 0 && (
            <p role="note" className="mt-1 text-xs text-failed">
              有 {planGraph.danglingEdgeCount} 条连线引用了缺失的节点，无法绘制；详见校验结果。
            </p>
          )}
          {phase === 'generating' && (
            <p role="status" className="mt-1 text-xs text-blocked">正在更新计划；已暂停提交编辑，当前显示的是上一份图。</p>
          )}
        </header>

        <WorkflowGraphCanvas
          nodes={layout.nodes}
          edges={layout.edges}
          onSelectNode={handleSelectNode}
          selectedEdgeId={selectedEdgeId}
          onSelectEdge={(edgeId) => { setSelectedEdgeId(edgeId); setSelectedNodeId(null) }}
          onConnectEdge={connectEdge}
          fullscreen={fullscreen}
          ariaLabel="当前计划流程图"
        />

        {selectedEdge !== null && !locked && (
          <div className="flex flex-wrap items-center gap-2 rounded-[10px] border border-subtle bg-raised p-2 text-xs" aria-label="依赖连线操作">
            <span>依赖连线 {selectedEdge.from_node_id} → {selectedEdge.to_node_id}（后者依赖前者）</span>
            <button type="button" className="editor-button border-failed text-failed" onClick={() => removeEdge(selectedEdgeId!)}>移除该依赖</button>
            <button type="button" className="editor-button" onClick={() => setSelectedEdgeId(null)}>取消选择</button>
          </div>
        )}

        {localNotice !== null && (
          <p role="alert" className="text-xs text-failed">{localNotice} <button type="button" onClick={() => setLocalNotice(null)}>关闭提示</button></p>
        )}

        {selectedInfo !== null ? (
          <>
            <PlanNodeInspector
              info={selectedInfo}
              responsibility={metadata[selectedInfo.node_id]?.responsibility}
              modelLine={
                selections[selectedInfo.node_id]
                  ? `${selections[selectedInfo.node_id]!.model.provider_id}/${selections[selectedInfo.node_id]!.model.model_id}（${modelSourceLabel(selections[selectedInfo.node_id]!.model_source)}）`
                  : '继承会话设置'
              }
              errors={diagnostics.filter((item) => item.node_id === selectedInfo.node_id && item.severity === 'error')}
              otherNodes={planGraph.nodes.filter((node) => node.node_id !== selectedInfo.node_id).map((node) => node.node_id)}
              value={bufferFor(selectedInfo)}
              server={serverValuesOf(selectedInfo)}
              serverVersion={draftVersion}
              locked={locked || past.has(selectedInfo.node_id)}
              past={past.has(selectedInfo.node_id)}
              removable={planGraph.nodes.length > 1 && !past.has(selectedInfo.node_id)}
              removeArmed={removeArmedId === selectedInfo.node_id}
              conflict={bufferFor(selectedInfo).baseVersion !== draftVersion}
              expanded={expandedIds.has(selectedInfo.node_id)}
              onChange={(patch) => patchBuffer(selectedInfo.node_id, patch, serverValuesOf(selectedInfo))}
              onExpandedChange={(expanded) =>
                setExpandedIds((current) => {
                  const next = new Set(current)
                  if (expanded) next.add(selectedInfo.node_id)
                  else next.delete(selectedInfo.node_id)
                  return next
                })
              }
              onSave={() => void saveNode(selectedInfo)}
              onDiscard={() => discardBuffer(selectedInfo.node_id)}
              onArmRemove={() => setRemoveArmedId(selectedInfo.node_id)}
              onRemove={() => {
                removeNode(selectedInfo)
                selectedRef.current = null
              }}
            />
            {removeArmedId === selectedInfo.node_id && (
              <DeleteImpactPreview info={selectedInfo} planGraph={planGraph} />
            )}
          </>
        ) : null}

        <div className="grid grid-cols-2 gap-2 text-xs">
          <label className="flex flex-col gap-1 text-secondary">最终交付
            <select aria-label="最终交付" className="editor-input" value={deliveryValue} disabled={locked || deliveryOptions.length === 0}
              onChange={(event) => setDelivery(event.target.value)}>
              {!deliveryOptions.includes(deliveryValue) && <option value={deliveryValue} disabled>无效输出：{deliveryValue || '未设置'}，请重新选择</option>}
              {deliveryOptions.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <div className="flex items-end gap-2">
            <button type="button" className="editor-button" disabled={locked} onClick={() => setAdding((value) => !value)}>
              {adding ? '收起新增' : '＋ 新增节点'}
            </button>
            {store.undoVersion !== null && (
              <button type="button" className="editor-button" disabled={locked} onClick={() => void store.undoEdit()}>
                撤回上次编辑
              </button>
            )}
          </div>
        </div>
        {reviewDeliveries.length > 0 && (
          <p className="text-xs text-secondary">审查交付：{reviewDeliveries.map((output) => `${output.node_id}.${output.output_slot}`).join(', ')}</p>
        )}
        {adding && (
          <div className="rounded-[10px] border border-subtle bg-raised p-3 text-xs">
            <label className="flex flex-col gap-1 text-secondary">任务描述
              <textarea aria-label="新节点任务描述" className="editor-input mt-1 min-h-16" value={newTask} onChange={(event) => setNewTask(event.target.value)} maxLength={4096} />
            </label>
            <label className="mt-2 flex items-center gap-2 text-secondary">执行者（自动选择）
              <select aria-label="新节点执行者" className="editor-input" value={newAgent} onChange={(event) => setNewAgent(event.target.value)}>
                {EXECUTOR_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            <fieldset className="mt-2 text-secondary">
              <legend>前置依赖</legend>
              <div className="mt-1 flex flex-wrap gap-2">
                {planGraph.nodes.length === 0 && <span>暂无其他节点。</span>}
                {planGraph.nodes.map((node) => (
                  <label key={node.node_id} className="flex items-center gap-1">
                    <input
                      type="checkbox"
                      aria-label={`新节点依赖 ${node.node_id}`}
                      checked={newDepends.includes(node.node_id)}
                      onChange={(event) =>
                        setNewDepends((current) =>
                          event.target.checked ? [...current, node.node_id] : current.filter((item) => item !== node.node_id),
                        )
                      }
                    />
                    {node.node_id}
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="mt-2 flex gap-2">
              <button type="button" className="editor-button border-accent text-accent" disabled={state.busy || !newTask.trim()} onClick={() => void submitAdd()}>添加节点</button>
              <button type="button" className="editor-button" onClick={() => setAdding(false)}>取消</button>
            </div>
          </div>
        )}
      </div>

      <PlanReviewFooter
        phase={phase}
        busy={state.busy}
        refreshing={state.refreshing}
        unsavedCount={unsavedIds.length}
        connected={connection === 'live'}
        blockers={view.execution.blockers}
        onStart={() => void store.startExecution()}
      />
    </section>
  )

  if (fullscreen) {
    return createPortal(
      <div className="plan-review-overlay" role="dialog" aria-modal="true" aria-label="当前计划全屏审阅">{content}</div>,
      document.body,
    )
  }
  return content
}

/** What the server will actually do on delete: reconnect through a single
 *  parent, drop multi-fan-out edges, and flag delivery damage. */
export function DeleteImpactPreview({ info, planGraph }: { info: PlanNodeInfo; planGraph: PlanGraph }) {
  const outgoing = planGraph.edges.filter((edge) => edge.from_node_id === info.node_id)
  const downstream = downstreamOf(planGraph.edges, info.node_id)
  const reconnected = outgoing.length === 1 ? outgoing[0]!.to_node_id : null
  const noImpact = reconnected === null && outgoing.length <= 1 && downstream.length === 0 && !info.finalDelivery
  return (
    <div className="rounded-[10px] border border-failed p-3 text-xs" role="alert" aria-label="删除影响">
      <p className="font-medium">删除 {info.node_id} 的影响（再次点击“确认删除”后生效）：</p>
      <ul className="mt-1 list-disc pl-4">
        {reconnected !== null && <li>唯一下游 {reconnected} 将改由 {info.parents.join('、') || '无前置'} 直接提供输入。</li>}
        {outgoing.length > 1 && <li>指向 {outgoing.map((edge) => edge.to_node_id).join('、')} 的连线将被移除，这些节点需要手动补依赖。</li>}
        {downstream.length > 0 && <li>受影响的下游节点：{downstream.join('、')}（同一层的节点仍可并行）。</li>}
        {info.finalDelivery && <li className="text-failed">该节点的输出是最终交付；删除后计划失效，需要重新选择最终交付才能开始。</li>}
        {noImpact && <li>该节点没有下游依赖，也不影响最终交付。</li>}
      </ul>
    </div>
  )
}

/** Fixed bottom action area: save state, blocking reasons, explicit start. */
export function PlanReviewFooter({
  phase, busy, refreshing, unsavedCount, connected, blockers, onStart,
}: {
  phase: TaskPlanPhase
  busy: boolean
  refreshing: boolean
  unsavedCount: number
  connected: boolean
  blockers: string[]
  onStart: () => void
}) {
  const canStart = canStartPlan({ phase, busy: busy || refreshing, unsaved: unsavedCount > 0, connected })
  const blockerLine = blockerText(blockers)
  return (
    <div className="sticky bottom-0 mt-1 flex flex-wrap items-center gap-2 border-t border-subtle bg-base p-3 text-xs" aria-label="计划操作">
      {phase === 'ready' && (
        <button type="button" className="editor-button border-accent text-accent" disabled={!canStart} onClick={onStart}>
          开始执行
        </button>
      )}
      {phase === 'ready' && unsavedCount > 0 && (
        <span className="text-xs text-blocked">有 {unsavedCount} 个节点的未保存修改；请先保存后再开始。</span>
      )}
      {busy && <span role="status" className="text-xs text-secondary">正在保存…</span>}
      {refreshing && <span role="status" className="text-xs text-secondary">正在刷新计划状态…</span>}
      {(phase === 'invalid' || phase === 'needs_input') && blockerLine !== '' && (
        <span className="text-xs text-failed">{blockerLine}</span>
      )}
      {!connected && <span className="text-xs text-blocked">连接中断，暂不能开始或保存。</span>}

    </div>
  )
}
