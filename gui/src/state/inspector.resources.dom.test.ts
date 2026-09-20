// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import { ActivityStore } from './activity'
import { ChatStore } from './chat'
import { INSPECTOR_KINDS, InspectorStore, type InspectorScope } from './inspector'
import { SyncStore } from './sync'
import { TaskPlanStore } from './taskPlan'

const scopeA: InspectorScope = { workspaceId: 'ws_1', sessionId: 'se_a' }
const scopeB: InspectorScope = { workspaceId: 'ws_1', sessionId: 'se_b' }

/**
 * X03 资源有界的 store 级验证：按 ChatWorkspace 的接线方式（SyncStore 一个、
 * ChatStore/ActivityStore/TaskPlanStore 按会话各一份，useMemo 持有）建立运行
 * store 后，把 Inspector 外壳的全部动作跑一遍——开满六标签、逐一切换、反复
 * 显隐、宽度切换、A→B→A 快速切会话——断言不新增任何 WebSocket、不重建任何
 * 运行 store 实例。socket 计数走各 store 的 factory 注入点（chat.ts /
 * activity.ts / sync.ts 的封装处）。
 */
describe('Inspector shell resource bound (X03)', () => {
  const sockets: string[] = []
  const countingFactory = (tag: string) => (url: string) => {
    sockets.push(`${tag}:${url}`)
    throw new Error('test harness never connects')
  }

  beforeEach(() => {
    sockets.length = 0
    sessionStorage.clear()
  })

  it('six tabs + visibility churn + session switches create no sockets and no new stores', () => {
    const client = new ApiClient({ baseUrl: '', token: '', fetchImpl: async () => new Response('{}') })
    const sync = new SyncStore({ client, token: '', wsFactory: countingFactory('sync') })
    // ChatWorkspace 的 useMemo 等价物：每个会话一份运行 store。
    const sessions = new Map(
      [scopeA.sessionId, scopeB.sessionId].map(sessionId => [
        sessionId,
        {
          chat: new ChatStore(client, scopeA.workspaceId, sessionId, countingFactory('chat')),
          activity: new ActivityStore(client, scopeA.workspaceId, sessionId, countingFactory('activity')),
          plan: new TaskPlanStore(client, scopeA.workspaceId, sessionId),
        },
      ]),
    )
    const before = new Map(
      [...sessions].map(([sessionId, stores]) => [sessionId, { ...stores }]),
    )

    const inspector = new InspectorStore()
    const exercise = () => {
      for (const kind of INSPECTOR_KINDS) inspector.openInspectorTab(kind)
      expect(inspector.getState().tabs).toHaveLength(6)
      for (const kind of INSPECTOR_KINDS) inspector.activateTab(kind)
      inspector.toggleVisible()
      inspector.toggleVisible()
      inspector.setVisible(false)
      inspector.setVisible(true)
      inspector.toggleSize()
      inspector.toggleSize()
    }
    inspector.setScope(scopeA)
    exercise()
    inspector.setScope(scopeB)
    exercise()
    inspector.setScope(scopeA)
    inspector.setScope(scopeB)
    inspector.setScope(null)
    inspector.setScope(scopeA)

    expect(sockets).toEqual([])
    expect(inspector.getState().tabs).toHaveLength(6)
    for (const [sessionId, stores] of sessions) {
      const original = before.get(sessionId)!
      expect(stores.chat).toBe(original.chat)
      expect(stores.activity).toBe(original.activity)
      expect(stores.plan).toBe(original.plan)
    }
    void sync
  })
})
