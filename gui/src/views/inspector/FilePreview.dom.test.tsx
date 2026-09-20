// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../api/client'
import type { WorkspaceFileInfo } from '../../api/types'
import { BinaryPreview, FileDownload, exceedsPixelBudget, formatBytes } from './FilePreview'

const INFO: WorkspaceFileInfo = {
  path: 'docs/图 片.png',
  kind: 'file',
  byte_size: 40,
  text: false,
  editable: false,
  preview: 'image',
}

describe('FilePreview', () => {
  let container: HTMLDivElement
  let root: Root
  let created: string[]
  let revoked: string[]
  let restore: () => void

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    created = []
    revoked = []
    const originalCreate = URL.createObjectURL
    const originalRevoke = URL.revokeObjectURL
    URL.createObjectURL = vi.fn(() => {
      const url = `blob:mock/${created.length + 1}`
      created.push(url)
      return url
    })
    URL.revokeObjectURL = vi.fn((url: string) => {
      revoked.push(url)
    })
    restore = () => {
      URL.createObjectURL = originalCreate
      URL.revokeObjectURL = originalRevoke
    }
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    restore()
  })

  function clientReturning(status: number, body: BodyInit = 'bytes') {
    return new ApiClient({
      baseUrl: '',
      token: '',
      fetchImpl: async () =>
        new Response(body, { status, headers: { 'content-type': 'image/png' } }),
    })
  }

  const render = async (ui: React.ReactElement) => {
    await act(async () => {
      root.render(ui)
      await Promise.resolve()
      await Promise.resolve()
    })
  }

  it('renders an image from authenticated bytes and releases the object URL', async () => {
    await render(<BinaryPreview client={clientReturning(200)} info={INFO} path={INFO.path} />)
    const image = container.querySelector('[data-file-preview="image"]') as HTMLImageElement
    expect(image).toBeTruthy()
    expect(image.getAttribute('src')).toBe('blob:mock/1')
    expect(container.textContent).toBe('')

    act(() => root.unmount())
    expect(revoked).toEqual(['blob:mock/1'])
  })

  it('renders a PDF in a frame with the same object URL', async () => {
    await render(
      <BinaryPreview
        client={clientReturning(200)}
        info={{ ...INFO, path: 'docs/报告.pdf', preview: 'pdf' }}
        path="docs/报告.pdf"
      />,
    )
    const frame = container.querySelector('[data-file-preview="pdf"]') as HTMLIFrameElement
    expect(frame.getAttribute('src')).toBe('blob:mock/1')
    expect(screen.queryByRole('link', { name: '下载' })).toBeNull()
  })

  it('reports a rejected byte read with a retry', async () => {
    await render(
      <BinaryPreview
        client={clientReturning(400, JSON.stringify({ error: { code: 'invalid', message: '文件超过 20 MiB 预览与下载上限' } }))}
        info={INFO}
        path={INFO.path}
      />,
    )
    expect(screen.getByRole('alert').textContent).toContain('20 MiB')
    expect(screen.getByRole('button', { name: '重试预览' })).toBeTruthy()
    expect(container.querySelector('[data-file-preview="image"]')).toBeNull()
  })

  it('offers a tokenless download link with an attachment disposition', async () => {
    await render(<FileDownload client={clientReturning(200)} path={INFO.path} filename="图 片.png" />)
    const link = screen.getByRole('link', { name: '下载' })
    expect(link.getAttribute('href')).toContain('disposition=attachment')
    expect(link.getAttribute('href')).toContain('path=docs%2F')
  })

  it('stops decoding an image beyond the pixel budget', async () => {
    await render(<BinaryPreview client={clientReturning(200)} info={INFO} path={INFO.path} />)
    const image = container.querySelector('[data-file-preview="image"]') as HTMLImageElement
    Object.defineProperty(image, 'naturalWidth', { value: 20000 })
    Object.defineProperty(image, 'naturalHeight', { value: 20000 })
    await act(async () => {
      fireEvent.load(image)
    })
    expect(container.textContent).toContain('图片过大')
    expect(container.querySelector('[data-file-preview="image"]')).toBeNull()
  })
})

describe('preview budgets', () => {
  it('bounds pixels and formats sizes honestly', () => {
    expect(exceedsPixelBudget(1000, 1000)).toBe(false)
    expect(exceedsPixelBudget(20000, 20000)).toBe(true)
    expect(exceedsPixelBudget(0, 10)).toBe(true)
    expect(formatBytes(40)).toBe('40 B')
    expect(formatBytes(2048)).toBe('2.0 KiB')
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MiB')
  })
})
