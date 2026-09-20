import type { AppLocation, KnowledgeFocus, KnowledgeSection } from '../state/navigation'

/**
 * Slash-command catalog and resolver for the two asset centres.
 *
 * Navigation is the only destination for management commands. Session-scoped
 * learning review (`/learn` and `/learn accept|edit|reject|show`) opens the
 * current Chat session's Learning Review Inspector; the left Knowledge page
 * remains the explicit all-history review surface.
 * Unknown subcommands return an explanation instead of guessing a target.
 */

export type AssetCommandResult =
  | { kind: 'navigate'; location: AppLocation; notice?: string }
  | { kind: 'learning-review' }
  | { kind: 'unknown'; message: string }

export interface AssetCommandEntry {
  name: string
  label: string
}

const PREFERENCE_FOCUS: Record<string, KnowledgeFocus> = {
  list: 'list',
  show: 'show',
  add: 'add',
  replace: 'replace',
  remove: 'remove',
  enable: 'enable',
  disable: 'disable',
}

const PROFILE_FOCUS: Record<string, KnowledgeFocus> = {
  edit: 'edit',
  reset: 'reset',
}

const MEMORY_ACTIONS = new Set(['list', 'show', 'enable', 'disable', 'dispute', 'delete'])
const LEARN_MANAGE = new Set(['status', 'mode', 'set-mode', 'inbox', 'list', 'reviews', 'review'])
const LEARN_PROMOTIONS = new Set(['promotions', 'undo', 'retry'])
const LEARN_REVIEW = new Set(['accept', 'edit', 'reject', 'show'])
const PROMOTION_ACTIONS = new Set(['retry', 'finalize', 'cancel', 'abort'])

function tokensAfter(text: string, root: string): string[] {
  const rest = text.trim().slice(root.length).trim()
  return rest ? rest.split(/\s+/) : []
}

function unknown(command: string, hint: string): AssetCommandResult {
  return { kind: 'unknown', message: `无法解析 ${command}。${hint}` }
}

function knowledge(section: KnowledgeSection, focus?: KnowledgeFocus): AppLocation {
  return focus ? { kind: 'knowledge', section, focus } : { kind: 'knowledge', section }
}

/** Resolve a typed slash command (full text) into a navigation target or an explanation. */
export function resolveAssetCommand(text: string): AssetCommandResult | null {
  const trimmed = text.trim()
  if (!trimmed.startsWith('/')) return null
  const root = trimmed.split(/\s+/, 1)[0]

  if (root === '/preferences') {
    const tokens = tokensAfter(trimmed, root)
    if (tokens.length === 0) return { kind: 'navigate', location: knowledge('preferences') }
    const focus = PREFERENCE_FOCUS[tokens[0]]
    if (focus && tokens.length === 1) return { kind: 'navigate', location: knowledge('preferences', focus) }
    return unknown(trimmed, '可用：/preferences [list|show|add|replace|remove|enable|disable]。')
  }

  if (root === '/workspace') {
    const tokens = tokensAfter(trimmed, root)
    if (tokens.length === 0) return { kind: 'navigate', location: knowledge('profile') }
    const focus = PROFILE_FOCUS[tokens[0]]
    if (focus && tokens.length === 1) return { kind: 'navigate', location: knowledge('profile', focus) }
    return unknown(trimmed, `可用：${root} [edit|reset]。`)
  }

  if (root === '/memory') {
    const tokens = tokensAfter(trimmed, root)
    if (tokens.length === 0 || (tokens.length === 1 && MEMORY_ACTIONS.has(tokens[0]))) {
      return { kind: 'navigate', location: knowledge('knowledge', 'records') }
    }
    if (tokens[0] === 'selection' && (tokens.length === 1 || (tokens.length === 2 && (tokens[1] === 'list' || tokens[1] === 'show')))) {
      return {
        kind: 'navigate',
        location: knowledge('knowledge', 'selection'),
      }
    }
    return unknown(trimmed, '可用：/memory [list|show|enable|disable|dispute|delete] 或 /memory selection [list|show]。')
  }

  if (root === '/learn') {
    const tokens = tokensAfter(trimmed, root)
    if (tokens.length === 0) {
      return { kind: 'learning-review' }
    }
    if (tokens[0] === 'promotions') {
      if (tokens.length === 1 || (tokens.length === 2 && PROMOTION_ACTIONS.has(tokens[1]))) {
        return { kind: 'navigate', location: knowledge('knowledge', 'promotions') }
      }
      return unknown(trimmed, '可用：/learn promotions [retry|finalize|cancel|abort]。')
    }
    if (tokens.length === 1 && LEARN_MANAGE.has(tokens[0])) {
      return { kind: 'navigate', location: knowledge('knowledge', 'learning') }
    }
    if (tokens.length === 1 && LEARN_PROMOTIONS.has(tokens[0])) {
      return { kind: 'navigate', location: knowledge('knowledge', 'promotions') }
    }
    if (tokens.length === 1 && LEARN_REVIEW.has(tokens[0])) {
      return { kind: 'learning-review' }
    }
    return unknown(
      trimmed,
      '管理类：/learn mode|set-mode|status|inbox|list|reviews|review|promotions|undo|retry；本轮审阅：/learn accept|edit|reject|show。',
    )
  }

  if (root === '/skills') {
    const rest = trimmed.slice('/skills'.length).trim()
    return {
      kind: 'navigate',
      location: rest
        ? { kind: 'tools', section: 'skills', item: rest.slice(0, 80) }
        : { kind: 'tools', section: 'skills' },
    }
  }

  if (root === '/mcp') {
    const rest = trimmed.slice('/mcp'.length).trim()
    return {
      kind: 'navigate',
      location: rest
        ? { kind: 'tools', section: 'mcp', item: rest.slice(0, 80) }
        : { kind: 'tools', section: 'mcp' },
    }
  }

  return null
}

