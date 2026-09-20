import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../api/client'
import type { WorkspaceFileInfo } from '../api/types'
import {
  FileBufferStore,
  editability,
  isDirty,
  normalizeToLf,
  submittedSha256,
  type FileBuffer,
  type FileScope,
} from './fileBuffer'

/** 可控的异步闸门：让测试决定响应何时到达，而不是猜测时序。 */
function deferred() {
  const box: { release: (() => void) | null } = { release: null }
  const promise = new Promise<void>(resolve => {
    box.release = resolve
  })
  return { promise, release: () => box.release?.() }
}

const SCOPE: FileScope = { workspaceId: 'ws_1', sessionId: 'ses_1' }
const PATH = 'docs/我的 文件.md'
const SHA_A = 'a'.repeat(64)
const SHA_B = 'b'.repeat(64)

interface Call {
  path: string
  method: string
  body: Record<string, unknown> | null
}

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function failure(code: string, status: number): Response {
  return json({ error: { code, message: `${code} 失败` } }, status)
}

function contentWire(overrides: Record<string, unknown> = {}) {
  return {
    path: PATH,
    text: 'first\nsecond\n',
    revision: { sha256: SHA_A, size: 14, mtime_ns: 0 },
    bom: false,
    newline: 'lf',
    ...overrides,
  }
}

function infoWire(overrides: Partial<WorkspaceFileInfo> = {}): WorkspaceFileInfo {
  return {
    path: PATH,
    kind: 'file',
    byte_size: 14,
    text: true,
    editable: true,
    preview: 'text',
    ...overrides,
  }
}

