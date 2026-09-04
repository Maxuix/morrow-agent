import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, type ApiClient } from '../api/client'
import type {
  AgentDefinitionViewWire,
  ArtifactContractCatalogWire,
  FutureGraphPatchWire,
  PatchApplyResultWire,
  PatchValidationWire,
  RunViewWire,
  WorkflowDefinitionSourceWire,
  WorkflowDraftDiagnosticWire,
  WorkflowRunWire,
  WorkflowStatus,
} from '../api/types'
import { EmptyState } from '../components/EmptyState'
import { WorkflowEditor } from './WorkflowEditor'
import {
  buildPatchDraft,
  canApplyPatch,
  commandId,
  diffSummaryLines,
  patchApplyErrorMessage,
  patchId,
  patchResultMessage,
  sourceFromRevision,
} from './lib/editor'
import { RISK_REASON_LABELS, shortId } from './lib/labels'

/**
 * Edit-pending flow (roadmap §6.3): the paused parent run is drained, the
 * Future nodes are edited in the WorkflowEditor (Past nodes locked via
 * nodeStatuses), the patch is previewed (validate), then applied. A successful
 * apply supersedes the parent run and starts a continuation child run.
 *
 * Pure logic (draft assembly, apply enablement, summary/message copy) lives in
 * `lib/editor.ts` so it stays testable without DOM.
 */
