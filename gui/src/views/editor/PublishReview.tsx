import type { WorkflowDraftDiagnosticWire } from '../../api/types'
import type { DraftCheckState, DraftSaveState } from '../../state/workflowDraft'
import { draftStalenessBlocksFreeze } from '../lib/editor'
import { diagnosticSummary } from './diagnostics'

export type PublishDraftStatus = 'draft' | 'validating' | 'valid' | 'invalid' | 'rejected' | 'frozen'

function saveLabel(state: DraftSaveState, dirty: boolean): string {
  if (state === 'scheduled') return '等待保存'
  if (state === 'saving') return '保存中…'
  if (state === 'failed') return '保存失败'
  if (state === 'conflict') return '保存冲突'
  if (state === 'unknown') return '结果待核对'
  if (state === 'saved' && !dirty) return '已保存'
  return dirty ? '未保存' : '待保存'
}

function checkLabel(state: DraftCheckState, status: PublishDraftStatus): string {
  if (state === 'checking' || status === 'validating') return '检查中'
  if (state === 'passed') return '检查通过'
  if (state === 'warning') return '有提醒'
  if (state === 'blocked' || status === 'invalid' || status === 'rejected') return '有阻断问题'
  return '尚未检查'
}

function versionLabel(status: PublishDraftStatus, revisionId: string | null): string {
  if (status === 'frozen') return revisionId === null ? '已发布 · 只读' : `已发布 · ${revisionId}`
  if (status === 'rejected') return '已拒绝 · 不可编辑'
  return '尚未生成发布版本'
}

export function publishBlockReason({
  status,
  saveState,
  checkState,
  dirty,
  staleReasons,
  unresolved,
}: {
  status: PublishDraftStatus
  saveState: DraftSaveState
  checkState: DraftCheckState
  dirty: boolean
  staleReasons: string[]
  unresolved: boolean
}): string | null {
  if (status === 'frozen') return '该 Draft 已冻结；请创建新草稿继续编辑。'
  if (status === 'rejected') return '该 Draft 已被拒绝；请创建新草稿重新整理。'
  if (unresolved || saveState === 'conflict' || saveState === 'unknown') return '保存结果需要先核对服务器版本；本地内容仍保留。'
  if (dirty || saveState === 'scheduled' || saveState === 'saving') return '仍有本地修改，发布前会先保存并检查最新内容。'
  if (draftStalenessBlocksFreeze(staleReasons)) return '工作流定义事实发生变化，请重新核对后再发布。'
  if (staleReasons.length > 0) return '目录有更新；当前精确 Agent 引用仍由 Compiler 检查。'
  if (status === 'invalid' || checkState === 'blocked') return '存在阻断问题；请先在检查摘要中修复。'
  if (status !== 'valid' || checkState === 'idle') return '请先检查 Draft，确认当前 Source 可以发布。'
  return null
}

export function PublishReview({
  status,
  frozenRevisionId,
  saveState,
  checkState,
  dirty,
  staleReasons,
  diagnostics,
  busy,
  unresolved,
  message,
  onCheck,
  onUpdateDefinition,
  onPublish,
  onReconcile,
  onUseServer,
  onNewDraft,
}: {
  status: PublishDraftStatus
  frozenRevisionId: string | null
  saveState: DraftSaveState
  checkState: DraftCheckState
  dirty: boolean
  staleReasons: string[]
  diagnostics: WorkflowDraftDiagnosticWire[]
  busy: boolean
  unresolved: boolean
  message: string | null
  onCheck: () => void
  onUpdateDefinition: () => void
  onPublish: () => void
  onReconcile: () => void
  onUseServer: () => void
  onNewDraft: () => void
}) {
  const terminal = status === 'frozen' || status === 'rejected'
  const canCheck = !busy && !terminal && !unresolved
  const canUpdate = !busy && !terminal && !dirty && !unresolved && (checkState === 'passed' || checkState === 'warning')
  const canPublish = !busy && !terminal && !unresolved && status === 'valid'
    && !draftStalenessBlocksFreeze(staleReasons)
    && (dirty || checkState === 'passed' || checkState === 'warning')
  const reason = publishBlockReason({ status, saveState, checkState, dirty, staleReasons, unresolved })
  return (
    <section className="workflow-publish-review workflow-canvas-toolbar" aria-label="发布审核">
      {message !== null && !['草稿已保存并检查。', '版本已发布。'].includes(message) && <span role="status" className="workflow-summary-hint">{message}</span>}
      <span className={`workflow-status-pill workflow-status-${status}`}>
        {terminal ? (status === 'frozen' ? '已发布' : '已拒绝') : dirty || saveState !== 'saved' ? saveLabel(saveState, dirty) : checkLabel(checkState, status)}
      </span>
      {frozenRevisionId && <details className="text-xs"><summary>版本详情</summary>{versionLabel(status, frozenRevisionId)}</details>}
      {diagnostics.length > 0 && <span className="workflow-summary-hint">{diagnosticSummary(diagnostics)}</span>}
      {reason !== null && !terminal && <span className="workflow-stale-label" title={staleReasons.join('，')}>{reason}</span>}
      <div className="flex flex-wrap items-center gap-2">
        {terminal ? <button type="button" className="editor-button border-accent text-accent" disabled={busy} onClick={onNewDraft}>创建新草稿继续编辑</button> : <>
          <button type="button" className="editor-button" disabled={!canCheck} onClick={onCheck}>检查草稿</button>
          <button type="button" className="editor-button" disabled={!canUpdate} onClick={onUpdateDefinition}>更新可复用定义</button>
          <button type="button" className="editor-button border-accent text-accent" disabled={!canPublish} onClick={onPublish}>发布版本</button>
        </>}
        {unresolved && <>
          <button type="button" className="editor-button" disabled={busy} onClick={onReconcile}>核对服务器结果</button>
          <button type="button" className="editor-button" disabled={busy} onClick={onUseServer}>采用服务器版本</button>
        </>}
      </div>
    </section>
  )
}