function harness(handler: (call: Call) => Response | Promise<Response>) {
  const calls: Call[] = []
  const client = new ApiClient({
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
  return { store: new FileBufferStore(client), calls, client }
}

async function loaded(handler: (call: Call) => Response | Promise<Response>) {
  const harnessed = harness(handler)
  await harnessed.store.load(SCOPE, PATH)
  return harnessed
}

describe('FileBufferStore', () => {
  it('loads info and content into a clean draft', async () => {
    const { store } = await loaded(call =>
      call.path.includes('/files/info')
        ? json({ schema_version: 1, workspace_id: 'ws_1', file: infoWire() })
        : json({ schema_version: 1, workspace_id: 'ws_1', file: contentWire() }),
    )
    const buffer = store.buffer(SCOPE, PATH)
    expect(buffer.loadedRevision).toBe(SHA_A)
    expect(buffer.draftText).toBe('first\nsecond\n')
    expect(buffer.info?.byte_size).toBe(14)
    expect(isDirty(buffer)).toBe(false)
    expect(editability(buffer).ok).toBe(true)
  })

  it('keeps a draft while hiding and re-reading the same file', async () => {
    const { store } = await loaded(call =>
      call.path.includes('/files/info')
        ? json({ schema_version: 1, workspace_id: 'ws_1', file: infoWire() })
        : json({ schema_version: 1, workspace_id: 'ws_1', file: contentWire() }),
    )
    store.setDraft(SCOPE, PATH, 'local edit')
    expect(store.hasDirty()).toBe(true)
    await store.load(SCOPE, PATH)
    expect(store.buffer(SCOPE, PATH).draftText).toBe('local edit')
    expect(isDirty(store.buffer(SCOPE, PATH))).toBe(true)
  })

  it('sends the loaded revision and a command id, then clears dirty on success', async () => {
    const { store, calls } = await loaded(call => {
      if (call.method === 'PUT') {
        return json({
          schema_version: 1,
          workspace_id: 'ws_1',
          file: contentWire({ text: 'edited\n', revision: { sha256: SHA_B, size: 7, mtime_ns: 0 } }),
          mutation: null,
          disposition: 'accepted',
        })
      }
      return call.path.includes('/files/info')
        ? json({ schema_version: 1, workspace_id: 'ws_1', file: infoWire() })
        : json({ schema_version: 1, workspace_id: 'ws_1', file: contentWire() })
    })
    store.setDraft(SCOPE, PATH, 'edited\n')
    const outcome = await store.save(SCOPE, PATH)

    expect(outcome).toBe('saved')
    const put = calls.find(call => call.method === 'PUT')
    expect(put?.body?.expected_sha256).toBe(SHA_A)
    expect(put?.body?.content).toBe('edited\n')
    expect(String(put?.body?.command_id)).toMatch(/^cmd_file_save_/)
    const buffer = store.buffer(SCOPE, PATH)
    expect(buffer.loadedRevision).toBe(SHA_B)
    expect(isDirty(buffer)).toBe(false)
    expect(buffer.save.phase).toBe('saved')
  })

  it('keeps text typed during an in-flight save dirty', async () => {
    const gate = deferred()
    const { store } = await loaded(async call => {
      if (call.method === 'PUT') {
        await gate.promise
        return json({
          schema_version: 1,
          workspace_id: 'ws_1',
          file: contentWire({ text: 'submitted\n', revision: { sha256: SHA_B, size: 10, mtime_ns: 0 } }),
          mutation: null,
          disposition: 'accepted',
        })
      }
      return call.path.includes('/files/info')
        ? json({ schema_version: 1, workspace_id: 'ws_1', file: infoWire() })
        : json({ schema_version: 1, workspace_id: 'ws_1', file: contentWire() })
    })
    store.setDraft(SCOPE, PATH, 'submitted\n')
    const pending = store.save(SCOPE, PATH)
    expect(store.buffer(SCOPE, PATH).save.phase).toBe('saving')
    expect(await store.save(SCOPE, PATH)).toBe('busy')
    store.setDraft(SCOPE, PATH, 'submitted\nmore')
    gate.release()
    expect(await pending).toBe('saved')
    const buffer = store.buffer(SCOPE, PATH)
    expect(buffer.loadedText).toBe('submitted\n')
    expect(buffer.draftText).toBe('submitted\nmore')
    expect(isDirty(buffer)).toBe(true)
  })

  it('reuses the command id only while the submission is unchanged', async () => {
    const { store, calls } = await loaded(call => {
      if (call.method === 'PUT') return failure('stale', 409)
      if (call.path.includes('/files/content')) {
        return json({ schema_version: 1, workspace_id: 'ws_1', file: contentWire({ revision: { sha256: SHA_B, size: 14, mtime_ns: 0 } }) })
      }
      return json({ schema_version: 1, workspace_id: 'ws_1', file: infoWire() })
    })
    store.setDraft(SCOPE, PATH, 'attempt one')
    expect(await store.save(SCOPE, PATH)).toBe('conflict')
    const first = String(calls.filter(call => call.method === 'PUT').at(-1)?.body?.command_id)
    expect(await store.save(SCOPE, PATH)).toBe('conflict')
    const second = String(calls.filter(call => call.method === 'PUT').at(-1)?.body?.command_id)
    expect(second).toBe(first)

    store.setDraft(SCOPE, PATH, 'attempt two')
    expect(await store.save(SCOPE, PATH)).toBe('conflict')
    const third = String(calls.filter(call => call.method === 'PUT').at(-1)?.body?.command_id)
    expect(third).not.toBe(first)
  })

  it('keeps the draft and reports the disk revision on a 409 conflict', async () => {
    // 初次读取是基线 SHA_A；保存前第三方已把磁盘改成 SHA_B。
    let reads = 0
    const { store } = await loaded(call => {
      if (call.method === 'PUT') return failure('stale', 409)
      if (call.path.includes('/files/content')) {
        reads += 1
        return json({
          schema_version: 1,
          workspace_id: 'ws_1',
          file: reads === 1
            ? contentWire()
            : contentWire({ text: 'other writer\n', revision: { sha256: SHA_B, size: 14, mtime_ns: 0 } }),
        })
      }
      return json({ schema_version: 1, workspace_id: 'ws_1', file: infoWire() })
    })
    store.setDraft(SCOPE, PATH, 'my draft')
    expect(await store.save(SCOPE, PATH)).toBe('conflict')
    const buffer = store.buffer(SCOPE, PATH)
    expect(buffer.save.phase).toBe('conflict')
    expect(buffer.save.diskRevision).toBe(SHA_B)
    expect(buffer.save.revision).toBe(SHA_A)
    expect(buffer.draftText).toBe('my draft')
    expect(buffer.loadedText).toBe('first\nsecond\n')
  })

  it('recognizes an already-written file after a lost response', async () => {
    const digest = await submittedSha256('edited\nsecond\n', false, 'lf')
    const calls: string[] = []
    // 故障注入：写入已经生效，但响应在返回途中丢失。
    let written = false
    const client = new ApiClient({
      baseUrl: '',
      token: '',
      fetchImpl: async (url, init) => {
        calls.push(String(url))
        if (init?.method === 'PUT') {
          written = true
          throw new TypeError('network request failed')
        }
        if (String(url).includes('/files/info')) {
          return json({ schema_version: 1, workspace_id: 'ws_1', file: infoWire() })
        }
        return json({
          schema_version: 1,
          workspace_id: 'ws_1',
          file: written
            ? contentWire({ text: 'edited\nsecond\n', revision: { sha256: digest, size: 15, mtime_ns: 0 } })
            : contentWire(),
        })
      },
    })
    const store = new FileBufferStore(client)
    await store.load(SCOPE, PATH)
    store.setDraft(SCOPE, PATH, 'edited\nsecond\n')
    expect(await store.save(SCOPE, PATH)).toBe('saved')
    const buffer = store.buffer(SCOPE, PATH)
    expect(buffer.save.phase).toBe('saved')
    expect(buffer.save.message).toContain('该内容已在磁盘上')
    expect(isDirty(buffer)).toBe(false)
    expect(calls.filter(path => path.includes('/files/content')).length).toBeGreaterThan(1)
  })

  it('keeps the draft when a read-back shows different content', async () => {
    const calls: string[] = []
    const client = new ApiClient({
      baseUrl: '',
      token: '',
      fetchImpl: async (url, init) => {
        calls.push(String(url))
        if (init?.method === 'PUT') return new Response('boom', { status: 503 })
        if (String(url).includes('/files/info')) {
          return json({ schema_version: 1, workspace_id: 'ws_1', file: infoWire() })
        }
        return json({ schema_version: 1, workspace_id: 'ws_1', file: contentWire() })
      },
    })
    const store = new FileBufferStore(client)
    await store.load(SCOPE, PATH)
    store.setDraft(SCOPE, PATH, 'never written')
    expect(await store.save(SCOPE, PATH)).toBe('unverified')
    const buffer = store.buffer(SCOPE, PATH)
    expect(buffer.save.phase).toBe('unverified')
    expect(buffer.draftText).toBe('never written')
    expect(isDirty(buffer)).toBe(true)
    expect(buffer.save.commandId).not.toBeNull()
  })

  it('marks a definite rejection as an error without losing the draft', async () => {
    const { store } = await loaded(call =>
      call.method === 'PUT'
        ? failure('invalid', 400)
        : json({ schema_version: 1, workspace_id: 'ws_1', file: call.path.includes('/files/info') ? infoWire() : contentWire() }),
    )
    store.setDraft(SCOPE, PATH, 'too long')
    expect(await store.save(SCOPE, PATH)).toBe('error')
    expect(store.buffer(SCOPE, PATH).save.phase).toBe('error')
    expect(store.buffer(SCOPE, PATH).draftText).toBe('too long')
  })

  it('refreshes the disk revision without dropping the draft', async () => {
    let revision = SHA_A
    const { store } = await loaded(call => {
      if (call.path.includes('/files/info')) {
        return json({ schema_version: 1, workspace_id: 'ws_1', file: infoWire() })
      }
      return json({ schema_version: 1, workspace_id: 'ws_1', file: contentWire({ revision: { sha256: revision, size: 14, mtime_ns: 0 } }) })
    })
    store.setDraft(SCOPE, PATH, 'merged draft')
    revision = SHA_B
    await store.refreshFromDisk(SCOPE, PATH)
    const buffer = store.buffer(SCOPE, PATH)
    expect(buffer.loadedRevision).toBe(SHA_B)
    expect(buffer.draftText).toBe('merged draft')
    expect(buffer.save.message).toContain('已读取磁盘当前版本')
  })

  it('drops a stale response for an abandoned target', async () => {
    const gate = deferred()
    const { store } = await loaded(async call => {
      if (call.method === 'PUT') {
        await gate.promise
        return json({
          schema_version: 1,
          workspace_id: 'ws_1',
          file: contentWire({ text: 'saved\n', revision: { sha256: SHA_B, size: 6, mtime_ns: 0 } }),
          mutation: null,
          disposition: 'accepted',
        })
      }
      return call.path.includes('/files/info')
        ? json({ schema_version: 1, workspace_id: 'ws_1', file: infoWire() })
        : json({ schema_version: 1, workspace_id: 'ws_1', file: contentWire() })
    })
    store.setDraft(SCOPE, PATH, 'saved\n')
    const pending = store.save(SCOPE, PATH)
    store.discardDraft(SCOPE, PATH)
    gate.release()
    await pending
    const buffer = store.buffer(SCOPE, PATH)
    expect(buffer.draftText).toBe('first\nsecond\n')
    expect(buffer.loadedRevision).toBe(SHA_A)
    expect(buffer.loadedText).toBe('first\nsecond\n')
  })

  it('never writes drafts to browser storage', async () => {
    const setItem = vi.fn()
    const getItem = vi.fn(() => null)
    const original = globalThis.sessionStorage
    Object.defineProperty(globalThis, 'sessionStorage', {
      configurable: true,
      value: { setItem, getItem },
    })
    try {
      const { store } = await loaded(call =>
        json({
          schema_version: 1,
          workspace_id: 'ws_1',
          file: call.path.includes('/files/info') ? infoWire() : contentWire(),
        }),
      )
      store.setDraft(SCOPE, PATH, 'secret draft')
      expect(setItem).not.toHaveBeenCalled()
    } finally {
      Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: original })
    }
  })
})

