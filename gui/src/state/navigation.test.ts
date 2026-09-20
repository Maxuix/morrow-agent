import { describe, expect, it } from 'vitest'
import {
  NavigationStore,
  parseLocation,
  serializeLocation,
  viewForLocation,
  type AppLocation,
  type DirtyGuard,
} from './navigation'

describe('parseLocation whitelist', () => {
  it('falls back to chat for empty, bare, and unknown targets', () => {
    expect(parseLocation('')).toEqual({ kind: 'chat' })
    expect(parseLocation('#')).toEqual({ kind: 'chat' })
    expect(parseLocation('#/chat')).toEqual({ kind: 'chat' })
    expect(parseLocation('#/admin/panel')).toEqual({ kind: 'chat' })
    expect(parseLocation('not-a-route')).toEqual({ kind: 'chat' })
    expect(parseLocation('#//')).toEqual({ kind: 'chat' })
  })

  it('parses sectioned kinds and falls back to the default section', () => {
    expect(parseLocation('#/knowledge/preferences')).toEqual({ kind: 'knowledge', section: 'preferences' })
    expect(parseLocation('#/knowledge')).toEqual({ kind: 'knowledge', section: 'profile' })
    expect(parseLocation('#/knowledge/bogus')).toEqual({ kind: 'knowledge', section: 'profile' })
    expect(parseLocation('#/knowledge/preferences?focus=add')).toEqual({ kind: 'knowledge', section: 'preferences', focus: 'add' })
    expect(parseLocation('#/knowledge/knowledge?focus=learning')).toEqual({ kind: 'knowledge', section: 'knowledge', focus: 'learning' })
    expect(parseLocation('#/knowledge/profile?focus=bogus')).toEqual({ kind: 'knowledge', section: 'profile' })
    expect(parseLocation('#/tools/mcp')).toEqual({ kind: 'tools', section: 'mcp' })
    expect(parseLocation('#/tools/bogus')).toEqual({ kind: 'tools', section: 'skills' })
    expect(parseLocation('#/tools/skills?item=报告助手')).toEqual({ kind: 'tools', section: 'skills', item: '报告助手' })
    expect(parseLocation('#/tools/mcp?item=&foo=1')).toEqual({ kind: 'tools', section: 'mcp' })
    expect(parseLocation('#/settings/agents')).toEqual({ kind: 'settings', section: 'agents' })
    expect(parseLocation('#/settings')).toEqual({ kind: 'settings', section: 'providers' })
    expect(parseLocation('#/settings/providers/extra')).toEqual({ kind: 'settings', section: 'providers' })
  })

  it('falls back to chat for the retired observe route', () => {
    expect(parseLocation('#/observe')).toEqual({ kind: 'chat' })
    expect(parseLocation('#/observe?task=t1&session=s1')).toEqual({ kind: 'chat' })
  })

  it('parses the editor target and falls back to the workflow definitions', () => {
    expect(parseLocation('#/editor')).toEqual({ kind: 'editor', target: 'workflows' })
    expect(parseLocation('#/editor/files')).toEqual({ kind: 'editor', target: 'workflows' })
    expect(parseLocation('#/editor/files?path=src/main.py')).toEqual({ kind: 'editor', target: 'workflows' })
    expect(parseLocation('#/editor/workflows')).toEqual({ kind: 'editor', target: 'workflows' })
    expect(parseLocation('#/editor/bogus')).toEqual({ kind: 'editor', target: 'workflows' })
  })
})

describe('serialize/parse round-trip', () => {
  const locations: AppLocation[] = [
    { kind: 'chat' },
    { kind: 'knowledge', section: 'profile' },
    { kind: 'knowledge', section: 'preferences' },
    { kind: 'knowledge', section: 'knowledge' },
    { kind: 'knowledge', section: 'preferences', focus: 'add' },
    { kind: 'knowledge', section: 'knowledge', focus: 'learning' },
    { kind: 'tools', section: 'skills' },
    { kind: 'tools', section: 'mcp' },
    { kind: 'tools', section: 'skills', item: '报告助手' },
    { kind: 'editor', target: 'workflows' },
    { kind: 'settings', section: 'providers' },
    { kind: 'settings', section: 'diagnostics' },
  ]
  it('parse(serialize(x)) deep-equals x for every variant', () => {
    for (const location of locations) {
      expect(parseLocation(serializeLocation(location))).toEqual(location)
    }
  })
})

