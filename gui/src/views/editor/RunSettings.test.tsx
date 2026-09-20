// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { WorkflowBudgetWire } from '../../api/types'
import { parseNullableNumber, RunSettings } from './RunSettings'

const budget: WorkflowBudgetWire = {
  max_agent_generation_requests: null,
  default_node_max_agent_generation_requests: null,
  admission_timeout_seconds: null,
  max_concurrency: 1,
}

describe('RunSettings', () => {
  it('keeps null and rejects invalid numeric values without sending NaN or zero', () => {
    expect(parseNullableNumber('', 'positive-integer')).toEqual({ value: null, error: null })
    expect(parseNullableNumber('0', 'positive-integer').error).toBeTruthy()
    expect(parseNullableNumber('1.2', 'positive-integer').error).toBeTruthy()
    expect(parseNullableNumber('0.5', 'positive-number')).toEqual({ value: 0.5, error: null })
    expect(parseNullableNumber('Infinity', 'positive-number').error).toBeTruthy()
  })

  it('renders semantic labels, inheritance hints and local validation', () => {
    const onChange = vi.fn()
    render(<RunSettings budget={budget} disabled={false} onChange={onChange} />)
    expect(screen.getByText('使用默认设置')).toBeTruthy()
    expect(screen.getByLabelText('准入时限（秒）')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('整个流程的模型调用次数上限'), { target: { value: '0' } })
    expect(screen.getByRole('alert').textContent).toContain('大于 0')
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('整个流程的模型调用次数上限'), { target: { value: '3' } })
    expect(onChange).toHaveBeenLastCalledWith({ ...budget, max_agent_generation_requests: 3 })
  })
})
