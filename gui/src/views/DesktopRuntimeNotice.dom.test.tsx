// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DesktopRuntimeNotice } from './DesktopRuntimeNotice'

describe('DesktopRuntimeNotice', () => {
  it('keeps unknown effects visible after the native call settles', () => {
    const { rerender, unmount } = render(<DesktopRuntimeNotice status={{scope: 'local_host', state: 'quarantined', native_pending: true, unknown_actions: 1}}/>)
    expect(screen.getByText(/尚未停稳/)).toBeDefined()
    expect(screen.getByRole('alert').textContent).toContain('效果未知')
    rerender(<DesktopRuntimeNotice status={{scope: 'local_host', state: 'idle', native_pending: false, unknown_actions: 1}}/>)
    expect(screen.queryByText(/尚未停稳/)).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain('不要自动重试')
    expect(screen.getByText(/已经投递的效果无法撤回/)).toBeDefined()
    unmount()
  })

  it('does not turn an unavailable status into proof of settled calls', () => {
    const { unmount } = render(<DesktopRuntimeNotice status={{scope: 'local_host', state: 'unknown', native_pending: null, unknown_actions: 0}}/>)
    expect(screen.getByText(/请刷新授权状态/)).toBeDefined()
    expect(screen.queryByRole('alert')).toBeNull()
    unmount()
  })
})
