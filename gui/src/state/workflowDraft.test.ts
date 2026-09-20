import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { WorkflowDraftViewWire } from '../api/types'
import { fixtureDraft, THREE_STEP_SOURCE } from '../views/editor/fixtures'
import { WorkflowDraftController, type WorkflowDraftControllerClient } from './workflowDraft'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function draftView(overrides: Partial<WorkflowDraftViewWire['draft']> = {}): WorkflowDraftViewWire {
  return fixtureDraft(THREE_STEP_SOURCE, overrides)
}

function clientFor(
  update: WorkflowDraftControllerClient['updateWorkflowDraft'],
  get: WorkflowDraftControllerClient['getWorkflowDraft'] = async () => draftView(),
): WorkflowDraftControllerClient {
  return { updateWorkflowDraft: update, getWorkflowDraft: get }
}

async function settlePromises() {
  await Promise.resolve()
  await Promise.resolve()
}

afterEach(() => vi.useRealTimers())

describe('WorkflowDraftController', () => {
  it('starts clean before a draft is selected and after loading a server snapshot', () => {
    const controller = new WorkflowDraftController(clientFor(async () => draftView()))
    expect(controller.dirty).toBe(false)
    controller.load({ workspaceId: 'ws_fixture', draftId: 'wdraft_three_step' }, draftView())
    expect(controller.dirty).toBe(false)
  })

  it('serializes A/B edits and uses the acknowledged row version for B', async () => {
    vi.useFakeTimers()
    const first = deferred<WorkflowDraftViewWire>()
    const second = deferred<WorkflowDraftViewWire>()
    const calls: Array<{ source: WorkflowDraftViewWire['draft']['source']; row: number; command: string }> = []
    const client = clientFor((_draftId, source, row, command) => {
      calls.push({ source, row, command })
      return calls.length === 1 ? first.promise : second.promise
    })
    const controller = new WorkflowDraftController(client, {
      makeCommandId: value => `cmd_${value}_${calls.length + 1}`,
    })
    controller.load({ workspaceId: 'ws_fixture', draftId: 'wdraft_three_step' }, draftView())

    const sourceA = { ...THREE_STEP_SOURCE, name: 'A' }
    const sourceB = { ...THREE_STEP_SOURCE, name: 'B' }
    controller.edit(sourceA)
    vi.advanceTimersByTime(400)
    await settlePromises()
    expect(calls).toHaveLength(1)
    expect(calls[0]).toMatchObject({ row: 1, source: sourceA, command: 'cmd_draft_update_1' })

    controller.edit(sourceB)
    first.resolve(draftView({ source: sourceA, source_hash: 'b'.repeat(64), row_version: 2 }))
    await settlePromises()
    expect(controller.getState().localSource?.name).toBe('B')
    vi.advanceTimersByTime(0)
    await settlePromises()
    expect(calls).toHaveLength(2)
    expect(calls[1]).toMatchObject({ row: 2, source: sourceB, command: 'cmd_draft_update_2' })

    second.resolve(draftView({ source: sourceB, source_hash: 'c'.repeat(64), row_version: 3 }))
    await settlePromises()
    expect(controller.getState().saveState).toBe('saved')
    expect(controller.getState().acknowledgedGeneration).toBe(2)
    expect(controller.getState().localSource?.name).toBe('B')
  })

  it('does not let a late response from an old scope overwrite the current draft', async () => {
    const oldResponse = deferred<WorkflowDraftViewWire>()
    const client = clientFor(() => oldResponse.promise)
    const controller = new WorkflowDraftController(client, { debounceMs: 0, makeCommandId: () => 'cmd_old' })
    const oldSource = { ...THREE_STEP_SOURCE, name: 'Old local' }
    controller.load({ workspaceId: 'ws_fixture', draftId: 'wdraft_old' }, draftView({ source: THREE_STEP_SOURCE }))
    controller.edit(oldSource)
    await settlePromises()
    controller.load({ workspaceId: 'ws_fixture', draftId: 'wdraft_new' }, draftView({ source: { ...THREE_STEP_SOURCE, name: 'New' } }))
    oldResponse.resolve(draftView({ source: oldSource, row_version: 2 }))
    await settlePromises()
    expect(controller.getState().scope).toEqual({ workspaceId: 'ws_fixture', draftId: 'wdraft_new' })
    expect(controller.getState().localSource?.name).toBe('New')
  })

  it('pauses on 409 and does not retry until an explicit reconciliation', async () => {
    vi.useFakeTimers()
    let calls = 0
    const client = clientFor(async () => {
      calls += 1
      throw new ApiError(409, 'conflict', 'Draft row version changed')
    })
    const controller = new WorkflowDraftController(client, { makeCommandId: () => 'cmd_conflict' })
    controller.load({ workspaceId: 'ws_fixture', draftId: 'wdraft_conflict' }, draftView())
    controller.edit({ ...THREE_STEP_SOURCE, name: 'Local edit' })
    vi.advanceTimersByTime(400)
    await settlePromises()
    expect(controller.getState().saveState).toBe('conflict')
    expect(controller.getState().unresolvedCommand?.commandId).toBe('cmd_conflict')
    vi.advanceTimersByTime(10_000)
    expect(calls).toBe(1)
    await expect(controller.saveNow()).resolves.toEqual({ ok: false, reason: 'conflict' })
  })

  it('reconciles an unknown result by matching the durable source and keeps invalid saved diagnostics', async () => {
    vi.useFakeTimers()
    const response = deferred<WorkflowDraftViewWire>()
    const invalid = draftView({
      source: { ...THREE_STEP_SOURCE, name: 'Local edit' },
      source_hash: 'd'.repeat(64),
      status: 'invalid',
      row_version: 2,
      diagnostics: [{ severity: 'error', code: 'graph_cycle', message: 'Remove one edge', node_id: 'review', edge_id: null }],
    })
    const client = clientFor(async () => {
      throw new ApiError(0, 'network_error', 'connection dropped')
    }, async () => {
      response.resolve(invalid)
      return response.promise
    })
    const controller = new WorkflowDraftController(client, { debounceMs: 0, makeCommandId: () => 'cmd_unknown' })
    controller.load({ workspaceId: 'ws_fixture', draftId: 'wdraft_unknown' }, draftView())
    controller.edit(invalid.draft.source)
    vi.runOnlyPendingTimers()
    await settlePromises()
    expect(controller.getState().saveState).toBe('unknown')
    await expect(controller.reconcile()).resolves.toBe('matched')
    expect(controller.getState().saveState).toBe('saved')
    expect(controller.getState().checkState).toBe('blocked')
    expect(controller.getState().diagnostics[0]?.code).toBe('graph_cycle')
  })

  it('never writes frozen or rejected drafts and ignores responses after dispose', async () => {
    const update = vi.fn(async () => draftView())
    const controller = new WorkflowDraftController(clientFor(update))
    controller.load({ workspaceId: 'ws_fixture', draftId: 'wdraft_frozen' }, draftView({ status: 'frozen', frozen_workflow_revision_id: 'wrev_frozen' }))
    expect(controller.edit({ ...THREE_STEP_SOURCE, name: 'ignored' })).toBe(false)
    controller.flush()
    expect(update).not.toHaveBeenCalled()
    controller.load({ workspaceId: 'ws_fixture', draftId: 'wdraft_rejected' }, draftView({ status: 'rejected' }))
    expect(controller.edit({ ...THREE_STEP_SOURCE, name: 'ignored' })).toBe(false)
    controller.dispose()
    expect(controller.getState().localSource?.name).toBe('三步交付流程')
  })
})
