import { useEffect, useMemo, useState } from 'react'
import type { ApiClient } from '../../api/client'
import type { Knowledge, LearningPage, ManagedStatus } from '../../api/management'
import type { DirtyGuard, KnowledgeFocus } from '../../state/navigation'
import { Card, CursorPager, FieldList } from '../management/components'
import { useKnowledgeAction } from '../LearningOperations'
import { CandidateCard, ProposalCard } from '../LearningManager'
import { buttonClass, fieldClass } from '../management/styles'
import type { Mutate } from '../management/types'
import { candidateSourceLabel, type CandidateSourceFields } from './source'

const CATEGORIES = ['architecture', 'convention', 'decision', 'environment', 'domain', 'other'] as const
const CATEGORY_LABELS: Record<string, string> = {
  architecture: '架构',
  convention: '约定',
  decision: '决策',
  environment: '环境',
  domain: '领域',
  other: '其他',
}
const STATUS_LABELS: Record<string, string> = {
  active: '已启用',
  disabled: '已停用',
  disputed: '有争议',
  deleted: '已删除',
}

type KnowledgeItem = Knowledge | {
  head: { knowledge_id: string; semantic_key: string; category: string; status: ManagedStatus; row_version: number }
  current_revision?: { statement: string } | null
  revision?: { statement: string } | null
  evidence?: Knowledge['evidence']
  timeline?: Knowledge['timeline']
}

type CandidateRow = {
  candidate_id: string
  candidate_type: string
  semantic_key: string
  status: string
} & CandidateSourceFields

type KnowledgePage = { items: KnowledgeItem[]; next_cursor: string | null }
type CandidatePage = { items: CandidateRow[]; next_cursor: string | null }

const MODE_LABELS: Record<string, string> = {
  off: '关闭',
  review_only: '审阅后决定',
  explicit_auto: '自动学习（不可用）',
}

function statementOf(item: KnowledgeItem): string {
  if (item.revision?.statement) return item.revision.statement
  if ('current_revision' in item) return item.current_revision?.statement ?? ''
  return ''
}

function knowledgeSource(item: KnowledgeItem): string {
  const evidence = item.evidence ?? []
  if (evidence.length === 0) return '尚无来源记录'
  const kinds = [...new Set(evidence.map(row => row.source_kind).filter(Boolean))]
  return `来源 ${evidence.length} 条${kinds.length ? ` · ${kinds.join('、')}` : ''}`
}

function knowledgeActions(status: string): { action: string; label: string }[] {
  if (status === 'active') return [
    { action: 'disable', label: '停用' },
    { action: 'dispute', label: '标记争议' },
    { action: 'delete', label: '删除' },
  ]
  if (status === 'disabled') return [
    { action: 'enable', label: '启用' },
    { action: 'delete', label: '删除' },
  ]
  if (status === 'disputed') return [{ action: 'delete', label: '删除' }]
  return []
}

function areaFromFocus(focus?: KnowledgeFocus): 'records' | 'learning' | 'promotions' {
  if (focus === 'learning' || focus === 'add') return 'learning'
  if (focus === 'promotions') return 'promotions'
  return 'records'
}

export function KnowledgeLibraryPage({
  client,
  workspaceId,
  connected,
  mutate,
  refresh,
  onChanged,
  focus,
  registerGuard,
}: {
  client: ApiClient
  workspaceId?: string
  connected: boolean
  mutate: Mutate
  refresh: number
  onChanged: () => void
  focus?: KnowledgeFocus
  registerGuard?: (guard: DirtyGuard) => () => void
}) {
  const [area, setArea] = useState<'records' | 'learning' | 'promotions'>(() => areaFromFocus(focus))
  useEffect(() => { setArea(areaFromFocus(focus)) }, [focus])
  return (
    <div className="asset-page" data-workspace={workspaceId ?? ''}>

      <nav className="asset-subnav" aria-label="知识库分区">
        {([
          ['records', '知识'],
          ['learning', '学习'],
          ['promotions', '历史'],
        ] as const).map(([id, label]) => (
          <button key={id} type="button" className={buttonClass} aria-pressed={area === id} onClick={() => setArea(id)}>
            {label}
          </button>
        ))}
      </nav>
      {area === 'records' && <KnowledgeRecords client={client} connected={connected} mutate={mutate} refresh={refresh} onReview={() => setArea('learning')} />}
      {area === 'learning' && <LearningSecondary client={client} connected={connected} mutate={mutate} refresh={refresh} onChanged={onChanged} registerGuard={registerGuard} />}
      {area === 'promotions' && <PromotionSecondary client={client} refresh={refresh} onChanged={onChanged} />}
    </div>
  )
}

