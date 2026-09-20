/**
 * Inspector（会话右侧执行面板）的外壳状态：纯 UI 状态，按 workspaceId +
 * sessionId 隔离，持久化到 sessionStorage 的版本化键
 * `morrow.inspector.v1.<workspace>.<session>`。只保存标签、显隐、尺寸与非
 * 敏感定位 target；不保存凭据、审批正文、终端正文或 Prompt。存储损坏/超额
 * 时回退内存并给出提示，不阻断聊天。
 *
 * 与 NavigationStore 一样，Inspector 显隐不是 AppLocation；运行 store
 * （ChatStore/TaskPlanStore/ActivityStore）的生命周期不随本状态变化。
 */

export type InspectorKind = 'context' | 'workflow' | 'artifacts' | 'terminal' | 'learning' | 'file'
export const INSPECTOR_KINDS: readonly InspectorKind[] = [
  'context',
  'workflow',
  'artifacts',
  'terminal',
  'learning',
  'file',
]

/** 非敏感定位信息：正文用它重新核验并加载目标，不存正文内容。 */
export interface InspectorTarget {
  taskRunId?: string
  workflowRunId?: string
  agentRunId?: string
  nodeRunId?: string
  artifactId?: string
  /** “文件”标签的工作区相对路径；打开当前文件时使用，绝不携带绝对路径。 */
  path?: string
  /** “文件”标签的显示名（结果里的文件名或输出槽）；不是正文。 */
  label?: string
  /** 1 起算的定位行号；打开后滚动到该行。 */
  line?: number
  /** 去掉行号后缀后的候选路径；只有真实文件不存在时才回退。 */
  fallbackPath?: string
}

/** 一次文件打开请求：结果列表、Markdown 链接与产物面板共用的判别联合。 */
export type FileTarget =
  | { kind: 'workspace'; path: string; line?: number; fallbackPath?: string }
  | {
      kind: 'artifact'
      artifactId: string
      label: string
      /** 可信的当前文件路径（来自变更证据）时，提供“打开当前文件”。 */
      path?: string | null
      taskRunId?: string
      workflowRunId?: string
      nodeRunId?: string
    }

/** 判别联合 → 持久化定位信息；未知联合分支返回 null，不猜测目标。 */
export function inspectorTargetOf(target: FileTarget): InspectorTarget {
  if (target.kind === 'workspace') {
    return {
      path: target.path,
      ...(target.line !== undefined ? { line: target.line } : {}),
      ...(target.fallbackPath !== undefined ? { fallbackPath: target.fallbackPath } : {}),
    }
  }
  return {
    artifactId: target.artifactId,
    label: target.label,
    ...(target.path ? { path: target.path } : {}),
    ...(target.taskRunId ? { taskRunId: target.taskRunId } : {}),
    ...(target.workflowRunId ? { workflowRunId: target.workflowRunId } : {}),
    ...(target.nodeRunId ? { nodeRunId: target.nodeRunId } : {}),
  }
}

export interface InspectorSessionState {
  version: 1
  visible: boolean
  size: 'normal' | 'expanded'
  width?: number
  tabs: InspectorKind[]
  activeTab: InspectorKind | null
  targets: Partial<Record<InspectorKind, InspectorTarget>>
}

export interface InspectorScope {
  workspaceId: string
  sessionId: string
}

/** 初次进入新会话：默认收起、无预打开标签。每次调用返回新对象。 */
export function createInspectorState(): InspectorSessionState {
  return { version: 1, visible: false, size: 'normal', tabs: [], activeTab: null, targets: {} }
}

export function inspectorStorageKey(scope: InspectorScope): string {
  return `morrow.inspector.v1.${scope.workspaceId}.${scope.sessionId}`
}

export type InspectorAction =
  | { type: 'open'; kind: InspectorKind; target?: InspectorTarget }
  | { type: 'close'; kind: InspectorKind }
  | { type: 'activate'; kind: InspectorKind }
  | { type: 'setVisible'; visible: boolean }
  | { type: 'setSize'; size: 'normal' | 'expanded' }
  | { type: 'setWidth'; width: number }

/**
 * 不变量：六种工具各一份；打开已存在标签只激活（有 target 更新 target）并先
 * 显示面板；关闭非活动标签不影响正文/目标/激活态；关闭活动标签优先右邻否则
 * 左邻，最后一个关闭后 activeTab=null 且面板保持打开；显隐与尺寸只改自身。
 */
