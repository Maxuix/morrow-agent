import { describe, expect, it } from 'vitest'
import {
  createInspectorState,
  InspectorStore,
  inspectorReducer,
  sanitizeInspectorState,
  type InspectorKind,
  type InspectorSessionState,
} from './inspector'
import { COMPACT_THRESHOLD, expandedClampNote, inspectorLayout } from '../views/inspector/layout'

function open(state: InspectorSessionState, kind: InspectorKind) {
  return inspectorReducer(state, { type: 'open', kind })
}

describe('inspectorReducer', () => {
  it('opens a new tab: appends, activates, and shows the panel first', () => {
    const state = inspectorReducer(createInspectorState(), { type: 'open', kind: 'context' })
    expect(state.tabs).toEqual(['context'])
    expect(state.activeTab).toBe('context')
    expect(state.visible).toBe(true)
  })

  it('reopening an existing tab activates without duplicating and updates the target', () => {
    let state = open(createInspectorState(), 'context')
    state = open(state, 'workflow')
    state = inspectorReducer(state, { type: 'open', kind: 'context', target: { taskRunId: 'tr_1' } })
    expect(state.tabs).toEqual(['context', 'workflow'])
    expect(state.activeTab).toBe('context')
    expect(state.targets.context).toEqual({ taskRunId: 'tr_1' })

    const again = inspectorReducer(state, { type: 'open', kind: 'context' })
    expect(again.activeTab).toBe('context')
    expect(again.targets.context).toEqual({ taskRunId: 'tr_1' })
  })

  it('holds at most one tab per kind across all six tools', () => {
    let state = createInspectorState()
    for (const kind of ['context', 'workflow', 'artifacts', 'terminal', 'learning', 'file', 'context', 'terminal'] as const) {
      state = open(state, kind)
    }
    expect(state.tabs).toEqual(['context', 'workflow', 'artifacts', 'terminal', 'learning', 'file'])
    expect(state.activeTab).toBe('terminal')
  })

  it('closing an inactive tab keeps body, targets and active tab untouched', () => {
    let state = open(createInspectorState(), 'context')
    state = open(state, 'workflow')
    state = open(state, 'terminal')
    const before = state
    state = inspectorReducer(state, { type: 'close', kind: 'context' })
    expect(state.tabs).toEqual(['workflow', 'terminal'])
    expect(state.activeTab).toBe('terminal')
    expect(state.visible).toBe(before.visible)
  })

  it('closing the active tab prefers the right neighbor, else the left one', () => {
    let state = open(createInspectorState(), 'context')
    state = open(state, 'workflow')
    state = open(state, 'terminal')
    state = inspectorReducer(state, { type: 'activate', kind: 'workflow' })
    state = inspectorReducer(state, { type: 'close', kind: 'workflow' })
    expect(state.activeTab).toBe('terminal')

    state = inspectorReducer(state, { type: 'activate', kind: 'terminal' })
    state = inspectorReducer(state, { type: 'close', kind: 'terminal' })
    expect(state.activeTab).toBe('context')
  })

  it('closing the last tab leaves the panel open with activeTab=null and clears its target', () => {
    let state = inspectorReducer(createInspectorState(), { type: 'open', kind: 'terminal', target: { taskRunId: 'tr_9' } })
    state = inspectorReducer(state, { type: 'close', kind: 'terminal' })
    expect(state.tabs).toEqual([])
    expect(state.activeTab).toBeNull()
    expect(state.visible).toBe(true)
    expect(state.targets.terminal).toBeUndefined()
  })

  it('closing a tab that is not open is a no-op', () => {
    const state = open(createInspectorState(), 'context')
    expect(inspectorReducer(state, { type: 'close', kind: 'learning' })).toBe(state)
  })

  it('visibility toggles preserve tabs, active tab and targets', () => {
    let state = inspectorReducer(createInspectorState(), { type: 'open', kind: 'workflow', target: { workflowRunId: 'wr_1' } })
    state = inspectorReducer(state, { type: 'setVisible', visible: false })
    expect(state.visible).toBe(false)
    expect(state.tabs).toEqual(['workflow'])
    expect(state.activeTab).toBe('workflow')
    expect(state.targets.workflow).toEqual({ workflowRunId: 'wr_1' })
    state = inspectorReducer(state, { type: 'setVisible', visible: true })
    expect(state.visible).toBe(true)
  })

  it('size toggles only change the layout size', () => {
    let state = open(createInspectorState(), 'context')
    state = inspectorReducer(state, { type: 'setSize', size: 'expanded' })
    expect(state.size).toBe('expanded')
    expect(state.tabs).toEqual(['context'])
    expect(state.activeTab).toBe('context')
    expect(inspectorReducer(state, { type: 'setSize', size: 'expanded' })).toBe(state)
  })

  it('setWidth updates width without affecting other state and no-ops on same value', () => {
    let state = open(createInspectorState(), 'context')
    state = inspectorReducer(state, { type: 'setWidth', width: 450 })
    expect(state.width).toBe(450)
    expect(state.tabs).toEqual(['context'])
    expect(inspectorReducer(state, { type: 'setWidth', width: 450 })).toBe(state)
  })

  it('activate only accepts open tabs and opening while hidden reveals the panel', () => {
    let state = open(createInspectorState(), 'context')
    expect(inspectorReducer(state, { type: 'activate', kind: 'learning' })).toBe(state)
    state = inspectorReducer(state, { type: 'setVisible', visible: false })
    state = inspectorReducer(state, { type: 'open', kind: 'context' })
    expect(state.visible).toBe(true)
  })
})

