import { describe, expect, it } from 'vitest'
import { vendorChunk } from './vendorChunk'

describe('vendorChunk', () => {
  it('keeps application modules in the default graph', () => {
    expect(vendorChunk('/Users/me/gui/src/views/AppShell.tsx')).toBeUndefined()
  })

  it('isolates React from xyflow so neither owns the app chunk', () => {
    expect(vendorChunk('/pnpm/react@19.0.0/node_modules/react/index.js')).toBe('vendor-react')
    expect(
      vendorChunk('/pnpm/react-dom@19.0.0/node_modules/react-dom/client.js'),
    ).toBe('vendor-react')
    expect(
      vendorChunk('/pnpm/scheduler@0.26.0/node_modules/scheduler/index.js'),
    ).toBe('vendor-react')
    expect(
      vendorChunk('/pnpm/@xyflow+react@12.0.0/node_modules/@xyflow/react/dist/esm/index.js'),
    ).toBe('vendor-xyflow')
  })
})
