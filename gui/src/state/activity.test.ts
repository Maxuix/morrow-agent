import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../api/client'
import type { ActivityItem } from '../api/activity'
import {
  ACTIVITY_METADATA_LIMIT,
} from '../api/activity'
import {
  ActivityStore,
  applyAvailability,
  applyDelta,
  applyRekey,
  applyReset,
  applyUpsert,
  buildParts,
  buildRunNodes,
  durationMsText,
  durationText,
  FrozenClock,
  groupByRun,
  mergeDurable,
  runFacts,
  runKeyOf,
  runOverview,
  settleRunFacts,
  toolCopy,
  toolGroupSummary,
} from './activity'

let seq = 0
const BASE = Date.parse('2026-09-08T00:00:00Z')
const at = (seconds: number) => new Date(BASE + seconds * 1000).toISOString()
const item = (over: Partial<ActivityItem> = {}): ActivityItem => ({
  schema_version: 1,
  activity_id: `act_call_${++seq}`,
  revision: 1,
  kind: 'tool',
  state: 'preparing',
  origin: 'agent_loop',
  identity: {workspace_id: 'ws', root_session_id: 's', source_session_id: 's', agent_run_id: 'arun_1', turn_id: 't1'},
  payload: {kind: 'tool', tool_name: 'read', call_id: `c${seq}`, ordinal: 1, total: 2},
  started_at: at(0),
  updated_at: at(0),
  ended_at: null,
  last_activity_at: null,
  safe_title: 'read',
  safe_summary: null,
  preview_ref: null,
  truncated: false,
  availability: 'none',
  ...over,
})
const tool = (name: string, title: string, over: Partial<ActivityItem> = {}): ActivityItem => item({
  payload: {kind: 'tool', tool_name: name, call_id: `c_${name}_${++seq}`},
  safe_title: title,
  ...over,
})
const model = (over: Partial<ActivityItem> = {}): ActivityItem => item({
  activity_id: `act_model_${++seq}`,
  kind: 'model',
  state: 'running',
  payload: {kind: 'model', stage: 'thinking'},
  ...over,
})

describe('activity reducer', () => {
  it('upserts by id and rejects stale revisions', () => {
    const first = item({activity_id: 'act_a', revision: 2, state: 'succeeded', ended_at: at(5)})
    const items = applyUpsert([], first)
    expect(applyUpsert(items, item({activity_id: 'act_a', revision: 1}))).toBe(items)
    expect(applyUpsert(items, {...first, revision: 2})).toBe(items)
  })

  it('never regresses a terminal item to running', () => {
    const done = item({activity_id: 'act_a', revision: 2, state: 'succeeded', ended_at: at(5)})
    const items = applyUpsert([], done)
    expect(applyUpsert(items, item({activity_id: 'act_a', revision: 3, state: 'running'}))).toBe(items)
  })

  it('keeps the earliest started_at across revisions', () => {
    const first = item({activity_id: 'act_a', started_at: at(0)})
    const updated = applyUpsert(applyUpsert([], first), item({activity_id: 'act_a', revision: 2, started_at: at(60)}))
    expect(updated[0].started_at).toBe(at(0))
  })

  it('stays bounded and prefers evicting completed metadata', () => {
    let items: ActivityItem[] = []
    const running = item({activity_id: 'act_running', state: 'preparing'})
    items = applyUpsert(items, running)
    for (let i = 0; i < ACTIVITY_METADATA_LIMIT + 5; i++) {
      items = applyUpsert(items, item({activity_id: `act_done_${i}`, state: 'succeeded', ended_at: at(5)}))
    }
    expect(items.length).toBeLessThanOrEqual(ACTIVITY_METADATA_LIMIT)
    expect(items.some(entry => entry.activity_id === 'act_running')).toBe(true)
  })
})

