// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiClient } from '../../api/client'
import type { TaskArtifactsWire } from '../../api/types'
import { InspectorStore, type InspectorScope } from '../../state/inspector'
import { inspectorResources } from '../../state/inspectorResources'
import { ChatInspector } from './ChatInspector'

const artifactScope: InspectorScope = { workspaceId: 'ws_artifacts', sessionId: 'se_artifacts' }
const terminalScope: InspectorScope = { workspaceId: 'ws_terminal', sessionId: 'se_terminal' }

const task = {
  task_run_id: 'task_artifacts',
  session_id: artifactScope.sessionId,
  purpose: 'user' as const,
  status: 'ready_for_acceptance' as const,
  attempt: 1,
  row_version: 4,
  created_at: '2026-01-01T10:00:00+00:00',
  updated_at: '2026-01-01T10:05:00+00:00',
  accepted_at: null,
  closed_at: null,
}

const baseArtifact = {
  contract: null,
  session_id: artifactScope.sessionId,
  task_run_id: task.task_run_id,
  workflow_run_id: null,
  node_run_id: null,
  output_slot: null,
  provenance: [{ kind: 'task', role: 'result', reference_id: 'outcome_ref' }],
  created_at: '2026-01-01T10:05:00+00:00',
  updated_at: '2026-01-01T10:05:00+00:00',
}

const artifactsFixture: TaskArtifactsWire = {
  schema_version: 1,
  session_id: artifactScope.sessionId,
  task_run_id: task.task_run_id,
  title: '实现任务',
  status: 'ready_for_acceptance',
  purpose: 'user',
  workflow_run_id: null,
  workflow_run_ids: [],
  node_run_id: null,
  task,
  outcomes: [{
    outcome_id: 'outcome_artifacts',
    task_run_id: task.task_run_id,
    session_id: artifactScope.sessionId,
    version: 1,
    task_status: 'ready_for_acceptance',
    summary: '实现已完成，等待审阅。',
    trigger: 'task_completed',
    completion_basis: 'tests',
    changed_paths: ['src/app.py'],
    side_effects: [],
    unresolved_items: [],
    feedback: [],
    artifact_refs: [],
    evidence_refs: [],
    created_at: '2026-01-01T10:05:00+00:00',
  }],
  artifacts: [
    {
      ...baseArtifact,
      artifact_id: 'art_patch',
      kind: 'implementation_patch',
      name: 'src/app.py 变更',
      path: 'src/app.py',
      mime: 'text/x-diff',
      source: 'task_evidence',
      availability: 'available',
      byte_size: 48,
      excerpt: '文件变更 Diff',
      diff: '+++ b/src/app.py\n+<script>alert(1)</script>',
      diff_truncated: false,
      content_complete: true,
      content_encoding: 'utf8',
      omission_reason: null,
      retention: 'standard',
      row_version: 1,
    },
    {
      ...baseArtifact,
      artifact_id: 'art_report',
      kind: 'report',
      name: '审阅报告',
      path: null,
      mime: 'text/plain',
      source: 'registered_result',
      availability: 'available',
      byte_size: 22,
      excerpt: '报告正文可按需读取。',
      diff: null,
      diff_truncated: false,
      content_complete: true,
      content_encoding: 'utf8',
      omission_reason: null,
      retention: 'standard',
      row_version: 2,
    },
  ],
  files: [{
    path: 'src/app.py',
    operation: 'modify',
    status: 'recorded',
    artifact_id: 'art_patch',
    source: 'task_evidence',
    availability: 'available',
    mime: 'text/x-diff',
    diff: '+++ b/src/app.py\n+<script>alert(1)</script>',
    diff_truncated: false,
    content_complete: true,
    content_encoding: 'utf8',
    omission_reason: null,
    message: null,
    updated_at: '2026-01-01T10:05:00+00:00',
  }],
  command_outputs: [],
  availability: 'available',
  message: null,
}

