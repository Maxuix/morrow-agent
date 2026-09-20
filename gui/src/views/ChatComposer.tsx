import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { ChatCapabilities } from '../api/chat'
export function shouldSend(event: {key: string; shiftKey: boolean; isComposing: boolean; keyCode?: number}, composing: boolean) {
  return event.key === 'Enter' && !event.shiftKey && !event.isComposing && !composing && event.keyCode !== 229
}
export interface ChatCommand {name: string; label: string; disabled?: boolean}

/**
 * Command dispatch: the command must be the first token of the message.
 * `/workflow <目标>` dispatches with the full text; quoted text, code blocks
 * and paths never start the message, so they are sent as ordinary chat.
 */
export function resolveCommand(text: string, commands: ChatCommand[]): {command: ChatCommand; fullText: string} | null {
  if (!text.startsWith('/')) return null
  const trimmed = text.trim()
  const exact = commands.find(c => c.name === trimmed)
  if (exact && !exact.disabled) return {command: exact, fullText: trimmed}
  const first = trimmed.split(/\s+/, 1)[0]
  const prefixed = commands.find(c => c.name === first)
  if (prefixed && !prefixed.disabled) return {command: prefixed, fullText: trimmed}
  return null
}

/**
 * The completion menu opens manually or whenever the text starts with '/',
 * and a click outside the menu keeps it closed until the text changes again,
 * so it never lingers under other popovers.
 */
export function commandMenuVisible(manualOpen: boolean, dismissed: boolean, text: string): boolean {
  return !dismissed && (manualOpen || text.startsWith('/'))
}

/**
 * “引用工作区文件” inserts the @ trigger the attachment search listens for.
 * A trailing @-token already counts as referencing; otherwise a word
 * boundary is added so the search regex `(?:^|\s)@…$` actually matches.
 */
export function insertFileReference(text: string): string {
  if (/(?:^|\s)@[^\s]*$/.test(text)) return text
  return text && !/\s$/.test(text) ? `${text} @` : `${text}@`
}

/** The character budget surfaces only near the limit, never as a permanent counter. */
export function charHintRemaining(limit: number, length: number): number | null {
  if (!(limit > 0)) return null
  const remaining = limit - length
  return remaining <= Math.max(200, Math.floor(limit * 0.1)) ? Math.max(remaining, 0) : null
}

const PlusIcon = () => <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>
const SendIcon = () => <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
const StopIcon = () => <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true"><rect x="2.5" y="2.5" width="7" height="7" rx="1.5" fill="currentColor"/></svg>
const PauseIcon = () => <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true"><rect x="3" y="2.5" width="2.4" height="7" rx="1" fill="currentColor"/><rect x="6.6" y="2.5" width="2.4" height="7" rx="1" fill="currentColor"/></svg>
const ResumeIcon = () => <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true"><path d="M3.5 2.5v7M3.5 6l5-3.5v7z" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" fill="none"/></svg>

/** Derived primary run control handed to the composer (P09.1, D01). */
export interface ComposerPrimaryControl {
  kind: 'pause' | 'resume'
  label: string
  /** Intent accepted but not settled; the button waits, disabled. */
  settling: boolean
  /** Non-null disables the control and explains why (e.g. pending wiring). */
  disabledReason: string | null
  onTrigger: () => void
}

/**
 * One input container: attachment previews above the text, the textarea, and
 * a single toolbar row — ＋ menu, permission, on-demand mode tags on the left;
 * character hint, model entry, stop and the round send button on the right.
 * Execution-mode choice, attachments, commands and settings all live inside
 * this container or its popovers; permanent explanation rows are gone.
 */
