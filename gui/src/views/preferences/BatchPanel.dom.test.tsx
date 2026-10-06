// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ApiClient } from '../../api/client'
import { usePreferenceDocuments } from '../../state/preferences'
import { PreferenceList } from './PreferenceList'

afterEach(cleanup)

function BatchPage({ client }: { client: ApiClient }) {
  const state = usePreferenceDocuments(client, 'ws_a')
  return <>
    <output aria-label="工作区版本">{state.documents.workspace.revision}</output>
    <PreferenceList client={client} documents={state.documents} rows={state.rows}
      write={state.write} mutate={state.mutate} onRefresh={() => void state.load()} />
  </>
}

describe('batch write outcome', () => {
  it.each([400, 403, 422, 409, 'network', 503] as const)(
    'shows the authoritative %s outcome and retains the batch for retry', async failure => {
      const commands: Record<string, unknown>[] = []
      const client = new ApiClient({ baseUrl: '', token: '', fetchImpl: async (_input, init) => {
        if (init?.method === 'POST') {
          commands.push(JSON.parse(String(init.body)))
          if (commands.length === 1) {
            if (failure === 'network') throw new TypeError('reply lost')
            return Response.json({ error: { code: 'test', message: '参数或权限不允许此操作' } }, { status: failure })
          }
          return Response.json({ result: { value: { status: 'saved' } } })
        }
        return Response.json({ document: { revision: commands.length ? 5 : 4, entries: [] }, history: [] })
      } })
      render(<BatchPage client={client} />)
      // The loaded revision is captured when the user first edits the batch.
      await waitFor(() => expect(screen.getByLabelText('工作区版本').textContent).toBe('4'))
      fireEvent.change(screen.getByLabelText('第 1 项正文'), { target: { value: '保留此批量规则' } })
      fireEvent.click(screen.getByRole('button', { name: '确认提交全部变更' }))
      const alert = await screen.findByRole('alert')
      const uncertain = failure === 'network' || failure === 503
      if (uncertain) expect(alert.textContent).toContain('结果未确认')
      else {
        expect(alert.textContent).not.toContain('结果未确认')
        expect(alert.textContent).toContain(failure === 409 ? '内容已变化' : '参数或权限不允许此操作')
      }
      expect((screen.getByLabelText('第 1 项正文') as HTMLInputElement).value).toBe('保留此批量规则')
      await waitFor(() => expect(screen.getByLabelText('工作区版本').textContent).toBe('5'))
      fireEvent.click(screen.getByRole('button', { name: '确认提交全部变更' }))
      await waitFor(() => expect(commands).toHaveLength(2))
      expect(commands[0].arguments).toEqual({ scope: 'workspace', expected_revision: 4,
        operations: [{ operation: 'add', statement: '保留此批量规则' }] })
      expect(commands[1].arguments).toEqual(commands[0].arguments)
      if (uncertain) expect(commands[1]).toEqual(commands[0])
      else expect(commands[1].command_id).not.toBe(commands[0].command_id)
      await waitFor(() => expect((screen.getByLabelText('第 1 项正文') as HTMLInputElement).value).toBe(''))
      expect(screen.queryByRole('alert')).toBeNull()
    },
  )
})