const terminalFixture: TaskArtifactsWire = {
  ...artifactsFixture,
  session_id: terminalScope.sessionId,
  task_run_id: 'task_terminal',
  title: '命令任务',
  task: { ...task, task_run_id: 'task_terminal', session_id: terminalScope.sessionId },
  outcomes: [],
  artifacts: [
    {
      ...baseArtifact,
      artifact_id: 'art_cmd',
      kind: 'command_output',
      name: 'bash 输出',
      path: null,
      mime: 'text/plain',
      source: 'command_output',
      availability: 'available',
      byte_size: 24,
      excerpt: '持久输出可按需读取。',
      diff: null,
      diff_truncated: false,
      content_complete: true,
      content_encoding: 'utf8',
      omission_reason: null,
      retention: 'standard',
      row_version: 1,
      session_id: terminalScope.sessionId,
      task_run_id: 'task_terminal',
    },
  ],
  files: [],
  command_outputs: [
    {
      tool_execution_id: 'tex_1',
      tool_name: 'bash',
      command_class: 'test',
      cwd: 'src',
      ordinal: 1,
      state: 'succeeded',
      disposition: 'recorded',
      started_at: '2026-01-01T10:00:00+00:00',
      ended_at: '2026-01-01T10:00:01+00:00',
      duration_ms: 1000,
      exit_code: 0,
      signal: null,
      output_artifact_id: 'art_cmd',
      output_availability: 'available',
      output_excerpt: '',
      output_truncated: false,
      output_source: 'artifact',
      message: null,
    },
    {
      tool_execution_id: 'tex_2',
      tool_name: 'run_command',
      command_class: 'lint',
      cwd: 'gui',
      ordinal: 2,
      state: 'failed',
      disposition: 'metadata_only',
      started_at: '2026-01-01T10:01:00+00:00',
      ended_at: '2026-01-01T10:01:01+00:00',
      duration_ms: 1000,
      exit_code: 1,
      signal: null,
      output_artifact_id: null,
      output_availability: 'not_persisted',
      output_excerpt: '<img src=x onerror=alert(1)>',
      output_truncated: true,
      output_source: 'none',
      message: '旧历史仅保留命令元数据。',
    },
  ],
  availability: 'partial',
  message: '部分历史输出仅保留安全元数据。',
}

function jsonResponse(value: unknown) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}

function InspectorHarness({ store, client, scope }: { store: InspectorStore; client: ApiClient; scope: InspectorScope }) {
  return <ChatInspector
    store={store}
    bodyProps={{
      client,
      workspaceId: scope.workspaceId,
      sessionId: scope.sessionId,
      currentTaskRunId: scope === artifactScope ? task.task_run_id : 'task_terminal',
    }}
  />
}