function KnowledgeRecords({
  client, connected, mutate, refresh, onReview,
}: {
  client: ApiClient
  connected: boolean
  mutate: Mutate
  refresh: number
  onReview: () => void
}) {
  const [status, setStatus] = useState('')
  const [category, setCategory] = useState('')
  const [query, setQuery] = useState('')
  const [deleted, setDeleted] = useState(false)
  const [cursor, setCursor] = useState<string | undefined>()
  const [history, setHistory] = useState<(string | undefined)[]>([])
  const [page, setPage] = useState<KnowledgePage>({ items: [], next_cursor: null })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const reset = () => { setCursor(undefined); setHistory([]) }
  useEffect(() => {
    let alive = true
    setLoading(true)
    setPage({ items: [], next_cursor: null })
    void client.knowledgeQuery<KnowledgePage>('knowledge', {
      status: status || undefined,
      category: category || undefined,
      include_deleted: deleted,
      cursor,
    }).then(value => { if (alive) { setPage({ items: value.items ?? [], next_cursor: value.next_cursor ?? null }); setError(''); setLoading(false) } }, err => { if (alive) { setError((err as Error).message); setLoading(false) } })
    return () => { alive = false }
  }, [client, refresh, status, category, deleted, cursor])

  const visible = useMemo(() => {
    const items = page.items ?? []
    const needle = query.trim().toLowerCase()
    if (!needle) return items
    return items.filter(item => {
      const text = `${item.head.semantic_key} ${statementOf(item)}`.toLowerCase()
      return text.includes(needle)
    })
  }, [page.items, query])

  return (
    <section className="asset-section" aria-label="已入库知识">
      <header className="pp-card-head">

        <button type="button" className="pp-button pp-primary" disabled={!connected} onClick={onReview}>审阅候选</button>
      </header>
      <div className="pp-toolbar">
        <label className="pp-field pp-grow">
          <span className="pp-field-title">检索</span>
          <input className="pp-input" aria-label="检索知识" placeholder="按正文或语义键筛选本页" value={query} onChange={event => setQuery(event.target.value)} />
        </label>
        <label className="pp-field">
          <span className="pp-field-title">状态</span>
          <select className={fieldClass} aria-label="知识状态" value={status} onChange={event => { setStatus(event.target.value); reset() }}>
            <option value="">全部可见</option>
            {['active', 'disabled', 'disputed', 'deleted'].map(value => <option key={value} value={value}>{STATUS_LABELS[value]}</option>)}
          </select>
        </label>
        <label className="pp-field">
          <span className="pp-field-title">类别</span>
          <select className={fieldClass} aria-label="知识类别" value={category} onChange={event => { setCategory(event.target.value); reset() }}>
            <option value="">全部类别</option>
            {CATEGORIES.map(value => <option key={value} value={value}>{CATEGORY_LABELS[value]}</option>)}
          </select>
        </label>
        <label className="text-sm"><input type="checkbox" checked={deleted} onChange={event => { setDeleted(event.target.checked); reset() }} /> 包括已删除</label>
      </div>
      {error && <p role="alert">{error}</p>}
      {loading && <p className="pp-hint" role="status">正在读取知识…</p>}
      {visible.filter(item => item?.head).map(item => (
        <KnowledgeRecordCard key={item.head.knowledge_id} item={item} connected={connected} mutate={mutate} />
      ))}
      {!loading && !visible.length && <p className="pp-hint">{query || status || category || deleted ? '无匹配结果' : '暂无知识'}{(query || status || category || deleted) && <button type="button" className={buttonClass} onClick={() => { setQuery(''); setStatus(''); setCategory(''); setDeleted(false); reset() }}>清除筛选</button>}</p>}
      <CursorPager
        hasPrev={history.length > 0}
        hasNext={Boolean(page.next_cursor)}
        onPrev={() => { setCursor(history.at(-1)); setHistory(rows => rows.slice(0, -1)) }}
        onNext={() => { if (page.next_cursor) { setHistory(rows => [...rows, cursor]); setCursor(page.next_cursor!) } }}
      />
    </section>
  )
}