export function ChatComposer({text, onText, onSend, active, running, stopping, pending, ready, capabilities, commands, onCommand, onStop, primaryControl, hasAttachments=false, onFiles, canAttach=false, onPickFiles, plusItems, tags, permissionControl, modelControl, accessories, placeholder='描述任务，或输入 / 查看命令'}: {
  /** Rendered above the text: attachment tray, @ search and attachment errors. */
  accessories?: ReactNode
  /** Toolbar-left quiet controls owned by the settings hook. */
  permissionControl?: ReactNode
  /** Toolbar-right model · effort entry owned by the settings hook. */
  modelControl?: ReactNode
  /** On-demand chips: workflow mode tags and per-run Host authorization. */
  tags?: ReactNode
  placeholder?: string
  /** Workspace-level ＋ menu entries between “引用工作区文件” and “查看命令”. */
  plusItems?: ReactNode
  /** Attachments feature is advertised; upload/reference entries appear. */
  canAttach?: boolean; onPickFiles?: () => void
  hasAttachments?: boolean; onFiles?: (files: File[]) => void
  text: string; onText: (text: string) => void; onSend: (intent: 'send'|'steer'|'follow_up') => void
  /** A chat AgentRun is active: supplementary steer/follow-up input is available. */
  active: boolean
  /** Any execution owns the Session: the routed stop control is available. */
  running?: boolean
  /** Stop intent recorded but not yet settled; the button waits, not claims. */
  stopping?: boolean
  pending: boolean; ready: boolean; capabilities: ChatCapabilities
  commands: ChatCommand[]; onCommand: (command: string, fullText?: string) => void; onStop: () => void
  /** Primary pause/continue control derived from the execution snapshot. */
  primaryControl?: ComposerPrimaryControl
}) {
  const composing = useRef(false); const [intent, setIntent] = useState<'steer'|'follow_up'>('follow_up')
  const [menu, setMenu] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [plusOpen, setPlusOpen] = useState(false)
  const [dragging, setDragging] = useState(false)
  const dragDepth = useRef(0)
  const visible = commandMenuVisible(menu, dismissed, text)
  const menuRef = useRef<HTMLDivElement | null>(null); const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const plusRef = useRef<HTMLDivElement | null>(null)
  // Presses outside a popover collapse it; presses inside keep their handlers.
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target
      if (!(target instanceof Node)) return
      if (visible && !(menuRef.current?.contains(target))) setMenu(false)
      if (plusOpen && !(plusRef.current?.contains(target))) setPlusOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [visible, plusOpen])
  const focusInput = () => {
    const el = inputRef.current
    if (!el) return
    el.focus(); const end = el.value.length; el.setSelectionRange(end, end)
  }
  const closePlusAndRefocus = () => { setPlusOpen(false); requestAnimationFrame(focusInput) }
  const isRunning = running ?? false; const isStopping = stopping ?? false
  const matching = commands.filter(c => !text.startsWith('/') || c.name.startsWith(text.trim().split(' ')[0]))
  const limit = capabilities.limits.text_chars
  const remaining = charHintRemaining(limit, text.length)
  const send = () => {
    if (pending || !ready || (!text.trim()&&!hasAttachments)) return
    const resolved = resolveCommand(text, commands)
    if (resolved) { onCommand(resolved.command.name, resolved.fullText); return }
    if (text.startsWith('/')) { setMenu(true); setDismissed(false); return }
    onSend(active ? intent : 'send')
  }
  const dragAccepted = !!onFiles && !pending
  const sendDisabled = pending || !ready || (!text.trim()&&!hasAttachments)
  const sendLabel = pending ? '发送中…' : active ? '提交补充' : '发送'
  return <div className="chat-composer"
    onDragEnter={e=>{if(!dragAccepted)return;e.preventDefault();dragDepth.current++;setDragging(true)}}
    onDragOver={e=>{if(dragAccepted)e.preventDefault()}}
    onDragLeave={()=>{if(!dragAccepted)return;dragDepth.current=Math.max(0,dragDepth.current-1);if(!dragDepth.current)setDragging(false)}}
    onDrop={e=>{if(!dragAccepted)return;e.preventDefault();dragDepth.current=0;setDragging(false);onFiles!(Array.from(e.dataTransfer.files))}}>
    {visible && <div ref={menuRef} className="chat-command-menu" aria-label="命令菜单">
      {matching.length ? matching.map(command => <button className="editor-button" key={command.name} disabled={command.disabled} onClick={() => {setMenu(false); focusInput(); onCommand(command.name)}}>{command.name} · {command.label}</button>) : <p className="text-xs">无匹配命令</p>}
    </div>}
    {accessories}
    <textarea ref={inputRef} aria-label="消息输入" placeholder={placeholder} rows={1} maxLength={capabilities.limits.text_chars}
      onPaste={event=>{const files=Array.from(event.clipboardData.items).filter(item=>item.kind==='file').flatMap(item=>{const file=item.getAsFile();return file?[file]:[]});if(files.length&&onFiles&&!pending){event.preventDefault();onFiles(files)}}}
      value={text} onChange={event => {setDismissed(false); onText(event.target.value)}}
      onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
      onKeyDown={event => { if (shouldSend({...event, isComposing: event.nativeEvent.isComposing, keyCode: event.nativeEvent.keyCode}, composing.current)) { event.preventDefault(); send() } if (event.key === 'Escape') {setMenu(false); setDismissed(true); setPlusOpen(false)} }}/>
    {dragging && <div className="composer-drop-hint" aria-hidden="true">松开以添加附件</div>}
    <div className="composer-toolbar">
      <div className="composer-plus" ref={plusRef}>
        <button className="composer-plus-toggle" aria-label="添加内容" aria-expanded={plusOpen} title="附件、文件引用、执行方式与命令" onClick={() => setPlusOpen(v => !v)}><PlusIcon/></button>
        {plusOpen && <div className="composer-plus-menu" role="menu" aria-label="输入工具"
          onClickCapture={event => {
            // Deferred so each item's own onClick fires before the menu unmounts;
            // closing synchronously in the capture phase would detach the target.
            if ((event.target as HTMLElement).closest('button')) setTimeout(closePlusAndRefocus, 0)
          }}>
          {canAttach && onPickFiles && <button role="menuitem" className="composer-menu-item" disabled={pending} onClick={onPickFiles}><span>上传附件</span><span className="menu-note">拖拽 / 粘贴</span></button>}
          {canAttach && <button role="menuitem" className="composer-menu-item" disabled={pending} onClick={() => onText(insertFileReference(text))}><span>引用工作区文件</span><span className="menu-note">@</span></button>}
          {plusItems}
          <button role="menuitem" className="composer-menu-item" onClick={() => {setDismissed(false); setMenu(true)}}><span>查看命令</span><span className="menu-note">/</span></button>
        </div>}
      </div>
      {permissionControl}
      {active && <select aria-label="运行中输入方式" className="composer-intent editor-button" value={intent} onChange={e => setIntent(e.target.value as typeof intent)}><option value="follow_up">完成后执行</option><option value="steer">运行中补充 / 纠正</option></select>}
      {tags}
      <span className="composer-toolbar-end">
        {remaining !== null && <span className={`composer-char-hint${text.length >= limit ? ' is-limit' : ''}`} role="status">{text.length >= limit ? '已达字数上限' : `剩余 ${remaining} 字`}</span>}
        {modelControl}
        <span className="composer-submit">
          {primaryControl && (() => {
            const disabled = primaryControl.settling || primaryControl.disabledReason !== null
            const reason = primaryControl.settling ? primaryControl.label : primaryControl.disabledReason
            const hint = primaryControl.kind === 'pause'
              ? '暂停'
              : '继续'
            const label = reason ?? hint
            return <button type="button" className="composer-pause editor-button" disabled={disabled}
              onClick={primaryControl.onTrigger}
              aria-label={primaryControl.label} title={label}>
              {primaryControl.kind === 'pause' ? <PauseIcon/> : <ResumeIcon/>}
              <span className="composer-pause-label">{primaryControl.label}</span>
            </button>
          })()}
          {isRunning && <button className="composer-stop editor-button" disabled={isStopping} onClick={onStop} aria-label={isStopping ? '正在停止…' : '停止运行'} title={isStopping ? '正在停止…' : '停止运行'}><StopIcon/></button>}
          <button className="composer-send editor-button" disabled={sendDisabled} onClick={send} aria-label={sendLabel} title={`${sendLabel} · Enter 发送，Shift+Enter 换行`}><SendIcon/></button>
        </span>
      </span>
    </div>
  </div>
}
