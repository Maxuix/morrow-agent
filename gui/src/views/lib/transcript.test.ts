import { describe, expect, it } from 'vitest'
import type { ActivityItem } from '../../api/activity'
import type { StageFrame, TimelineItem } from '../../api/chat'
import type { ApprovalWire, ControlReceiptWire } from '../../api/types'
import type { ActivityRun } from '../../state/activity'
import { buildTranscript, type WorkflowCardView } from './transcript'

// P10.1/P10.2: the unified mixed transcript — one ordered entry list, no
// separately appended blocks, plans/results anchored at their task message.

let seq = 0
const item = (over: Partial<TimelineItem> = {}): TimelineItem => ({
  item_id: `i${++seq}`,
  kind: 'user_message',
  workspace_id: 'ws',
  session_id: 'ses',
  order_key: [seq, 0, 0, `i${seq}`],
  source: {},
  revision: 1,
  content: 'hello',
  content_ref: null,
  ...over,
})
const run = (key: string, count = 1): ActivityRun => ({
  key,
  items: Array.from({length: count}, (_, i) => ({activity_id: `${key}:${i}`}) as ActivityItem),
})
const card = (over: Partial<WorkflowCardView> = {}): WorkflowCardView => ({
  client_message_id: `cmid_${++seq}`,
  position: seq,
  text: '任务目标',
  status: 'accepted',
  workflow_run_id: null,
  task_run_id: null,
  run_status: null,
  outcome: null,
  workflow: {workflow_definition_id: 'wfd', workflow_revision_id: 'wrev'},
  ...over,
})
const receipt = (over: Partial<ControlReceiptWire> = {}): ControlReceiptWire => ({
  command_id: `cmd_${++seq}`,
  client_message_id: null,
  session_id: 'ses',
  text: '继续',
  state: null,
  intent: 'resume',
  status: 'executed',
  disposition: null,
  message: null,
  result_kind: null,
  result_id: null,
  error_code: null,
  outcome: {},
  revision: 1,
  created_at: '2026-09-09T12:00:00Z',
  updated_at: '2026-09-09T12:00:00Z',
  ...over,
})
const approval = (id: string): ApprovalWire => ({approval_id: id, tool_name: 'bash', task_run_id: 'task_1'} as ApprovalWire)
const stage = (wf: string): StageFrame => ({
  workflow_run_id: wf, node_run_id: `node_${wf}`, stage: 'awaiting_model',
})
const base = {items: [], runs: [], stageFrames: [], busy: false, receipts: [], cards: [], draft: null, approvals: [], acceptReady: false}

