// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { NavigationStore, serializeLocation, type DirtyGuard } from './navigation'

/** hashchange is dispatched asynchronously; let it settle (plus a revert round). */
function flush(times = 3): Promise<void> {
  let chain = Promise.resolve()
  for (let index = 0; index < times; index += 1) {
    chain = chain.then(() => new Promise<void>(resolve => setTimeout(resolve, 0)))
  }
  return chain
}

beforeEach(() => {
  window.location.hash = ''
})

describe('NavigationStore hash adapter', () => {
  it('parses the entry hash and normalizes illegal targets without a new entry', async () => {
    window.location.hash = '#/settings/agents'
    const store = new NavigationStore()
    expect(store.getState().location).toEqual({ kind: 'settings', section: 'agents' })
    const stop = store.start()
    expect(window.location.hash).toBe('#/settings/agents')
    stop()

    window.location.hash = '#/nonsense'
    const fallback = new NavigationStore()
    expect(fallback.getState().location).toEqual({ kind: 'chat' })
    const stopFallback = fallback.start()
    expect(window.location.hash).toBe('#/chat')
    stopFallback()
  })

  it('mirrors navigate() into the address bar', async () => {
    const store = new NavigationStore()
    const stop = store.start()
    await store.navigate({ kind: 'knowledge', section: 'preferences' })
    expect(window.location.hash).toBe('#/knowledge/preferences')
    await flush()
    expect(store.getState().location).toEqual({ kind: 'knowledge', section: 'preferences' })
    stop()
  })

  it('adopts hash jumps (browser back/forward) when no guard objects', async () => {
    const store = new NavigationStore()
    const stop = store.start()
    await store.navigate({ kind: 'settings', section: 'providers' })
    // Simulate the browser moving history: the URL changes first.
    window.location.hash = '#/editor'
    await flush()
    expect(store.getState().location).toEqual({ kind: 'editor', target: 'workflows' })
    stop()
  })

  it('restores the current hash when a guard cancels the history jump', async () => {
    const store = new NavigationStore()
    const stop = store.start()
    await store.navigate({ kind: 'knowledge', section: 'profile' })
    store.registerDirtyGuard({ isDirty: () => true, confirmLeave: () => Promise.resolve(false) })
    window.location.hash = '#/chat'
    await flush()
    expect(store.getState().location).toEqual({ kind: 'knowledge', section: 'profile' })
    expect(window.location.hash).toBe(serializeLocation(store.getState().location))
    stop()
  })

  it('accepts the history jump once the guard approves', async () => {
    const store = new NavigationStore()
    const stop = store.start()
    await store.navigate({ kind: 'knowledge', section: 'profile' })
    let dirty = true
    const guard: DirtyGuard = {
      isDirty: () => dirty,
      confirmLeave: () => {
        dirty = false
        return Promise.resolve(true)
      },
    }
    store.registerDirtyGuard(guard)
    window.location.hash = '#/settings/providers'
    await flush()
    expect(store.getState().location).toEqual({ kind: 'settings', section: 'providers' })
    expect(window.location.hash).toBe('#/settings/providers')
    stop()
  })

  it('drives native beforeunload protection from dirty guards only', async () => {
    const store = new NavigationStore()
    const stop = store.start()
    let dirty = false
    const unregister = store.registerDirtyGuard({
      isDirty: () => dirty,
      confirmLeave: () => Promise.resolve(true),
    })
    const first = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(first)
    expect(first.defaultPrevented).toBe(false)

    dirty = true
    const second = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(second)
    expect(second.defaultPrevented).toBe(true)

    unregister()
    const third = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(third)
    expect(third.defaultPrevented).toBe(false)
    stop()
  })
})
