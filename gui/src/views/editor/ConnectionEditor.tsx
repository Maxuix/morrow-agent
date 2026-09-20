import { useEffect, useMemo, useState } from 'react'
import type { WorkflowDefinitionSourceWire, WorkflowStatus } from '../../api/types'
import { nodeIsLocked } from '../lib/editor'
import {
  applyConnectionEdit,
  completionOutputs,
  hasControlEdge,
  outputContract,
  wouldCreateCycle,
  type ConnectionEditResult,
} from './relations'

interface BindingDraft {
  inputName: string
  outputSlot: string
}

export function ConnectionEditor({
  source,
  initialFromNodeId,
  initialToNodeId,
  disabled,
  nodeStatuses,
  onConfirm,
  onCancel,
}: {
  source: WorkflowDefinitionSourceWire
  initialFromNodeId: string | null
  initialToNodeId: string | null
  disabled: boolean
  nodeStatuses: Record<string, WorkflowStatus>
  onConfirm: (source: WorkflowDefinitionSourceWire) => void
  onCancel: () => void
}) {
  const [fromNodeId, setFromNodeId] = useState(initialFromNodeId ?? '')
  const [toNodeId, setToNodeId] = useState(initialToNodeId ?? '')
  const [keepControlEdge, setKeepControlEdge] = useState(
    initialFromNodeId === null || initialToNodeId === null || hasControlEdge(source, initialFromNodeId, initialToNodeId),
  )
  const [bindings, setBindings] = useState<BindingDraft[]>(() => existingBindings(source, initialFromNodeId, initialToNodeId))
  const [error, setError] = useState<string | null>(null)

  const outputs = useMemo(() => completionOutputs(source, fromNodeId || undefined), [fromNodeId, source])
  const target = source.nodes.find(node => node.node_id === toNodeId)
  const locked = disabled || nodeIsLocked(nodeStatuses[fromNodeId]) || nodeIsLocked(nodeStatuses[toNodeId])
  const isExistingEdge = fromNodeId !== '' && toNodeId !== '' && hasControlEdge(source, fromNodeId, toNodeId)
  const fromOptions = useMemo(() => source.nodes.filter(node => (
    node.node_id === fromNodeId ||
    toNodeId === '' ||
    !wouldCreateCycle(source, node.node_id, toNodeId)
  )), [fromNodeId, source, toNodeId])
  const toOptions = useMemo(() => source.nodes.filter(node => (
    node.node_id === toNodeId ||
    fromNodeId === '' ||
    !wouldCreateCycle(source, fromNodeId, node.node_id)
  )), [fromNodeId, source, toNodeId])

  useEffect(() => {
    setFromNodeId(initialFromNodeId ?? '')
    setToNodeId(initialToNodeId ?? '')
    setKeepControlEdge(
      initialFromNodeId === null || initialToNodeId === null || hasControlEdge(source, initialFromNodeId, initialToNodeId),
    )
    setBindings(existingBindings(source, initialFromNodeId, initialToNodeId))
    setError(null)
  }, [initialFromNodeId, initialToNodeId])

  function setEndpoint(kind: 'from' | 'to', value: string) {
    if (kind === 'from') {
      setFromNodeId(value)
      setBindings(existingBindings(source, value, toNodeId))
      return
    }
    setToNodeId(value)
    setBindings(existingBindings(source, fromNodeId, value))
  }

  function addBinding() {
    let index = bindings.length + 1
    let inputName = `input_${index}`
    while (bindings.some(binding => binding.inputName === inputName)) {
      index += 1
      inputName = `input_${index}`
    }
    setBindings(current => [...current, { inputName, outputSlot: '' }])
  }

  function commit() {
    if (locked) return
    const result: ConnectionEditResult = applyConnectionEdit(source, {
      fromNodeId,
      toNodeId,
      keepControlEdge,
      bindings,
    })
    if (result.source === null) {
      setError(result.blockers.join(' '))
      return
    }
    onConfirm(result.source)
  }

  return (
    <section className="workflow-connection-editor" aria-label="连接详情">
      <div className="workflow-inspector-heading">
        <div>
          <span className="workflow-kicker">控制依赖与数据传递</span>
          <h2>{isExistingEdge ? `${fromNodeId} → ${toNodeId}` : '添加连接'}</h2>
        </div>
        <span className="workflow-inspector-state">{locked ? '只读' : '未提交'}</span>
      </div>
      <p className="workflow-field-help">执行顺序与传递结果可分别设置。</p>
      <label className="workflow-field">
        <span>上游步骤</span>
        <select aria-label="上游步骤" className="editor-input" value={fromNodeId} disabled={locked || initialFromNodeId !== null} onChange={event => setEndpoint('from', event.target.value)}>
          <option value="">选择上游步骤</option>
          {fromOptions.map(node => <option key={node.node_id} value={node.node_id}>{node.node_id}</option>)}
        </select>
      </label>
      <label className="workflow-field">
        <span>下游步骤</span>
        <select aria-label="下游步骤" className="editor-input" value={toNodeId} disabled={locked || initialToNodeId !== null} onChange={event => setEndpoint('to', event.target.value)}>
          <option value="">选择下游步骤</option>
          {toOptions.map(node => <option key={node.node_id} value={node.node_id}>{node.node_id}</option>)}
        </select>
      </label>
      <label className="workflow-checkbox-field">
        <input type="checkbox" checked={keepControlEdge} disabled={locked} onChange={event => setKeepControlEdge(event.target.checked)} />
        完成后继续（控制依赖）
      </label>
      <section className="workflow-connection-data" aria-label="结果传递">
        <div className="workflow-connection-data-heading"><h3>传递的结果</h3><span className="workflow-summary-hint">可为无</span></div>
        {bindings.length === 0 && <p className="workflow-field-help">无传递结果</p>}
        {bindings.map((binding, index) => {
          const current = outputContract(source, { node_id: fromNodeId, output_slot: binding.outputSlot })
          const selectedKnown = outputs.some(output => output.output_slot === binding.outputSlot)
          return <div key={`${binding.inputName}:${index}`} className="workflow-connection-binding-row">
            <label className="workflow-field"><span>目标输入名称</span><input aria-label={`目标输入名称 ${index + 1}`} className="editor-input" value={binding.inputName} disabled={locked} onChange={event => setBindings(currentBindings => currentBindings.map((item, itemIndex) => itemIndex === index ? { ...item, inputName: event.target.value } : item))} /></label>
            <label className="workflow-field"><span>来源结果</span><select aria-label={`来源结果 ${index + 1}`} className="editor-input" value={binding.outputSlot} disabled={locked || fromNodeId === ''} onChange={event => setBindings(currentBindings => currentBindings.map((item, itemIndex) => itemIndex === index ? { ...item, outputSlot: event.target.value } : item))}>
              <option value="">选择结果（不自动选择）</option>
              {!selectedKnown && binding.outputSlot !== '' && <option value={binding.outputSlot} disabled>无效结果 · {fromNodeId}.{binding.outputSlot}</option>}
              {outputs.map(output => <option key={output.output_slot} value={output.output_slot}>{fromNodeId}.{output.output_slot} · {output.kind} v{output.version}</option>)}
            </select>{current !== null && <span className="workflow-field-help">{current.kind} v{current.version}</span>}</label>
            <button type="button" className="workflow-link-button" disabled={locked} onClick={() => setBindings(currentBindings => currentBindings.filter((_, itemIndex) => itemIndex !== index))}>移除传递</button>
          </div>
        })}
        <button type="button" className="editor-button" disabled={locked || fromNodeId === '' || target === undefined} onClick={addBinding}>添加结果传递</button>
      </section>
      {error !== null && <p role="alert" className="workflow-editor-notice">{error}</p>}
      <div className="workflow-dialog-actions"><button type="button" className="editor-button" onClick={onCancel}>取消</button><button type="button" className="editor-button border-accent text-accent" disabled={locked} onClick={commit}>确认连接修改</button></div>
    </section>
  )
}

function existingBindings(
  source: WorkflowDefinitionSourceWire,
  fromNodeId: string | null,
  toNodeId: string | null,
): BindingDraft[] {
  if (fromNodeId === null || toNodeId === null) return []
  return source.nodes.find(node => node.node_id === toNodeId)?.input_bindings.flatMap(binding =>
    binding.source === 'node_output' && binding.node_output.node_id === fromNodeId
      ? [{ inputName: binding.input_name, outputSlot: binding.node_output.output_slot }]
      : [],
  ) ?? []
}
