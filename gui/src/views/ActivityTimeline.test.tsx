import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { ActivityItem } from '../api/activity'
import type { ApiClient } from '../api/client'
import type { ApprovalWire } from '../api/types'
import type { StageFrame } from '../api/chat'
import type { ActivityContentState } from '../state/activity'
import { ActivityTimeline } from './ActivityTimeline'
import { AssetGroup, ThinkingBlock, ToolRow } from './ActivityItem'

const BASE = Date.parse('2026-09-08T00:00:00Z')
const at = (seconds: number) => new Date(BASE + seconds * 1000).toISOString()
const NOW = BASE + 34_000
let seq = 0
const item = (over: Partial<ActivityItem> = {}): ActivityItem => ({
  schema_version: 1,
  activity_id: `act_${++seq}`,
  revision: 1,
  kind: 'tool',
  state: 'succeeded',
  origin: 'agent_loop',
  identity: {workspace_id: 'ws', root_session_id: 's', source_session_id: 's', agent_run_id: 'arun', turn_id: 't1'},
  payload: {kind: 'tool', tool_name: 'read', call_id: `c${seq}`},
  started_at: at(0),
  updated_at: at(3),
  ended_at: at(3),
  last_activity_at: null,
  safe_title: 'read',
  safe_summary: null,
  preview_ref: null,
  truncated: false,
  availability: 'none',
  ...over,
})
const client = {} as ApiClient
const render = (ui: React.ReactElement) => renderToStaticMarkup(ui)
const region = (items: ActivityItem[], props: Partial<Parameters<typeof ActivityTimeline>[0]> = {}) => <ActivityTimeline
  client={client} run={{key: 'turn:t1', items}} content={{}} status="live" connection="live"
  stages={[]} busy={false} approvals={[]} nowOverride={NOW} {...props}/>

describe('execution region overview', () => {
  it('collapses to one status line with a single elapsed span', () => {
    const running = item({kind: 'model', state: 'running', payload: {kind: 'model', stage: 'thinking'}, ended_at: null, updated_at: at(0)})
    const html = render(region([running], {expandedOverride: false}))
    expect(html).toContain('正在思考 · 34 秒')
    expect(html).toContain('aria-expanded="false"')
    expect(html).not.toContain('项')
  })

  it('expands into grouped content with the 5-row preview expander', () => {
    const items = Array.from({length: 7}, (_, i) => item({activity_id: `t${i}`, safe_title: `read file${i}.ts`}))
    const html = render(region(items, {expandedOverride: true}))
    expect(html).toContain('读取了 7 个文件')
    expect(html).toContain('已读取 file4.ts')
    expect(html).not.toContain('已读取 file5.ts')
    expect(html).toContain('显示其余 2 条')
  })

  it('keeps failures visible while collapsed', () => {
    const failed = item({state: 'failed', safe_title: 'read config.json', ended_at: at(34), updated_at: at(34)})
    const html = render(region([failed], {expandedOverride: false}))
    expect(html).toContain('执行失败 · 34 秒')
    expect(html).toContain('data-tone="failed"')
  })

  it('offers a direct approval entry on the overview', () => {
    const waiting = item({kind: 'model', state: 'running', payload: {kind: 'model', stage: 'thinking'}, ended_at: null})
    const approval: ApprovalWire = {
      approval_id: 'ap1', tool_execution_id: 'tx1', tool_name: 'bash', session_id: 's',
      task_run_id: 'task1', agent_run_id: 'arun', workflow_run_id: null, node_run_id: null,
      node_id: null, agent_id: null, effect_class: 'workspace_write', risk_level: 'medium',
      session_scope_allowed: true, affected_objects: ['file.txt'], requested_scope: 'workspace_write',
      granted_scope: null, preview: ['bash --safe-preview'], resolution: 'pending',
      created_at: at(0), expires_at: '2099-01-01T00:00:00Z', resolved_at: null, row_version: 1,
    }
    const html = render(region([waiting], {approvals: [approval]}))
    expect(html).toContain('等待批准')
    expect(html).toContain('允许本次执行')
  })

  it('falls back to stage frames when a run streams no activity items', () => {
    const stage: StageFrame = {workflow_run_id: 'w1', node_run_id: 'n1', node_id: 'gamma', stage: 'tool_running', tool: 'read', ordinal: 2, total: 3, ts: at(4)}
    const html = render(region([], {stages: [stage], expandedOverride: true}))
    expect(html).toContain('正在执行工具 · read（2/3）')
    expect(html).toContain('节点 gamma')
    expect(html).toContain('已进行 30 秒')
  })

  it('reports a disconnect without inventing an outcome', () => {
    const running = item({state: 'running', safe_title: 'read gui/src/App.tsx', ended_at: null, updated_at: at(0)})
    const html = render(region([running], {status: 'reconnecting', expandedOverride: true}))
    expect(html).toContain('状态同步中 · 上次状态：正在读取 gui/src/App.tsx')
    expect(html).toContain('连接中断，正在重连')
  })

  it('renders nothing for an idle session', () => {
    expect(render(region([]))).toBe('')
  })
})

