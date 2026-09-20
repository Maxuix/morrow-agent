import type { WorkflowDraftDiagnosticWire } from '../api/types'
import { agentSelectionLabel, RESPONSIBILITY_LABELS } from './lib/taskPlan'
import type { PlanNodeInfo } from './lib/planGraph'

export const EXECUTOR_OPTIONS: Array<{
  value: string
  label: string
  responsibility: 'implementation' | 'research' | 'review'
}> = [
  { value: 'preset:general', label: 'General · 通用执行（含测试）', responsibility: 'implementation' },
  { value: 'preset:explore', label: 'Explore · 只读探查', responsibility: 'research' },
  { value: 'preset:review', label: 'Review · 只读审查', responsibility: 'review' },
]

/** Plan-level edit buffer for one node, keyed by node id (never remounted). */
export interface NodeEditBuffer {
  task: string
  agent: string
  depends: string[]
  /** One completion criterion per line; empty lines ignored on save. */
  completion: string
  /** Draft version the buffer was created from — drives conflict display. */
  baseVersion: number
}

/** Server truth for one node, used for dirty detection and conflict compare. */
export interface NodeServerValues {
  task: string
  agent: string
  parents: string[]
  completion: string
}

export function bufferDiffersFromServer(
  buffer: NodeEditBuffer,
  server: NodeServerValues,
): boolean {
  return (
    buffer.task !== server.task ||
    buffer.agent !== server.agent ||
    buffer.depends.join(',') !== server.parents.join(',') ||
    buffer.completion !== server.completion
  )
}

/**
 * Controlled inspector for one plan node. All values come from the
 * plan-level buffer (kept across draft-version bumps and other nodes' saves,
 * so saving node A never loses node C's unsaved input); edits write through
 * `onChange`. Version conflicts keep the user input and show the server
 * version for comparison — never silently overwriting either side.
 */
