import type { StageFrame, TimelineItem } from '../../api/chat'
import type { ApprovalWire, ControlReceiptWire } from '../../api/types'
import { uniqueApprovals } from '../../state/approvalDecision'
import type { ActivityRun } from '../../state/activity'

/**
 * Unified mixed-transcript merge (P10.1, D11/D15).
 *
 * One ordered entry list drives the whole chat transcript: timeline items,
 * execution regions, workflow task cards, durable control receipts, the live
 * reply draft and pending approvals. Nothing renders as a separately appended
 * block any more, and every entry carries a deterministic position:
 *
 * - items keep their server order_key as the spine;
 * - execution regions and workflow cards anchor right after their host user
 *   message (turn id / task id), so plans and results stay where the task
 *   started — unanchorable runs fall back to the tail in arrival order;
 * - control receipts keep their durable created_at order after the spine;
 * - stage-frame fallback and the busy placeholder are regions too, so a
 *   duplicate trailing region cannot coexist with a real one;
 * - approvals anchor after their task's user message when possible; the draft
 *   and unanchorable approvals stay at the tail.
 *
 * The merged list is intentionally shape-compatible with the C-lane unified
 * timeline page (item_id/parent anchoring): when Core serves one ordered
 * stream, this merge degrades to a pass-through.
 */

/** One workflow task card as served by `/workflow-interactions`. */
export interface WorkflowCardView {
  client_message_id: string
  position: number
  text: string
  status: string
  workflow_run_id: string | null
  task_run_id: string | null
  run_status: string | null
  outcome: {summary: string; task_status: string} | null
  workflow: {workflow_definition_id: string; workflow_revision_id: string}
}

export type TranscriptEntry =
  | {kind: 'item'; id: string; item: TimelineItem}
  | {kind: 'region'; id: string; run: ActivityRun; stages: StageFrame[]; placeholder?: 'pending'}
  | {kind: 'card'; id: string; card: WorkflowCardView}
  | {kind: 'receipt'; id: string; receipt: ControlReceiptWire}
  | {kind: 'draft'; id: string; text: string}
  | {kind: 'approval'; id: string; approval: ApprovalWire}
  | {kind: 'acceptance'; id: string}

export interface TranscriptSource {
  items: TimelineItem[]
  /** Activity runs (already grouped per run key) to place in the transcript. */
  runs: ActivityRun[]
  /** Live stage frames backing regions and the no-activity fallback. */
  stageFrames: StageFrame[]
  busy: boolean
  receipts: ControlReceiptWire[]
  cards: WorkflowCardView[]
  draft: {message_id: string; text: string} | null
  approvals: ApprovalWire[]
  /** Root task result is ready for acceptance. */
  acceptReady: boolean
  /** `wf:{workflow_run_id}` → root task run id, from the durable run view. */
  rootTaskOf?: (workflowRunId: string) => string | null
}

export interface Transcript {
  entries: TranscriptEntry[]
  /** True only when no source produced anything (P10.2 empty state). */
  empty: boolean
}

/** Region key → run; defensively merges duplicates for continuous grouping. */
function mergeRuns(runs: ActivityRun[]): ActivityRun[] {
  const merged = new Map<string, ActivityRun>()
  for (const run of runs) {
    const existing = merged.get(run.key)
    if (existing) existing.items.push(...run.items)
    else merged.set(run.key, {...run, items: [...run.items]})
  }
  return [...merged.values()]
}