describe('Task artifacts and terminal inspectors', () => {
  let container: HTMLDivElement
  let root: Root
  let calls: { url: string; method: string; body: string | undefined }[]

  beforeEach(() => {
    sessionStorage.clear()
    inspectorResources.clear()
    calls = []
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    inspectorResources.clear()
  })

  it('shows one empty state without empty file sections or a preview column', async () => {
    const api = new ApiClient({ baseUrl: '', token: '', fetchImpl: async () => jsonResponse({
      ...artifactsFixture, status: null, task: null, outcomes: [], files: [], artifacts: [],
      availability: 'empty', message: '此会话还没有可显示的任务产物。',
    }) })
    const store = new InspectorStore()
    store.setScope(artifactScope)
    act(() => { store.openInspectorTab('artifacts'); root.render(<InspectorHarness store={store} client={api} scope={artifactScope} />) })
    expect(await screen.findByText('暂无产物')).toBeTruthy()
    expect(screen.queryByRole('region', { name: '文件变化' })).toBeNull()
    expect(screen.queryByRole('region', { name: '正文预览' })).toBeNull()
    expect(container.textContent).not.toContain('未开始')
    expect(container.textContent).not.toContain('选择左侧')
  })

  it('keeps a real missing-content warning instead of replacing it with an empty state', async () => {
    const api = new ApiClient({ baseUrl: '', token: '', fetchImpl: async () => jsonResponse({
      ...artifactsFixture, task: null, outcomes: [], files: [], artifacts: [],
      availability: 'partial', message: '部分产物已丢失，请检查存储。',
    }) })
    const store = new InspectorStore()
    store.setScope(artifactScope)
    act(() => { store.openInspectorTab('artifacts'); root.render(<InspectorHarness store={store} client={api} scope={artifactScope} />) })
    expect(await screen.findByText('部分产物已丢失，请检查存储。')).toBeTruthy()
    expect(screen.queryByText('暂无产物')).toBeNull()
  })

  it('renders task changes safely and sends scoped outcome/retention commands', async () => {
    const client = new ApiClient({
      baseUrl: '',
      token: '',
      workspaceId: artifactScope.workspaceId,
      fetchImpl: async (url, init) => {
        const path = String(url)
        calls.push({ url: path, method: init?.method ?? 'GET', body: typeof init?.body === 'string' ? init.body : undefined })
        if (path.includes('/task-artifacts/art_report/content')) return jsonResponse({ content: 'report <script>text</script>', truncated: false, byte_size: 28, encoding: 'utf8' })
        if (path.includes('/files/content')) return jsonResponse({ schema_version: 1, workspace_id: artifactScope.workspaceId, file: { schema_version: 1, path: 'src/app.py', text: 'print("full safe <script>content</script>")', byte_size: 40, truncated: false, encoding: 'utf8', revision: { sha256: 'abc' }, writable: true } })
        if (path.includes('/task-artifacts')) return jsonResponse(artifactsFixture)
        if (path.includes('/tasks/task_artifacts/accept')) return jsonResponse({ result: { task } })
        if (path.includes('/artifacts/art_report/retention')) return jsonResponse({ artifact: { ...artifactsFixture.artifacts[1], retention: 'pinned', row_version: 3 }, disposition: 'pinned' })
        return jsonResponse({})
      },
    })
    const store = new InspectorStore()
    store.setScope(artifactScope)
    act(() => {
      store.openInspectorTab('artifacts')
      root.render(<InspectorHarness store={store} client={client} scope={artifactScope} />)
    })

    expect(await screen.findByText('实现已完成，等待审阅。')).toBeTruthy()
    expect(container.textContent).toContain('+++ b/src/app.py')
    expect(container.querySelector('script')).toBeNull()
    expect(container.textContent).not.toContain('art_patch')
    expect(container.textContent).not.toContain('outcome_artifacts')
    expect(screen.queryByText('在项目文件中打开')).toBeNull()

    // Test toggle to read-only full content
    await userEvent.setup().click(screen.getByRole('tab', { name: '只读全文' }))
    await waitFor(() => expect(container.textContent).toContain('print("full safe <script>content</script>")'))
    expect(container.querySelector('script')).toBeNull()
    const fileCall = calls.find(call => call.url.includes('/files/content'))
    expect(fileCall).toBeDefined()

    // Test toggle back to diff
    await userEvent.setup().click(screen.getByRole('tab', { name: '变更对比' }))
    expect(container.textContent).toContain('+++ b/src/app.py')

    await userEvent.setup().click(screen.getByRole('button', { name: '接受结果' }))
    await screen.findByText('接受结果')
    const accept = calls.find(call => call.url.includes('/tasks/task_artifacts/accept'))
    expect(accept?.method).toBe('POST')
    expect(accept?.body).toContain('expected_row_version')
    expect(accept?.body).toContain('command_id')

    await userEvent.setup().click(screen.getByRole('button', { name: /审阅报告/ }))
    expect(calls.map(call => call.url)).toContain('/v1/workspaces/ws_artifacts/sessions/se_artifacts/task-artifacts/art_report/content?task_run_id=task_artifacts')
    await waitFor(() => expect(container.textContent).toContain('report <script>text</script>'))
    expect(container.querySelector('script')).toBeNull()
    await userEvent.setup().click(screen.getByRole('button', { name: '固定保留' }))
    const retention = calls.find(call => call.url.includes('/artifacts/art_report/retention'))
    expect(retention?.method).toBe('POST')
    expect(retention?.body).toContain('"expected_row_version":2')
    expect(retention?.body).toContain('"confirmed":true')
  })

  it('reuses safe terminal history and explains missing output without fabricating channels', async () => {
    const client = new ApiClient({
      baseUrl: '',
      token: '',
      workspaceId: terminalScope.workspaceId,
      fetchImpl: async (url) => {
        const path = String(url)
        calls.push({ url: path, method: 'GET', body: undefined })
        if (path.includes('/task-artifacts/art_cmd/content')) return jsonResponse({ content: 'persistent <b>output</b>', truncated: false, byte_size: 24, encoding: 'utf8' })
        if (path.includes('/task-artifacts')) return jsonResponse(terminalFixture)
        return jsonResponse({})
      },
    })
    const store = new InspectorStore()
    store.setScope(terminalScope)
    act(() => {
      store.openInspectorTab('terminal')
      root.render(<InspectorHarness store={store} client={client} scope={terminalScope} />)
    })

    expect(await screen.findByText('历史命令输出 · 2')).toBeTruthy()
    expect(screen.getByText('退出码 0')).toBeTruthy()
    expect(screen.getByText('输出未保存')).toBeTruthy()
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).not.toContain('tex_1')

    await userEvent.setup().click(screen.getByRole('button', { name: /第 2 个命令/ }))
    expect(await screen.findByText('旧历史仅保留命令元数据。')).toBeTruthy()

    await userEvent.setup().click(screen.getByRole('button', { name: /第 1 个命令/ }))
    expect(calls.map(call => call.url)).toContain('/v1/workspaces/ws_terminal/sessions/se_terminal/task-artifacts/art_cmd/content?task_run_id=task_terminal')
    await waitFor(() => expect(container.textContent).toContain('persistent <b>output</b>'))
    expect(container.querySelector('b')).toBeNull()

    const filter = screen.getByRole('textbox', { name: '过滤命令输出' })
    await userEvent.setup().type(filter, 'run_command')
    expect(screen.queryByRole('button', { name: /第 1 个命令/ })).toBeNull()
    expect(screen.getByRole('button', { name: /第 2 个命令/ })).toBeTruthy()
  })
})
