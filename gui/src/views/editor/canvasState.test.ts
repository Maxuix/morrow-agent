import { describe, expect, it } from 'vitest'
import {
  CANVAS_UI_CACHE_LIMIT,
  canvasScopeKey,
  clearCanvasUiState,
  readCanvasUiState,
  updateCanvasUiState,
} from './canvasState'

describe('workflow canvas UI cache', () => {
  it('isolates positions and viewport by workspace plus draft without storing Source', () => {
    clearCanvasUiState()
    const one = canvasScopeKey({ workspaceId: 'ws', draftId: 'one' })
    const two = canvasScopeKey({ workspaceId: 'ws', draftId: 'two' })
    updateCanvasUiState(one, () => ({ positions: { node: { x: 10, y: 20 } }, viewport: { x: 1, y: 2, zoom: 1 }, selectedNodeId: 'node' }))
    updateCanvasUiState(two, () => ({ positions: { node: { x: 30, y: 40 } }, viewport: null, selectedNodeId: null }))
    expect(readCanvasUiState(one)).toEqual({ positions: { node: { x: 10, y: 20 } }, viewport: { x: 1, y: 2, zoom: 1 }, selectedNodeId: 'node' })
    expect(readCanvasUiState(two)?.positions.node).toEqual({ x: 30, y: 40 })
  })

  it('keeps a bounded LRU cache and returns defensive copies', () => {
    clearCanvasUiState()
    for (let index = 0; index < CANVAS_UI_CACHE_LIMIT + 2; index += 1) {
      updateCanvasUiState(`scope-${index}`, () => ({ positions: {}, viewport: null, selectedNodeId: null }))
    }
    expect(readCanvasUiState('scope-0')).toBeNull()
    const value = readCanvasUiState('scope-2')!
    value.positions.new = { x: 1, y: 1 }
    expect(readCanvasUiState('scope-2')?.positions).toEqual({})
  })
})