describe('activity content', () => {
  it('appends bounded deltas and marks truncation at the item cap', () => {
    let content = applyDelta({}, 'act_a', '第一段')
    expect(content.act_a?.text).toBe('第一段')
    const chunk = '字'.repeat(1300) // ~3.8 KiB, within the 4 KiB frame bound
    for (let i = 0; i < 9; i++) content = applyDelta(content, 'act_a', chunk)
    expect(content.act_a?.truncated).toBe(true)
  })

  it('drops oversize deltas and buffers early arrivals boundedly', () => {
    expect(applyDelta({}, 'act_a', 'x'.repeat(5000))).toEqual({})
    expect(applyDelta({}, 'act_a', '早到片段')).toEqual({act_a: {text: '早到片段', truncated: false}})
  })

  it('resets cached content', () => {
    const content = applyDelta({}, 'act_a', '片段')
    expect(applyReset(content, 'act_a')).toEqual({})
  })

  it('rekeys the prepared item and its cached content to the stable identity', () => {
    const prepared = item({activity_id: 'act_call_c1'})
    const other = item({activity_id: 'act_model_t1_1'})
    const content = applyDelta({}, 'act_call_c1', '输出片段')
    const merged = applyRekey([prepared, other], content, 'act_call_c1', 'act_tool_tex_1')
    expect(merged.items.map(entry => entry.activity_id)).toEqual(['act_tool_tex_1', 'act_model_t1_1'])
    expect(merged.items[0].revision).toBe(prepared.revision)
    expect(merged.content.act_tool_tex_1).toEqual({text: '输出片段', truncated: false})
    expect(merged.content.act_call_c1).toBeUndefined()
  })

  it('rekey without a live prepared row still carries the content across', () => {
    const merged = applyRekey([], applyDelta({}, 'act_call_c1', '片段'), 'act_call_c1', 'act_tool_tex_1')
    expect(merged.items).toEqual([])
    expect(merged.content.act_tool_tex_1?.text).toBe('片段')
  })

  it('rekey drops the stale prepared row when the stable row already exists', () => {
    const prepared = item({activity_id: 'act_call_c1'})
    const stable = item({activity_id: 'act_tool_tex_1', state: 'succeeded', ended_at: at(5)})
    const merged = applyRekey([prepared, stable], {}, 'act_call_c1', 'act_tool_tex_1')
    expect(merged.items.map(entry => entry.activity_id)).toEqual(['act_tool_tex_1'])
  })

  it('writes delta availability back onto the item', () => {
    const live = item({activity_id: 'act_a'})
    const committed = applyAvailability([live], 'act_a', 'committed')
    expect(committed).toEqual([{...live, availability: 'committed'}])
    // Unchanged availability keeps the original array reference.
    expect(applyAvailability(committed, 'act_a', 'committed')).toBe(committed)
    // Unknown ids are left alone.
    expect(applyAvailability(committed, 'act_missing', 'evicted')).toBe(committed)
  })
})

describe('durable merge', () => {
  it('dedupes by execution identity and keeps recovered leftovers', () => {
    const live = item({activity_id: 'act_call_live', payload: {kind: 'tool', tool_name: 'read', call_id: 'provider_1', ordinal: 1}})
    const durableSame = item({activity_id: 'act_tool_durable', payload: {kind: 'tool', tool_name: 'read', call_id: 'normalized_hash', ordinal: 1}, availability: 'unsaved'})
    const durableOther = item({activity_id: 'act_tool_durable2', payload: {kind: 'tool', tool_name: 'read', call_id: 'normalized_hash2', ordinal: 2}, availability: 'unsaved'})
    const merged = mergeDurable([live], {items: [durableSame, durableOther], truncated: false})
    expect(merged.map(entry => entry.activity_id)).toEqual(['act_call_live', 'act_tool_durable2'])
  })
})

describe('run attribution', () => {
  it('keys runs by workflow run, then turn, else session', () => {
    expect(runKeyOf(item({identity: {workspace_id: 'ws', root_session_id: 's', source_session_id: 's', workflow_run_id: 'w1', node_run_id: 'n1'}}))).toBe('wf:w1')
    expect(runKeyOf(item({}))).toBe('turn:t1')
    expect(runKeyOf(item({identity: {workspace_id: 'ws', root_session_id: 's', source_session_id: 's'}}))).toBe('session')
  })

  it('partitions items into insertion-ordered runs', () => {
    const a = item({activity_id: 'a'})
    const b = item({activity_id: 'b', identity: {workspace_id: 'ws', root_session_id: 's', source_session_id: 's', turn_id: 't2'}})
    const runs = groupByRun([a, b, item({activity_id: 'c'})])
    expect(runs.map(run => run.key)).toEqual(['turn:t1', 'turn:t2'])
    expect(runs[0].items.map(entry => entry.activity_id)).toEqual(['a', 'c'])
  })
})