export function PatchEditor({
  client,
  run,
  view,
  workspaceId,
  onClose,
}: {
  client: ApiClient
  run: WorkflowRunWire
  view: RunViewWire
  workspaceId: string
  onClose: () => void
}) {
  const revisionSource = useMemo(() => sourceFromRevision(view.revision), [view.revision])
  const [localSource, setLocalSource] = useState<WorkflowDefinitionSourceWire | null>(
    revisionSource,
  )
  const [agents, setAgents] = useState<AgentDefinitionViewWire[]>([])
  const [contracts, setContracts] = useState<ArtifactContractCatalogWire[]>([])
  const [catalogsLoading, setCatalogsLoading] = useState(true)
  const [catalogsError, setCatalogsError] = useState<string | null>(null)

  const [preview, setPreview] = useState<PatchValidationWire | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [applyDiagnostics, setApplyDiagnostics] = useState<WorkflowDraftDiagnosticWire[]>([])
  const [applied, setApplied] = useState<PatchApplyResultWire | null>(null)
  const [riskAcknowledged, setRiskAcknowledged] = useState(false)

  // One patch id per editor session — preview and apply must share it so the
  // server can treat them as the same logical patch.
  const workflowPatchIdRef = useRef<string | null>(null)
  if (workflowPatchIdRef.current === null) workflowPatchIdRef.current = patchId()
  const workflowPatchId = workflowPatchIdRef.current

  // Past/Future boundary: any node that was admitted/started (anything but
  // queued) is immutable history; WorkflowEditor renders those locked.
  const nodeStatuses = useMemo(() => {
    const map: Record<string, WorkflowStatus> = {}
    for (const item of view.nodes) map[item.node.node_id] = item.node.status
    return map
  }, [view.nodes])

  useEffect(() => {
    let cancelled = false
    setCatalogsLoading(true)
    setCatalogsError(null)
    Promise.all([client.listAgentDefinitions(), client.editorCatalogs()])
      .then(([nextAgents, catalogs]) => {
        if (cancelled) return
        setAgents(nextAgents)
        setContracts(catalogs.contracts)
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setCatalogsError(error instanceof Error ? error.message : 'Editor Catalog 加载失败')
        }
      })
      .finally(() => {
        if (!cancelled) setCatalogsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [client])

  const patch = useMemo<FutureGraphPatchWire | null>(
    () =>
      localSource === null
        ? null
        : buildPatchDraft({
            workflow_patch_id: workflowPatchId,
            workspace_id: workspaceId,
            run,
            source: localSource,
          }),
    [localSource, run, workflowPatchId, workspaceId],
  )

  // Latest patch snapshot: a validate response that resolves after the user
  // edited the graph must not re-arm apply against a different source.
  const patchRef = useRef<FutureGraphPatchWire | null>(null)
  patchRef.current = patch

  function handleSourceChange(source: WorkflowDefinitionSourceWire) {
    setLocalSource(source)
    // A preview belongs to one source snapshot; editing invalidates it so the
    // apply button can never run against a stale validation.
    setPreview(null)
    setRiskAcknowledged(false)
    setPreviewError(null)
    setApplyError(null)
    setApplyDiagnostics([])
  }

  async function handlePreview() {
    if (patch === null) return
    const snapshot = patch
    setPreviewing(true)
    setPreviewError(null)
    setApplyError(null)
    setApplyDiagnostics([])
    try {
      const result = await client.validatePatch(snapshot)
      // Discard a result for a graph the user has since edited; they must
      // preview again before apply is armed.
      if (patchRef.current === snapshot) setPreview(result)
    } catch (error) {
      if (patchRef.current === snapshot) {
        setPreview(null)
        setPreviewError(patchApplyErrorMessage(error))
      }
    } finally {
      setPreviewing(false)
    }
  }

  async function handleApply() {
    if (patch === null || preview === null || !canApplyPatch(preview, riskAcknowledged)) return
    setApplying(true)
    setApplyError(null)
    setApplyDiagnostics([])
    try {
      setApplied(await client.applyPatch(patch, commandId('patch_apply')))
    } catch (error) {
      setApplyError(patchApplyErrorMessage(error))
      if (error instanceof ApiError) setApplyDiagnostics(error.diagnostics)
    } finally {
      setApplying(false)
    }
  }

  return (
    <main className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center gap-3 border-b border-subtle px-4 py-2">
        <button type="button" className="editor-button" onClick={onClose}>
          返回
        </button>
        <h1 className="text-sm font-medium">编辑待定节点</h1>
        <span className="rounded-[8px] border border-paused px-2 py-0.5 text-xs text-paused">
          父运行已暂停
        </span>
        <span className="font-mono text-xs text-secondary">{shortId(run.workflow_run_id)}</span>
        <span className="text-xs text-secondary">
          补丁通过后父运行终止为 superseded，子运行从 running 继续
        </span>
        {catalogsLoading && (
          <span className="text-xs text-secondary">正在加载 Agent/Contract 目录…</span>
        )}
        {catalogsError !== null && (
          <span role="status" className="text-xs text-failed">
            {catalogsError}
          </span>
        )}
      </header>
      {revisionSource === null ? (
        <EmptyState
          title="无法从该 Revision 构建可编辑源"
          hint="Revision 数据不完整，无法编辑待定节点；请返回观察后重试。"
        />
      ) : (
        <>
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <WorkflowEditor
              source={localSource ?? revisionSource}
              diagnostics={preview?.diagnostics ?? []}
              agents={agents}
              contracts={contracts}
              disabled={applied !== null || applying}
              onChange={handleSourceChange}
              nodeStatuses={nodeStatuses}
            />
          </div>
          <section className="relative z-10 max-h-[45vh] shrink-0 overflow-y-auto border-t border-subtle bg-raised px-4 py-3">
            {applied !== null ? (
              <div className="rounded-[10px] border border-completed bg-base p-3">
                <p className="text-xs text-completed">{patchResultMessage(applied)}</p>
                <p className="mt-1 font-mono text-xs text-secondary">
                  新 Revision {shortId(applied.workflow_revision_id)}
                </p>
                <button
                  type="button"
                  className="editor-button mt-2 border-accent text-accent"
                  onClick={onClose}
                >
                  返回观察
                </button>
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className="editor-button border-accent text-accent"
                    disabled={previewing || applying}
                    onClick={() => void handlePreview()}
                  >
                    {previewing ? '正在校验…' : '预览补丁'}
                  </button>
                  {preview !== null && preview.valid && (
                    <>
                      {preview.risk?.level === 'elevated' && (
                        <label className="flex items-center gap-1.5 text-xs text-blocked">
                          <input
                            type="checkbox"
                            checked={riskAcknowledged}
                            onChange={(event) => setRiskAcknowledged(event.target.checked)}
                          />
                          我已知晓上述风险
                        </label>
                      )}
                      <button
                        type="button"
                        className={`editor-button ${
                          preview.risk?.level === 'elevated'
                            ? 'border-blocked text-blocked'
                            : 'border-accent text-accent'
                        }`}
                        disabled={!canApplyPatch(preview, riskAcknowledged) || applying}
                        onClick={() => void handleApply()}
                      >
                        {applying ? '正在应用…' : '确认应用补丁'}
                      </button>
                    </>
                  )}
                </div>
                {previewError !== null && (
                  <p
                    role="alert"
                    className="mt-2 rounded-[8px] border border-blocked px-2 py-1 text-xs text-failed"
                  >
                    {previewError}
                  </p>
                )}
                {preview !== null && !preview.valid && (
                  <div className="mt-2">
                    <p className="text-xs text-secondary">补丁校验未通过：</p>
                    {preview.diagnostics.length === 0 ? (
                      <p className="mt-1 text-xs text-failed">未提供具体诊断。</p>
                    ) : (
                      <ul className="mt-1 flex flex-col gap-0.5">
                        {preview.diagnostics.map((item) => (
                          <li
                            key={`${item.code}:${item.edge_id}`}
                            className={
                              item.severity === 'error'
                                ? 'text-xs text-failed'
                                : 'text-xs text-blocked'
                            }
                          >
                            {item.message}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
                {preview !== null && preview.valid && (
                  <div className="mt-2 rounded-[10px] border border-subtle bg-base p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-[8px] border border-completed px-2 py-0.5 text-xs text-completed">
                        校验通过
                      </span>
                      {preview.risk !== null && (
                        <span
                          className={`rounded-[8px] border px-2 py-0.5 text-xs ${
                            preview.risk.level === 'elevated'
                              ? 'border-blocked text-blocked'
                              : 'border-completed text-completed'
                          }`}
                        >
                          {preview.risk.level === 'elevated' ? '需确认的风险' : '低风险'}
                        </span>
                      )}
                    </div>
                    <ul className="mt-2 flex flex-col gap-1">
                      {diffSummaryLines(preview.diff).map((line) => (
                        <li key={line.label} className="text-xs">
                          <span className="text-secondary">{line.label}</span>
                          {line.value !== null && (
                            <span className="ml-1 font-mono">{line.value}</span>
                          )}
                        </li>
                      ))}
                      <li className="text-xs">
                        <span className="text-secondary">执行节点</span>
                        <span className="ml-1 font-mono">
                          {preview.execution_node_ids.length > 0
                            ? preview.execution_node_ids.join(', ')
                            : '（空）'}
                        </span>
                      </li>
                      <li className="text-xs">
                        <span className="text-secondary">继承 Past 节点</span>
                        <span className="ml-1 font-mono">
                          {preview.past_node_ids.length > 0
                            ? preview.past_node_ids.join(', ')
                            : '（空）'}
                        </span>
                      </li>
                    </ul>
                    {preview.risk?.level === 'elevated' && preview.risk.reasons.length > 0 && (
                      <ul className="mt-2 flex flex-col gap-0.5">
                        {preview.risk.reasons.map((reason) => (
                          <li key={reason} className="text-xs text-blocked">
                            {RISK_REASON_LABELS[reason] ?? <span className="font-mono">{reason}</span>}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
                {applyError !== null && (
                  <div role="alert" className="mt-2 rounded-[8px] border border-blocked px-2 py-1">
                    <p className="text-xs text-failed">{applyError}</p>
                    {applyDiagnostics.length > 0 && (
                      <ul className="mt-1 flex flex-col gap-0.5">
                        {applyDiagnostics.map((item) => (
                          <li
                            key={`${item.code}:${item.edge_id}`}
                            className={
                              item.severity === 'error'
                                ? 'text-xs text-failed'
                                : 'text-xs text-blocked'
                            }
                          >
                            {item.message}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </>
            )}
          </section>
        </>
      )}
    </main>
  )
}