describe('viewForLocation render mapping', () => {
  it('maps each kind onto its shell view container', () => {
    expect(viewForLocation({ kind: 'chat' })).toBe('chat')
    expect(viewForLocation({ kind: 'editor', target: 'workflows' })).toBe('edit')
    expect(viewForLocation({ kind: 'settings', section: 'providers' })).toBe('settings')
    expect(viewForLocation({ kind: 'knowledge', section: 'profile' })).toBe('knowledge')
    expect(viewForLocation({ kind: 'knowledge', section: 'knowledge' })).toBe('knowledge')
    expect(viewForLocation({ kind: 'tools', section: 'skills' })).toBe('tools')
    expect(viewForLocation({ kind: 'tools', section: 'mcp' })).toBe('tools')
  })
})

function guard(dirty: boolean, log: string[], name: string, allow: boolean): DirtyGuard & { calls: number } {
  const self = {
    calls: 0,
    isDirty: () => dirty,
    confirmLeave: () => {
      self.calls += 1
      log.push(name)
      return Promise.resolve(allow)
    },
  }
  return self
}

describe('dirty guard stack', () => {
  it('passes with no guards and skips clean guards', async () => {
    const store = new NavigationStore('')
    expect(await store.confirmLeave()).toBe(true)
    const log: string[] = []
    const clean = guard(false, log, 'clean', true)
    store.registerDirtyGuard(clean)
    expect(await store.confirmLeave()).toBe(true)
    expect(clean.calls).toBe(0)
    expect(store.hasDirtyGuards()).toBe(false)
  })

  it('consults dirty guards newest first and stops on cancel', async () => {
    const store = new NavigationStore('')
    const log: string[] = []
    const older = guard(true, log, 'older', true)
    const newer = guard(true, log, 'newer', false)
    store.registerDirtyGuard(older)
    store.registerDirtyGuard(newer)
    expect(store.hasDirtyGuards()).toBe(true)
    expect(await store.confirmLeave()).toBe(false)
    expect(log).toEqual(['newer'])
    expect(older.calls).toBe(0)
  })

  it('unregisters guards via the returned function', async () => {
    const store = new NavigationStore('')
    const log: string[] = []
    const unregister = store.registerDirtyGuard(guard(true, log, 'gone', true))
    unregister()
    expect(await store.confirmLeave()).toBe(true)
    expect(log).toEqual([])
    expect(store.hasDirtyGuards()).toBe(false)
  })
})

describe('navigate', () => {
  it('commits the location, records returnTo, and notifies subscribers', async () => {
    const store = new NavigationStore('')
    let notified = 0
    store.subscribe(() => {
      notified += 1
    })
    const chat: AppLocation = { kind: 'chat' }
    expect(await store.navigate({ kind: 'settings', section: 'providers' }, chat)).toBe(true)
    expect(store.getState()).toEqual({ location: { kind: 'settings', section: 'providers' }, returnTo: chat })
    expect(notified).toBe(1)
  })

  it('is a no-op for the current location and is cancelled by guards', async () => {
    const store = new NavigationStore('')
    let notified = 0
    store.subscribe(() => {
      notified += 1
    })
    expect(await store.navigate({ kind: 'chat' })).toBe(true)
    expect(notified).toBe(0)
    let allow = false
    store.registerDirtyGuard({ isDirty: () => true, confirmLeave: () => Promise.resolve(allow) })
    expect(await store.navigate({ kind: 'editor', target: 'workflows' })).toBe(false)
    expect(store.getState().location).toEqual({ kind: 'chat' })
    expect(notified).toBe(0)
    // An approving guard lets the same navigation through.
    allow = true
    expect(await store.navigate({ kind: 'editor', target: 'workflows' })).toBe(true)
    expect(store.getState().location).toEqual({ kind: 'editor', target: 'workflows' })
  })
})
