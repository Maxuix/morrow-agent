// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { InspectorStore, inspectorStorageKey, type InspectorScope } from './inspector'

const scopeA: InspectorScope = { workspaceId: 'ws_1', sessionId: 'se_a' }
const scopeB: InspectorScope = { workspaceId: 'ws_1', sessionId: 'se_b' }
const scopeOtherWorkspace: InspectorScope = { workspaceId: 'ws_2', sessionId: 'se_a' }

beforeEach(() => {
  sessionStorage.clear()
})

describe('InspectorStore persistence', () => {
  it('restores tab order, active tab, targets, visibility and size from sessionStorage', () => {
    const first = new InspectorStore()
    first.setScope(scopeA)
    first.openInspectorTab('workflow', { workflowRunId: 'wr_1' })
    first.openInspectorTab('terminal')
    first.openInspectorTab('context', { taskRunId: 'tr_1' })
    first.toggleVisible()
    first.toggleSize()
    expect(inspectorStorageKey(scopeA)).toBe('morrow.inspector.v1.ws_1.se_a')
    expect(sessionStorage.getItem('morrow.inspector.v1.ws_1.se_a')).not.toBeNull()

    // A fresh store (e.g. after a workspace-switch remount) recovers the scope.
    const second = new InspectorStore()
    second.setScope(scopeA)
    const state = second.getState()
    expect(state.tabs).toEqual(['workflow', 'terminal', 'context'])
    expect(state.activeTab).toBe('context')
    expect(state.targets.workflow).toEqual({ workflowRunId: 'wr_1' })
    expect(state.visible).toBe(false)
    expect(state.size).toBe('expanded')
  })

  it('keeps scopes isolated and restores the original scope after switching', () => {
    const store = new InspectorStore()
    store.setScope(scopeA)
    store.openInspectorTab('context')
    store.setScope(scopeB)
    expect(store.getState().tabs).toEqual([])
    expect(store.getState().visible).toBe(false)

    store.openInspectorTab('terminal')
    store.setScope(scopeOtherWorkspace)
    expect(store.getState().tabs).toEqual([])

    store.setScope(scopeA)
    expect(store.getState().tabs).toEqual(['context'])
    expect(store.getState().activeTab).toBe('context')
    store.setScope(scopeB)
    expect(store.getState().tabs).toEqual(['terminal'])
    expect(sessionStorage.getItem('morrow.inspector.v1.ws_2.se_a')).toBeNull()
  })

  it('falls back to the default layout with a notice on corrupt JSON', () => {
    sessionStorage.setItem('morrow.inspector.v1.ws_1.se_a', '{broken json')
    const store = new InspectorStore()
    store.setScope(scopeA)
    expect(store.getState().tabs).toEqual([])
    expect(store.getState().visible).toBe(false)
    expect(store.getStorageWarning()).toContain('损坏')
  })

  it('resets unrecognized versions with a notice instead of crashing', () => {
    sessionStorage.setItem('morrow.inspector.v1.ws_1.se_a', JSON.stringify({ version: 99, tabs: ['context'] }))
    const store = new InspectorStore()
    store.setScope(scopeA)
    expect(store.getState().tabs).toEqual([])
    expect(store.getStorageWarning()).toContain('损坏')
  })

  it('keeps state in memory with a notice when sessionStorage is over quota', () => {
    const store = new InspectorStore({
      storage: {
        getItem: () => null,
        setItem: () => {
          throw new DOMException('quota', 'QuotaExceededError')
        },
      },
    })
    store.setScope(scopeA)
    store.openInspectorTab('learning')
    expect(store.getState().tabs).toEqual(['learning'])
    expect(store.getStorageWarning()).toContain('内存')

    // In-memory cache still restores the scope within the same store.
    store.setScope(scopeB)
    store.setScope(scopeA)
    expect(store.getState().tabs).toEqual(['learning'])
  })

  it('rapid A→B→A scope switches restore A exactly; B writes never land under A', () => {
    // I04 迟到回填防护的 store 级等价：外壳不取数，可测竞态是「切换后旧 scope
    // 的写入不得污染新 scope」。快速往返中 B 的每次 dispatch 只写自己的键。
    const store = new InspectorStore()
    store.setScope(scopeA)
    store.openInspectorTab('workflow', { workflowRunId: 'wr_a' })
    store.openInspectorTab('terminal')
    store.setSize('expanded')
    const aKey = inspectorStorageKey(scopeA)
    const aSnapshot = sessionStorage.getItem(aKey)

    store.setScope(scopeB)
    store.openInspectorTab('learning', { taskRunId: 'tr_b' })
    store.toggleVisible()
    store.setScope(scopeA)
    const restored = store.getState()
    expect(restored.tabs).toEqual(['workflow', 'terminal'])
    expect(restored.activeTab).toBe('terminal')
    expect(restored.targets.workflow).toEqual({ workflowRunId: 'wr_a' })
    expect(restored.targets.learning).toBeUndefined()
    expect(restored.size).toBe('expanded')

    // B 的写不落在 A 的持久化记录上，反之 B 自己的记录完整。
    expect(sessionStorage.getItem(aKey)).toBe(aSnapshot)
    const bRecord = JSON.parse(sessionStorage.getItem(inspectorStorageKey(scopeB))!)
    expect(bRecord.tabs).toEqual(['learning'])
    expect(bRecord.targets.learning).toEqual({ taskRunId: 'tr_b' })

    // 再切回 B：状态从内存缓存恢复，未被 A 的后续操作污染。
    store.openInspectorTab('context', { taskRunId: 'tr_a2' })
    store.setScope(scopeB)
    expect(store.getState().tabs).toEqual(['learning'])
    expect(store.getState().targets.context).toBeUndefined()
    store.setScope(null)
    expect(store.getState().tabs).toEqual([])
    store.setScope(scopeA)
    expect(store.getState().tabs).toEqual(['workflow', 'terminal', 'context'])
  })

  it('clears the warning after a successful write and notifies subscribers', () => {
    let fail = true
    const store = new InspectorStore({
      storage: {
        getItem: () => null,
        setItem: () => {
          if (fail) throw new DOMException('quota', 'QuotaExceededError')
        },
      },
    })
    store.setScope(scopeA)
    store.openInspectorTab('context')
    expect(store.getStorageWarning()).not.toBeNull()
    fail = false
    let notified = 0
    const unsubscribe = store.subscribe(() => {
      notified += 1
    })
    store.openInspectorTab('terminal')
    expect(notified).toBe(1)
    expect(store.getStorageWarning()).toBeNull()
    unsubscribe()
  })
})