describe('run overview copy', () => {
  const overview = (items: ActivityItem[], now: number, opts: {busy?: boolean; syncing?: boolean} = {}) =>
    runOverview(runFacts(items), {now, ...opts})

  it('keeps quiet when idle and shows 等待执行 once busy', () => {
    expect(overview([], BASE).text).toBe('')
    expect(overview([], BASE, {busy: true}).text).toBe('等待执行')
  })

  it('answers the running question with one focus and one elapsed span', () => {
    const now = Date.parse(at(34))
    expect(overview([model({started_at: at(0)})], now).text).toBe('正在思考 · 34 秒')
    expect(overview([tool('read', 'read gui/src/App.tsx', {state: 'running', started_at: at(0)})], Date.parse(at(12))).text)
      .toBe('正在读取 gui/src/App.tsx · 12 秒')
  })

  it('counts parallel nodes once instead of stacking durations', () => {
    const nodeA = {workspace_id: 'ws', root_session_id: 's', source_session_id: 's', workflow_run_id: 'w1', node_run_id: 'n1', node_id: 'left'} as const
    const nodeB = {...nodeA, node_run_id: 'n2', node_id: 'right'} as const
    const items = [
      item({identity: {...nodeA}, state: 'running', started_at: at(0)}),
      item({identity: {...nodeB}, state: 'running', started_at: at(10)}),
    ]
    expect(overview(items, Date.parse(at(34))).text).toBe('2 个节点执行中 · 34 秒')
  })

  it('surfaces pending approvals without digging', () => {
    const approval = item({kind: 'approval', payload: {kind: 'approval', approval_id: 'ap1'}, state: 'waiting'})
    const view = overview([approval], Date.parse(at(10)))
    expect(view.text).toBe('等待批准')
    expect(view.tone).toBe('attention')
  })

  it('uses the run start/end for the settled total, never summed node spans', () => {
    const sequential = [
      model({activity_id: 'm1', state: 'succeeded', started_at: at(0), ended_at: at(10), payload: {kind: 'model', stage: 'responding'}}),
      model({activity_id: 'm2', state: 'succeeded', started_at: at(10), ended_at: at(38), payload: {kind: 'model', stage: 'responding'}}),
    ]
    expect(overview(sequential, Date.parse(at(99))).text).toBe('用时 38 秒')
    const nodeA = {workspace_id: 'ws', root_session_id: 's', source_session_id: 's', workflow_run_id: 'w1', node_run_id: 'n1'} as const
    const nodeB = {...nodeA, node_run_id: 'n2'} as const
    const parallel = [
      item({identity: {...nodeA}, state: 'succeeded', started_at: at(0), ended_at: at(30)}),
      item({identity: {...nodeB}, state: 'succeeded', started_at: at(10), ended_at: at(38)}),
    ]
    expect(overview(parallel, Date.parse(at(99))).text).toBe('用时 38 秒')
  })

  it('marks failures and stops, and never calls a steered turn stopped', () => {
    const failed = [tool('read', 'read config.json', {state: 'failed', ended_at: at(38), started_at: at(0)})]
    const failedView = overview(failed, Date.parse(at(99)))
    expect(failedView.text).toBe('执行失败 · 38 秒')
    expect(failedView.tone).toBe('failed')
    const stopped = [model({state: 'cancelled', ended_at: at(38), payload: {kind: 'model', stage: 'responding'}})]
    expect(overview(stopped, Date.parse(at(99))).text).toBe('已停止 · 38 秒')
    const steered = [
      model({activity_id: 'm1', state: 'cancelled', started_at: at(0), ended_at: at(10), payload: {kind: 'model', stage: 'responding'}}),
      model({activity_id: 'm2', state: 'succeeded', started_at: at(10), ended_at: at(20), payload: {kind: 'model', stage: 'responding'}}),
    ]
    expect(overview(steered, Date.parse(at(99))).text).toBe('用时 20 秒')
  })

  it('reports a disconnect as syncing with the last known state', () => {
    const items = [tool('read', 'read gui/src/App.tsx', {state: 'running', started_at: at(0)})]
    expect(overview(items, Date.parse(at(12)), {syncing: true}).text)
      .toBe('状态同步中 · 上次状态：正在读取 gui/src/App.tsx')
  })

  it('keeps a per-item failure visible even when the run succeeded', () => {
    const items = [
      tool('bash', 'bash pnpm test', {state: 'failed', started_at: at(0), ended_at: at(5)}),
      model({state: 'succeeded', started_at: at(5), ended_at: at(38), payload: {kind: 'model', stage: 'responding'}}),
    ]
    expect(overview(items, Date.parse(at(99))).text).toBe('用时 38 秒 · 1 项失败')
  })

  it('falls back to stage frames when no activity items exist', () => {
    const view = runOverview(runFacts([]), {now: Date.parse(at(12)), stageText: '正在执行工具 · read'})
    expect(view.text).toBe('正在执行工具 · read')
    expect(view.kind).toBe('running')
  })

  it('hides elapsed spans without parsable timestamps', () => {
    expect(durationMsText(null, null, 1)).toBe('')
    expect(durationText('not-a-date', null, 1)).toBe('')
    expect(durationText(at(0), at(59), Date.parse(at(65)))).toBe('59 秒')
    expect(durationText(at(0), at(65), Date.parse(at(65)))).toBe('1 分 5 秒')
  })
})

