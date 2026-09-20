import { describe, expect, it } from 'vitest'
import { parseFileHref, tokenizeInline } from './inlineLinks'

describe('tokenizeInline', () => {
  it('keeps code spans, emphasis and external links as their own tokens', () => {
    const tokens = tokenizeInline('a `b c` **d** *e* [f](https://example.test/x)')
    expect(tokens).toEqual([
      { kind: 'text', text: 'a ' },
      { kind: 'code', text: 'b c' },
      { kind: 'text', text: ' ' },
      { kind: 'strong', text: 'd' },
      { kind: 'text', text: ' ' },
      { kind: 'em', text: 'e' },
      { kind: 'text', text: ' ' },
      { kind: 'link', label: 'f', href: 'https://example.test/x' },
    ])
  })

  it('reads a link href containing spaces and one level of parentheses', () => {
    const tokens = tokenizeInline('[说明](docs/我的 文件(v2).md)')
    expect(tokens).toEqual([
      { kind: 'link', label: '说明', href: 'docs/我的 文件(v2).md' },
    ])
  })

  it('leaves an unterminated span as plain text', () => {
    expect(tokenizeInline('[a](b')).toEqual([{ kind: 'text', text: '[a](b' }])
    expect(tokenizeInline('`open')).toEqual([{ kind: 'text', text: '`open' }])
  })

  it('never scans past an unbounded candidate', () => {
    const long = `[l](${'x'.repeat(2000)})`
    expect(tokenizeInline(long)).toEqual([{ kind: 'text', text: long }])
  })
})

describe('parseFileHref', () => {
  it('accepts workspace-relative candidates including Chinese and spaces', () => {
    expect(parseFileHref('docs/我的 文件.md')).toEqual({ path: 'docs/我的 文件.md' })
    expect(parseFileHref('src/模块(a).ts')).toEqual({ path: 'src/模块(a).ts' })
  })

  it('decodes exactly once', () => {
    expect(parseFileHref('docs/%E4%B8%AD%E6%96%87.md')).toEqual({ path: 'docs/中文.md' })
    // A doubly-encoded traversal stays encoded: the server performs no second decode.
    expect(parseFileHref('docs/%252e%252e/etc')).toEqual({ path: 'docs/%2e%2e/etc' })
    expect(parseFileHref('docs/%zz')).toBeNull()
  })

  it('keeps the full candidate and offers the line-stripped fallback', () => {
    expect(parseFileHref('src/notes:12')).toEqual({
      path: 'src/notes:12',
      line: 12,
      fallbackPath: 'src/notes',
    })
    expect(parseFileHref('src/notes#L7')).toEqual({
      path: 'src/notes#L7',
      line: 7,
      fallbackPath: 'src/notes',
    })
    expect(parseFileHref(':12')).toEqual({ path: ':12' })
  })

  it('never turns a scheme, anchor or control character into a file link', () => {
    expect(parseFileHref('https://example.test/a')).toBeNull()
    expect(parseFileHref('file:///etc/passwd')).toBeNull()
    expect(parseFileHref('mailto:a@b.test')).toBeNull()
    expect(parseFileHref('#L1')).toBeNull()
    expect(parseFileHref('a\u0000b')).toBeNull()
    expect(parseFileHref('  ')).toBeNull()
  })
})
