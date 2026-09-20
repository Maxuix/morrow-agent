import type {
  WorkflowDefinitionViewWire,
  WorkflowDraftViewWire,
} from '../../api/types'

function dateLabel(value: string | null | undefined): string {
  if (value === null || value === undefined) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? '时间未知' : date.toLocaleString()
}

export function WorkflowLibrary({
  open,
  drafts,
  workflows,
  selectedDraftId,
  selectedWorkflowId,
  more,
  onToggle,
  onNew,
  onDraft,
  onWorkflow,
  onLoadMore,
  onManageAgents,
  onManageTools,
}: {
  open: boolean
  drafts: WorkflowDraftViewWire[]
  workflows: WorkflowDefinitionViewWire[]
  selectedDraftId: string | null
  selectedWorkflowId: string | null
  more: { agents: boolean; workflows: boolean; drafts: boolean }
  onToggle: () => void
  onNew: () => void
  onDraft: (draft: WorkflowDraftViewWire) => void
  onWorkflow: (workflow: WorkflowDefinitionViewWire) => void
  onLoadMore: (kind: 'workflows' | 'drafts') => void
  onManageAgents?: () => void
  onManageTools?: () => void
}) {
  return (
    <aside
      id="workflow-library"
      className={`workflow-library min-h-0 overflow-y-auto border-r border-subtle p-3 ${open ? 'is-open' : 'is-collapsed'}`}
      aria-label="工作流目录"
    >
      <div className="workflow-library-header">
        {open && <h2>工作流目录</h2>}
        <button type="button" className="editor-button" aria-label={open ? '收起工作流目录' : '打开工作流目录'} aria-expanded={open} onClick={onToggle}>
          {open ? '收起' : '目录'}
        </button>
      </div>
      {open && <>
        {onManageAgents && <button type="button" className="editor-button mt-2 w-full" onClick={onManageAgents}>资源管理 · Agent</button>}
        {onManageTools && <button type="button" className="editor-button mt-1 w-full" onClick={onManageTools}>资源管理 · 工具</button>}
        <button type="button" className="editor-button mt-3 w-full" onClick={onNew}>＋ 新建工作流</button>

        <h2 className="workflow-library-section-title">草稿</h2>
        {drafts.length === 0 && <p className="workflow-library-empty">还没有持久化草稿。</p>}
        {drafts.length > 0 && <ul className="workflow-library-list">
          {drafts.map(value => <li key={value.draft.draft_id}>
            <button type="button" onClick={() => onDraft(value)} className={`workflow-library-item ${selectedDraftId === value.draft.draft_id ? 'is-selected' : ''}`}>
              <span className="workflow-library-item-name">{value.draft.source.name}</span>
              <span className="workflow-library-item-meta">{draftStatus(value.draft.status)} · {dateLabel(value.draft.updated_at)}</span>
            </button>
          </li>)}
        </ul>}
        {more.drafts && <button type="button" className="editor-button mt-2 w-full" onClick={() => onLoadMore('drafts')}>加载更多草稿</button>}

        <h2 className="workflow-library-section-title">已发布定义</h2>
        {workflows.length === 0 && <p className="workflow-library-empty">还没有可复制的工作流定义。</p>}
        <ul className="workflow-library-list">
          {workflows.map(workflow => <li key={workflow.workflow_definition_id}>
            <button type="button" onClick={() => onWorkflow(workflow)} className={`workflow-library-item ${selectedWorkflowId === workflow.workflow_definition_id && selectedDraftId === null ? 'is-selected' : ''}`}>
              <span className="workflow-library-item-name">{workflow.source?.name ?? workflow.workflow_definition_id}</span>
              <span className="workflow-library-item-meta">{workflow.origin === 'builtin' ? '内置' : '自定义'} · {workflowStatus(workflow)}</span>
            </button>
          </li>)}
        </ul>
        {more.workflows && <button type="button" className="editor-button mt-2 w-full" onClick={() => onLoadMore('workflows')}>加载更多定义</button>}
      </>}
    </aside>
  )
}

function draftStatus(status: WorkflowDraftViewWire['draft']['status']): string {
  switch (status) {
    case 'valid': return '检查通过'
    case 'invalid': return '有阻断问题'
    case 'validating': return '检查中'
    case 'frozen': return '已发布 · 只读'
    case 'rejected': return '已拒绝 · 只读'
    default: return '草稿'
  }
}

function workflowStatus(workflow: WorkflowDefinitionViewWire): string {
  if (workflow.revoked) return '已撤销 · 只读'
  if (workflow.head === null) return '未发布'
  return workflow.head.enabled
    ? `已发布 · ${dateLabel(workflow.published_revision?.created_at as string | undefined)}`
    : `已停用 · ${dateLabel(workflow.published_revision?.created_at as string | undefined)}`
}
