// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen, waitFor, within } from '@testing-library/react'
import { EditorView } from '@codemirror/view'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiClient } from '../../api/client'
import { FileBufferStore } from '../../state/fileBuffer'
import { inspectorResources } from '../../state/inspectorResources'
import { InspectorStore, type FileTarget } from '../../state/inspector'
import type { DirtyGuard } from '../../state/navigation'
import { FileInspector } from './FileInspector'

const FILE = {
  path: 'docs/我的 文件.md',
  kind: 'file' as const,
  byte_size: 24,
  text: true,
  editable: true,
  preview: 'text' as const,
}
const SHA_A = 'a'.repeat(64)
const SHA_B = 'b'.repeat(64)

interface Call {
  path: string
  method: string
  body: Record<string, unknown> | null
}

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function content(overrides: Record<string, unknown> = {}) {
  return {
    path: FILE.path,
    text: 'first\nsecond\nthird',
    revision: { sha256: SHA_A, size: 24, mtime_ns: 0 },
    bom: false,
    newline: 'lf',
    ...overrides,
  }
}

describe('FileInspector', () => {
  let container: HTMLDivElement
  let root: Root
  let calls: Call[]
  let client: ApiClient
  let buffers: FileBufferStore
  let inspector: InspectorStore
  let onOpenFile: (target: FileTarget) => void
  let guards: DirtyGuard[]

  let restoreObjectUrl: () => void

  beforeEach(() => {
    inspectorResources.clear()
    calls = []
    guards = []
    container = document.createElement("div")
    document.body.appendChild(container)
    root = createRoot(container)
    inspector = new InspectorStore()
    // jsdom 没有对象 URL：这里只补齐浏览器能力，预览逻辑本身不被打桩。
    const originalCreate = URL.createObjectURL
    const originalRevoke = URL.revokeObjectURL
    let counter = 0
    URL.createObjectURL = () => `blob:test/${(counter += 1)}`
    URL.revokeObjectURL = () => {}
    restoreObjectUrl = () => {
      URL.createObjectURL = originalCreate
      URL.revokeObjectURL = originalRevoke
    }
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    inspectorResources.clear()
    restoreObjectUrl()
  })

  function makeClient(handler: (call: Call) => Response | Promise<Response>) {
    return new ApiClient({
      baseUrl: '',
      token: '',
      fetchImpl: async (url, init) => {
        const call: Call = {
          path: String(url),
          method: init?.method ?? 'GET',
          body: typeof init?.body === 'string' ? JSON.parse(init.body) : null,
        }
        calls.push(call)
        return handler(call)
      },
    })
  }

  function textHandler(overrides: {
    info?: Record<string, unknown>
    content?: Record<string, unknown>
    put?: (call: Call) => Response | Promise<Response>
  } = {}) {
    return (call: Call): Response | Promise<Response> => {
      if (call.method === 'PUT') {
        if (overrides.put) return overrides.put(call)
        return jsonResponse({ schema_version: 1, workspace_id: 'ws_1', file: content(), mutation: null, disposition: 'accepted' })
      }
      if (call.path.includes('/files/info')) {
        return jsonResponse({ schema_version: 1, workspace_id: 'ws_1', file: { ...FILE, ...overrides.info } })
      }
      return jsonResponse({ schema_version: 1, workspace_id: 'ws_1', file: content(overrides.content) })
    }
  }

  // 编辑器是按需加载的 chunk：冲刷微任务后再让出一个宏任务，测试不必猜时序。
  const settle = async (rounds = 8) => {
    await act(async () => {
      for (let index = 0; index < rounds; index += 1) await Promise.resolve()
      await new Promise(resolve => setTimeout(resolve, 0))
      for (let index = 0; index < rounds; index += 1) await Promise.resolve()
    })
  }

  const render = async (
    target: Parameters<typeof FileInspector>[0]['target'],
    options: { withBuffers?: boolean } = {},
  ) => {
    buffers = new FileBufferStore(client)
    const store = options.withBuffers === false ? undefined : buffers
    await act(async () => {
      root.render(
        <FileInspector
          active
          target={target}
          client={client}
          workspaceId='ws_1'
          sessionId='ses_root'
          onOpenFile={onOpenFile}
          fileBuffers={store}
          inspectorStore={inspector}
          registerGuard={guard => {
            guards.push(guard)
            return () => {
              guards.splice(guards.indexOf(guard), 1)
            }
          }}
        />,
      )
      await Promise.resolve()
    })
    await settle()
  }

  const editor = () => container.querySelector('.cm-editor') as HTMLElement
  const editorView = () => EditorView.findFromDOM(editor())
  const editorText = () => editorView()?.state.doc.toString() ?? ''
  const waitForEditor = async () => {
    await waitFor(() => expect(container.querySelector('.cm-editor')).not.toBeNull())
  }

  it('shows one path row and read-only source with target line, without a toolbar', async () => {
    client = makeClient(textHandler({ info: { path: 'docs/code.py' }, content: { path: 'docs/code.py' } }))
    await render({ path: 'docs/code.py', line: 3 })
    await waitForEditor()
    expect(container.querySelector('header')?.textContent).toBe('docs/code.py')
    expect(editorText()).toBe('first\nsecond\nthird')
    expect(editorView()?.state.selection.main.head).toBe('first\nsecond\n'.length)
    expect(container.querySelector('[contenteditable="true"]')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
    expect(calls.some(call => call.method !== 'GET')).toBe(false)
  })

  it('keeps the real file first and only then reads a line suffix', async () => {
    client = makeClient(call => {
      if (call.path.includes('/files/info')) {
        if (decodeURIComponent(call.path).includes('notes:12')) {
          return jsonResponse({ error: { code: 'not_found', message: '路径不存在' } }, 404)
        }
        return jsonResponse({ schema_version: 1, workspace_id: 'ws_1', file: { ...FILE, path: 'notes' } })
      }
      return jsonResponse({
        schema_version: 1,
        workspace_id: 'ws_1',
        file: {
          path: 'notes',
          text: 'a\nb\nc',
          revision: { sha256: SHA_A, size: 5, mtime_ns: 0 },
          bom: false,
          newline: 'lf',
        },
      })
    })
    await render({ path: 'notes:12', line: 12, fallbackPath: 'notes' })
    await settle()
    await waitForEditor()
    expect(decodeURIComponent(calls[0].path)).toContain('notes:12')
    expect(container.textContent).not.toContain('已按“文件:行号”解释为 notes')
    expect(editorText()).toBe('a\nb\nc')
  })

  it('reports an unreadable path instead of pretending to have content', async () => {
    client = makeClient(() => jsonResponse({ error: { code: 'not_found', message: '路径不存在' } }, 404))
    await render({ path: 'missing.md' })
    expect(container.textContent).toContain('路径不存在')
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
  })

  it('labels a non-text file instead of decoding it', async () => {
    client = makeClient(call =>
      call.path.includes('/files/info')
        ? jsonResponse({
            schema_version: 1,
            workspace_id: 'ws_1',
            file: { ...FILE, path: 'logo.png', text: false, editable: false, preview: 'image', byte_size: 2048 },
          })
        : jsonResponse({ schema_version: 1, workspace_id: 'ws_1', file: content() }),
    )
    await render({ path: 'logo.png' })
    expect(screen.queryByRole('button')).toBeNull()
    expect(container.querySelector('[data-file-preview="image"]')).toBeTruthy()
    expect(container.querySelector('[data-source-editor]')).toBeNull()
  })

  it('shows the historical source without reading the current file', async () => {
    const opened: FileTarget[] = []
    onOpenFile = target => opened.push(target)
    client = makeClient(call => {
      if (call.path.includes('/task-artifacts/')) {
        return jsonResponse({
          content: '@@ -1 +1 @@',
          truncated: false,
          byte_size: 12,
          encoding: 'utf8',
          content_kind: 'text',
          name: 'app.py',
          path: 'src/app.py',
          mime: 'text/x-diff',
          preview: 'text',
        })
      }
      return jsonResponse({}, 404)
    })
    await render({ artifactId: 'art_1', label: 'src/app.py', path: 'src/app.py', taskRunId: 'task_1' })
    await settle()
    await waitFor(() => expect(container.textContent).toContain('@@ -1 +1 @@'))
    expect(container.querySelector('[data-source-editor]')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
    expect(opened).toEqual([])
    expect(calls.every(call => call.path.includes('/task-artifacts/'))).toBe(true)
  })

  it('shows historical HTML source without starting a browser preview', async () => {
    client = makeClient(call => {
      if (call.path.includes('/previews')) {
        return jsonResponse({
          schema_version: 1,
          workspace_id: 'ws_1',
          preview_id: 'pv_1',
          url: 'http://127.0.0.1:9/pv_1/index.html',
          entry_path: 'index.html',
          revision: 'a'.repeat(64),
          missing: [],
          total_bytes: 30,
          files: [],
        })
      }
      if (call.path.includes('/task-artifacts/')) {
        return jsonResponse({
          content: '<div id="root"></div>',
          truncated: false,
          byte_size: 30,
          encoding: 'utf8',
          content_kind: 'text',
          name: 'index.html',
          path: 'index.html',
          mime: 'text/html',
          preview: 'html',
        })
      }
      return jsonResponse({}, 404)
    })
    await render({
      artifactId: 'art_html1',
      label: 'index.html',
      path: 'index.html',
      taskRunId: 'task_1',
      workflowRunId: 'wrun_1',
    })
    await settle()
    expect(calls.some(call => call.path.includes('/previews'))).toBe(false)
    expect(container.querySelector('iframe')).toBeNull()
    await waitFor(() => expect(container.textContent).toContain('<div id="root"></div>'))
  })

  it('explains that nothing is selected yet', async () => {
    client = makeClient(() => jsonResponse({}, 404))
    await render(undefined)
    expect(container.textContent).toContain('未选择文件')
  })

  it('renders Markdown directly without controls or explanatory chrome', async () => {
    client = makeClient(textHandler({ content: { text: '# 标题\n\n正文' } }))
    await render({ path: FILE.path })
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('标题')
    expect(container.querySelector('[data-source-view]')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
    expect(container.textContent).not.toContain('草稿')
  })

  function htmlHandler(extra: {
    missing?: string[]
    previewFailure?: boolean
  } = {}) {
    return (call: Call): Response | Promise<Response> => {
      if (call.method === 'DELETE') {
        return jsonResponse({ schema_version: 1, released: true })
      }
      if (call.path.includes('/previews')) {
        if (extra.previewFailure) {
          return jsonResponse({ error: { code: 'invalid', message: '预览集合超过 20 MiB 上限' } }, 400)
        }
        return jsonResponse({
          schema_version: 1,
          workspace_id: 'ws_1',
          preview_id: 'prev_1',
          url: 'http://127.0.0.1:5555/prev_1/docs/page.html',
          entry_path: 'docs/page.html',
          revision: SHA_A,
          missing: extra.missing ?? [],
          total_bytes: 2048,
          files: [{ path: 'docs/page.html', media_type: 'text/html', byte_size: 2048 }],
        })
      }
      if (call.path.includes('/files/info')) {
        return jsonResponse({
          schema_version: 1,
          workspace_id: 'ws_1',
          file: { ...FILE, path: 'docs/page.html', preview: 'text' },
        })
      }
      if (call.method === 'PUT') {
        return jsonResponse({
          schema_version: 1,
          workspace_id: 'ws_1',
          file: {
            path: 'docs/page.html',
            text: '<h1>saved</h1>',
            revision: { sha256: SHA_B, size: 15, mtime_ns: 0 },
            bom: false,
            newline: 'lf',
          },
          mutation: null,
          disposition: 'accepted',
        })
      }
      return jsonResponse({
        schema_version: 1,
        workspace_id: 'ws_1',
        file: {
          path: 'docs/page.html',
          text: '<h1>hi</h1>',
          revision: { sha256: SHA_A, size: 12, mtime_ns: 0 },
          bom: false,
          newline: 'lf',
        },
      })
    }
  }

  it('shows highlighted HTML source directly and never builds a preview', async () => {
    client = makeClient(htmlHandler())
    await render({ path: 'docs/page.html' })
    await waitForEditor()
    expect(editorText()).toBe('<h1>hi</h1>')
    expect(container.querySelector('.cm-line span')).not.toBeNull()
    expect(container.querySelector('iframe')).toBeNull()
    expect(calls.some(call => call.path.includes('/previews'))).toBe(false)
    expect(screen.queryByRole('button')).toBeNull()
    expect(container.querySelector('[contenteditable="true"]')).toBeNull()
  })

  it('asks before replacing the target and keeps the draft when the user cancels', async () => {
    client = makeClient(textHandler())
    await render({ path: FILE.path })
    act(() => buffers.setDraft({ workspaceId: 'ws_1', sessionId: 'ses_root' }, FILE.path, 'unsaved'))
    expect(guards).toHaveLength(1)
    expect(guards[0].isDirty()).toBe(true)

    let verdict: boolean | null = null
    await act(async () => {
      void guards[0].confirmLeave().then(allowed => {
        verdict = allowed
      })
      await Promise.resolve()
    })
    expect(screen.getByRole('dialog', { name: '未保存的文件' })).toBeTruthy()
    await userEvent.setup().click(screen.getByRole('button', { name: '取消' }))
    await settle()
    expect(verdict).toBe(false)
    expect(buffers.buffer({ workspaceId: 'ws_1', sessionId: 'ses_root' }, FILE.path).draftText).toBe('unsaved')

    await act(async () => {
      void guards[0].confirmLeave().then(allowed => {
        verdict = allowed
      })
      await Promise.resolve()
    })
    await userEvent.setup().click(screen.getByRole('button', { name: '放弃草稿并继续' }))
    await settle()
    expect(verdict).toBe(true)
    expect(buffers.hasDirty()).toBe(false)
  })

  it('reports a leftover draft when saving before leaving cannot finish', async () => {
    client = makeClient(call =>
      call.method === 'PUT'
        ? jsonResponse({ error: { code: 'stale', message: '文件已变化' } }, 409)
        : call.path.includes('/files/info')
          ? jsonResponse({ schema_version: 1, workspace_id: 'ws_1', file: FILE })
          : jsonResponse({ schema_version: 1, workspace_id: 'ws_1', file: content() }),
    )
    await render({ path: FILE.path })
    act(() => buffers.setDraft({ workspaceId: 'ws_1', sessionId: 'ses_root' }, FILE.path, 'unsaved'))
    await act(async () => {
      void guards[0].confirmLeave()
      await Promise.resolve()
    })
    await userEvent.setup().click(screen.getByRole('button', { name: '保存并继续' }))
    await settle()
    const dialog = screen.getByRole('dialog', { name: '未保存的文件' })
    expect(within(dialog).getByRole('alert').textContent).toContain('仍有未保存或未核对的草稿')
    expect(buffers.buffer({ workspaceId: 'ws_1', sessionId: 'ses_root' }, FILE.path).draftText).toBe('unsaved')
  })
})
