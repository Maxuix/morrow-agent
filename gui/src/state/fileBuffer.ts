/**
 * 单文件编辑缓冲区（计划 output-preview-editor，子计划 2）。
 *
 * 一次只保存当前文件的一份缓冲区：`loadedRevision`/`loadedText` 是磁盘基线，
 * `draftText` 是用户草稿，dirty 由正文比较得出，不维护第二份重复状态。
 * 缓冲区按 workspace + session + path 隔离并保持在内存中：隐藏面板、切换文件或
 * 会话后回到同一文件时草稿仍在，但绝不写入 sessionStorage/localStorage/日志。
 *
 * 保存复用 PUT `files/content`：显式保存、单次只允许一个在途请求、同一逻辑提交
 * 重放原 command_id、新的修改属于新命令。409/STALE 保留草稿；响应丢失（网络错误、
 * 5xx）时读回磁盘并按提交正文的字节 hash 核对，绝不静默覆盖。
 */

import { ApiError, type ApiClient } from '../api/client'
import type {
  WorkspaceFileContentWire,
  WorkspaceFileInfo,
  WorkspaceFileWriteWire,
} from '../api/types'
/** 保存命令编号：前缀固定，便于在事件与日志中辨认来源（不含正文）。 */
function fileCommandId(): string {
  return `cmd_file_save_${crypto.randomUUID().replaceAll('-', '_')}`
}

/** 与服务端 `MAX_EDITABLE_FILE_BYTES` 一致的单文件编辑上限。 */
export const MAX_EDITABLE_FILE_BYTES = 1024 * 1024

export interface FileScope {
  workspaceId: string
  sessionId: string
}

export type FileSavePhase = 'idle' | 'saving' | 'saved' | 'conflict' | 'unverified' | 'error'

export interface FileSaveState {
  phase: FileSavePhase
  message: string | null
  /** 服务端确认的版本；与当前磁盘正文一致时等于 loadedRevision。 */
  revision: string | null
  /** 冲突时读到的磁盘当前版本；读不到时为 null。 */
  diskRevision: string | null
  /** 同一逻辑保存请求的命令编号；正文没变时重用它。 */
  commandId: string | null
  /** 上次提交的正文，用于响应丢失后的核对与重放判定。 */
  submittedText: string | null
}

export interface FileBuffer {
  workspaceId: string
  sessionId: string
  path: string
  /** 请求代次：重新加载、放弃草稿后递增，旧响应据此被丢弃。 */
  generation: number
  loading: boolean
  loadError: string | null
  info: WorkspaceFileInfo | null
  loadedRevision: string | null
  loadedText: string
  draftText: string
  bom: boolean
  newline: WorkspaceFileContentWire['newline']
  save: FileSaveState
}

export type SaveOutcome =
  | 'clean'
  | 'saved'
  | 'replay'
  | 'conflict'
  | 'unverified'
  | 'error'
  | 'busy'
  | 'readonly'

/** 编辑一律以 LF 文本进行；文件原有的换行风格由服务端写入时还原。 */
export function normalizeToLf(text: string): string {
  return text.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
}

export function isDirty(buffer: FileBuffer): boolean {
  return buffer.draftText !== buffer.loadedText
}

function emptySave(previous?: FileSaveState): FileSaveState {
  return {
    phase: 'idle',
    message: null,
    revision: previous?.revision ?? null,
    diskRevision: null,
    commandId: null,
    submittedText: null,
  }
}

function createBuffer(scope: FileScope, path: string): FileBuffer {
  return {
    workspaceId: scope.workspaceId,
    sessionId: scope.sessionId,
    path,
    generation: 0,
    loading: false,
    loadError: null,
    info: null,
    loadedRevision: null,
    loadedText: '',
    draftText: '',
    bom: false,
    newline: 'lf',
    save: emptySave(),
  }
}