describe('editability', () => {
  const base = (overrides: Partial<FileBuffer> = {}): FileBuffer => ({ ...storeShape(), ...overrides })

  function storeShape(): FileBuffer {
    return {
      workspaceId: 'ws_1',
      sessionId: 'ses_1',
      path: PATH,
      generation: 0,
      loading: false,
      loadError: null,
      info: infoWire(),
      loadedRevision: SHA_A,
      loadedText: 'x',
      draftText: 'x',
      bom: false,
      newline: 'lf' as const,
      save: {
        phase: 'idle',
        message: null,
        revision: null,
        diskRevision: null,
        commandId: null,
        submittedText: null,
      },
    }
  }

  it('refuses binary, oversized and mixed-newline files', () => {
    expect(editability(base({ info: infoWire({ text: false, preview: 'image' }) })).ok).toBe(false)
    expect(editability(base({ info: infoWire({ byte_size: 2 * 1024 * 1024, editable: false }) })).reason).toContain('1 MiB')
    expect(editability(base({ newline: 'mixed' })).reason).toContain('混合换行')
    expect(editability(base()).ok).toBe(true)
  })
})

describe('normalizeToLf and submittedSha256', () => {
  it('normalizes CRLF for editing and hashes against the written bytes', async () => {
    expect(normalizeToLf('a\r\nb\rc')).toBe('a\nb\nc')
    const digest = await submittedSha256('a\nb\n', false, 'crlf')
    expect(digest).toBe(await submittedSha256('a\nb\n', false, 'crlf'))
    expect(digest).not.toBe(await submittedSha256('a\nb\n', false, 'lf'))
  })
})
