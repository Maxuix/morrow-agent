// @vitest-environment jsdom
import { act, fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../api/client'
import type { WorkspaceChange, RestoreOperation } from '../../api/changes'
import { ChangesInspector } from './ChangesInspector'

const manifest = {exists: true, sha256: 'a'.repeat(64), byte_size: 4, mode: 420, snapshot_id: 'art_before'}
const row: WorkspaceChange = {change_id: 'chg_one', task_run_id: 'task_one', tool_execution_id: 'tex_one', workspace_sequence: 1, path: 'src/a-very-long-name.ts', operation: 'replace', origin: 'tool', related_change_ids: [], before: manifest, after: {...manifest, sha256: 'b'.repeat(64)}, effect_state: 'applied', coverage: 'complete', omission_reason: null, review_state: 'unreviewed', row_version: 2, restore_support: 'ready', restore_reason: null}
const operation: RestoreOperation = {operation_id: 'rst_one', session_id: 'ses_one', preview_digest: 'c'.repeat(64), state: 'previewed', items: [{change_ids: ['chg_one'], change_id: null, path: row.path, expected: row.after, desired: row.before, capture_name: '.morrow-restore-capture', temporary_name: '.morrow-restore-new', state: 'pending', reason: null}], expires_at: '2099-01-01T00:00:00Z', apply_command_id: null}
const page = {items: [row], next_cursor: null, view_digest: 'd', uncovered_commands: 1, coverage_message: '仅受控文本写入可撤销', legacy_message: null}
function client() {
  return {
    workspaceChanges: vi.fn().mockResolvedValue(page),
    workspaceChangeDiff: vi.fn().mockResolvedValue({change: row, before: row.before, after: row.after, diff: '--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+<script>inert</script>', next_offset: null, complete: true, restore_support: 'ready'}),
    previewRestore: vi.fn().mockResolvedValue(operation),
    applyRestore: vi.fn().mockResolvedValue({...operation, state: 'completed', items: [{...operation.items[0], state: 'applied', change_id: 'chg_restore'}]}),
    reviewChanges: vi.fn().mockResolvedValue({reviewed: ['chg_one']}),
    restoreStatus: vi.fn().mockResolvedValue({...operation, state: 'completed'}),
  }
}
const props = {active: true, workspaceId: 'ws_one', sessionId: 'ses_one', currentTaskRunId: 'task_one'}
afterEach(() => {cleanup(); sessionStorage.clear()})

describe('scoped changes review and undo', () => {
  it('shows coverage and inert historical diff; review does not restore a file', async () => {
    const api = client()
    render(<ChangesInspector {...props} client={api as unknown as ApiClient}/>)
    await screen.findByText(/另有 1 条命令/)
    fireEvent.click(screen.getByRole('button', {name: '查看历史 Diff'}))
    await screen.findByText('+<script>inert</script>')
    expect(document.querySelector('script')).toBeNull()
    fireEvent.click(screen.getByRole('checkbox', {name: /选择 src/}))
    fireEvent.click(screen.getByRole('button', {name: '标记已审阅'}))
    await waitFor(() => expect(api.reviewChanges).toHaveBeenCalledTimes(1))
    expect(api.applyRestore).not.toHaveBeenCalled()
  })

  it('requires a preview and explicit confirmation; duplicate clicks keep one operation', async () => {
    const api = client()
    render(<ChangesInspector {...props} client={api as unknown as ApiClient}/>)
    await screen.findByText(row.path)
    fireEvent.click(screen.getByRole('checkbox', {name: /选择 src/}))
    fireEvent.click(screen.getByRole('button', {name: /预览撤销/}))
    await screen.findByRole('button', {name: '确认按预览恢复'})
    expect(api.applyRestore).not.toHaveBeenCalled()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', {name: '确认按预览恢复'}))
      fireEvent.click(screen.getByRole('button', {name: '确认按预览恢复'}))
    })
    expect(api.applyRestore).toHaveBeenCalledTimes(1)
    expect(api.applyRestore.mock.calls[0][2].preview_digest).toBe(operation.preview_digest)
    expect(screen.getByRole('button', {name: '预览恢复刚才撤销的内容'})).toBeTruthy()
    expect(screen.getByText(/原任务结果、审阅和测试记录仍属于历史版本/)).toBeTruthy()
  })

  it('selects the whole loaded file chain in the default net view', async () => {
    const api = client()
    api.workspaceChanges.mockResolvedValue({...page, items: [row, {...row, change_id: 'chg_two', workspace_sequence: 2, before: row.after, after: {...row.after, sha256: 'e'.repeat(64)}}]})
    render(<ChangesInspector {...props} client={api as unknown as ApiClient}/>)
    await screen.findByText(row.path)
    fireEvent.click(screen.getByRole('checkbox', {name: /选择 src/}))
    fireEvent.click(screen.getByRole('button', {name: /预览撤销/}))
    await waitFor(() => expect(api.previewRestore).toHaveBeenCalledTimes(1))
    expect(api.previewRestore.mock.calls[0][2].change_ids).toEqual(['chg_one', 'chg_two'])
  })

  it('queries the persisted operation after a lost response and remount', async () => {
    const api = client()
    api.applyRestore.mockRejectedValueOnce(new Error('connection lost'))
    const mounted = render(<ChangesInspector {...props} client={api as unknown as ApiClient}/>)
    await screen.findByText(row.path)
    fireEvent.click(screen.getByRole('checkbox', {name: /选择 src/}))
    fireEvent.click(screen.getByRole('button', {name: /预览撤销/}))
    fireEvent.click(await screen.findByRole('button', {name: '确认按预览恢复'}))
    await screen.findByText('connection lost')
    mounted.unmount()
    render(<ChangesInspector {...props} client={api as unknown as ApiClient}/>)
    await waitFor(() => expect(api.restoreStatus).toHaveBeenCalledWith('ws_one', 'ses_one', 'rst_one'))
    expect(api.applyRestore).toHaveBeenCalledTimes(1)
  })
})
