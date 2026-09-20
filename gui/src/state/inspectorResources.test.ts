import { describe, expect, it } from 'vitest'
import { InspectorResourceCache, inspectorResourceKey } from './inspectorResources'

describe('InspectorResourceCache', () => {
  it('deduplicates an in-flight read and preserves the value on a normal read', async () => {
    const cache = new InspectorResourceCache()
    let calls = 0
    let resolve: ((value: string) => void) | undefined
    const loader = () => {
      calls += 1
      return new Promise<string>(done => { resolve = done })
    }

    const first = cache.load('context', loader)
    const second = cache.load('context', loader)
    expect(calls).toBe(1)
    resolve?.('frozen context')
    await Promise.all([first, second])
    expect(cache.read<string>('context')).toEqual({ data: 'frozen context', error: null, loading: false })
    await cache.load('context', loader)
    expect(calls).toBe(1)
  })

  it('keeps stale data visible during an explicit refresh and records a safe error', async () => {
    const cache = new InspectorResourceCache()
    await cache.load('learning', async () => ({ count: 1 }))
    const failed = cache.load('learning', async () => { throw new Error('temporary read failure') }, true)
    expect(cache.read<{ count: number }>('learning')).toEqual({ data: { count: 1 }, error: null, loading: true })
    await failed
    expect(cache.read<{ count: number }>('learning')).toEqual({ data: { count: 1 }, error: 'temporary read failure', loading: false })
  })

  it('makes target keys independent of object insertion order', () => {
    expect(inspectorResourceKey('context', 'ws', 'session', { agent: 'a', task: 't' }))
      .toBe(inspectorResourceKey('context', 'ws', 'session', { task: 't', agent: 'a' }))
  })
})
