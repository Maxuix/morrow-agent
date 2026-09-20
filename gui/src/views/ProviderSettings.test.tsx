import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import type { ProviderSettingsView } from '../api/settings'
import { ProviderCard } from './ProviderSettings'

const here = dirname(fileURLToPath(import.meta.url))

const value: ProviderSettingsView = {revision: 3, active_model: null, presets: [], adapters: [{adapter_id: 'fake', discovery: false}], providers: [{provider_id: 'demo', adapter: 'fake', base_url: 'https://example.test', credential_configured: false, last_test: null, models: [{model_id: 'local', api_model_id: 'vendor', capabilities: null}]}]}
describe('Provider management', () => {
  it('shows real mappings and keeps probes unavailable until their prerequisites exist', () => {
    const html = renderToStaticMarkup(<ProviderCard provider={value.providers[0]} value={value} busy={false} mutate={async () => true} credential={async () => {}} />)
    expect(html).toContain('local → vendor')
    expect(html).toContain('type="password"')
    expect(html).toMatch(/disabled="">测试连接/)
    expect(html).toMatch(/disabled="">发现模型/)
    expect(html).toContain('保存凭据')
    expect(html).toContain('设为全局默认')
  })
  it('reads without sending probes and sends credentials only to the dedicated endpoint', async () => {
    const calls: {url: string; body: unknown}[] = []
    const client = new ApiClient({baseUrl: '', token: 'test', fetchImpl: async (url, init) => {
      calls.push({url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null})
      return new Response(JSON.stringify(value), {status: 200})
    }})
    await client.providerSettings()
    expect(calls).toEqual([{url: '/v1/providers', body: null}])
    await client.providerCredential('demo', 'test-key', 3)
    expect(calls[1]).toEqual({url: '/v1/providers/demo/credentials', body: {secret: 'test-key', expected_revision: 3}})
    await client.providerControl('test', {expected_revision: 4}, 'demo')
    expect(calls[2]).toEqual({url: '/v1/providers/demo/test', body: {expected_revision: 4}})
  })

  it('does not write credentials into browser storage or logs from the form source', () => {
    const source = readFileSync(join(here, 'ProviderSettings.tsx'), 'utf8')
    expect(source).toContain('type="password"')
    expect(source).toContain('autoComplete="new-password"')
    expect(source).not.toMatch(/localStorage|sessionStorage/)
    expect(source).not.toMatch(/console\.(log|info|debug|warn|error)/)
    const theme = readFileSync(join(here, '../state/theme.ts'), 'utf8')
    expect(theme).toContain("THEME_STORAGE_KEY = 'morrow.appearance.theme'")
    expect(theme).toMatch(/storage\.setItem\(THEME_STORAGE_KEY, theme\)/)
    expect(theme).not.toMatch(/setItem\([^)]*(secret|credential|api[_-]?key)/i)
  })
})