export function applyAssetCommand(
  text: string,
  handlers: {
    navigate: (location: AppLocation, returnTo?: AppLocation) => void
    openLearningReview: () => void
    notify: (message: string) => void
  },
): boolean {
  const result = resolveAssetCommand(text)
  if (!result) return false
  if (result.kind === 'navigate') {
    if (result.notice) handlers.notify(result.notice)
    handlers.navigate(result.location, { kind: 'chat' })
    return true
  }
  if (result.kind === 'learning-review') {
    handlers.openLearningReview()
    return true
  }
  handlers.notify(result.message)
  return true
}

/** Composer menu entries. Labels describe the new left-page destinations. */
export const knowledgeCommands: Record<string, { label: string }> = {}
const catalog: AssetCommandEntry[] = [
  { name: '/preferences', label: '打开行为偏好' },
  { name: '/workspace', label: '打开项目画像' },
  { name: '/learn', label: '本轮学习审阅' },
  { name: '/memory', label: '打开知识库' },
  { name: '/skills', label: '打开 Skills' },
  { name: '/mcp', label: '打开 MCP 服务' },
]
for (const sub of ['list', 'show', 'add', 'replace', 'remove', 'enable', 'disable']) {
  catalog.push({ name: `/preferences ${sub}`, label: '打开行为偏好' })
}
for (const sub of ['edit', 'reset']) {
  catalog.push({ name: `/workspace ${sub}`, label: '打开项目画像' })
}
for (const sub of ['status', 'mode', 'set-mode', 'inbox', 'list', 'reviews', 'review']) {
  catalog.push({ name: `/learn ${sub}`, label: '知识库 · 学习模式与全部候选' })
}
for (const sub of ['promotions', 'undo', 'retry']) {
  catalog.push({ name: `/learn ${sub}`, label: '知识库 · 推广历史与撤销' })
}
for (const sub of ['retry', 'finalize', 'cancel', 'abort']) {
  catalog.push({ name: `/learn promotions ${sub}`, label: '知识库 · 推广历史与撤销' })
}
for (const sub of ['accept', 'edit', 'reject', 'show']) {
  catalog.push({ name: `/learn ${sub}`, label: '本轮学习审阅' })
}
for (const sub of ['list', 'show', 'enable', 'disable', 'dispute', 'delete']) {
  catalog.push({ name: `/memory ${sub}`, label: '打开知识库' })
}
catalog.push(
  { name: '/memory selection list', label: '打开知识库' },
  { name: '/memory selection show', label: '打开知识库' },
)
for (const entry of catalog) knowledgeCommands[entry.name] = { label: entry.label }

export const assetCommandEntries: AssetCommandEntry[] = catalog
