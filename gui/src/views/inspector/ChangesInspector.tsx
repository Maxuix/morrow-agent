import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeDiff, ChangePage, RestoreOperation, WorkspaceChange } from '../../api/changes'
import type { InspectorBodyProps } from './InspectorBodyProps'
import { selectorParams } from './TaskArtifactsInspector'
import { buttonClass } from '../management/styles'

const statusLabels: Record<string, string> = {
  ready: '可撤销', conflict: '存在后续修改', busy: '工作区忙碌', unsupported: '仅记录',
  unavailable: '恢复材料不可用', already_target: '目标内容已存在', prepared: '已保存意图',
  applied: '已恢复', target_reached: '目标已达到', pending: '待执行', not_executed: '未执行',
  needs_reconciliation: '需要核对', completed: '恢复完成', blocked: '未写入', partially_applied: '部分恢复',
  previewed: '撤销预览', applying: '执行中',
}
const label = (value: string) => statusLabels[value] ?? value
const commandId = () => `cmd_${crypto.randomUUID().replaceAll('-', '')}`
const short = (hash: string | null) => hash?.slice(0, 10) ?? '不存在'

/** Lines remain inert text nodes, including source that looks like HTML. */
function Diff({ value }: {value: string}) {
  const [mode, setMode] = useState<'unified' | 'split'>('split')
  return <div className="min-w-0 rounded-lg border border-subtle bg-base">
    <div className="flex flex-wrap gap-2 border-b border-subtle p-2" aria-label="差异显示方式">
      <button className={buttonClass} aria-pressed={mode === 'split'} onClick={() => setMode('split')}>两侧对照</button>
      <button className={buttonClass} aria-pressed={mode === 'unified'} onClick={() => setMode('unified')}>统一 Diff</button>
    </div>
    {mode === 'split' && <div className="grid grid-cols-2 gap-2 border-b border-subtle px-2 py-1 text-xs"><span>修改前 A</span><span>修改后 B</span></div>}
    <div className="max-h-96 overflow-auto font-mono text-xs leading-relaxed" aria-label="历史版本差异">
      {value.split('\n').map((line, index) => {
        const add = line.startsWith('+') && !line.startsWith('+++')
        const remove = line.startsWith('-') && !line.startsWith('---')
        const heading = line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++')
        const color = {backgroundColor: add ? 'color-mix(in srgb, var(--status-completed) 12%, transparent)' : remove ? 'color-mix(in srgb, var(--status-failed) 12%, transparent)' : undefined}
        return mode === 'unified' || heading ? <pre key={index} className="whitespace-pre-wrap break-all px-2" style={color}>{line || ' '}</pre>
          : <div key={index} className="grid grid-cols-2 gap-2" style={color}><pre className="whitespace-pre-wrap break-all border-r border-subtle px-2">{add ? ' ' : line || ' '}</pre><pre className="whitespace-pre-wrap break-all px-2">{remove ? ' ' : line || ' '}</pre></div>
      })}
    </div>
  </div>
}

export function ChangesInspector(props: InspectorBodyProps) {
  const { client, workspaceId, sessionId } = props
  if (!client || !workspaceId || !sessionId) return <p className="p-4">选择会话后查看代码变更。</p>
  return <ChangesBody key={JSON.stringify([workspaceId, sessionId, props.target?.taskRunId, props.target?.workflowRunId, props.target?.nodeRunId, props.currentTaskRunId])} {...props} client={client} workspaceId={workspaceId} sessionId={sessionId} />
}