export function buildTranscript(source: TranscriptSource): Transcript {
  const {items} = source
  const runs = mergeRuns(source.runs)
  const entries: TranscriptEntry[] = []
  // Anchors: execution region / workflow card → host item id.
  const regionHost = new Map<string, ActivityRun>()
  const cardHost = new Map<string, WorkflowCardView>()
  const approvalHost = new Map<string, ApprovalWire[]>()
  const trailingRuns: ActivityRun[] = []
  const trailingApprovals: ApprovalWire[] = []

  for (const item of items) {
    entries.push({kind: 'item', id: item.item_id, item})
  }
  for (const run of runs) {
    let host: TimelineItem | undefined
    if (run.key.startsWith('turn:')) {
      const turnId = run.key.slice(5)
      host = items.find(i => i.kind === 'user_message' && i.source.turn_id === turnId)
    } else {
      const rootTask = run.key.startsWith('wf:') ? source.rootTaskOf?.(run.key.slice(3)) ?? null : null
      host = rootTask ? items.find(i => i.kind === 'user_message' && i.source.task_run_id === rootTask) : undefined
    }
    if (host) regionHost.set(host.item_id, run)
    else trailingRuns.push(run)
  }
  const seenCards = new Set<string>()
  for (const card of source.cards) {
    if (seenCards.has(card.client_message_id)) continue
    seenCards.add(card.client_message_id)
    const host = card.task_run_id
      ? items.find(i => i.kind === 'user_message' && i.source.task_run_id === card.task_run_id)
      : undefined
    if (host) cardHost.set(host.item_id, card)
      else entries.push({kind: 'card', id: `card:${card.client_message_id}`, card})
  }
  for (const approval of uniqueApprovals(source.approvals)) {
    const host = items.find(item => item.kind === 'user_message' && item.source.task_run_id === approval.task_run_id)
    if (host) {
      const approvals = approvalHost.get(host.item_id) ?? []
      approvals.push(approval)
      approvalHost.set(host.item_id, approvals)
    } else {
      trailingApprovals.push(approval)
    }
  }
  // Interleave anchored entries right after their host item, then tail entries.
  const withAnchors: TranscriptEntry[] = []
  for (const entry of entries) {
    withAnchors.push(entry)
    const region = regionHost.get(entry.id)
    if (region) {
      withAnchors.push({
        kind: 'region',
        id: `region:${region.key}`,
        run: region,
        stages: region.key.startsWith('wf:')
          ? source.stageFrames.filter(frame => frame.workflow_run_id === region.key.slice(3))
          : [],
      })
    }
    const card = cardHost.get(entry.id)
    if (card) withAnchors.push({kind: 'card', id: `card:${card.client_message_id}`, card})
    const approvals = approvalHost.get(entry.id)
    if (approvals) {
      for (const approval of approvals) {
        withAnchors.push({kind: 'approval', id: `approval:${approval.approval_id}`, approval})
      }
    }
  }
  // Durable control receipts keep their accepted order in the flow.
  const receipts = [...source.receipts].sort((a, b) =>
    a.created_at === b.created_at ? a.command_id.localeCompare(b.command_id) : a.created_at.localeCompare(b.created_at),
  )
  for (const receipt of receipts) {
    withAnchors.push({kind: 'receipt', id: `receipt:${receipt.command_id}`, receipt})
  }
  // Unanchorable regions follow; the busy placeholder never duplicates them.
  for (const run of trailingRuns) {
    withAnchors.push({
      kind: 'region',
      id: `region:${run.key}`,
      run,
      stages: run.key.startsWith('wf:')
        ? source.stageFrames.filter(frame => frame.workflow_run_id === run.key.slice(3))
        : [],
    })
  }
  // Stage frames of runs without any activity items stay one fallback region.
  const orphanStages = source.stageFrames.filter(frame =>
    !runs.some(run => run.key === `wf:${frame.workflow_run_id}`),
  )
  if (orphanStages.length > 0) {
    withAnchors.push({kind: 'region', id: 'region:stages', run: {key: 'stages', items: []}, stages: orphanStages})
  } else if (source.busy && runs.length === 0 && source.stageFrames.length === 0) {
    withAnchors.push({kind: 'region', id: 'region:pending', run: {key: 'pending', items: []}, stages: [], placeholder: 'pending'})
  }
  // Tail: live draft (unless its durable replacement arrived) and approvals.
  const draft = source.draft
  if (draft && !items.some(i => i.item_id === draft.message_id)) {
    withAnchors.push({kind: 'draft', id: 'draft', text: draft.text})
  }
  for (const approval of trailingApprovals) {
    withAnchors.push({kind: 'approval', id: `approval:${approval.approval_id}`, approval})
  }
  if (source.acceptReady) withAnchors.push({kind: 'acceptance', id: 'acceptance'})
  return {entries: withAnchors, empty: withAnchors.length === 0}
}
