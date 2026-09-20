// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowDiagnostics } from './WorkflowDiagnostics'

describe('WorkflowDiagnostics', () => {
  it('renders actionable targets and escapes untrusted unknown messages', async () => {
    render(<WorkflowDiagnostics diagnostics={[{
      severity: 'error',
      code: 'future_code',
      message: '<script>steal()</script>',
      node_id: null,
      edge_id: 'a->b',
    }]} onSelectEdge={vi.fn()} />)
    expect(screen.getByRole('button', { name: '查看连接' })).toBeTruthy()
    expect(screen.getByText('<script>steal()</script>')).toBeTruthy()
    expect(document.querySelector('script')).toBeNull()
  })
})
