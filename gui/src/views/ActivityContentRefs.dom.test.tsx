// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActivityItem } from '../api/activity'
import type { ApiClient } from '../api/client'
import { AssetGroup, ToolRow } from './ActivityItem'

const BASE = Date.parse('2026-09-08T00:00:00Z')
const at = (seconds: number) => new Date(BASE + seconds * 1000).toISOString()

const toolItem = (over: Partial<ActivityItem> = {}): ActivityItem => ({
  schema_version: 1,
  activity_id: 'act_tool_tex_1',
  revision: 2,
  kind: 'tool',
  state: 'succeeded',
  origin: 'tool_executor',
  identity: {workspace_id: 'ws', root_session_id: 's', source_session_id: 's', agent_run_id: 'arun', tool_execution_id: 'tex_1', call_id: 'c1'},
  payload: {kind: 'tool', tool_name: 'read', call_id: 'c1', tool_execution_id: 'tex_1'},
  started_at: at(0),
  updated_at: at(3),
  ended_at: at(3),
  last_activity_at: null,
  safe_title: 'read shot.png',
  safe_summary: null,
  preview_ref: null,
  content_ref: null,
  truncated: false,
  availability: 'none',
  ...over,
})

function mockClient(over: Partial<ApiClient> = {}): ApiClient {
  return over as ApiClient
}

describe('activity content refs (3c)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    vi.stubGlobal('URL', {...URL, createObjectURL: vi.fn(() => 'blob:mock'), revokeObjectURL: vi.fn()})
  })
  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    vi.unstubAllGlobals()
  })

  it('fetches binary preview_refs through the raw variant and renders images', async () => {
    const fetchBlob = vi.fn().mockResolvedValue(new Blob(['png-bytes'], {type: 'image/png'}))
    const client = mockClient({fetchBlob})
    const asset = toolItem({
      activity_id: 'act_tool_tex_a',
      preview_ref: '/v1/workspaces/ws/sessions/s/artifacts/art_1/content',
    })
    act(() => {
      root.render(<AssetGroup items={[asset]} client={client} open={true} onToggle={() => {}}/>)
    })
    await waitFor(() => expect(fetchBlob).toHaveBeenCalledWith(
      '/v1/workspaces/ws/sessions/s/artifacts/art_1/content?raw=1',
    ))
    await screen.findByAltText('read shot.png')
  })

  it('fetches durable text through content_ref when the transient map is empty', async () => {
    const activityContent = vi.fn().mockResolvedValue({content: '持久化输出', truncated: false})
    const client = mockClient({activityContent})
    const recovered = toolItem({
      activity_id: 'act_tool_tex_1',
      content_ref: '/v1/workspaces/ws/sessions/s/activity-content/act_tool_tex_1',
      availability: 'committed',
    })
    act(() => {
      root.render(<ToolRow item={recovered} now={BASE} open={true} onToggle={() => {}} client={client}/>)
    })
    await waitFor(() => expect(activityContent).toHaveBeenCalledWith(
      '/v1/workspaces/ws/sessions/s/activity-content/act_tool_tex_1',
    ))
    await screen.findByText('持久化输出')
  })

  it('fetches durable content after recovery removes an existing transient preview', async () => {
    const activityContent = vi.fn().mockResolvedValue({content: 'complete output', truncated: false})
    const client = mockClient({activityContent})
    const live = toolItem({content_ref: '/durable'})
    act(() => root.render(<ToolRow item={live} content={{text: 'partial output', truncated: false}}
      now={BASE} open={true} onToggle={() => {}} client={client}/>))
    expect(screen.getByText('partial output')).toBeTruthy()
    act(() => root.render(<ToolRow item={live} now={BASE} open={true} onToggle={() => {}} client={client}/>))
    await screen.findByText('complete output')
    expect(screen.queryByText('partial output')).toBeNull()
    // Reusing the component for a different reference cannot show the old body.
    act(() => root.render(<ToolRow item={{...live, content_ref: null}}
      now={BASE} open={true} onToggle={() => {}} client={client}/>))
    expect(screen.queryByText('complete output')).toBeNull()
  })

  it('prefers streamed transient content over the durable content_ref fetch', async () => {
    const activityContent = vi.fn()
    const client = mockClient({activityContent})
    const live = toolItem({content_ref: '/v1/workspaces/ws/sessions/s/activity-content/act_tool_tex_1'})
    act(() => {
      root.render(
        <ToolRow item={live} content={{text: '实时片段', truncated: false}}
          now={BASE} open={true} onToggle={() => {}} client={client}/>,
      )
    })
    expect(screen.getByText('实时片段')).toBeTruthy()
    expect(activityContent).not.toHaveBeenCalled()
  })
  it('keeps desktop evidence with its screenshot and never labels unknown effects as success', async () => {
    const fetchBlob = vi.fn().mockResolvedValue(new Blob(['png-bytes'], {type: 'image/png'}))
    const client = mockClient({fetchBlob})
    const desktop = toolItem({state: 'unknown', safe_title: 'computer_action',
      payload: {kind: 'tool', tool_name: 'computer_action', tool_execution_id: 'tex_1'},
      preview_ref: '/v1/workspaces/ws/sessions/s/artifacts/art_1/content'})
    act(() => root.render(<ToolRow item={desktop} now={BASE} open={true} onToggle={() => {}} client={client}/>))
    await screen.findByAltText('computer_action')
    expect(screen.getByRole('alert').textContent).toContain('不要自动重试')
    expect(screen.getByRole('button', {name: /桌面动作效果未知/})).toBeDefined()
    expect(container.textContent).not.toContain('桌面动作已返回结果')
    expect(fetchBlob).toHaveBeenCalledWith('/v1/workspaces/ws/sessions/s/artifacts/art_1/content?raw=1')
  })

  it('separates native completion, actual delivery and failed postconditions from task completion', () => {
    const desktop = toolItem({safe_title: 'computer_action Controlled notes', payload: {
      kind: 'tool', tool_name: 'computer_action', tool_execution_id: 'tex_1', computer: {
        operation: 'action', action: 'type_text', target_label: 'Controlled notes', bundle_id: 'com.example.Notes',
        target_count: null, delivery: 'background', completion: 'completed', postcondition: 'failed',
        error_code: 'verification_failed', observation_error: 'image_publish_failed',
      },
    }})
    act(() => root.render(<ToolRow item={desktop} now={BASE} open={true} onToggle={() => {}}/>))
    expect(screen.getByText('后台')).toBeDefined()
    expect(screen.getByText('已返回')).toBeDefined()
    expect(screen.getByText('验证失败')).toBeDefined()
    expect(screen.getByText('输入文本（内容隐藏）')).toBeDefined()
    expect(screen.getByText(/image_publish_failed/).textContent).toContain('不要自动重试')
    expect(container.textContent).not.toContain('任务已完成')
    if (desktop.payload.kind !== 'tool') throw new Error('expected tool fixture')
    const payload = desktop.payload
    act(() => root.render(<ToolRow item={{...desktop, state: 'unknown', payload: {
      ...payload, computer: {...payload.computer!, completion: 'unknown', delivery: null, postcondition: 'not_checked'},
    }}} now={BASE} open={true} onToggle={() => {}}/>))
    expect(screen.getByText('未记录实际投递方式')).toBeDefined()
    expect(screen.getByText('未验证')).toBeDefined()
  })

})
