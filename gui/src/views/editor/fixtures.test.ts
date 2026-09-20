import { describe, expect, it } from 'vitest'
import { BROKEN_REFERENCE_SOURCE, BRANCH_MERGE_SOURCE, MULTI_OUTPUT_SOURCE, THREE_STEP_SOURCE, WORKFLOW_EDITOR_FIXTURE } from './fixtures'

describe('workflow editor deterministic fixtures', () => {
  it('includes the ordinary three-step topology and preserves semantic objectives', () => {
    expect(THREE_STEP_SOURCE.nodes.map(node => node.node_id)).toEqual(['research', 'implement', 'review'])
    expect(THREE_STEP_SOURCE.edges).toHaveLength(2)
    expect(THREE_STEP_SOURCE.nodes[1]?.task_contract.objective).toContain('实现')
  })

  it('covers branch/merge, multiple outputs, and invalid references', () => {
    expect(BRANCH_MERGE_SOURCE.edges).toHaveLength(4)
    expect(MULTI_OUTPUT_SOURCE.required_outputs).toHaveLength(2)
    expect(BROKEN_REFERENCE_SOURCE.required_outputs[0]?.node_id).toBe('missing')
    expect(WORKFLOW_EDITOR_FIXTURE.drafts.frozen.draft.status).toBe('frozen')
    expect(WORKFLOW_EDITOR_FIXTURE.drafts.rejected.draft.status).toBe('rejected')
    expect(WORKFLOW_EDITOR_FIXTURE.drafts.missingAgent.draft.status).toBe('invalid')
  })
})