export function PlanNodeInspector({
  info, responsibility, modelLine, errors, otherNodes, value, server, serverVersion, locked, past,
  removable, removeArmed, conflict, expanded, onChange, onExpandedChange, onSave, onDiscard, onArmRemove, onRemove,
}: {
  info: PlanNodeInfo
  responsibility: string | undefined
  modelLine: string
  errors: WorkflowDraftDiagnosticWire[]
  otherNodes: string[]
  value: NodeEditBuffer
  server: NodeServerValues
  /** Current server draft version, shown in the conflict banner. */
  serverVersion: number
  locked: boolean
  past: boolean
  removable: boolean
  removeArmed: boolean
  conflict: boolean
  expanded: boolean
  onChange: (patch: Partial<NodeEditBuffer>) => void
  onExpandedChange: (expanded: boolean) => void
  onSave: () => void
  onDiscard: () => void
  onArmRemove: () => void
  onRemove: () => void
}) {
  const parsedCompletions = value.completion.split('\n').map((line) => line.trim()).filter(Boolean)
  const dirty = bufferDiffersFromServer(value, server)
  const agentOption = EXECUTOR_OPTIONS.find((option) => option.value === value.agent)
  return (
    <article
      className={`rounded-[10px] border p-3 text-xs ${errors.length > 0 ? 'border-failed' : 'border-subtle bg-raised'}`}
      data-node-id={info.node_id}
      aria-label={`节点编辑 ${info.node_id}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono">{info.node_id}</span>
        <span className="text-secondary">{info.title}</span>
        <span className="rounded-[8px] border border-subtle px-1.5 py-0.5 text-secondary">
          {agentSelectionLabel(info.agent)}
        </span>
        <span className="rounded-[8px] border border-subtle px-1.5 py-0.5 text-secondary">
          {RESPONSIBILITY_LABELS[responsibility ?? 'general'] ?? '通用'}
        </span>
        {past && <span className="rounded-[8px] border border-subtle px-1.5 py-0.5 text-blocked">已执行，不可改写</span>}
        {errors.length > 0 && <span className="text-failed">校验错误 {errors.length} 项</span>}
        <button type="button" className="ml-auto editor-button" aria-expanded={expanded} onClick={() => onExpandedChange(!expanded)}>
          {expanded ? '收起详情' : '详情'}
        </button>
      </div>
      <p className="mt-2 whitespace-pre-wrap">{info.objective}</p>
      <p className="mt-2 text-secondary">依赖：{info.parents.join(', ') || '无'}{info.finalDelivery ? ' · 最终交付' : ''}</p>
      {errors.map((item, index) => <p key={index} className="mt-1 text-failed">{item.message}</p>)}
      {conflict && (
        <div className="mt-2 rounded-[8px] border border-blocked p-2" role="alert">
          <p className="text-blocked">服务端已保存新版本 v{serverVersion}；以下为服务端当前内容，你的修改已保留。</p>
          <p className="mt-1 whitespace-pre-wrap text-secondary">服务端任务：{server.task}</p>
          <p className="text-secondary">服务端依赖：{server.parents.join(', ') || '无'}</p>
          <div className="mt-1 flex gap-2">
            <button type="button" className="editor-button border-accent text-accent" onClick={onSave}>按当前服务端版本保存我的修改</button>
            <button type="button" className="editor-button" onClick={onDiscard}>放弃我的修改</button>
          </div>
        </div>
      )}
      {expanded && (
        <div className="mt-2 flex flex-col gap-2 border-t border-subtle pt-2">
          <label className="flex flex-col gap-1 text-secondary">节点任务
            <textarea
              aria-label={`节点任务 ${info.node_id}`}
              className="editor-input mt-1 min-h-16"
              value={value.task}
              disabled={locked}
              maxLength={4096}
              onChange={(event) => onChange({ task: event.target.value })}
            />
          </label>
          <label className="flex items-center gap-2 text-secondary">执行者
            <select
              aria-label={`节点执行者 ${info.node_id}`}
              className="editor-input"
              value={value.agent}
              disabled={locked}
              onChange={(event) => onChange({ agent: event.target.value })}
            >
              {EXECUTOR_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              {!EXECUTOR_OPTIONS.some((option) => option.value === value.agent) && <option value={value.agent}>{value.agent}</option>}
            </select>
          </label>
          <fieldset className="text-secondary">
            <legend>依赖</legend>
            <div className="mt-1 flex flex-wrap gap-2">
              {otherNodes.length === 0 && <span>暂无其他节点。</span>}
              {otherNodes.map((other) => (
                <label key={other} className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    aria-label={`依赖 ${other}`}
                    disabled={locked}
                    checked={value.depends.includes(other)}
                    onChange={(event) =>
                      onChange({
                        depends: event.target.checked
                          ? [...value.depends, other]
                          : value.depends.filter((item) => item !== other),
                      })
                    }
                  />
                  {other}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="flex flex-col gap-1 text-secondary">完成条件（每行一条；至少保留一条）
            <textarea
              aria-label={`完成条件 ${info.node_id}`}
              className="editor-input mt-1 min-h-16"
              value={value.completion}
              disabled={locked}
              onChange={(event) => onChange({ completion: event.target.value })}
            />
          </label>
          <p className="text-secondary">模型：{modelLine}</p>
          <div className="flex gap-2">
            <button
              type="button"
              className="editor-button border-accent text-accent"
              disabled={locked || !dirty || !value.task.trim() || parsedCompletions.length === 0}
              onClick={onSave}
            >
              保存节点
            </button>
            <button type="button" className="editor-button" disabled={locked || !dirty} onClick={onDiscard}>还原</button>
            <button
              type="button"
              className={`editor-button ${removeArmed ? 'border-failed text-failed' : ''}`}
              disabled={locked || !removable}
              onClick={removeArmed ? onRemove : onArmRemove}
            >
              {removeArmed ? '确认删除（影响交付/下游）' : '删除节点'}
            </button>
          </div>
          {parsedCompletions.length === 0 && <p className="text-failed">至少需要一条完成条件才能保存。</p>}
          {!past && agentOption === undefined && value.agent.startsWith('preset:') === false && value.agent.startsWith('custom:') === false && (
            <p className="text-failed">执行者选择无效。</p>
          )}
        </div>
      )}
    </article>
  )
}