export function inspectorReducer(state: InspectorSessionState, action: InspectorAction): InspectorSessionState {
  switch (action.type) {
    case 'open': {
      const exists = state.tabs.includes(action.kind)
      if (exists && action.target === undefined && state.activeTab === action.kind && state.visible) {
        return state
      }
      return {
        ...state,
        visible: true,
        tabs: exists ? state.tabs : [...state.tabs, action.kind],
        activeTab: action.kind,
        targets: action.target === undefined ? state.targets : { ...state.targets, [action.kind]: action.target },
      }
    }
    case 'close': {
      const index = state.tabs.indexOf(action.kind)
      if (index < 0) return state
      const targets = { ...state.targets }
      delete targets[action.kind]
      return {
        ...state,
        tabs: state.tabs.filter(kind => kind !== action.kind),
        activeTab: state.activeTab === action.kind
          ? state.tabs[index + 1] ?? state.tabs[index - 1] ?? null
          : state.activeTab,
        targets,
      }
    }
    case 'activate': {
      if (!state.tabs.includes(action.kind) || state.activeTab === action.kind) return state
      return { ...state, activeTab: action.kind }
    }
    case 'setVisible':
      return state.visible === action.visible ? state : { ...state, visible: action.visible }
    case 'setSize':
      return state.size === action.size ? state : { ...state, size: action.size }
    case 'setWidth':
      return state.width === action.width ? state : { ...state, width: action.width }
  }
}

const TARGET_KEYS = [
  'taskRunId',
  'workflowRunId',
  'agentRunId',
  'nodeRunId',
  'artifactId',
  'path',
  'label',
  'fallbackPath',
] as const

/** 定位信息里的字符串字段逐项白名单；其余键一律丢弃。 */
function sanitizeTarget(value: unknown): InspectorTarget | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const target: InspectorTarget = {}
  for (const key of TARGET_KEYS) {
    if (typeof raw[key] === 'string' && raw[key] !== '') target[key] = raw[key] as string
  }
  const line = raw.line
  if (typeof line === 'number' && Number.isInteger(line) && line >= 1 && line <= 1_000_000) {
    target.line = line
  }
  return target
}

/** 白名单修复持久化状态；无法辨认（非 v1 对象）时返回 null 走默认布局。 */
export function sanitizeInspectorState(value: unknown): InspectorSessionState | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  if (raw.version !== 1) return null
  const tabs: InspectorKind[] = []
  if (Array.isArray(raw.tabs)) {
    for (const item of raw.tabs) {
      if (INSPECTOR_KINDS.includes(item as InspectorKind) && !tabs.includes(item as InspectorKind)) {
        tabs.push(item as InspectorKind)
      }
    }
  }
  const activeTab = tabs.includes(raw.activeTab as InspectorKind)
    ? (raw.activeTab as InspectorKind)
    : tabs[0] ?? null
  const targets: Partial<Record<InspectorKind, InspectorTarget>> = {}
  if (raw.targets && typeof raw.targets === 'object' && !Array.isArray(raw.targets)) {
    const rawTargets = raw.targets as Record<string, unknown>
    for (const kind of tabs) {
      const target = sanitizeTarget(rawTargets[kind])
      if (target) targets[kind] = target
    }
  }
  const width = typeof raw.width === 'number' && Number.isFinite(raw.width) && raw.width >= 280
    ? Math.round(raw.width)
    : undefined
  return {
    version: 1,
    visible: raw.visible === true,
    size: raw.size === 'expanded' ? 'expanded' : 'normal',
    width,
    tabs,
    activeTab,
    targets,
  }
}

/** 同一文件目标：路径/产物身份与定位一致时视为重复打开。 */
function sameFileTarget(left: InspectorTarget | undefined, right: InspectorTarget): boolean {
  if (left === undefined) return false
  return (
    (left.path ?? null) === (right.path ?? null) &&
    (left.artifactId ?? null) === (right.artifactId ?? null) &&
    (left.line ?? null) === (right.line ?? null)
  )
}

export interface InspectorStoreOptions {
  storage?: Pick<Storage, 'getItem' | 'setItem'> | null
}

/** 内存 scope 缓存上限；sessionStorage 仍是恢复来源，淘汰不丢状态。 */
const MAX_CACHED_SCOPES = 50

export class InspectorStore {
  private state: InspectorSessionState = createInspectorState()
  private scope: InspectorScope | null = null
  private scopes = new Map<string, InspectorSessionState>()
  private storageWarning: string | null = null
  private listeners = new Set<() => void>()
  private readonly storage: Pick<Storage, 'getItem' | 'setItem'> | null
  /** 面板内部的离开确认（文件草稿）；未注册时所有切换都直接提交。 */
  private leaveConfirm: (() => Promise<boolean>) | null = null

