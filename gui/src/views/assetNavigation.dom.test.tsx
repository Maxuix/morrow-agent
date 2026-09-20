// @vitest-environment jsdom
import { useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatCapabilities } from '../api/chat'
import { ApiClient } from '../api/client'
import type { AppLocation } from '../state/navigation'
import { ChatComposer } from './ChatComposer'
import { applyAssetCommand, assetCommandEntries } from './knowledgeCommands'
import { ContextInspector } from './inspector/ContextInspector'
import { WorkspaceKnowledgePage } from './knowledge/WorkspaceKnowledgePage'
import { WorkspaceToolsPage } from './tools/WorkspaceToolsPage'

const capabilities: ChatCapabilities = {
  interaction_protocol_version: 1,
  workspace_id: 'ws_1',
  features: {},
  limits: { text_chars: 8000 },
  execution_ready: true,
}

function Harness({ onNavigate }: { onNavigate: (location: AppLocation, returnTo?: AppLocation) => void }) {
  const [text, setText] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  return (
    <>
      {notice && <p role="status">{notice}</p>}
      <ChatComposer
        text={text}
        onText={setText}
        onSend={() => {}}
        active={false}
        pending={false}
        ready
        capabilities={capabilities}
        commands={assetCommandEntries}
        onCommand={(name, fullText) => {
          applyAssetCommand(fullText ?? name, {
            navigate: onNavigate,
            openLearningReview: () => setNotice('learning-review'),
            notify: setNotice,
          })
        }}
        onStop={() => {}}
      />
    </>
  )
}

describe('asset command dispatch and context inspector jumps', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })
  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  it('dispatches slash commands through ChatComposer onto navigate', async () => {
    const onNavigate = vi.fn()
    const user = userEvent.setup()
    act(() => { root.render(<Harness onNavigate={onNavigate} />) })
    await user.type(screen.getByLabelText('消息输入'), '/preferences add{Enter}')
    expect(onNavigate).toHaveBeenCalledWith(
      { kind: 'knowledge', section: 'preferences', focus: 'add' },
      { kind: 'chat' },
    )
    onNavigate.mockClear()
    await user.clear(screen.getByLabelText('消息输入'))
    await user.type(screen.getByLabelText('消息输入'), '/skills 报告助手{Enter}')
    expect(onNavigate).toHaveBeenCalledWith(
      { kind: 'tools', section: 'skills', item: '报告助手' },
      { kind: 'chat' },
    )
  })

  it('navigates from the read-only context inspector to asset pages', async () => {
    const onNavigate = vi.fn()
    const user = userEvent.setup()
    const client = new ApiClient({
      baseUrl: '',
      token: '',
      fetchImpl: async () => new Response(JSON.stringify({
        workspace_id: 'ws_1', available_runs: [], status: 'not_started',
        preferences: [], profile: null, knowledge: [], source_revisions: [],
        preference_digest: '', omitted_count: 0, refresh_status: 'ok',
        memory_selection_id: '', pending_learning_count: 0, convention_count: 0,
        language: null, verbosity: null,
      }), { status: 200, headers: { 'content-type': 'application/json' } }),
    })
    await act(async () => {
      root.render(<ContextInspector client={client} workspaceId="ws_1" sessionId="ses_1" active currentTaskRunId={null} onNavigate={onNavigate} />)
    })
    await waitFor(() => expect(screen.getByText('查看项目画像')).toBeDefined())
    await user.click(screen.getByText('查看项目画像'))
    expect(onNavigate).toHaveBeenCalledWith({ kind: 'knowledge', section: 'profile' })
    await user.click(screen.getByText('查看行为偏好'))
    expect(onNavigate).toHaveBeenCalledWith({ kind: 'knowledge', section: 'preferences' })
  })

})

describe('knowledge/tools pages have no cross-page drawer loop', () => {
  let container: HTMLDivElement
  let root: Root
  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })
  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  it('renders knowledge and tools pages without any cross-page drawer path', async () => {
    const pending = new ApiClient({ baseUrl: '', token: '', fetchImpl: () => new Promise<Response>(() => {}) })
    await act(async () => {
      root.render(<WorkspaceKnowledgePage client={pending} workspaceId="ws_1" section="knowledge" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    expect(container.innerHTML).not.toContain('打开上下文与学习管理')
    expect(container.innerHTML).not.toContain('上下文、学习与 Skills')
    expect(container.innerHTML).not.toContain('上下文与学习')
    await act(async () => {
      root.render(<WorkspaceToolsPage client={pending} workspaceId="ws_1" section="skills" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    expect(container.innerHTML).not.toContain('打开上下文与学习管理')
    expect(container.innerHTML).not.toContain('上下文、学习与 Skills')
  })
})
