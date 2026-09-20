import { expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import { AppErrorBoundary } from './ErrorBoundary'

it('maps thrown values into a stable error state', () => {
  const fromError = AppErrorBoundary.getDerivedStateFromError(new TypeError('boom'))
  expect(fromError.error).toBeInstanceOf(TypeError)
  expect(fromError.error?.message).toBe('boom')
  // Non-Error throws still render a readable message instead of crashing again.
  const fromString = AppErrorBoundary.getDerivedStateFromError('plain failure')
  expect(fromString.error).toBeInstanceOf(Error)
  expect(fromString.error?.message).toBe('plain failure')
})

it('reports catches to console so the terminal keeps the evidence', () => {
  const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
  const boundary = new AppErrorBoundary({ children: null })
  boundary.componentDidCatch(new Error('boom'), { componentStack: '\n    at SessionChat' } as never)
  expect(spy).toHaveBeenCalledOnce()
  spy.mockRestore()
})

it('renders children untouched when healthy', () => {
  const boundary = new AppErrorBoundary({ children: createElement('p', null, 'ok') })
  boundary.state = { error: null }
  expect(renderToStaticMarkup(boundary.render())).toContain('ok')
})

it('falls back to a visible card with the cause and recovery actions', () => {
  const boundary = new AppErrorBoundary({ children: createElement('p', null, 'ok') })
  boundary.state = { error: new TypeError("Cannot read properties of null (reading 'availability')") }
  const html = renderToStaticMarkup(boundary.render())
  expect(html).toContain('role="alert"')
  expect(html).toContain('界面渲染出错')
  expect(html).toContain('availability')
  expect(html).toContain('重试渲染')
  expect(html).toContain('重新加载页面')
  expect(html).not.toContain('ok')
})