function KnowledgeRecordCard({ item, connected, mutate }: { item: KnowledgeItem; connected: boolean; mutate: Mutate }) {
  const status = item.head.status
  const [confirm, setConfirm] = useState<string | null>(null)
  const run = (action: string) => {
    if (action === 'delete' && confirm !== 'delete') { setConfirm('delete'); return }
    void mutate('knowledge', { action, expected_row_version: item.head.row_version }, item.head.knowledge_id)
    setConfirm(null)
  }
  return (
    <Card title={item.head.semantic_key}>
      <p>{statementOf(item) || '正文不可用'}</p>
      <p className="text-xs text-secondary">
        {CATEGORY_LABELS[item.head.category] ?? item.head.category} · {STATUS_LABELS[status] ?? status} · {knowledgeSource(item)}
      </p>
      {(item.evidence?.length ?? 0) > 0 && (
        <ul className="asset-evidence">
          {item.evidence!.map(row => (
            <li key={row.evidence_id}>{row.source_kind}{row.excerpt_redacted ? ` · ${row.excerpt_redacted}` : ''}</li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        {knowledgeActions(status).map(entry => (
          <button key={entry.action} type="button" className={buttonClass} disabled={!connected} onClick={() => run(entry.action)}>
            {entry.label}
          </button>
        ))}
      </div>
      {confirm === 'delete' && (
        <p className="pp-hint" role="status">
          删除后普通列表不再显示，历史修订保留。
          <button type="button" className="pp-button pp-danger" onClick={() => run('delete')}>确认删除</button>
          <button type="button" className="pp-button" onClick={() => setConfirm(null)}>取消</button>
        </p>
      )}
    </Card>
  )
}

function LearningSecondary({
  client, connected, mutate, refresh, onChanged, registerGuard,
}: {
  client: ApiClient
  connected: boolean
  mutate: Mutate
  refresh: number
  onChanged: () => void
  registerGuard?: (guard: DirtyGuard) => () => void
}) {
  const { act, busy, message } = useKnowledgeAction(client, onChanged)
  const [policy, setPolicy] = useState<{ mode: string; row_version: number } | null>(null)
  const [statusCounts, setStatusCounts] = useState<{ pending_reviews?: number; proposed_candidates?: number } | null>(null)
  const [type, setType] = useState('')
  const [status, setStatus] = useState('proposed')
  const [cursor, setCursor] = useState<string | undefined>()
  const [history, setHistory] = useState<(string | undefined)[]>([])
  const [page, setPage] = useState<CandidatePage>({ items: [], next_cursor: null })
  const [selected, setSelected] = useState<LearningPage['candidates'][number] | null>(null)
  const [error, setError] = useState('')
  const [proposals, setProposals] = useState<LearningPage['proposals']>([])
  const [loading, setLoading] = useState(true)

  const reset = () => { setCursor(undefined); setHistory([]); setSelected(null) }
  useEffect(() => {
    let alive = true
    setPolicy(null)
    void client.knowledgeQuery<{ policy: { mode: string; row_version: number }; pending_reviews?: number; proposed_candidates?: number }>('learning-status')
      .then(value => { if (alive) { setPolicy(value.policy); setStatusCounts(value) } }, err => { if (alive) setError((err as Error).message) })
    return () => { alive = false }
  }, [client, refresh])
  useEffect(() => {
    let alive = true
    setLoading(true)
    setPage({ items: [], next_cursor: null })
    void client.knowledgeQuery<CandidatePage>('candidates', { status, candidate_type: type, cursor })
      .then(value => { if (alive) { setPage(value); setError(''); setLoading(false) } }, err => { if (alive) { setError((err as Error).message); setLoading(false) } })
    return () => { alive = false }
  }, [client, refresh, status, type, cursor])
  useEffect(() => {
    let alive = true
    void client.knowledgeQuery<{ items: LearningPage['proposals']; next_cursor: string | null }>('proposals', { status: 'proposed' })
      .then(value => { if (alive) setProposals(value.items ?? []) }, () => { if (alive) setProposals([]) })
    return () => { alive = false }
  }, [client, refresh])

  const inspect = (id: string) => {
    void client.knowledgeQuery<LearningPage['candidates'][number]>('candidates', { identity: id })
      .then(value => setSelected(value), err => setError((err as Error).message))
  }

  return (
    <section className="asset-section space-y-4" aria-label="学习模式与全部候选">
      <Card title="学习模式">

        {policy && (
          <label className="block text-sm">学习模式
            <select aria-label="学习模式" className={fieldClass} value={policy.mode} disabled={busy || !connected}
              onChange={event => void act('learning', { action: 'mode', mode: event.target.value, expected_row_version: policy.row_version })}>
              <option value="off">{MODE_LABELS.off}</option>
              <option value="review_only">{MODE_LABELS.review_only}</option>
              {policy.mode === 'explicit_auto' && <option value="explicit_auto" disabled>{MODE_LABELS.explicit_auto}</option>}
            </select>
          </label>
        )}
        <FieldList items={[
          { label: '待处理审阅', value: statusCounts?.pending_reviews != null ? String(statusCounts.pending_reviews) : undefined },
          { label: '待确认候选', value: statusCounts?.proposed_candidates != null ? String(statusCounts.proposed_candidates) : undefined },
        ]} />
      </Card>

      <Card title="全部候选审阅">

        <div className="flex flex-wrap gap-2">
          <label className="text-sm">类型
            <select className={fieldClass} aria-label="候选类型" value={type} onChange={event => { setType(event.target.value); reset() }}>
              <option value="">全部类型</option>
              {['profile', 'project_knowledge', 'skill_candidate'].map(value => <option key={value}>{value}</option>)}
            </select>
          </label>
          <label className="text-sm">状态
            <select className={fieldClass} aria-label="候选状态" value={status} onChange={event => { setStatus(event.target.value); reset() }}>
              <option value="">全部状态</option>
              {['proposed', 'accepted', 'edited_and_accepted', 'rejected', 'promoting', 'expired', 'superseded'].map(value => <option key={value}>{value}</option>)}
            </select>
          </label>
        </div>
        {loading && <p className="pp-hint" role="status">正在读取候选…</p>}
        {page.items.map(row => (
          <button type="button" className={`${buttonClass} block w-full text-left`} key={row.candidate_id}
            onClick={() => inspect(row.candidate_id)}>
            {row.semantic_key} · {row.candidate_type} · {row.status} · {candidateSourceLabel(row)}
          </button>
        ))}
        {!loading && !page.items.length && <p className="pp-hint">此筛选没有候选。</p>}
        <CursorPager
          hasPrev={history.length > 0}
          hasNext={Boolean(page.next_cursor)}
          onPrev={() => { setCursor(history.at(-1)); setHistory(rows => rows.slice(0, -1)) }}
          onNext={() => { if (page.next_cursor) { setHistory(rows => [...rows, cursor]); setCursor(page.next_cursor!) } }}
        />
        {selected && <CandidateCard key={`${selected.candidate.candidate_id}:${selected.candidate.row_version}`} item={selected} mutate={mutate} registerGuard={registerGuard} />}
      </Card>

      {proposals.length > 0 && (
        <Card title="偏好候选">
          {proposals.map(row => <ProposalCard key={`${row.proposal_id}:${row.row_version}`} p={row} mutate={mutate} />)}
        </Card>
      )}
      {(error || message) && <p role="status">{error || message}</p>}
    </section>
  )
}

type PromotionRow = {
  operation_id?: string
  activation_id?: string
  status?: string
  state?: string
  target?: string
  path?: string
  failure_code?: string | null
  row_version?: number
}
type PromotionPage = { items: PromotionRow[]; next_cursor: string | null }

function PromotionSecondary({
  client, refresh, onChanged,
}: {
  client: ApiClient
  refresh: number
  onChanged: () => void
}) {
  const { act, busy, message } = useKnowledgeAction(client, onChanged)
  const [tab, setTab] = useState<'promotions' | 'activations'>('promotions')
  const [status, setStatus] = useState('')
  const [cursor, setCursor] = useState<string | undefined>()
  const [history, setHistory] = useState<(string | undefined)[]>([])
  const [page, setPage] = useState<PromotionPage>({ items: [], next_cursor: null })
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<{ id: string; action: string; label: string; lines?: string[] } | null>(null)

  const reset = () => { setCursor(undefined); setHistory([]); setPreview(null) }
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    setLoading(true)
    setPage({ items: [], next_cursor: null })
    void client.knowledgeQuery<PromotionPage>(tab, { status, cursor, limit: 50 })
      .then(value => { if (alive) { setPage(Array.isArray(value) ? { items: value, next_cursor: null } : value); setError(''); setLoading(false) } }, err => { if (alive) { setError((err as Error).message); setLoading(false) } })
    return () => { alive = false }
  }, [client, refresh, tab, status, cursor])

  const identity = (row: PromotionRow) => tab === 'activations' ? row.activation_id! : row.operation_id!

  return (
    <section className="asset-section space-y-4" aria-label="推广历史与撤销">
      <nav className="flex flex-wrap gap-2" aria-label="推广记录">
        <button type="button" className={buttonClass} aria-pressed={tab === 'promotions'} onClick={() => { setTab('promotions'); setStatus(''); reset() }}>待恢复推广</button>
        <button type="button" className={buttonClass} aria-pressed={tab === 'activations'} onClick={() => { setTab('activations'); setStatus(''); reset() }}>配置激活与撤销</button>
      </nav>
      <label className="block text-sm">状态
        <select className={fieldClass} aria-label="推广状态" value={status} onChange={event => { setStatus(event.target.value); reset() }}>
          <option value="">全部 / 待处理</option>
          {(tab === 'promotions' ? ['prepared', 'needs_resolution', 'finalized', 'aborted'] : []).map(value => <option key={value}>{value}</option>)}
        </select>
      </label>
      {page.items.map(row => {
        const id = identity(row)
        return (
          <article className="rounded-lg border border-subtle p-3 text-sm space-y-2" key={id}>
            <p>{row.target ?? row.path ?? '配置项'} · {row.status ?? row.state}</p>
            {row.failure_code && <p className="pp-error">失败：{row.failure_code}</p>}
            {tab === 'promotions' && (
              <div className="flex flex-wrap gap-2">
                {[['retry', '重试推广'], ['finalize', '完成已写入推广'], ['cancel', '取消未写入推广'], ['abort', '终止冲突推广']].map(([action, label]) => (
                  <button type="button" className={buttonClass} disabled={busy} key={action} onClick={() => setPreview({ id, action, label })}>{label}</button>
                ))}
              </div>
            )}
            {tab === 'activations' && (row.status === 'active' || row.state === 'active') && (
              <button type="button" className={buttonClass} onClick={() => void client.knowledgeQuery<{ preview?: string[]; expected_revision?: number }>('undo-preview', { identity: id })
                .then(value => setPreview({ id, action: 'undo', label: '撤销此激活', lines: value.preview }), err => setError((err as Error).message))}>
                预览撤销此激活
              </button>
            )}
          </article>
        )
      })}
      {loading && <p className="pp-hint" role="status">正在读取推广记录…</p>}
      {!loading && !page.items.length && <p className="pp-hint">当前筛选没有记录。</p>}
      <CursorPager
        hasPrev={history.length > 0}
        hasNext={Boolean(page.next_cursor)}
        onPrev={() => { setCursor(history.at(-1)); setHistory(rows => rows.slice(0, -1)) }}
        onNext={() => { if (page.next_cursor) { setHistory(rows => [...rows, cursor]); setCursor(page.next_cursor!) } }}
      />
      {preview && (
        <Card title="确认具体变更">
          <p>{preview.label}</p>
          {preview.lines?.length ? <ul className="asset-evidence">{preview.lines.map(line => <li key={line}>{line}</li>)}</ul> : <p className="pp-hint">未提供变更预览。</p>}
          <div className="flex gap-2">
            <button type="button" className={buttonClass} disabled={busy} onClick={() => void act('learning', preview.action === 'undo'
              ? { action: 'undo', target: preview.id }
              : { action: 'promotion', target: preview.id, recovery_action: preview.action }).then(ok => { if (ok) setPreview(null) })}>
              确认执行
            </button>
            <button type="button" className={buttonClass} onClick={() => setPreview(null)}>返回</button>
          </div>
        </Card>
      )}
      {(error || message) && <p role="status">{error || message}</p>}
    </section>
  )
}
