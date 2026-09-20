import { Component, type ErrorInfo, type ReactNode } from 'react'

interface BoundaryState { error: Error | null }

/**
 * Top-level crash guard for render errors. Without it an uncaught render
 * error unmounts the whole tree and leaves only the dark page background —
 * the "silent black screen". The fallback names the cause and keeps a way
 * back; server-side data is untouched by a client render crash.
 */
export class AppErrorBoundary extends Component<{ children: ReactNode }, BoundaryState> {
  override state: BoundaryState = { error: null }

  static getDerivedStateFromError(error: unknown): BoundaryState {
    return { error: error instanceof Error ? error : new Error(String(error)) }
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack)
  }

  override render() {
    const error = this.state.error
    if (error === null) return this.props.children
    return <div className="p-6" role="alert">
      <h2 className="text-lg font-medium">界面渲染出错</h2>
      <p className="mt-2 text-sm text-secondary">请重新加载此区域。</p>
      <pre className="mt-3 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded border border-subtle p-2 text-xs">{error.message}</pre>
      <div className="mt-4 flex gap-2">
        <button className="editor-button" onClick={() => this.setState({ error: null })}>重试渲染</button>
        <button className="editor-button" onClick={() => window.location.reload()}>重新加载页面</button>
      </div>
    </div>
  }
}
