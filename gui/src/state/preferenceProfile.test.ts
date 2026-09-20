import { describe, expect, it } from 'vitest'
import type { Profile } from '../api/management'
import {
  PROFILE_ITEM_MAX,
  addItem,
  emptyProfile,
  intentKey,
  isDirty,
  normalizeItem,
  profileEquals,
  removeItem,
  toPayload,
  updateItem,
  validateProfile,
} from './preferenceProfile'

const full: Profile = {
  name: 'Morrow',
  summary: '当前工程的简要描述',
  tech_stack: ['Python 3.12', 'React 19'],
  goals: ['统一入口'],
  constraints: ['不得写入凭据'],
  conventions: ['Ruff line-length 100'],
}

describe('profile draft primitives', () => {
  it('starts blank and keeps the six fields independent', () => {
    expect(emptyProfile()).toEqual({
      name: '', summary: null, tech_stack: [], goals: [], constraints: [], conventions: [],
    })
    expect(profileEquals(full, { ...full, tech_stack: [...full.tech_stack] })).toBe(true)
    expect(profileEquals(full, { ...full, tech_stack: ['React 19', 'Python 3.12'] })).toBe(false)
    expect(profileEquals(null, emptyProfile())).toBe(false)
    expect(isDirty(null, full)).toBe(false)
    expect(isDirty(emptyProfile(), null)).toBe(true)
    expect(isDirty({ ...full }, full)).toBe(false)
  })

  it('folds whitespace and case the way the server does', () => {
    expect(normalizeItem('  Python   3.12 ')).toBe('python 3.12')
    expect(normalizeItem('Ruff')).toBe(normalizeItem(' ruff '))
  })

  it('adds items in order and rejects a normalized duplicate', () => {
    const first = addItem([], '先给结论')
    expect(first).toEqual({ items: ['先给结论'], error: '' })
    expect(addItem(first.items, '  先给结论  ').error).toBe('已存在相同条目')
    expect(addItem(['Ruff'], 'ruff').error).toBe('已存在相同条目')
    expect(addItem([], '   ')).toEqual({ items: [], error: '' })
    expect(addItem([], 'x'.repeat(PROFILE_ITEM_MAX + 1)).error).toContain('512')
  })

  it('edits and removes items without reordering the rest', () => {
    const edited = updateItem(['A', 'B', 'C'], 1, 'B2')
    expect(edited).toEqual({ items: ['A', 'B2', 'C'], error: '' })
    expect(updateItem(['A', 'B'], 0, 'B').error).toBe('已存在相同条目')
    expect(updateItem(['A'], 0, '   ').error).toBe('条目不能为空')
    expect(removeItem(['A', 'B', 'C'], 1)).toEqual(['A', 'C'])
  })

  it('locates every invalid field and returns the first one in form order', () => {
    const { errors, first } = validateProfile({
      ...emptyProfile(),
      name: '   ',
      summary: 'x'.repeat(2049),
      goals: ['先给结论', ' 先给结论 '],
    })
    expect(first).toBe('name')
    expect(errors.name).toBe('请填写项目名称')
    expect(errors.summary).toContain('2048')
    expect(errors.goals).toBe('项目目标已存在相同条目')
    expect(validateProfile(full).first).toBeNull()
  })

  it('never truncates long stored values and reports them instead', () => {
    const stored: Profile = { ...emptyProfile(), name: 'x'.repeat(2049) }
    const { errors, first } = validateProfile(stored)
    expect(first).toBe('name')
    expect(errors.name).toContain('2048')
    expect(stored.name.length).toBe(2049)
  })

  it('sends a blank summary as null and preserves list order', () => {
    expect(toPayload({ ...full, summary: '   ' }).summary).toBeNull()
    expect(toPayload({ ...full, summary: '  保留  ' }).summary).toBe('  保留  ')
    expect(toPayload(full).tech_stack).toEqual(['Python 3.12', 'React 19'])
  })

  it('keeps one command id for an identical intent and changes it with content', () => {
    const body = { expected_revision: 3, profile: full }
    expect(intentKey('ws_1', 'profile-save', body)).toBe(intentKey('ws_1', 'profile-save', body))
    expect(intentKey('ws_1', 'profile-save', body)).not.toBe(intentKey('ws_2', 'profile-save', body))
    expect(intentKey('ws_1', 'profile-save', body)).not.toBe(
      intentKey('ws_1', 'profile-save', { ...body, expected_revision: 4 }),
    )
  })
})
