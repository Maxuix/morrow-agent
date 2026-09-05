import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import type { ManagedSkill, ResolvedContext, SkillDraft } from '../api/management'
import { contextSummary } from './ContextBar'
import { ResolvedContextView } from './ContextDrawer'
import { DraftCard, partitionSkills } from './SkillManager'

const resolved: ResolvedContext = {
  workspace_id: 'ws_demo', task_run_id: 'task_selected', agent_run_id: 'arun_old',
  available_runs: [{ agent_run_id: 'arun_old' }], status: 'resolved',
  preferences: [{ preference_id: 'pref_old', statement: 'Original frozen rule', scope: 'workspace', revision: 1, updated_at: '2026-09-05' }],
  profile: { name: 'Demo', summary: null, goals: [], tech_stack: [], constraints: [], conventions: ['Use pnpm'] },
  source_revisions: [], preference_digest: 'frozen-digest', omitted_count: 2, refresh_status: 'ok',
  knowledge: [{ selection: { record_id: 'knw_old', revision: 1 }, revision: { knowledge_revision_id: 'krv_old', revision: 1, statement: 'Historical selected knowledge', supersedes_revision_id: null, source_candidate_id: 'lcn_old' } }],
  memory_selection_id: 'msel_old', pending_learning_count: 3, convention_count: 1, language: null, verbosity: null,
}

describe('Context/Learning/Skill management contract', () => {
  it('shows actually resolved rules, scopes and immutable Knowledge revisions', () => {
    const html = renderToStaticMarkup(<ResolvedContextView value={resolved} />)
    expect(html).toContain('Original frozen rule')
    expect(html).toContain('Historical selected knowledge')
    expect(html).toContain('arun_old')
    expect(html).toContain('r1')
    expect(html).toContain('编辑影响之后的解析')
    expect(contextSummary(resolved)).toContain('见已解析规则')
    expect(contextSummary(resolved)).toContain('待确认学习 3')
    expect(contextSummary(resolved)).toContain('约定 1')
  })

  it('never invents language or verbosity from generic statements', () => {
    const summary = contextSummary({ ...resolved, preferences: [{ ...resolved.preferences[0], statement: 'Maybe Chinese, maybe detailed' }] })
    expect(summary).not.toContain('Chinese')
    expect(summary).not.toContain('detailed')
  })

  it('renders untrusted context as escaped text', () => {
    const html = renderToStaticMarkup(<ResolvedContextView value={{ ...resolved, preferences: [{ ...resolved.preferences[0], statement: '<img src=x onerror=alert(1)>' }] }} />)
    expect(html).toContain('&lt;img')
    expect(html).not.toContain('<img')
  })

  it('separates enabled packages and inactive packages from generated Drafts', () => {
    const active = { enabled: true, status: { skill_id: 'active' } } as ManagedSkill
    const inactive = { enabled: false, status: { skill_id: 'inactive' } } as ManagedSkill
    expect(partitionSkills([active, inactive])).toEqual({ active: [active], inactive: [inactive] })
    const draft: SkillDraft = { draft: { draft_id: 'sdf_demo', name: 'Generated report', status: 'validated', row_version: 1, revision: 1, tree_digest: 'digest', evidence_refs: ['lcn_demo'], accepted_version_id: null, parent_draft_id: null }, validation: { valid: true, findings: [] }, diff: null }
    const html = renderToStaticMarkup(<DraftCard item={draft} mutate={async () => true} />)
    expect(html).toContain('Generated report · Draft')
    expect(html).toContain('接受只发布不可变版本')
    expect(html).not.toContain('启用 Skill')
    const invalid = renderToStaticMarkup(<DraftCard item={{ ...draft, validation: { valid: false, findings: [] } }} mutate={async () => true} />)
    expect(invalid).toMatch(/disabled="">接受并发布版本/)
  })

  it('sends explicit revision and stable identity through authenticated same-origin commands', async () => {
    const calls: { url: string; method: string | undefined; auth: string | null; body: unknown }[] = []
    const client = new ApiClient({ baseUrl: '', token: 'test-only', fetchImpl: async (url, init) => {
      calls.push({ url: String(url), method: init?.method, auth: new Headers(init?.headers).get('authorization'), body: init?.body ? JSON.parse(String(init.body)) : null })
      return new Response(JSON.stringify({ result: { status: 'applied' } }), { status: 200 })
    } })
    const body = { command_id: 'cmd_same', action: 'disable', expected_row_version: 4 }
    await client.managementCommand('knowledge', body, 'knw_demo')
    await client.managementCommand('knowledge', body, 'knw_demo')
    expect(calls[0]).toEqual(calls[1])
    expect(calls[0]).toEqual({ url: '/v1/management/knowledge/knw_demo', method: 'POST', auth: 'Bearer test-only', body })
    await client.managementQuery('context', { task_run_id: 'task_selected', agent_run_id: 'arun_old' })
    expect(calls[2].url).toBe('/v1/management/context?task_run_id=task_selected&agent_run_id=arun_old')
  })
})
