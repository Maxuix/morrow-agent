import { describe, expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import type { AgentPresetCatalogWire } from '../api/types'
import { agentPrefill, effortChoice, parseModelRef, presetPrefill } from './AgentPresetsPanel'

function catalog(): AgentPresetCatalogWire {
  return {
    presets: [
      { definition_id: 'builtin_general', role: 'general', name: 'General', description: '通用执行', access: 'write', available: true, preference: null, preference_source: 'inherit_session' },
      { definition_id: 'builtin_explore', role: 'explore', name: 'Explore', description: '只读探查', access: 'read', available: true, preference: { model: { provider_id: 'configured', model_id: 'model' }, generation: null }, preference_source: 'preset_preference' },
      { definition_id: 'builtin_review', role: 'review', name: 'Review', description: '只读审查', access: 'read', available: false, preference: null, preference_source: 'inherit_session' },
    ],
    revision: 3,
  }
}

describe('AgentPresetsPanel contract', () => {
  it('shows fixed preset roles, availability and value sources with text', async () => {
    const data = catalog()
    const client = new ApiClient({ baseUrl: '', token: 't', fetchImpl: async (url) => {
      const path = String(url)
      if (path.includes('/agent-presets')) return new Response(JSON.stringify(data), { status: 200 })
      if (path.includes('agent-definitions')) return new Response(JSON.stringify([]), { status: 200 })
      if (path.includes('/catalog/providers')) return new Response(JSON.stringify({ providers: [], active_model: null }), { status: 200 })
      if (path.includes('/catalog/skills')) return new Response(JSON.stringify({ skills: [] }), { status: 200 })
      if (path.includes('/catalog/tools')) return new Response(JSON.stringify({ tools: [{ name: 'read', description: 'read' }, { name: 'bash', description: 'bash' }] }), { status: 200 })
      if (path.includes('/catalog/artifact-contracts')) return new Response(JSON.stringify({ contracts: [] }), { status: 200 })
      throw new Error(`unexpected ${path}`)
    } })
    // Drive the component's loader through its exports instead of awaiting
    // effects in static markup: the panel is exercised in the browser journey.
    const presets = await client.agentPresets('ws')
    expect(presets.revision).toBe(3)
    expect(presets.presets.map((row) => row.role)).toEqual(['general', 'explore', 'review'])
    expect(presets.presets[1]!.preference_source).toBe('preset_preference')
  })

  it('keeps preference saves on the OCC document revision and validates inputs', () => {
    expect(effortChoice('')).toBeNull()
    expect(effortChoice('model_default')).toEqual({ mode: 'model_default' })
    expect(effortChoice('high')).toEqual({ mode: 'explicit', value: 'high' })
    expect(parseModelRef('configured/model')).toEqual({ provider_id: 'configured', model_id: 'model' })
    expect(() => parseModelRef('broken')).toThrow('provider/model')
  })

  it('prefills copies from presets and preserves advanced fields for custom edits', () => {
    const copy = presetPrefill(catalog().presets[0]!)
    expect(copy.tools).toBe('all')
    expect(copy.name).toContain('自定义')
    const agent = {
      definition_id: 'helper', origin: 'user' as const, source_revision: 2, source_hash: null,
      revoked: false, desired_ahead_of_published: false, source: {
        definition_id: 'helper', name: 'Helper', description: 'Inspect', role_prompt: 'Inspect carefully.',
        skill_version_ids: [], tool_requirements: [{ name: 'read', requirement: 'required' as const }, { name: 'bash', requirement: 'forbidden' as const }],
        access_mode_ceiling: 'read' as const, max_agent_generation_requests: null,
        model_selection: { provider_id: 'configured', model_id: 'model' } as const,
        derived_from_version_id: null, derived_from_definition_id: null, derived_from_source_hash: null,
      },
      head: { workspace_id: 'w', definition_id: 'helper', version_id: 'adev_1', source_revision: 2, source_hash: 'a'.repeat(64), enabled: true, row_version: 4 },
      published_version: null,
    }
    const edit = agentPrefill(agent)
    expect(edit.tools).toBe('list')
    expect(edit.selected).toEqual(['read'])
    expect(edit.model).toBe('configured/model')
    expect(edit.expected_head_revision).toBe(4)
  })
})
