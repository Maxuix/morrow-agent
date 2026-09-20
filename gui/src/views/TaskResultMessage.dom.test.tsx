// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../api/client'
import type { TimelineItem } from '../api/chat'
import type { TaskResultWire } from '../api/types'
import type { FileTarget } from '../state/inspector'
import { TaskResultMessage, resultStatusLabel } from './TaskResultMessage'

const RESULT: TaskResultWire = {
  outcome_id: 'out_1',
  task_run_id: 'task_1',
  workflow_run_id: 'wrun_1',
  task_status: 'ready_for_acceptance',
  result_status: 'succeeded',
  trigger: 'snapshot',
  version: 1,
  summary: "Workflow 'Authorization audit' completed with result succeeded.",
  sections: [],
  files: [
    {
      label: 'result',
      kind: 'task_summary',
      role: 'output',
      source: 'workflow_output',
      artifact_id: 'art_out1',
      path: null,
      mime: null,
      byte_size: 128,
      availability: 'available',
      inherited: false,
      node_id: 'nrun_1',
      output_slot: 'result',
      note: null,
    },
    {
      label: 'src/app.py',
      kind: 'changed_path',
      role: 'changed',
      source: 'task_evidence',
      artifact_id: null,
      path: 'src/app.py',
      mime: null,
      byte_size: null,
      availability: 'missing',
      inherited: false,
      node_id: null,
      output_slot: null,
      note: '结果只记录了变更路径。',
    },
  ],
  body: { kind: 'text', text: 'final answer', truncated: false, content_complete: true },
  body_ref: { record_id: 'rec_1', sha256: 'a'.repeat(64), content_complete: true },
  notes: [],
  truncated: false,
}

function item(result: TaskResultWire | undefined): TimelineItem {
  return {
    item_id: 'result:ses_root:task_1:out_1',
    kind: 'result',
    workspace_id: 'ws_1',
    session_id: 'ses_root',
    order_key: [1, 0, 0, 'result:ses_root:task_1:out_1'],
    revision: 1,
    source: { origin_session_id: 'ses_root', source_kind: 'task_outcome', source_id: 'out_1', task_run_id: 'task_1' },
    content: null,
    content_ref: null,
    ...(result === undefined ? {} : { result }),
  }
}

function jsonResponse(value: unknown) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}

describe('TaskResultMessage', () => {
  let container: HTMLDivElement
  let root: Root
  let calls: string[]
  let client: ApiClient
  let onOpenFile: ReturnType<typeof vi.fn<(target: FileTarget) => void>>

  beforeEach(() => {
    calls = []
    client = new ApiClient({
      baseUrl: '',
      token: '',
      fetchImpl: async url => {
        const path = String(url)
        calls.push(path)
        if (path.includes('/content/')) return jsonResponse({ content: 'the full answer body', attachments: [] })
        return jsonResponse({})
      },
    })
    onOpenFile = vi.fn<(target: FileTarget) => void>()
    container = document.createElement("div")
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  it('renders the real answer and opens declared outputs and changed paths differently', async () => {
    await act(async () => {
      root.render(
        <TaskResultMessage item={item(RESULT)} client={client} onOpenFile={onOpenFile} onTask={() => {}} />,
      )
    })
    expect(container.textContent).toContain('final answer')
    expect(container.textContent).toContain('待验收')
    expect(container.textContent).not.toContain("completed with result succeeded")

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'result' }))
    expect(onOpenFile).toHaveBeenCalledWith({
      kind: 'artifact',
      artifactId: 'art_out1',
      label: 'result',
      path: null,
      taskRunId: 'task_1',
    })
    await user.click(screen.getByRole('button', { name: 'src/app.py' }))
    expect(onOpenFile).toHaveBeenCalledWith({ kind: 'workspace', path: 'src/app.py' })
  })

  it('opens a registered delivery by its real name and shows its type', async () => {
    const delivery: TaskResultWire = {
      ...RESULT,
      files: [
        {
          label: '页面',
          kind: 'deliverable',
          role: 'delivery',
          source: 'workflow_delivery',
          artifact_id: 'art_file1',
          path: 'cyber-town.html',
          name: 'cyber-town.html',
          mime: 'text/html',
          byte_size: 4096,
          availability: 'available',
          inherited: false,
          node_id: 'nrun_1',
          output_slot: 'result',
          resource_count: 2,
          note: null,
        },
      ],
    }
    await act(async () => {
      root.render(<TaskResultMessage item={item(delivery)} client={client} onOpenFile={onOpenFile} onTask={() => {}} />)
    })
    expect(container.textContent).toContain('交付文件')
    expect(container.textContent).toContain('text/html')
    expect(container.textContent).toContain('2 个依赖')
    await userEvent.setup().click(screen.getByRole('button', { name: '页面' }))
    expect(onOpenFile).toHaveBeenCalledWith({
      kind: 'artifact',
      artifactId: 'art_file1',
      label: '页面',
      path: 'cyber-town.html',
      taskRunId: 'task_1',
    })
  })

  it('fetches the full result only when the reader asks for it', async () => {
    await act(async () => {
      root.render(<TaskResultMessage item={item(RESULT)} client={client} onOpenFile={onOpenFile} onTask={() => {}} />)
    })
    expect(calls.filter(path => path.includes('/content/'))).toEqual([])
    await userEvent.setup().click(screen.getByRole('button', { name: '查看完整结果' }))
    await act(async () => { await Promise.resolve() })
    expect(calls).toContain('/v1/workspaces/ws_1/sessions/ses_root/content/rec_1')
    expect(container.textContent).toContain('the full answer body')
  })

  it('renders structured sections when the contract has no text body', async () => {
    const structured: TaskResultWire = {
      ...RESULT,
      body: null,
      body_ref: null,
      sections: [{ label: '综合结论', kind: 'text', items: ['授权测试覆盖不足'] }],
    }
    await act(async () => {
      root.render(<TaskResultMessage item={item(structured)} client={client} onOpenFile={onOpenFile} onTask={() => {}} />)
    })
    expect(container.textContent).not.toContain("completed with result succeeded.")
    expect(container.textContent).toContain("待验收")
    expect(container.textContent).toContain('综合结论')
    expect(container.textContent).toContain('授权测试覆盖不足')
  })

  it('keeps the existing entry point for a Core without the projection', async () => {
    const onTask = vi.fn<(id: string) => void>()
    await act(async () => {
      root.render(<TaskResultMessage item={item(undefined)} client={client} onOpenFile={onOpenFile} onTask={onTask} />)
    })
    expect(container.textContent).toContain('结果正文不可用')
    await userEvent.setup().click(screen.getByRole('button', { name: '查看任务与产物' }))
    expect(onTask).toHaveBeenCalledWith('task_1')
  })

  it('never reports a failed or cancelled task as a success', () => {
    expect(resultStatusLabel({ ...RESULT, task_status: 'failed' })).toBe('未成功 · 失败')
    expect(resultStatusLabel({ ...RESULT, task_status: 'cancelled' })).toBe('未成功 · 已取消')
    expect(resultStatusLabel({ ...RESULT, result_status: 'needs_revision' })).toBe('需要修改')
  })
})
