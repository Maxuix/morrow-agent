import type { Viewport } from '@xyflow/react'

export interface CanvasPosition {
  x: number
  y: number
}

export interface CanvasUiState {
  positions: Record<string, CanvasPosition>
  viewport: Viewport | null
  selectedNodeId: string | null
}

const MAX_CACHED_CANVASES = 40
const cache = new Map<string, CanvasUiState>()

export function canvasScopeKey(scope: { workspaceId: string; draftId: string } | undefined): string {
  return scope === undefined ? 'workflow-editor:unscoped' : `${scope.workspaceId}:${scope.draftId}`
}

function touch(key: string, value: CanvasUiState): CanvasUiState {
  cache.delete(key)
  cache.set(key, value)
  while (cache.size > MAX_CACHED_CANVASES) cache.delete(cache.keys().next().value!)
  return value
}

function clone(value: CanvasUiState): CanvasUiState {
  return {
    positions: { ...value.positions },
    viewport: value.viewport === null ? null : { ...value.viewport },
    selectedNodeId: value.selectedNodeId,
  }
}

export function readCanvasUiState(key: string): CanvasUiState | null {
  const value = cache.get(key)
  if (value === undefined) return null
  touch(key, value)
  return clone(value)
}

export function updateCanvasUiState(
  key: string,
  update: (current: CanvasUiState | null) => CanvasUiState,
): CanvasUiState {
  const value = update(readCanvasUiState(key))
  return touch(key, clone(value))
}

export function clearCanvasUiState(): void {
  cache.clear()
}

export const CANVAS_UI_CACHE_LIMIT = MAX_CACHED_CANVASES
