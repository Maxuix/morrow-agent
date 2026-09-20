import { describe, expect, it } from 'vitest'
import { candidateSourceLabel, isCandidateSourceKnown } from './source'

describe('candidate source projection (package 5 filter seam)', () => {
  it('treats missing origin as unknown and keeps session/task fields for later filtering', () => {
    expect(isCandidateSourceKnown({})).toBe(false)
    expect(candidateSourceLabel({})).toBe('来源未知')
    expect(isCandidateSourceKnown({ session_id: 'se_1' })).toBe(true)
    expect(candidateSourceLabel({ session_id: 'se_1', source_kind: 'chat' })).toContain('来源已知')
    expect(isCandidateSourceKnown({ evidence: [{ source_id: 'lev_1', source_kind: 'task' }] })).toBe(true)
    expect(isCandidateSourceKnown({ evidence: [{ source_kind: 'task' }] })).toBe(false)
  })
})