/** 能否编辑；不可编辑时给出可直接展示的原因，不猜测。 */
export function editability(buffer: FileBuffer): { ok: boolean; reason: string | null } {
  if (buffer.info === null) return { ok: false, reason: '正在读取文件信息…' }
  const info = buffer.info
  if (!info.text) return { ok: false, reason: '该文件不是可读取的 UTF-8 文本，只能下载。' }
  if (info.byte_size > MAX_EDITABLE_FILE_BYTES) {
    return { ok: false, reason: '文件超过 1 MiB 编辑上限，只能下载。' }
  }
  if (buffer.newline === 'mixed') {
    return { ok: false, reason: '混合换行文件暂不支持安全修改，只能下载。' }
  }
  if (buffer.loadedRevision === null) {
    return { ok: false, reason: '正在读取正文…' }
  }
  return { ok: true, reason: null }
}

/**
 * 提交正文的字节级 SHA-256：与服务端写盘后 `revision.sha256` 同一口径
 * （BOM + 该文件原有换行风格）。环境没有 WebCrypto 时返回 null，调用方退回
 * 文本比较，不因此夸大“已核对”。
 */
export async function submittedSha256(
  text: string,
  bom: boolean,
  newline: WorkspaceFileContentWire['newline'],
): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle
  if (!subtle) return null
  const body =
    newline === 'crlf'
      ? text.replace(/\n/g, '\r\n')
      : newline === 'cr'
        ? text.replace(/\n/g, '\r')
        : text
  const bytes = new TextEncoder().encode((bom ? '\ufeff' : '') + body)
  const digest = await subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest))
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('')
}

function sameTextAsDisk(
  file: WorkspaceFileContentWire,
  submitted: string,
  digest: string | null,
): boolean | null {
  if (digest !== null) return file.revision.sha256 === digest
  return normalizeToLf(file.text) === submitted ? true : null
}

/**
 * 缓冲区唯一持有者。组件只通过本 store 读写，避免第二份 React 状态。
 */
export class FileBufferStore {
  private buffers = new Map<string, FileBuffer>()
  private listeners = new Set<() => void>()
  private revision = 0

  constructor(private readonly client: ApiClient) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /** 快照版本号：useSyncExternalStore 据此重渲染，缓冲区本身按 key 读取。 */
  getVersion = (): number => this.revision

  key(scope: FileScope, path: string): string {
    return `${scope.workspaceId}\u0000${scope.sessionId}\u0000${path}`
  }

  /** 读取（必要时创建）一份缓冲区；创建不触发通知。 */
  buffer(scope: FileScope, path: string): FileBuffer {
    const key = this.key(scope, path)
    const existing = this.buffers.get(key)
    if (existing) return existing
    const created = createBuffer(scope, path)
    this.buffers.set(key, created)
    return created
  }

  /** 已有草稿的缓冲区；用于离开保护提示。 */
  dirtyBuffers(): FileBuffer[] {
    return [...this.buffers.values()].filter(isDirty)
  }

  hasDirty(): boolean {
    return this.dirtyBuffers().length > 0
  }

  /** 读取文件信息与正文；已有草稿不被覆盖。 */
  async load(scope: FileScope, path: string): Promise<void> {
    const key = this.key(scope, path)
    const start = this.buffer(scope, path).generation + 1
    this.commit(key, buffer => ({
      ...buffer,
      generation: start,
      loading: true,
      loadError: null,
    }))
    let info: WorkspaceFileInfo
    try {
      const wire = await this.client.workspaceFileInfo(path)
      info = wire.file
    } catch (error) {
      if (!this.current(key, start)) return
      this.commit(key, buffer => ({
        ...buffer,
        loading: false,
        loadError: error instanceof Error ? error.message : '读取文件信息失败',
      }))
      return
    }
    if (!this.current(key, start)) return
    if (!info.text) {
      this.commit(key, buffer => ({ ...buffer, info, loading: false, loadError: null }))
      return
    }
    this.commit(key, buffer => ({ ...buffer, info }))
    let content: WorkspaceFileContentWire
    try {
      const wire = await this.client.workspaceFileContent(path)
      content = wire.file
    } catch (error) {
      if (!this.current(key, start)) return
      this.commit(key, buffer => ({
        ...buffer,
        loading: false,
        loadError: error instanceof Error ? error.message : '读取正文失败',
      }))
      return
    }
    if (!this.current(key, start)) return
    this.applyDisk(key, content, { keepDraft: true, loading: false })
  }