function ChangesBody({client, workspaceId, sessionId, active, target, currentTaskRunId, connection, syncStore, onOpenFile, fileBuffers}: InspectorBodyProps & {client: NonNullable<InspectorBodyProps['client']>; workspaceId: string; sessionId: string}) {
  const params = useMemo(() => selectorParams(target, currentTaskRunId), [target, currentTaskRunId])
  const [page, setPage] = useState<ChangePage | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [focused, setFocused] = useState<string | null>(null)
  const [diff, setDiff] = useState<ChangeDiff | null>(null)
  const [operation, setOperation] = useState<RestoreOperation | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [timeline, setTimeline] = useState(false)
  const mounted = useRef(true)
  const gate = useRef(false)
  const generation = useRef(0)
  const diffGeneration = useRef(0)
  const applyKey = useRef<string | null>(null)
  const retryPreview = useRef<{command_id: string; change_ids: string[]} | null>(null)
  const storageKey = `morrow.restore.v1.${workspaceId}.${sessionId}`
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  const refresh = async (append = false) => {
    const version = ++generation.current
    try {
      const result = await client.workspaceChanges(workspaceId, sessionId, {...params, ...(append && page?.next_cursor ? {cursor: page.next_cursor} : {})})
      if (mounted.current && version === generation.current) {
        setPage(previous => append && previous ? {...result, items: [...previous.items, ...result.items]} : result)
        setError(null)
      }
    } catch (e) { if (mounted.current && version === generation.current) setError((e as Error).message) }
  }
  useEffect(() => { if (active) void refresh() }, [active, connection])
  useEffect(() => {
    if (!syncStore || !active) return
    let cursor = syncStore.getState().cursor
    return syncStore.subscribe(() => {
      if (cursor !== syncStore.getState().cursor) { cursor = syncStore.getState().cursor; void refresh() }
    })
  }, [syncStore, active])

  const showDiff = async (row: WorkspaceChange, append = false, chainIds: string[] = [row.change_id]) => {
    const version = ++diffGeneration.current
    setFocused(row.change_id)
    if (!append) setDiff(null)
    try {
      const value = await client.workspaceChangeDiff(workspaceId, sessionId, row.change_id, {...params, chain: (append ? diff?.chain_ids ?? chainIds : chainIds).join(','), ...(append && diff?.next_offset !== null ? {offset: diff?.next_offset} : {})})
      if (mounted.current && version === diffGeneration.current) setDiff(previous => append && previous ? {...value, diff: previous.diff + value.diff} : value)
    } catch (e) { if (mounted.current && version === diffGeneration.current) setError((e as Error).message) }
  }
  const act = async (work: () => Promise<void>) => {
    if (gate.current) return
    gate.current = true; setPending(true); setError(null)
    try { await work() } catch (e) { if (mounted.current) setError((e as Error).message) }
    finally { gate.current = false; if (mounted.current) setPending(false) }
  }
  const remember = (op: RestoreOperation, key: string) => {
    setOperation(op); applyKey.current = key
    try { sessionStorage.setItem(storageKey, JSON.stringify({operation_id: op.operation_id, command_id: key})) }
    catch { setNotice('浏览器无法保存重试标识，请保留操作 ID。') }
  }
  const status = async (id = operation?.operation_id) => {
    if (!id) return
    const value = await client.restoreStatus(workspaceId, sessionId, id)
    if (mounted.current) { setOperation(value); await refresh() }
  }
  useEffect(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null') as {operation_id: string; command_id: string} | null
      if (saved) { applyKey.current = saved.command_id; void status(saved.operation_id).catch(e => setError((e as Error).message)) }
    } catch { /* Invalid browser state cannot authorize a write. */ }
  }, [])
  useEffect(() => {
    const check = () => { if (operation) void status().catch(e => setError((e as Error).message)); else if (active) void refresh() }
    window.addEventListener('online', check); window.addEventListener('focus', check)
    return () => { window.removeEventListener('online', check); window.removeEventListener('focus', check) }
  }, [operation, active])

  const rows = page?.items ?? []
  const groups = new Map<string, WorkspaceChange[]>()
  for (const row of rows) groups.set(row.path, [...(groups.get(row.path) ?? []), row])
  const toggle = (ids: string[], enabled: boolean) => {
    setSelected(old => enabled ? [...new Set([...old, ...ids])] : old.filter(id => !ids.includes(id)))
    setOperation(null); retryPreview.current = null
  }
  const preview = () => act(async () => {
    const request = retryPreview.current ?? {command_id: commandId(), change_ids: selected}
    retryPreview.current = request
    const result = await client.previewRestore(workspaceId, sessionId, {...params, ...request})
    remember(result, commandId())
  })
  const apply = () => act(async () => {
    if (!operation || !applyKey.current) return
    const result = await client.applyRestore(workspaceId, sessionId, {preview_id: operation.operation_id, preview_digest: operation.preview_digest, command_id: applyKey.current})
    setOperation(result)
    setNotice('文件版本已变化；原任务结果、审阅和测试记录仍属于历史版本，请重新验证。')
    for (const item of result.items) if (['applied','target_reached'].includes(item.state)) {
      if (fileBuffers) void fileBuffers.load({workspaceId, sessionId}, item.path)
    }
    window.dispatchEvent(new CustomEvent('morrow:workspace-restored', {detail: {workspaceId, sessionId, paths: result.items.map(item => item.path)}}))
    setSelected([]); setDiff(null); await refresh()
  })
  const review = () => act(async () => {
    await client.reviewChanges(workspaceId, sessionId, {...params, command_id: commandId(), change_ids: selected, versions: Object.fromEntries(rows.filter(r => selected.includes(r.change_id)).map(r => [r.change_id, r.row_version]))})
    setOperation(null); setSelected([]); await refresh()
  })
  const focusedRow = rows.find(row => row.change_id === focused)
  const completed = operation && operation.state !== 'previewed'
  return <section className="flex h-full min-h-0 min-w-0 flex-col overflow-y-auto p-4" aria-label="代码变更" data-inspector-kind="changes">
    <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-medium">任务代码变更</h2><button className={buttonClass} disabled={pending} onClick={() => void refresh()}>刷新</button></div>
    <p className="mt-2 text-xs text-secondary">{page?.coverage_message ?? '正在读取修改记录…'}</p>
    {!!page?.uncovered_commands && <p role="status" className="mt-2 text-sm">另有 {page.uncovered_commands} 条命令的文件影响未完整捕获。</p>}
    {page?.legacy_message && <p className="mt-2 text-sm">{page.legacy_message}</p>}
    {error && <p role="alert" className="mt-2 break-words text-sm text-failed">{error}</p>}
    {rows.some(row => row.origin === 'restore' && row.effect_state === 'applied') && <p className="mt-2 text-xs text-secondary">此任务包含后续恢复；原测试结果早于当前文件版本，请重新验证。</p>}
    {notice && <p role="status" className="mt-2 text-sm">{notice}</p>}
    <div className="my-3 flex flex-wrap gap-2">
      <button className={buttonClass} disabled={!selected.length || pending} onClick={() => void review()}>标记已审阅</button>
      <button className={buttonClass} disabled={!selected.length || pending} onClick={() => void preview()}>预览撤销（{selected.length} 次修改）</button>
      {rows.some(r => ['prepared','outcome_unknown'].includes(r.effect_state)) && <button className={buttonClass} disabled={!selected.length || pending} onClick={() => void act(async () => {await client.reconcileChanges(workspaceId, sessionId, {...params, command_id: commandId(), change_ids: selected}); await refresh()})}>核对修改记录</button>}
      <button className={buttonClass} disabled={!selected.length || pending} onClick={() => void act(async () => {await client.pinChangeSnapshots(workspaceId, sessionId, {...params, command_id: commandId(), change_ids: selected}); setNotice('所选恢复快照已固定保留。'); await refresh()})}>固定恢复快照</button>
      <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={timeline} onChange={e => setTimeline(e.target.checked)}/>修改时间线</label>
    </div>
    <p className="mb-3 text-xs text-secondary">普通快照 30 天内可恢复，可手动固定。审阅仅表示已阅读。撤销不改变 Git 暂存区与提交历史。</p>
    <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(12rem,0.8fr)_minmax(0,1.2fr)]">
      <div className="min-w-0 space-y-3"><p className="text-xs">{groups.size} 个已加载文件 · 已审阅 {rows.filter(r => r.review_state === 'reviewed').length}/{rows.length} 次修改</p>
        {[...groups].map(([path, chain]) => <div key={path} className="min-w-0 rounded-lg border border-subtle p-2">
          <p className="break-all font-mono text-xs">{path}</p>
          {!timeline && <p className="my-1 break-all text-xs text-secondary">{chain.length} 次记录 · 连续链可合并查看；存在间隙时请切换时间线。</p>}
          {(timeline ? chain : [chain.at(-1)!]).map(row => <div key={row.change_id} className="mt-2 flex min-w-0 flex-wrap items-center gap-2 text-xs">
            <label className="flex items-center gap-1"><input type="checkbox" aria-label={`选择 ${path} 第 ${row.workspace_sequence} 次修改`} checked={selected.includes(row.change_id)} onChange={e => toggle([row.change_id], e.target.checked)} />#{row.workspace_sequence}</label>
            <button className={buttonClass} onClick={() => void showDiff(row, false, timeline ? [row.change_id] : chain.map(r => r.change_id))}>查看历史 Diff</button>
            <span>{row.review_state === 'reviewed' ? '已审阅' : '待审阅'} · {label(row.restore_support)}</span>
            <p className="w-full break-words text-secondary">{row.origin === 'restore' ? '用户恢复' : '受控工具'} · {row.operation} · +{row.added_lines ?? 0} −{row.removed_lines ?? 0}{row.restore_reason ? ` · ${row.restore_reason}` : ''}</p>
          </div>)}
          {!timeline && chain.length > 1 && <button className={`${buttonClass} mt-2`} onClick={() => toggle(chain.map(r => r.change_id), true)}>选择此文件的已加载修改链</button>}
        </div>)}
        {page?.next_cursor && <button className={buttonClass} onClick={() => void refresh(true)}>加载更多（当前列表尚不完整）</button>}
      </div>
      <div className="min-w-0 space-y-2">
        {focusedRow && <><h3 className="break-all text-xs font-medium">{focusedRow.path} · 历史 A → B</h3><p className="break-all text-xs text-secondary">{short(diff?.before?.sha256 ?? focusedRow.before.sha256)} → {short(focusedRow.after.sha256)} · 当前磁盘：{label(diff?.restore_support ?? focusedRow.restore_support)}</p><button className={buttonClass} onClick={() => onOpenFile?.({kind: 'workspace', path: focusedRow.path})}>比较当前文件 C</button></>}
        {diff && <><Diff value={diff.diff}/>{diff.next_offset !== null && focusedRow && <button className={buttonClass} onClick={() => void showDiff(focusedRow, true)}>继续加载完整差异</button>}</>}
        {!focused && <p className="text-sm text-secondary">选择一个历史 Diff 开始审阅。</p>}
      </div>
    </div>
    {operation && <section className="mt-4 rounded-lg border border-subtle p-3" aria-label="恢复预览与结果">
      <h3 className="text-sm font-medium" role="status">{label(operation.state)}</h3>
      <p className="mt-1 break-all text-xs text-secondary">操作 {operation.operation_id}</p>
      <p className="mt-1 text-xs">{completed ? '以下为后端持久化结果。' : `预览有效至 ${new Date(operation.expires_at).toLocaleTimeString()}；执行时会再次核对文件。`}</p>
      <ul className="mt-2 space-y-2 text-xs">{operation.items.map(item => <li key={item.path} className="break-all"><strong>{item.path}</strong> · {label(item.state)}<br/>当前 B {short(item.expected.sha256)} → 恢复 A {short(item.desired.sha256)}{item.reason && <p>{item.reason}</p>}{completed && <p className="text-secondary">保全位置：{[...item.path.split('/').slice(0,-1), item.capture_name].join('/')}；临时位置：{[...item.path.split('/').slice(0,-1), item.temporary_name].join('/')}</p>}</li>)}</ul>
      <div className="mt-3 flex flex-wrap gap-2">
        {operation.state === 'previewed' && <button className={buttonClass} disabled={pending || operation.items.some(item => item.state === 'conflict')} onClick={() => void apply()}>确认按预览恢复</button>}
        <button className={buttonClass} disabled={pending} onClick={() => void act(() => status())}>查询原操作状态</button>
        {operation.state === 'needs_reconciliation' && <button className={buttonClass} disabled={pending} onClick={() => void act(async () => {setOperation(await client.reconcileRestore(workspaceId, sessionId, operation.operation_id, commandId())); await refresh()})}>核对中断结果</button>}
        {completed && operation.items.some(i => i.change_id && ['applied','target_reached'].includes(i.state)) && <button className={buttonClass} disabled={pending} onClick={() => void act(async () => { const ids = operation.items.filter(i => ['applied','target_reached'].includes(i.state) && i.change_id).map(i => i.change_id!); remember(await client.previewRestore(workspaceId, sessionId, {...params, command_id: commandId(), change_ids: ids}), commandId()) })}>预览恢复刚才撤销的内容</button>}
      </div>
    </section>}
  </section>
}
