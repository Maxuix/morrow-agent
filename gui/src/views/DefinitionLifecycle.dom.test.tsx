// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../api/client'
import { DefinitionLifecycle } from './DefinitionLifecycle'

afterEach(cleanup)

function clientWith(action: (kind: string, id: string, body: unknown) => Promise<Record<string, unknown>>) {
  return { definitionAction: action } as unknown as ApiClient
}

describe('DefinitionLifecycle semantic DOM', () => {
  it('keeps validation useful while removing secrets and traceback text', async () => {
    const action = vi.fn(async () => ({
      diagnostics: ['token=sk-live Traceback (most recent call last):\nsecret details'],
    }))
    render(<DefinitionLifecycle client={clientWith(action)} kind="workflow" id="workflow_1" revision={2} enabled version="wrev_2" onRefresh={vi.fn(async () => {})} />)

    fireEvent.click(screen.getByRole('button', { name: '校验定义' }))
    await screen.findByText('校验完成，请处理下方问题。')
    expect(screen.getByText(/token=已隐藏/)).toBeTruthy()
    expect(screen.queryByText(/secret details|Traceback/)).toBeNull()
  })

  it('keeps revoke as an explicit version action with a required reason', async () => {
    const action = vi.fn(async () => ({}))
    const refresh = vi.fn(async () => {})
    render(<DefinitionLifecycle client={clientWith(action)} kind="workflow" id="workflow_1" revision={3} enabled version="wrev_3" onRefresh={refresh} />)

    fireEvent.click(screen.getByRole('button', { name: '撤销版本' }))
    const confirm = screen.getByRole('button', { name: '确认撤销版本' }) as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('撤销原因'), { target: { value: '不再使用该版本' } })
    expect(confirm.disabled).toBe(false)
    fireEvent.click(confirm)
    await screen.findByText('操作已完成。')
    expect(action).toHaveBeenCalledWith('workflow', 'workflow_1', expect.objectContaining({
      action: 'revoke',
      expected_head_revision: 3,
      version_id: 'wrev_3',
      reason: '不再使用该版本',
    }))
    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('keeps built-in lifecycle mutations disabled while retaining safe validation', async () => {
    const action = vi.fn(async () => ({ diagnostics: [] }))
    render(<DefinitionLifecycle client={clientWith(action)} kind="workflow" id="builtin_workflow" revision={4} enabled version="wrev_builtin" readOnly onRefresh={vi.fn(async () => {})} />)

    expect(screen.getByText('只读')).toBeTruthy()
    expect((screen.getByRole('button', { name: '发布定义' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '停用定义' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '撤销版本' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '校验定义' }))
    await screen.findByText('校验通过。')
    expect(action).toHaveBeenCalledTimes(1)
  })
})
