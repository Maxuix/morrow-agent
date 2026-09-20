import { describe, expect, it } from 'vitest'
import { buildStepDisplayModels, objectiveTitle, truncateDisplay } from './display'
import { fixtureAgent, fixtureSource, THREE_STEP_SOURCE } from './fixtures'

describe('workflow step display model', () => {
  it('uses the first semantic objective line and keeps the full title separately', () => {
    expect(objectiveTitle('  \n  先收集资料   \n再写报告')).toBe('先收集资料')
    const source = fixtureSource('display', 'Display', [
      {
        ...THREE_STEP_SOURCE.nodes[0]!,
        node_id: 'one',
        task_contract: { objective: '  第一行目标\n第二行不能成为标题', scope: [], constraints: [], source_refs: [] },
      },
    ], [], [{ node_id: 'one', output_slot: 'result' }])
    const model = buildStepDisplayModels(source, [fixtureAgent('helper', '真实助手')])[0]!
    expect(model.title).toBe('第一行目标')
    expect(model.fullTitle).toBe('第一行目标 第二行不能成为标题')
    expect(model.agentName).toBe('未命名助手')
    expect(model.nodeId).toBe('one')
  })

  it('adds ordinal context for duplicate titles without changing node identity', () => {
    const source = fixtureSource('duplicates', 'Duplicates', [
      {
        ...THREE_STEP_SOURCE.nodes[0]!,
        node_id: 'a',
        task_contract: { objective: '相同目标', scope: [], constraints: [], source_refs: [] },
      },
      {
        ...THREE_STEP_SOURCE.nodes[0]!,
        node_id: 'b',
        task_contract: { objective: '相同目标', scope: [], constraints: [], source_refs: [] },
      },
    ], [], [{ node_id: 'a', output_slot: 'result' }])
    const models = buildStepDisplayModels(source, [])
    expect(models.map(model => model.cardTitle)).toEqual(['步骤 1 · 相同目标', '步骤 2 · 相同目标'])
    expect(models.map(model => model.nodeId)).toEqual(['a', 'b'])
  })

  it('truncates by code point and falls back for an empty objective', () => {
    expect(truncateDisplay('😀'.repeat(5), 5)).toBe('😀😀😀😀…')
    expect(objectiveTitle(' \n\t')).toBe('未命名步骤')
  })
})