  /** 同步磁盘基线而不动草稿：冲突后“读取磁盘新版本 / 手动合并”的入口。 */
  async refreshFromDisk(scope: FileScope, path: string): Promise<void> {
    const key = this.key(scope, path)
    const generation = this.buffer(scope, path).generation
    let content: WorkspaceFileContentWire
    try {
      const wire = await this.client.workspaceFileContent(path)
      content = wire.file
    } catch (error) {
      if (!this.current(key, generation)) return
      this.commit(key, buffer => ({
        ...buffer,
        save: {
          ...buffer.save,
          phase: 'error',
          message: error instanceof Error ? error.message : '读取磁盘版本失败',
        },
      }))
      return
    }
    if (!this.current(key, generation)) return
    this.applyDisk(key, content, { keepDraft: true, loading: false })
    this.commit(key, buffer => ({
      ...buffer,
      save: { ...buffer.save, phase: 'idle', message: '已读取磁盘当前版本，可手动合并后再次保存。', diskRevision: null },
    }))
  }

  setDraft(scope: FileScope, path: string, text: string): void {
    const key = this.key(scope, path)
    this.commit(key, buffer => (buffer.draftText === text ? buffer : { ...buffer, draftText: text }))
  }

  /** 放弃草稿并重新读取磁盘：冲突后不再与旧基线纠缠。 */
  async discardAndReload(scope: FileScope, path: string): Promise<void> {
    await this.refreshFromDisk(scope, path)
    this.discardDraft(scope, path)
  }

  /** 放弃本地草稿，回到磁盘基线；用于“放弃并重载”。 */
  discardDraft(scope: FileScope, path: string): void {
    const key = this.key(scope, path)
    this.commit(key, buffer => ({
      ...buffer,
      generation: buffer.generation + 1,
      draftText: buffer.loadedText,
      save: emptySave(buffer.save),
    }))
  }

  async save(scope: FileScope, path: string): Promise<SaveOutcome> {
    const key = this.key(scope, path)
    const buffer = this.buffer(scope, path)
    if (!isDirty(buffer)) return 'clean'
    const editable = editability(buffer)
    if (!editable.ok || buffer.loadedRevision === null) return 'readonly'
    if (buffer.save.phase === 'saving') return 'busy'
    const submission = buffer.draftText
    const reuse = buffer.save.submittedText === submission ? buffer.save.commandId : null
    const command = reuse ?? fileCommandId()
    const generation = buffer.generation
    const revision = buffer.loadedRevision
    this.commit(key, buffer => ({
      ...buffer,
      save: {
        phase: 'saving',
        message: null,
        revision: buffer.save.revision,
        diskRevision: null,
        commandId: command,
        submittedText: submission,
      },
    }))
    let result: WorkspaceFileWriteWire
    try {
      result = await this.client.writeWorkspaceFile(path, submission, revision, command)
    } catch (error) {
      return this.reconcileFailure(key, generation, submission, command, error)
    }
    if (!this.current(key, generation)) return 'saved'
    const text = normalizeToLf(result.file.text)
    const disk = { ...result.file, text }
    if (text !== submission) {
      this.applyDisk(key, disk, { keepDraft: true, loading: false })
      this.commit(key, buffer => ({
        ...buffer,
        save: {
          phase: 'unverified',
          message: '服务端返回的正文与你提交的内容不同，已保留草稿，请核对后再保存。',
          revision: result.file.revision.sha256,
          diskRevision: result.file.revision.sha256,
          commandId: command,
          submittedText: submission,
        },
      }))
      return 'unverified'
    }
    this.applyDisk(key, disk, { keepDraft: true, loading: false })
    const note =
      result.disposition === 'replay'
        ? '该内容已在磁盘上（重放同一命令）。'
        : result.disposition === 'reconciled'
          ? '上次写入已生效、回执丢失；服务端按内容核对确认，未重复写入。'
          : null
    this.commit(key, buffer => ({
      ...buffer,
      save: {
        phase: 'saved',
        message: note,
        revision: result.file.revision.sha256,
        diskRevision: null,
        commandId: null,
        submittedText: null,
      },
    }))
    return result.disposition === 'accepted' ? 'saved' : 'replay'
  }

