import { expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { resolveDocumentHref } from './inlineLinks'
import { MarkdownBody } from './MarkdownBody'

it('resolves a document-relative link against the viewed file directory', () => {
  const html = renderToStaticMarkup(
    <MarkdownBody
      text={'[b](b.md:12) 与 [c](../c.md)'}
      basePath="docs/guide"
      onOpenFile={() => {}}
    />,
  )
  expect(html).toContain('docs/guide/b.md')
  expect(html).toContain('docs/c.md')
})

it('marks a historical body link as the current workspace version', () => {
  const html = renderToStaticMarkup(
    <MarkdownBody text={'[a](a.md)'} basePath="docs" historical onOpenFile={() => {}} />,
  )
  expect(html).toContain('历史正文')
  expect(html).toContain('docs/a.md')
})

it('keeps chat markdown workspace-relative', () => {
  const html = renderToStaticMarkup(
    <MarkdownBody text={'[a](src/a.py)'} onOpenFile={() => {}} />,
  )
  expect(html).toContain('src/a.py')
  expect(html).not.toContain('历史正文')
})

it('leaves absolute, fragment, scheme and escaping references untouched', () => {
  expect(resolveDocumentHref('docs', '/root.md')).toBe('/root.md')
  expect(resolveDocumentHref('docs', '#part')).toBe('#part')
  expect(resolveDocumentHref('docs', 'https://example.com/x')).toBe('https://example.com/x')
  expect(resolveDocumentHref('docs', '../../escape.md')).toBe('../../escape.md')
  expect(resolveDocumentHref(undefined, 'a.md')).toBe('a.md')
  expect(resolveDocumentHref('docs', './sub/./b.md')).toBe('docs/sub/b.md')
})