describe('tool action copy', () => {
  it('derives human summaries from the projected title only', () => {
    expect(toolCopy(tool('find', 'find drawing.html', {state: 'succeeded'})).rowLabel).toBe('已搜索 drawing.html')
    expect(toolCopy(tool('read', 'read gui/src/views/ChatComposer.tsx', {state: 'succeeded'})).rowLabel).toBe('已读取 gui/src/views/ChatComposer.tsx')
    expect(toolCopy(tool('ls', 'ls gui', {state: 'succeeded'})).rowLabel).toBe('已列出 gui 中的文件')
    expect(toolCopy(tool('bash', 'bash pnpm test', {state: 'succeeded'})).rowLabel).toBe('已运行 pnpm test')
  })

  it('degrades to category nouns and keeps failures factual', () => {
    expect(toolCopy(tool('read', 'read', {state: 'succeeded'})).rowLabel).toBe('已读取文件')
    expect(toolCopy(tool('read', 'read config.json', {state: 'failed'})).failedLabel).toBe('读取 config.json 失败')
    expect(toolCopy(tool('read', 'read', {state: 'failed'})).failedLabel).toBe('读取文件失败')
    expect(toolCopy(tool('read', 'read drawing.html', {state: 'running'})).runningLabel).toBe('正在读取 drawing.html')
    expect(toolCopy(tool('web_search', 'web_search', {state: 'succeeded'})).rowLabel).toBe('已调用 web_search')
  })

  it('aggregates consecutive calls without re-classifying the run', () => {
    const items = [
      tool('read', 'read a.ts', {state: 'succeeded'}),
      tool('read', 'read b.ts', {state: 'succeeded'}),
      tool('read', 'read c.ts', {state: 'succeeded'}),
      tool('bash', 'bash pnpm test', {state: 'succeeded'}),
      tool('bash', 'bash pnpm build', {state: 'succeeded'}),
    ]
    expect(toolGroupSummary(items)).toBe('读取了 3 个文件，运行了 2 条命令')
  })
})