describe('unified transcript merge (P10.1)', () => {
  it('anchors execution regions right after their host user message', () => {
    const user = item({source: {turn_id: 't1'}})
    const reply = item({kind: 'assistant_message', source: {turn_id: 't1'}})
    const transcript = buildTranscript({...base, items: [user, reply], runs: [run('turn:t1')]})
    expect(transcript.entries.map(e => e.kind)).toEqual(['item', 'region', 'item'])
    const region = transcript.entries[1]
    expect(region.kind === 'region' && region.id).toBe('region:turn:t1')
  })

  it('anchors workflow runs and cards via the root task mapping', () => {
    const user = item({source: {task_run_id: 'task_root'}})
    const transcript = buildTranscript({
      ...base,
      items: [user],
      runs: [run('wf:wrun_1')],
      cards: [card({task_run_id: 'task_root'})],
      rootTaskOf: wfId => (wfId === 'wrun_1' ? 'task_root' : null),
    })
    expect(transcript.entries.map(e => e.kind)).toEqual(['item', 'region', 'card'])
  })

  it('keeps unanchorable runs and cards at the tail in arrival order', () => {
    const transcript = buildTranscript({
      ...base,
      items: [item()],
      runs: [run('wf:wrun_x'), run('session')],
      cards: [card({task_run_id: 'task_missing'})],
    })
    expect(transcript.entries.map(e => e.kind)).toEqual(['item', 'card', 'region', 'region'])
  })

  it('appends durable control receipts in their accepted order after the spine', () => {
    const late = receipt({command_id: 'cmd_late', created_at: '2026-09-09T12:02:00Z'})
    const early = receipt({command_id: 'cmd_early', created_at: '2026-09-09T12:01:00Z'})
    const transcript = buildTranscript({...base, items: [item()], receipts: [late, early]})
    expect(transcript.entries.map(e => e.kind)).toEqual(['item', 'receipt', 'receipt'])
    expect(transcript.entries[1].kind === 'receipt' && transcript.entries[1].receipt.command_id).toBe('cmd_early')
  })

  it('never renders a duplicate trailing region: orphan stages once, pending only when truly idle', () => {
    const orphan = buildTranscript({...base, items: [item()], stageFrames: [stage('wrun_orphan')]})
    expect(orphan.entries).toHaveLength(2)
    expect(orphan.entries[1].kind === 'region' && orphan.entries[1].id).toBe('region:stages')
    // A real run's stage frames belong to that run, not the orphan region.
    const owned = buildTranscript({...base, items: [], runs: [run('wf:wrun_1')], stageFrames: [stage('wrun_1')]})
    expect(owned.entries.map(e => e.kind === 'region' && e.id)).toEqual(['region:wf:wrun_1'])
    // The busy placeholder appears only with nothing else.
    const pending = buildTranscript({...base, items: [], busy: true})
    expect(pending.entries.map(e => e.kind === 'region' && e.id)).toEqual(['region:pending'])
    const covered = buildTranscript({...base, items: [], runs: [run('session')], busy: true})
    expect(covered.entries.map(e => e.kind === 'region' && e.id)).toEqual(['region:session'])
  })

  it('merges old and new segments of one run into a single continuous region', () => {
    const transcript = buildTranscript({...base, runs: [run('wf:wrun_1', 2), run('wf:wrun_1', 3)]})
    expect(transcript.entries).toHaveLength(1)
    expect(transcript.entries[0].kind === 'region' && transcript.entries[0].run.items).toHaveLength(5)
  })

  it('drops the live draft once its durable replacement arrived', () => {
    const draft = {message_id: 'i_draft', text: 'partial'}
    const pending = buildTranscript({...base, items: [item()], draft})
    expect(pending.entries.at(-1)?.kind).toBe('draft')
    const committed = buildTranscript({...base, items: [item(), item({item_id: 'i_draft', kind: 'assistant_message'})], draft})
    expect(committed.entries.some(e => e.kind === 'draft')).toBe(false)
  })

  it('keeps approval cards and the acceptance entry at the tail', () => {
    const transcript = buildTranscript({
      ...base, items: [item()], approvals: [approval('ap1'), approval('ap2')], acceptReady: true,
    })
    expect(transcript.entries.map(e => e.kind)).toEqual(['item', 'approval', 'approval', 'acceptance'])
  })

  it('anchors a task approval after its originating user message and deduplicates projections', () => {
    const user = item({source: {task_run_id: 'task_1'}})
    const reply = item({kind: 'assistant_message'})
    const transcript = buildTranscript({
      ...base,
      items: [user, reply],
      approvals: [approval('approval_1'), approval('approval_1'), {...approval('approval_2'), task_run_id: 'task_missing'}],
    })
    expect(transcript.entries.map(e => e.kind)).toEqual(['item', 'approval', 'item', 'approval'])
    expect(transcript.entries.filter(e => e.kind === 'approval')).toHaveLength(2)
  })
})

describe('transcript empty state (P10.2)', () => {
  it('is empty only when no source produced anything', () => {
    expect(buildTranscript(base).empty).toBe(true)
    expect(buildTranscript({...base, approvals: [approval('ap1')]}).empty).toBe(false)
    expect(buildTranscript({...base, receipts: [receipt()]}).empty).toBe(false)
    expect(buildTranscript({...base, cards: [card()]}).empty).toBe(false)
    expect(buildTranscript({...base, draft: {message_id: 'd', text: 'x'}}).empty).toBe(false)
    expect(buildTranscript({...base, items: [item()]}).empty).toBe(false)
    expect(buildTranscript({...base, busy: true}).empty).toBe(false)
  })
})
