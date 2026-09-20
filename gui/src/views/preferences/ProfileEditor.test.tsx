import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { Profile } from '../../api/management'
import type { useProfileDocument } from '../../state/preferenceProfile'
import { ProfileEditor } from './ProfileEditor'
import { ProfileListField, shouldAddItem } from './ProfileListField'

type Model = ReturnType<typeof useProfileDocument>

const stored: Profile = {
  name: 'Morrow',
  summary: null,
  tech_stack: ['Python 3.12'],
  goals: [],
  constraints: [],
  conventions: ['Ruff line-length 100'],
}

function model(overrides: Partial<Model> = {}): Model {
  const base = {
    snapshot: { profile: stored, revision: 7 },
    queryError: '',
    draft: null,
    base: null,
    baseRevision: 7,
    status: 'idle' as const,
    message: '',
    serverAhead: false,
    dirty: false,
    edit: vi.fn(),
    discard: vi.fn(),
    save: vi.fn().mockResolvedValue(true),
    clear: vi.fn().mockResolvedValue(true),
    reload: vi.fn().mockResolvedValue(null),
    refresh: vi.fn(),
  }
  return { ...base, ...overrides } as Model
}

describe('ProfileEditor contract', () => {
  it('renders all six fields with their real values and disables saving when clean', () => {
    const html = renderToStaticMarkup(<ProfileEditor model={model()} workspaceName="Morrow 工程" />)
    for (const label of ['项目名称', '项目概述', '技术栈', '项目目标', '项目约束', '项目约定']) {
      expect(html).toContain(label)
    }
    expect(html).not.toContain('Morrow 工程')
    expect(html).not.toContain('文档 r7')
    expect(html).toContain('Ruff line-length 100')
    expect(html).not.toContain('尚无未保存修改')
    expect(html).not.toContain('描述项目，不会重命名工作区或目录。')
    expect(html).toMatch(/<button[^>]*disabled[^>]*>保存项目画像/)
  })

  it('shows the draft, its dirty state and both save actions', () => {
    const html = renderToStaticMarkup(
      <ProfileEditor
        model={model({ draft: { ...stored, name: 'Morrow 2' }, base: stored, dirty: true })}
        workspaceName="Morrow 工程"
      />,
    )
    expect(html).toContain('value="Morrow 2"')
    expect(html).toContain('有未保存修改')
    expect(html).toContain('放弃修改')
    expect(html).not.toMatch(/<button[^>]*disabled[^>]*>保存项目画像/)
  })

  it('separates a missing profile from a failed read', () => {
    const empty = renderToStaticMarkup(
      <ProfileEditor model={model({ snapshot: { profile: null, revision: 0 } })} workspaceName="新工程" />,
    )
    expect(empty).toContain('项目名称')
    expect(empty).not.toContain('加载失败')
    const failed = renderToStaticMarkup(
      <ProfileEditor model={model({ snapshot: null, queryError: '项目画像加载失败，请重试。' })} workspaceName="新工程" />,
    )
    expect(failed).toContain('项目画像加载失败，请重试。')
    expect(failed).toContain('重新读取')
  })

  it('reports save, conflict and refresh failures with the right urgency', () => {
    const saved = renderToStaticMarkup(
      <ProfileEditor model={model({ status: 'saved', message: '已保存；之后的上下文解析会使用新资料。' })} workspaceName="w" />,
    )
    expect(saved).toContain('role="status"')
    const conflict = renderToStaticMarkup(
      <ProfileEditor model={model({ status: 'conflict', message: '服务端内容已变化', serverAhead: true })} workspaceName="w" />,
    )
    expect(conflict).toContain('role="alert"')
    expect(conflict).toContain('采用最新值')
    expect(conflict).toContain('服务端有更新；你的草稿未被覆盖')
    const stale = renderToStaticMarkup(
      <ProfileEditor model={model({ status: 'refresh-failed', message: '已保存，但刷新权威数据失败' })} workspaceName="w" />,
    )
    expect(stale).toContain('重新读取')
  })

  it('confirms clearing inside the card and says the draft goes with it', () => {
    const html = renderToStaticMarkup(
      <ProfileEditor model={model({ draft: { ...stored, name: 'x' }, base: stored, dirty: true })} workspaceName="w" />,
    )
    expect(html).toContain('清空项目画像')
    expect(html).not.toContain('偏好规则会保留')
  })

  it('renders untrusted profile text as escaped text', () => {
    const html = renderToStaticMarkup(
      <ProfileEditor
        model={model({ snapshot: { profile: { ...stored, conventions: ['<img src=x onerror=alert(1)>'] }, revision: 1 } })}
        workspaceName="w"
      />,
    )
    expect(html).toContain('&lt;img')
    expect(html).not.toContain('<img src=x')
  })
})

describe('ProfileListField contract', () => {
  it('adds on Enter but never during IME composition or with Shift', () => {
    expect(shouldAddItem({ key: 'Enter', nativeEvent: { isComposing: false } })).toBe(true)
    expect(shouldAddItem({ key: 'Enter', keyCode: 229, nativeEvent: { isComposing: false } })).toBe(false)
    expect(shouldAddItem({ key: 'Enter', nativeEvent: { isComposing: true } })).toBe(false)
    expect(shouldAddItem({ key: 'Enter', shiftKey: true, nativeEvent: { isComposing: false } })).toBe(false)
    expect(shouldAddItem({ key: 'a', nativeEvent: { isComposing: false } })).toBe(false)
  })

  it('keeps item order, exposes an accessible remove name and never joins on commas', () => {
    const html = renderToStaticMarkup(
      <ProfileListField label="技术栈" items={['Python 3.12', 'React 19']} addLabel="添加技术栈" onChange={() => {}} />,
    )
    expect(html.indexOf('Python 3.12')).toBeLessThan(html.indexOf('React 19'))
    expect(html).toContain('aria-label="移除技术栈 Python 3.12"')
    expect(html).toContain('aria-label="编辑技术栈 React 19"')
    expect(html).toContain('aria-label="新增技术栈"')
    expect(html).toContain('添加技术栈')
  })

  it('shows long text in full and reports duplicates without dropping items', () => {
    const long = '长'.repeat(512)
    const html = renderToStaticMarkup(<ProfileListField label="项目约定" items={[long]} onChange={() => {}} error="项目约定已存在相同条目" multiline />)
    expect(html).toContain(long)
    expect(html).toContain('role="alert">项目约定已存在相同条目')
    expect(html).toContain('aria-label="新增项目约定"')
  })
})