describe('content grouping', () => {
  it('merges consecutive tool calls and lets thinking end the group', () => {
    const thinking = model({activity_id: 'act_model_1', state: 'succeeded'})
    const items = [
      tool('read', 'read a.ts', {activity_id: 't1', state: 'succeeded'}),
      tool('ls', 'ls gui', {activity_id: 't2', state: 'succeeded'}),
      thinking,
      tool('bash', 'bash pnpm test', {activity_id: 't3', state: 'succeeded'}),
    ]
    const parts = buildParts(items, {act_model_1: {text: '先看目录结构', truncated: false}})
    expect(parts.map(part => part.kind)).toEqual(['tools', 'thinking', 'tools'])
    expect(parts[0].kind === 'tools' && parts[0].items.map(entry => entry.activity_id)).toEqual(['t1', 't2'])
  })

  it('folds finished stage markers and keeps live ones or thinking text', () => {
    const finished = model({activity_id: 'm_done', state: 'succeeded', payload: {kind: 'model', stage: 'thinking'}})
    const live = model({activity_id: 'm_live', state: 'running'})
    const withText = model({activity_id: 'm_text', state: 'succeeded'})
    const parts = buildParts([finished, live, withText], {m_text: {text: '思路', truncated: false}})
    expect(parts.map(part => part.kind)).toEqual(['stage', 'thinking'])
  })

  it('hoists verifiable asset references out of the tool flow', () => {
    const items = [
      tool('read', 'read a.png', {activity_id: 't1', state: 'succeeded', preview_ref: '/v1/workspaces/ws/sessions/se1/artifacts/a1/content'}),
      tool('write', 'write b.png', {activity_id: 't2', state: 'succeeded', preview_ref: '/v1/workspaces/ws/sessions/se1/artifacts/a2/content'}),
      tool('bash', 'bash ls', {activity_id: 't3', state: 'succeeded'}),
    ]
    const parts = buildParts(items, {})
    expect(parts.map(part => part.kind)).toEqual(['assets', 'tools'])
    const assets = parts[0]
    expect(assets.kind === 'assets' && assets.items.map(entry => entry.activity_id)).toEqual(['t1', 't2'])
  })

  it('keeps retries, approvals and control receipts in execution order', () => {
    const items = [
      tool('read', 'read a.ts', {activity_id: 't1', state: 'succeeded'}),
      item({activity_id: 'r1', kind: 'retry', state: 'running', payload: {kind: 'retry', attempt_ordinal: 2, retry_delay_seconds: 4}}),
      item({activity_id: 'ap1', kind: 'approval', state: 'waiting', payload: {kind: 'approval', approval_id: 'ap1'}}),
      item({activity_id: 'ctl1', kind: 'control', state: 'succeeded', payload: {kind: 'control', command: 'steer', receipt: 'applied'}}),
    ]
    expect(buildParts(items, {}).map(part => part.kind)).toEqual(['tools', 'retry', 'approval', 'control'])
  })
})

describe('node grouping', () => {
  const nodeItem = (nodeRun: string, nodeId: string, over: Partial<ActivityItem> = {}): ActivityItem => item({
    identity: {workspace_id: 'ws', root_session_id: 'root', source_session_id: 'leaf',
      workflow_run_id: 'wfr', node_run_id: nodeRun, node_id: nodeId, agent_run_id: 'arun'},
    ...over,
  })

  it('returns one unlabelled group for plain chat', () => {
    const {nodes, plain} = buildRunNodes([item({activity_id: 'x'}), item({activity_id: 'y'})], {})
    expect(plain).toBe(true)
    expect(nodes).toHaveLength(1)
    expect(nodes[0].label).toBe('')
  })

  it('groups by node and disambiguates same-named nodes by execution id', () => {
    const items = [
      nodeItem('nrun_1', 'left', {activity_id: 'a'}),
      nodeItem('nrun_2', 'right', {activity_id: 'b'}),
      nodeItem('nrun_3', 'left', {activity_id: 'c'}),
    ]
    const {nodes, plain} = buildRunNodes(items, {})
    expect(plain).toBe(false)
    // Both same-named nodes carry the execution-id suffix so rows never blur.
    expect(nodes.map(node => node.label)).toEqual(['left · nrun_1', 'right', 'left · nrun_3'])
    expect(nodes.map(node => node.items.length)).toEqual([1, 1, 1])
  })
})

