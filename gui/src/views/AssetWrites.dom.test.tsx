// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiClient } from '../api/client'
import { McpManager } from './McpManager'
import { SkillOperations } from './SkillOperations'

afterEach(cleanup)

it('MCP read-back retains the configuration form and original command after a lost reply', async () => {
  const commands: Record<string, unknown>[] = []
  let reads = 0
  const client = new ApiClient({ baseUrl: '', token: '', fetchImpl: async (_input, init) => {
    if (init?.method === 'POST') {
      commands.push(JSON.parse(String(init.body)))
      if (commands.length === 1) throw new TypeError('reply lost after commit')
      return Response.json({ status: 'saved' })
    }
    reads++
    return Response.json({ scope: 'workspace', revision: commands.length ? 1 : 0, digest: 'a'.repeat(64), servers: [], next_cursor: null })
  } })
  render(<McpManager client={client} scope="workspace" refresh={0} />)
  fireEvent.click(await screen.findByRole('button', { name: '添加 MCP 服务' }))
  fireEvent.change(screen.getByLabelText('MCP服务ID'), { target: { value: 'mcp_fixture' } })
  fireEvent.change(screen.getByLabelText('MCP可执行文件'), { target: { value: '/fixture/tool' } })
  const readsBefore = reads
  fireEvent.click(screen.getByRole('button', { name: '保存为停用配置' }))
  await screen.findByText(/操作结果未确认/)
  expect(reads).toBeGreaterThan(readsBefore)
  expect((screen.getByLabelText('MCP可执行文件') as HTMLInputElement).value).toBe('/fixture/tool')
  fireEvent.click(screen.getByRole('button', { name: '保存为停用配置' }))
  await waitFor(() => expect(commands).toHaveLength(2))
  expect(commands[1]).toEqual(commands[0])
  await waitFor(() => expect(screen.queryByLabelText('MCP可执行文件')).toBeNull())
})

it('Skill installation retains its validated input and command when the reply is lost', async () => {
  const commands: Record<string, unknown>[] = []
  const client = new ApiClient({ baseUrl: '', token: '', fetchImpl: async (_input, init) => {
    const body = JSON.parse(String(init?.body))
    if (body.action === 'validate') return Response.json({ valid: true, tree_digest: 'a'.repeat(64), conflicts: [], errors: [], file_count: 1, total_bytes: 10 })
    commands.push(body)
    if (commands.length === 1) throw new TypeError('reply lost after commit')
    return Response.json({ saved: true })
  } })
  const changed = vi.fn()
  render(<SkillOperations client={client} scope="workspace" mutate={async () => false} onChanged={changed} />)
  fireEvent.change(screen.getByLabelText('Skill 本地目录'), { target: { value: '/fixture/skill' } })
  fireEvent.click(screen.getByRole('button', { name: '校验目录与预览' }))
  fireEvent.click(await screen.findByRole('button', { name: '确认安装此版本（默认停用）' }))
  await screen.findByText(/操作结果未确认/)
  expect(changed).toHaveBeenCalledOnce()
  expect((screen.getByLabelText('Skill 本地目录') as HTMLInputElement).value).toBe('/fixture/skill')
  fireEvent.click(screen.getByRole('button', { name: '确认安装此版本（默认停用）' }))
  await waitFor(() => expect(commands).toHaveLength(2))
  expect(commands[1]).toEqual(commands[0])
  await waitFor(() => expect(changed).toHaveBeenCalledTimes(2))
})
