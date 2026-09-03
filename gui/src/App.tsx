import { useEffect, useState } from 'react'
import { ApiClient, getToken, hasToken } from './api/client'
import { SyncStore } from './state/sync'
import { AppShell } from './views/AppShell'
import type { Theme } from './views/TopBar'

/**
 * Boot: token bootstrap → ApiClient → SyncStore → `store.start()`. Without a
 * session token there is no app chrome at all — just how to obtain one.
 */
export default function App() {
  const [theme, setTheme] = useState<Theme>('system')

  useEffect(() => {
    if (theme === 'system') {
      delete document.documentElement.dataset.theme
    } else {
      document.documentElement.dataset.theme = theme
    }
  }, [theme])

  if (!hasToken()) {
    return <MissingTokenScreen />
  }
  return <BootedApp theme={theme} onThemeChange={setTheme} />
}

function BootedApp({
  theme,
  onThemeChange,
}: {
  theme: Theme
  onThemeChange: (theme: Theme) => void
}) {
  const [{ client, store }] = useState(() => {
    const token = getToken() as string
    const apiClient = new ApiClient({ baseUrl: '', token })
    return { client: apiClient, store: new SyncStore({ client: apiClient, token }) }
  })
  const [workspaceId, setWorkspaceId] = useState<string | null>(null)

  useEffect(() => {
    void store.start()
    return () => store.stop()
  }, [store])

  useEffect(() => {
    let cancelled = false
    client
      .meta()
      .then((meta) => {
        if (!cancelled) setWorkspaceId(meta.workspace_id)
      })
      .catch(() => {
        // The workspace id is decorative; the sync store owns connection truth.
      })
    return () => {
      cancelled = true
    }
  }, [client])

  return (
    <AppShell
      client={client}
      store={store}
      workspaceId={workspaceId}
      theme={theme}
      onThemeChange={onThemeChange}
    />
  )
}

function MissingTokenScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <main className="max-w-md rounded-[10px] border border-subtle bg-raised p-8">
        <h1 className="font-serif text-2xl font-semibold">Morrow</h1>
        <p className="mt-3 text-sm leading-relaxed text-secondary">
          此界面需要一次性会话令牌才能连接到本地 Morrow Core。请在终端中运行：
        </p>
        <p className="mt-3 rounded-[8px] border border-subtle bg-base px-3 py-2 font-mono text-sm text-accent">
          $ morrow gui
        </p>
        <p className="mt-3 text-sm leading-relaxed text-secondary">
          该命令会启动本地服务并自动打开带有令牌的浏览器页面。
        </p>
      </main>
    </div>
  )
}