describe('owner-terminal precedence', () => {
  it('never keeps a stale running item when the owning run already ended', () => {
    const running = item({
      activity_id: 'act_model_1', kind: 'model', state: 'running',
      payload: {kind: 'model', stage: 'thinking'},
      started_at: at(0), updated_at: at(0), last_activity_at: at(0), ended_at: null,
    })
    const live = settleRunFacts([running], 'running')
    expect(live.active).toBe(true)
    expect(live.interrupted).toBe(false)

    const cancelled = settleRunFacts([running], 'cancelled')
    expect(cancelled.active).toBe(false)
    expect(cancelled.interrupted).toBe(true)
    expect(cancelled.cancelled).toBe(true)
    expect(runOverview(cancelled, {now: Date.parse(at(30))}).text).toContain('已中断')

    const done = settleRunFacts(
      [item({state: 'succeeded', ended_at: at(5), updated_at: at(5)})],
      'completed',
    )
    expect(done.active).toBe(false)
    expect(done.interrupted).toBe(false)
    expect(runOverview(done, {now: Date.parse(at(30))}).text).toContain('用时')
  })

  it('keeps unknown owners untouched', () => {
    const running = item({state: 'running', ended_at: null})
    expect(settleRunFacts([running], null).active).toBe(true)
  })
})

describe('frozen clock (P10.3)', () => {
  it('excludes paused and disconnected spans from elapsed time', () => {
    let nowMs = 1_000
    const clock = new FrozenClock(() => nowMs)
    expect(clock.now()).toBe(1_000)
    // Paused at 1s; wall clock runs to 61s but the region clock stays put.
    clock.freeze()
    nowMs = 61_000
    expect(clock.frozen).toBe(true)
    expect(clock.now()).toBe(1_000)
    // Resumed at 61s: elapsed resumes without the 60s gap.
    clock.unfreeze()
    nowMs = 91_000
    expect(clock.now()).toBe(31_000)
    // A second frozen span accumulates too: live time resumes from 31s and
    // the 10s gap is excluded again.
    clock.freeze()
    nowMs = 101_000
    expect(clock.now()).toBe(31_000)
    clock.unfreeze()
    nowMs = 111_000
    expect(clock.now()).toBe(41_000)
  })

  it('starts frozen without charging the pre-observation span', () => {
    let nowMs = 5_000
    const clock = new FrozenClock(() => nowMs)
    clock.freeze()
    nowMs = 45_000
    expect(clock.now()).toBe(5_000)
    clock.unfreeze()
    nowMs = 46_000
    expect(clock.now()).toBe(6_000)
  })

  it('labels a settled run without a start fact as 时长未知 instead of inventing a span', () => {
    // No parsable start fact: the overview must not invent an elapsed span.
    const facts = runFacts([tool('read', 'read a.ts', {state: 'failed', started_at: 'not-a-date', ended_at: at(9), updated_at: at(9)})])
    const view = runOverview({...facts, active: false}, {now: Date.parse(at(10))})
    expect(view.kind).toBe('settled')
    expect(view.text).toContain('时长未知')
  })
})

