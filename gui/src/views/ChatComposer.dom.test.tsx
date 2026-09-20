// @vitest-environment jsdom
import { useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatCapabilities } from '../api/chat'
import { ChatComposer, type ChatCommand } from './ChatComposer'

const capabilities: ChatCapabilities = {
  interaction_protocol_version: 1,
  workspace_id: 'ws_1',
  features: {},
  limits: {text_chars: 8000},
  execution_ready: true,
}

const commands: ChatCommand[] = [
  {name: '/workflow', label: '生成当前任务计划'},
  {name: '/plan', label: '别名'},
]

function Harness({onCommand}: {onCommand: (command: string, fullText?: string) => void}) {
  const [text, setText] = useState('')
  return <ChatComposer
    text={text}
    onText={setText}
    onSend={() => {}}
    active={false}
    pending={false}
    ready
    capabilities={capabilities}
    commands={commands}
    onCommand={onCommand}
    onStop={() => {}}
  />
}

describe('ChatComposer DOM command menu', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
  })

  it('opens the slash menu on / and dispatches the clicked command', async () => {
    const onCommand = vi.fn()
    const user = userEvent.setup()
    act(() => {
      root.render(<Harness onCommand={onCommand} />)
    })

    await user.type(screen.getByLabelText('消息输入'), '/')

    expect(screen.getByLabelText('命令菜单')).toBeTruthy()
    expect(screen.getByRole('button', {name: '/workflow · 生成当前任务计划'})).toBeTruthy()
    expect(screen.getByRole('button', {name: '/plan · 别名'})).toBeTruthy()

    await user.click(screen.getByRole('button', {name: '/workflow · 生成当前任务计划'}))

    expect(onCommand).toHaveBeenCalledTimes(1)
    expect(onCommand).toHaveBeenCalledWith('/workflow')
    expect(onCommand).not.toHaveBeenCalledWith('/plan')
  })

  it('dispatches the typed command on Enter', async () => {
    const onCommand = vi.fn()
    const user = userEvent.setup()
    act(() => {
      root.render(<Harness onCommand={onCommand} />)
    })

    await user.type(screen.getByLabelText('消息输入'), '/workflow{Enter}')

    expect(onCommand).toHaveBeenCalledTimes(1)
    expect(onCommand).toHaveBeenCalledWith('/workflow', '/workflow')
  })
})