  constructor(options: InspectorStoreOptions = {}) {
    this.storage = options.storage ?? (typeof sessionStorage === 'undefined' ? null : sessionStorage)
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  getState = (): InspectorSessionState => this.state
  /** 存储损坏/超额时的提示；展示层只播报，不阻断聊天。 */
  getStorageWarning = (): string | null => this.storageWarning

  /** 切换 workspace/session：原 scope 状态留在内存缓存与 sessionStorage。 */
  setScope(scope: InspectorScope | null) {
    const nextKey = scope ? inspectorStorageKey(scope) : null
    const currentKey = this.scope ? inspectorStorageKey(this.scope) : null
    if (nextKey === currentKey) return
    this.scope = scope
    this.state = scope ? this.load(scope) : createInspectorState()
    this.notify()
  }

  openInspectorTab(kind: InspectorKind, target?: InspectorTarget) {
    this.dispatch({ type: 'open', kind, target })
  }

  /**
   * 注册面板内部的离开确认。文件草稿属于面板而不是导航目标，所以“打开另一个
   * 文件”“关闭 file 标签”这类切换必须先经过同一个保存/放弃/取消流程；返回
   * 解绑函数，面板卸载后恢复为直接提交。
   */
  setLeaveConfirm(handler: (() => Promise<boolean>) | null): () => void {
    this.leaveConfirm = handler
    return () => {
      if (this.leaveConfirm === handler) this.leaveConfirm = null
    }
  }

  /** 无确认处理器时同步返回 true，避免把普通切换变成一次多余的异步跳转。 */
  private mayLeave(): true | Promise<boolean> {
    if (this.leaveConfirm === null) return true
    return this.leaveConfirm()
  }

  /** 打开“文件”标签并选中一个文件；重复打开同一文件不重复加载。 */
  openFile(target: FileTarget): boolean | Promise<boolean> {
    const mapped = inspectorTargetOf(target)
    const current = this.state
    if (
      current.visible &&
      current.activeTab === 'file' &&
      current.tabs.includes('file') &&
      sameFileTarget(current.targets.file, mapped)
    ) {
      return true
    }
    const verdict = this.mayLeave()
    if (verdict === true) {
      this.openInspectorTab('file', mapped)
      return true
    }
    return verdict.then(allowed => {
      if (allowed) this.openInspectorTab('file', mapped)
      return allowed
    })
  }

  closeTab(kind: InspectorKind): boolean | Promise<boolean> {
    const verdict = kind === 'file' ? this.mayLeave() : true
    if (verdict === true) {
      this.dispatch({ type: 'close', kind })
      return true
    }
    return verdict.then(allowed => {
      if (allowed) this.dispatch({ type: 'close', kind })
      return allowed
    })
  }

  activateTab(kind: InspectorKind) {
    this.dispatch({ type: 'activate', kind })
  }

  setVisible(visible: boolean) {
    this.dispatch({ type: 'setVisible', visible })
  }

  toggleVisible() {
    this.setVisible(!this.state.visible)
  }

  setSize(size: 'normal' | 'expanded') {
    this.dispatch({ type: 'setSize', size })
  }

  toggleSize() {
    this.setSize(this.state.size === 'normal' ? 'expanded' : 'normal')
  }

  setWidth(width: number) {
    this.dispatch({ type: 'setWidth', width: Math.round(width) })
  }

  private dispatch(action: InspectorAction) {
    const next = inspectorReducer(this.state, action)
    if (next === this.state) return
    this.state = next
    this.persist()
    this.notify()
  }

  private notify() {
    for (const listener of this.listeners) listener()
  }

  private load(scope: InspectorScope): InspectorSessionState {
    const key = inspectorStorageKey(scope)
    const memory = this.scopes.get(key)
    if (memory) return memory
    if (!this.storage) return createInspectorState()
    let raw: string | null = null
    try {
      raw = this.storage.getItem(key)
    } catch {
      return createInspectorState()
    }
    if (raw === null) return createInspectorState()
    try {
      const parsed = sanitizeInspectorState(JSON.parse(raw))
      if (parsed !== null) return parsed
    } catch {
      // fall through to the reset notice
    }
    this.storageWarning = '执行面板的本地状态已损坏，已重置为默认布局；会话内容不受影响。'
    return createInspectorState()
  }

  private persist() {
    if (!this.scope) return
    const key = inspectorStorageKey(this.scope)
    if (!this.scopes.has(key) && this.scopes.size >= MAX_CACHED_SCOPES) {
      const oldest = this.scopes.keys().next().value
      if (oldest !== undefined) this.scopes.delete(oldest)
    }
    this.scopes.set(key, this.state)
    if (!this.storage) return
    try {
      this.storage.setItem(key, JSON.stringify(this.state))
      this.storageWarning = null
    } catch {
      this.storageWarning = '执行面板状态无法写入会话存储；本次布局仅保留在内存中。'
    }
  }
}