describe('ActivityStore reconnection', () => {
  class FakeSocket {
    static instances: FakeSocket[] = []

    onopen: (() => void) | null = null
    onmessage: ((event: {data: string}) => void) | null = null
    onclose: (() => void) | null = null
    closed = false
    sent: string[] = []

    constructor(readonly url: string) {
      FakeSocket.instances.push(this)
    }

    send(text: string) {
      this.sent.push(text)
    }

    close() {
      this.closed = true
    }
  }

  const SNAPSHOT = {activities: [], activity_epoch: 'e1', activity_sequence: 0}

  function fakeClient(snapshot: () => Promise<unknown>) {
    return {
      chatActivitySnapshot: snapshot,
      chatActivities: async () => ({items: []}),
      chatSocketUrl: () => 'ws://test/activity',
    } as unknown as ApiClient
  }

  function makeStore(snapshot: () => Promise<unknown>) {
    return new ActivityStore(fakeClient(snapshot), 'ws', 'ses', url => new FakeSocket(url))
  }

  beforeEach(() => {
    FakeSocket.instances = []
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('retries a failed snapshot on the backoff timer and recovers', async () => {
    vi.useFakeTimers()
    let snapshotCalls = 0
    const store = makeStore(async () => {
      snapshotCalls += 1
      if (snapshotCalls < 3) throw new Error('boom')
      return SNAPSHOT
    })
    await store.start()
    expect(store.getState().status).toBe('reconnecting')
    expect(snapshotCalls).toBe(1)
    await vi.advanceTimersByTimeAsync(300)
    expect(snapshotCalls).toBe(2)
    expect(store.getState().status).toBe('reconnecting')
    await vi.advanceTimersByTimeAsync(600)
    expect(snapshotCalls).toBe(3)
    expect(FakeSocket.instances).toHaveLength(1)
    expect(store.getState().status).not.toBe('offline')
    store.stop()
  })

  it('closes the old socket on gap reconnect and resets attempts once live', async () => {
    vi.useFakeTimers()
    const store = makeStore(async () => SNAPSHOT)
    await store.start()
    const first = FakeSocket.instances[0]
    first.onopen?.()
    // A frame from another epoch while the socket is still open forces a gap
    // reconnect; the old socket must be closed with its handlers detached.
    first.onmessage?.({data: JSON.stringify({type: 'heartbeat', stream_epoch: 'other', sequence: 1})})
    expect(first.closed).toBe(true)
    expect(first.onclose).toBeNull()
    expect(store.getState().status).toBe('reconnecting')
    await vi.advanceTimersByTimeAsync(300)
    expect(FakeSocket.instances).toHaveLength(2)
    const second = FakeSocket.instances[1]
    second.onopen?.()
    // Opening a fresh socket resets the backoff: the next gap schedules the
    // base delay again instead of compounding stale attempts.
    second.onmessage?.({data: JSON.stringify({type: 'heartbeat', stream_epoch: 'other', sequence: 1})})
    await vi.advanceTimersByTimeAsync(300)
    expect(FakeSocket.instances).toHaveLength(3)
    store.stop()
  })

  it('discards transient content when a reconnect installs a durable snapshot', async () => {
    vi.useFakeTimers()
    let calls = 0
    const store = makeStore(async () => ++calls === 1 ? SNAPSHOT : {
      ...SNAPSHOT, activity_sequence: 2,
      activities: [item({activity_id: 'act_1', state: 'succeeded', content_ref: '/durable'})],
    })
    await store.start()
    const socket = FakeSocket.instances[0]
    socket.onmessage?.({data: JSON.stringify({type: 'activity_upsert', stream_epoch: 'e1', sequence: 1,
      payload: {item: item({activity_id: 'act_1', state: 'running'})}})})
    socket.onmessage?.({data: JSON.stringify({type: 'activity_delta', stream_epoch: 'e1', sequence: 2,
      payload: {activity_id: 'act_1', delta: 'incomplete transient'}})})
    expect(store.getState().content.act_1.text).toBe('incomplete transient')
    socket.onclose?.()
    await vi.advanceTimersByTimeAsync(300)
    expect(store.getState().content).toEqual({})
    expect(store.getState().items[0].content_ref).toBe('/durable')
    store.stop()
  })

  it('evicts cached content together with the bounded metadata row', async () => {
    const store = makeStore(async () => SNAPSHOT)
    await store.start()
    const socket = FakeSocket.instances[0]
    socket.onopen?.()
    const frame = (sequence: number, payload: unknown) => socket.onmessage?.({data: JSON.stringify({
      type: 'activity_upsert', workspace_id: 'ws', session_id: 'ses', stream_epoch: 'e1', sequence,
      payload,
    })})
    const delta = (sequence: number, activityId: string, text: string) => socket.onmessage?.({data: JSON.stringify({
      type: 'activity_delta', workspace_id: 'ws', session_id: 'ses', stream_epoch: 'e1', sequence,
      payload: {activity_id: activityId, revision: 1, delta: text},
    })})
    const upsert = (id: string, sequence: number) => frame(sequence, {
      item: item({activity_id: id, state: 'succeeded', ended_at: at(1)}),
    })
    upsert('act_1', 1)
    delta(2, 'act_1', 'early text')
    upsert('act_2', 3)
    delta(4, 'act_2', 'kept text')
    for (let n = 3; n <= ACTIVITY_METADATA_LIMIT + 1; n += 1) upsert(`act_${n}`, n + 2)
    const state = store.getState()
    expect(state.items).toHaveLength(ACTIVITY_METADATA_LIMIT)
    expect(state.content.act_1).toBeUndefined()
    expect(state.content.act_2).toEqual({text: 'kept text', truncated: false})
    store.stop()
  })
})
