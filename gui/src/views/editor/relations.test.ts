import { describe, expect, it } from 'vitest'
import { fixtureSource, MULTI_OUTPUT_SOURCE, THREE_STEP_SOURCE } from './fixtures'
import {
  applyConnectionEdit,
  completionOutputs,
  edgeRemovalImpact,
  nodeRemovalImpact,
  reachableNodes,
  renameOutputReferences,
  wouldCreateCycle,
} from './relations'

describe('workflow relationship analysis', () => {
  it('finds reachable ancestors, cycles and completion-required output choices', () => {
    expect([...reachableNodes(THREE_STEP_SOURCE, 'research')]).toEqual(['research', 'implement', 'review'])
    expect(wouldCreateCycle(THREE_STEP_SOURCE, 'review', 'research')).toBe(true)
    expect(wouldCreateCycle(THREE_STEP_SOURCE, 'research', 'review')).toBe(false)
    expect(completionOutputs(THREE_STEP_SOURCE, 'implement')).toEqual([
      { node_id: 'implement', output_slot: 'result', kind: 'TextResult', version: 1 },
    ])
  })

  it('commits control-only and data-carrying edits atomically, with no mutation on blockers', () => {
    const original = fixtureSource(
      'relations',
      'Relations',
      [THREE_STEP_SOURCE.nodes[0]!, { ...THREE_STEP_SOURCE.nodes[2]!, input_bindings: [] }],
      [],
      [{ node_id: 'review', output_slot: 'result' }],
    )
    const edit = applyConnectionEdit(original, {
      fromNodeId: 'research',
      toNodeId: 'review',
      keepControlEdge: true,
      bindings: [{ inputName: 'research_result', outputSlot: 'result' }],
    })
    expect(edit.blockers).toEqual([])
    expect(edit.source?.edges).toEqual([{ from_node_id: 'research', to_node_id: 'review' }])
    expect(edit.source?.nodes[1]?.input_bindings[0]).toMatchObject({
      input_name: 'research_result',
      node_output: { node_id: 'research', output_slot: 'result' },
    })
    const blocked = applyConnectionEdit(edit.source!, {
      fromNodeId: 'review',
      toNodeId: 'research',
      keepControlEdge: true,
      bindings: [],
    })
    expect(blocked.source).toBeNull()
    expect(blocked.blockers.join(' ')).toContain('环')
    expect(original.edges).toEqual([])
  })

  it('reports edge/node impact and renames all exact references without dropping other outputs', () => {
    const edge = edgeRemovalImpact(THREE_STEP_SOURCE, 'research', 'implement')
    expect(edge.bindings).toEqual([{ node_id: 'implement', input_name: 'research_result', output_slot: 'result' }])
    expect(edge.hasAlternativePath).toBe(false)
    const impact = nodeRemovalImpact(THREE_STEP_SOURCE, 'implement')
    expect(impact.edgeIds).toEqual(['research->implement', 'implement->review'])
    expect(impact.bindings).toEqual([{ node_id: 'review', input_name: 'implementation', output_slot: 'result' }])
    const renamed = renameOutputReferences(THREE_STEP_SOURCE, 'implement', 'result', 'report')
    expect(renamed.nodes[2]?.input_bindings[0]).toMatchObject({ node_output: { output_slot: 'report' } })
    expect(renamed.required_outputs).toEqual([{ node_id: 'review', output_slot: 'result' }])
    expect(THREE_STEP_SOURCE.nodes[1]?.output_contracts[0]?.slot).toBe('result')
  })

  it('preserves unrelated bindings while adding a second producer and rejects input-name conflicts', () => {
    const result = applyConnectionEdit(THREE_STEP_SOURCE, {
      fromNodeId: 'research',
      toNodeId: 'review',
      keepControlEdge: true,
      bindings: [{ inputName: 'research_context', outputSlot: 'result' }],
    })
    expect(result.blockers).toEqual([])
    expect(result.source?.nodes.find(node => node.node_id === 'review')?.input_bindings).toEqual([
      expect.objectContaining({ input_name: 'implementation', node_output: { node_id: 'implement', output_slot: 'result' } }),
      expect.objectContaining({ input_name: 'research_context', node_output: { node_id: 'research', output_slot: 'result' } }),
    ])

    const blocked = applyConnectionEdit(THREE_STEP_SOURCE, {
      fromNodeId: 'research',
      toNodeId: 'review',
      keepControlEdge: true,
      bindings: [{ inputName: 'implementation', outputSlot: 'result' }],
    })
    expect(blocked.source).toBeNull()
    expect(blocked.blockers.join(' ')).toContain('其他来源')
  })

  it('keeps alternative control reachability visible and repairs duplicate edges on confirmation', () => {
    const withAlternativePath = structuredClone(THREE_STEP_SOURCE)
    withAlternativePath.edges = [
      ...withAlternativePath.edges,
      { from_node_id: 'research', to_node_id: 'review' },
    ]
    const impact = edgeRemovalImpact(withAlternativePath, 'research', 'review')
    expect(impact.hasAlternativePath).toBe(true)

    const repaired = applyConnectionEdit(withAlternativePath, {
      fromNodeId: 'research',
      toNodeId: 'review',
      keepControlEdge: true,
      bindings: [],
    })
    expect(repaired.blockers).toEqual([])
    expect(repaired.source?.edges.filter(edge => edge.from_node_id === 'research' && edge.to_node_id === 'review')).toHaveLength(1)
    expect(repaired.source?.nodes.find(node => node.node_id === 'review')?.input_bindings).toEqual([
      expect.objectContaining({ input_name: 'implementation' }),
    ])
  })

  it('keeps multiple completion outputs available without inventing a result', () => {
    expect(completionOutputs(MULTI_OUTPUT_SOURCE)).toEqual([
      { node_id: 'produce', output_slot: 'summary', kind: 'TextResult', version: 1 },
      { node_id: 'produce', output_slot: 'evidence', kind: 'TextResult', version: 1 },
    ])
    const blocked = applyConnectionEdit(MULTI_OUTPUT_SOURCE, {
      fromNodeId: 'produce',
      toNodeId: 'produce',
      keepControlEdge: true,
      bindings: [{ inputName: 'self', outputSlot: 'result' }],
    })
    expect(blocked.source).toBeNull()
    expect(blocked.blockers.join(' ')).toContain('自身')
  })
})
