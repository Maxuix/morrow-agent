import { describe, expect, it } from 'vitest'
import type { AgentDefinitionVersionWire } from '../../api/types'
import { cloneWorkflowSource, newSingleNodeWorkflow, nodeIsLocked, sourceFromRevision, structuralDiff } from './editor'

const agent: AgentDefinitionVersionWire = {
  version_id: 'adev_one',
  workspace_id: 'ws_one',
  version: 1,
  source: {
    definition_id: 'helper',
    name: 'Helper',
    description: '',
    role_prompt: 'Help.',
    skill_version_ids: [],
    tool_requirements: [],
    access_mode_ceiling: 'read',
    max_agent_generation_requests: null,
    model_selection: 'invoking_active',
    derived_from_version_id: null,
    derived_from_definition_id: null,
    derived_from_source_hash: null,
  },
  content_hash: 'a'.repeat(64),
  origin: 'user',
  source_revision: 1,
  created_at: '2026-09-04T00:00:00Z',
}

describe('Workflow editor source helpers', () => {
  it('creates a legal-shaped one-node user source without guessed caps', () => {
    const source = newSingleNodeWorkflow('my_flow', 'My flow', agent)

    expect(source.required_outputs).toEqual([{ node_id: 'agent', output_slot: 'result' }])
    expect(source.default_budget.max_agent_generation_requests).toBeNull()
    expect(source.nodes[0]?.agent_definition_ref.version_id).toBe('adev_one')
  })

  it('clones source identity without mutating the template', () => {
    const template = newSingleNodeWorkflow('template', 'Template', agent)
    const clone = cloneWorkflowSource(template, 'copy', 'Copy')

    expect(clone.workflow_definition_id).toBe('copy')
    expect(clone.origin).toBe('user')
    expect(template.workflow_definition_id).toBe('template')
  })

  it('produces deterministic path-level diffs', () => {
    expect(structuralDiff({ name: 'a', nodes: [1] }, { name: 'b', nodes: [1, 2] })).toEqual([
      { path: '$.name', before: '"a"', after: '"b"' },
      { path: '$.nodes[1]', before: '∅', after: '2' },
    ])
  })

  it('removes compiler-only node fields from a Revision diff base', () => {
    const source = newSingleNodeWorkflow('flow', 'Flow', agent)
    const revision = {
      ...source,
      budget: source.default_budget,
      nodes: source.nodes.map((node) => ({ ...node, resolved_model_ref: { provider_id: 'p', model_id: 'm' } })),
    }
    delete (revision as { default_budget?: unknown }).default_budget

    expect(sourceFromRevision(revision)).toEqual(source)
  })

  it('locks every admitted or historical node status', () => {
    expect(nodeIsLocked('queued')).toBe(false)
    expect(nodeIsLocked('running')).toBe(true)
    expect(nodeIsLocked('completed')).toBe(true)
    expect(nodeIsLocked('failed')).toBe(true)
  })
})