  /**
   * 保守处理失败：409 视为冲突并保留草稿；网络错误/5xx 读回磁盘，按提交正文的
   * 字节 hash 判断该内容是否已经落盘；明确拒绝才标记为错误。
   */
  private async reconcileFailure(
    key: string,
    generation: number,
    submission: string,
    command: string,
    error: unknown,
  ): Promise<SaveOutcome> {
    const status = error instanceof ApiError ? error.status : 0
    const message = error instanceof Error ? error.message : '保存失败'
    if (status === 409) {
      let diskRevision: string | null = null
      try {
        const disk = await this.client.workspaceFileContent(this.bufferByKey(key).path)
        diskRevision = disk.file.revision.sha256
      } catch {
        diskRevision = null
      }
      if (!this.current(key, generation)) return 'conflict'
      this.commit(key, buffer => ({
        ...buffer,
        save: {
          phase: 'conflict',
          message,
          revision: buffer.save.revision,
          diskRevision,
          commandId: command,
          submittedText: submission,
        },
      }))
      return 'conflict'
    }
    if (status === 0 || status >= 500) {
      let disk: WorkspaceFileContentWire | null = null
      try {
        disk = (await this.client.workspaceFileContent(this.bufferByKey(key).path)).file
      } catch {
        disk = null
      }
      if (!this.current(key, generation)) return 'unverified'
      if (disk !== null) {
        const digest = await submittedSha256(submission, disk.bom, disk.newline)
        const match = sameTextAsDisk(disk, submission, digest)
        if (match === true) {
          this.applyDisk(key, disk, { keepDraft: true, loading: false })
          this.commit(key, buffer => ({
            ...buffer,
            save: {
              phase: 'saved',
              message: '上次保存的响应丢失，已按磁盘 hash 核对：该内容已在磁盘上。',
              revision: disk.revision.sha256,
              diskRevision: null,
              commandId: null,
              submittedText: null,
            },
          }))
          return 'saved'
        }
        this.commit(key, buffer => ({
          ...buffer,
          save: {
            phase: 'unverified',
            message: `${message}；已读回磁盘，但内容与你提交的正文不同，草稿保留。`,
            revision: buffer.save.revision,
            diskRevision: disk.revision.sha256,
            commandId: command,
            submittedText: submission,
          },
        }))
        return 'unverified'
      }
      this.commit(key, buffer => ({
        ...buffer,
        save: {
          phase: 'unverified',
          message: `${message}；无法读回磁盘核对，草稿保留，重试使用同一命令编号。`,
          revision: buffer.save.revision,
          diskRevision: null,
          commandId: command,
          submittedText: submission,
        },
      }))
      return 'unverified'
    }
    if (!this.current(key, generation)) return 'error'
    this.commit(key, buffer => ({
      ...buffer,
      save: {
        phase: 'error',
        message,
        revision: buffer.save.revision,
        diskRevision: null,
        commandId: command,
        submittedText: submission,
      },
    }))
    return 'error'
  }

  private applyDisk(
    key: string,
    content: WorkspaceFileContentWire,
    options: { keepDraft: boolean; loading: boolean },
  ) {
    const text = normalizeToLf(content.text)
    this.commit(key, buffer => {
      const wasDirty = isDirty(buffer)
      const keepDraft = options.keepDraft && wasDirty
      return {
        ...buffer,
        loading: options.loading,
        loadError: null,
        loadedRevision: content.revision.sha256,
        loadedText: text,
        draftText: keepDraft ? buffer.draftText : text,
        bom: content.bom,
        newline: content.newline,
        save: { ...buffer.save, revision: content.revision.sha256 },
      }
    })
  }

  private bufferByKey(key: string): FileBuffer {
    const buffer = this.buffers.get(key)
    if (!buffer) throw new Error(`unknown file buffer: ${key}`)
    return buffer
  }

  private current(key: string, generation: number): boolean {
    const buffer = this.buffers.get(key)
    return buffer !== undefined && buffer.generation === generation
  }

  private commit(key: string, update: (buffer: FileBuffer) => FileBuffer) {
    const buffer = this.buffers.get(key)
    if (!buffer) return
    const next = update(buffer)
    if (next === buffer) return
    this.buffers.set(key, next)
    this.revision += 1
    for (const listener of this.listeners) listener()
  }
}