describe('InspectorStore.openFile', () => {
  it('maps a workspace target and a line suffix into the persisted target', () => {
    const store = new InspectorStore({ storage: null })
    store.setScope({ workspaceId: 'ws_1', sessionId: 'se_1' })
    store.openFile({ kind: 'workspace', path: 'docs/我的 文件.md', line: 12, fallbackPath: 'docs/我的 文件.md' })
    expect(store.getState().tabs).toEqual(['file'])
    expect(store.getState().activeTab).toBe('file')
    expect(store.getState().visible).toBe(true)
    expect(store.getState().targets.file).toEqual({
      path: 'docs/我的 文件.md',
      line: 12,
      fallbackPath: 'docs/我的 文件.md',
    })
  })

  it('keeps an artifact target distinguishable from the current file', () => {
    const store = new InspectorStore({ storage: null })
    store.setScope({ workspaceId: 'ws_1', sessionId: 'se_1' })
    store.openFile({ kind: 'artifact', artifactId: 'art_1', label: 'src/app.py', path: 'src/app.py', taskRunId: 'task_1' })
    expect(store.getState().targets.file).toEqual({
      artifactId: 'art_1',
      label: 'src/app.py',
      path: 'src/app.py',
      taskRunId: 'task_1',
    })
  })

  it('re-opening the same file does not reload, but a different file does', () => {
    const store = new InspectorStore({ storage: null })
    store.setScope({ workspaceId: 'ws_1', sessionId: 'se_1' })
    store.openFile({ kind: 'workspace', path: 'a.md' })
    const before = store.getState()
    store.openFile({ kind: 'workspace', path: 'a.md' })
    expect(store.getState()).toBe(before)
    store.openFile({ kind: 'workspace', path: 'b.md' })
    expect(store.getState()).not.toBe(before)
    expect(store.getState().targets.file).toEqual({ path: 'b.md' })
  })
})

describe('sanitizeInspectorState', () => {
  it('rejects non-v1 and non-object payloads', () => {
    expect(sanitizeInspectorState(null)).toBeNull()
    expect(sanitizeInspectorState('x')).toBeNull()
    expect(sanitizeInspectorState({ version: 2, tabs: [] })).toBeNull()
  })

  it('drops unknown kinds, duplicates and invalid active tabs', () => {
    const parsed = sanitizeInspectorState({
      version: 1,
      visible: true,
      size: 'normal',
      width: 480,
      tabs: ['context', 'context', 'invalid'],
      activeTab: 'invalid',
    })
    expect(parsed?.tabs).toEqual(['context'])
    expect(parsed?.activeTab).toBe('context')
    expect(parsed?.width).toBe(480)

    const parsedInvalidWidth = sanitizeInspectorState({
      version: 1,
      visible: true,
      size: 'normal',
      width: 100,
      tabs: ['context'],
      activeTab: 'context',
    })
    expect(parsedInvalidWidth?.width).toBeUndefined()
  })

  it('keeps the file target whitelist and drops a hostile or malformed line', () => {
    const parsed = sanitizeInspectorState({
      version: 1,
      visible: true,
      size: 'normal',
      tabs: ['file'],
      activeTab: 'file',
      targets: {
        file: {
          path: 'docs/a.md',
          label: 'a.md',
          fallbackPath: 'docs/a.md',
          line: 3,
          absolutePath: '/etc/passwd',
          body: 'leaked content',
        },
      },
    })
    expect(parsed?.targets.file).toEqual({
      path: 'docs/a.md',
      label: 'a.md',
      fallbackPath: 'docs/a.md',
      line: 3,
    })
    const badLine = sanitizeInspectorState({
      version: 1,
      visible: true,
      size: 'normal',
      tabs: ['file'],
      activeTab: 'file',
      targets: { file: { path: 'a.md', line: -4 } },
    })
    expect(badLine?.targets.file).toEqual({ path: 'a.md' })
  })
})

describe('inspectorLayout', () => {
  it('uses regular mode with 400/620 widths above the threshold', () => {
    expect(inspectorLayout(1440, 'normal')).toEqual({ mode: 'regular', width: 400 })
    expect(inspectorLayout(1440, 'expanded')).toEqual({ mode: 'regular', width: 620 })
  })

  it('clamps the expanded width to keep the chat minimum', () => {
    const layout = inspectorLayout(1000, 'expanded')
    expect(layout.mode).toBe('regular')
    expect(layout.width).toBe(1000 - 480 - 1)
  })

  it('respects customWidth within bounds', () => {
    expect(inspectorLayout(1440, 'normal', 500)).toEqual({ mode: 'regular', width: 500 })
    expect(inspectorLayout(1000, 'normal', 700)).toEqual({ mode: 'regular', width: 1000 - 480 - 1 })
    expect(inspectorLayout(1440, 'normal', 250)).toEqual({ mode: 'regular', width: 280 })
  })

  it('flags compact mode below the 480+400+divider threshold for 2.5', () => {
    expect(COMPACT_THRESHOLD).toBe(881)
    expect(inspectorLayout(880, 'normal').mode).toBe('compact')
    expect(inspectorLayout(881, 'normal').mode).toBe('regular')
  })
})

describe('expandedClampNote', () => {
  it('is silent for normal size or an unclamped expanded panel', () => {
    expect(expandedClampNote('normal', { mode: 'regular', width: 400 })).toBeNull()
    expect(expandedClampNote('expanded', { mode: 'regular', width: 620 })).toBeNull()
    expect(expandedClampNote('expanded', null)).toBeNull()
  })

  it('explains a width-clamped expanded panel with the actual width', () => {
    const note = expandedClampNote('expanded', { mode: 'regular', width: 519 })
    expect(note).toContain('519')
    expect(note).toContain('620')
  })

  it('explains the compact fallback when the width is below the threshold', () => {
    expect(expandedClampNote('expanded', { mode: 'compact', width: 620 })).toContain('紧凑')
  })
})