describe('tool rows', () => {
  it('shows the human action summary without per-row 已完成 noise', () => {
    const html = render(<ToolRow item={item({safe_title: 'find drawing.html', payload: {kind: 'tool', tool_name: 'find', call_id: 'c1'}})} now={NOW} open={false} onToggle={() => {}}/>)
    expect(html).toContain('已搜索 drawing.html')
    expect(html).not.toContain('已完成')
  })

  it('opens a detail with safe facts, output and availability notes', () => {
    const content: Record<string, ActivityContentState> = {t1: {text: 'file body', truncated: true}}
    const row = item({activity_id: 't1', safe_summary: '涉及 2 个路径', availability: 'unsaved'})
    const html = render(<ToolRow item={row} content={content.t1} now={NOW} open={true} onToggle={() => {}}/>)
    expect(html).toContain('涉及 2 个路径')
    expect(html).toContain('file body')
    expect(html).toContain('复制输出')
    expect(html).toContain('超出显示上限')
    expect(html).toContain('输出未保存')
  })

  it('marks failed rows with the failure-first phrasing', () => {
    const html = render(<ToolRow item={item({state: 'failed', safe_title: 'read config.json', payload: {kind: 'tool', tool_name: 'read', call_id: 'c2'}})} now={NOW} open={false} onToggle={() => {}}/>)
    expect(html).toContain('读取 config.json 失败')
    expect(html).toContain('失败')
  })
})

describe('thinking block', () => {
  it('shows a short preview plus 展开全文 when collapsed', () => {
    const long = '很'.repeat(200)
    const html = render(<ThinkingBlock content={{text: long, truncated: false}} open={false} onToggle={() => {}}/>)
    expect(html).toContain('思考摘要')
    expect(html).toContain('展开全文')
    expect(html).not.toContain('很'.repeat(200))
  })

  it('renders full prose with paragraphs and keeps code mono', () => {
    const text = '第一段。\n\n第二段。\n```js\nconst a = 1\n```'
    const html = render(<ThinkingBlock content={{text, truncated: false}} open={true} onToggle={() => {}}/>)
    expect(html).toContain('第一段。')
    expect(html).toContain('第二段。')
    expect(html).toContain('exec-thinking-code')
    expect(html).toContain('const a = 1')
  })
})

describe('asset group', () => {
  it('groups verifiable references and shows loading placeholders before fetch', () => {
    const assets = [
      item({activity_id: 'a1', safe_title: 'read shot1.png', payload: {kind: 'tool', tool_name: 'read', call_id: 'ca1'}, preview_ref: '/v1/workspaces/ws/sessions/se1/artifacts/a1/content'}),
      item({activity_id: 'a2', safe_title: 'write shot2.png', payload: {kind: 'tool', tool_name: 'write', call_id: 'ca2'}, preview_ref: '/v1/workspaces/ws/sessions/se1/artifacts/a2/content'}),
    ]
    const html = render(<AssetGroup items={assets} client={client} open={true} onToggle={() => {}}/>)
    expect(html).toContain('产物引用 · 2 项')
    expect((html.match(/加载中…/g) ?? []).length).toBe(2)
  })
})
