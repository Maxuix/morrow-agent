import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import type { ApiClient } from '../api/client'
import type { ActivityItem } from '../api/activity'
import type { ApprovalWire } from '../api/types'
import type { StageFrame } from '../api/chat'
import {
  buildRunNodes,
  FrozenClock,
  runFacts,
  runOverview,
  settleRunFacts,
  toolGroupSummary,
  type ActivityContentState,
  type ActivityRun,
  type RegionPart,
} from '../state/activity'
import { elapsedText, stageText } from './lib/stages'
import {
  ActivityIcon,
  ApprovalLine,
  AssetGroup,
  MarkerLine,
  RetryLine,
  StageLine,
  SteerInput,
  ToolRow,
  ThinkingBlock,
} from './ActivityItem'
import { InlineApprovalCard } from './chat/InlineApprovalCard'
import { uniqueApprovals } from '../state/approvalDecision'

/** Consecutive tool rows visible before the 显示其余 N 条 expander. */
const GROUP_PREVIEW_ROWS = 5

const nodeStatusText = (items: ActivityItem[], now: number): string => {
  if (!items.length) return ''
  const facts = runFacts(items)
  if (facts.active) return '执行中'
  const total = facts.startMs !== null
    ? Math.max(0, Math.floor(((facts.endMs ?? now) - facts.startMs) / 1000))
    : null
  if (facts.failed) return total ? `失败 · ${total} 秒` : '失败'
  if (facts.cancelled) return '已停止'
  return total ? `用时 ${total} 秒` : ''
}

const partKey = (part: RegionPart): string =>
  part.kind === 'tools' || part.kind === 'assets' ? part.items[0].activity_id : part.item.activity_id

/**
 * The per-request execution region (formerly the tail "行动轨迹" list).
 *
 * One compact overview line answers "how is it going / how long"; expanding
 * reveals content groups (thinking, tool groups, assets, retries, approvals)
 * in execution order — workflow runs additionally group by node. Collapse
 * memory is per level and user-owned: new events never force it, and an
 * open detail keeps the region open when the run finishes. Long outputs
 * scroll locally; the chat page owns the main scroll.
 */
export function ActivityTimeline({
  client, run, content, status, connection, stages, busy, approvals, workspace, onApprovalSettled, onSteer,
  ownerStatus, expandedOverride, nowOverride, frozen,
}: {
  client: ApiClient
  run: ActivityRun
  content: Record<string, ActivityContentState>
  status: 'connecting' | 'live' | 'reconnecting' | 'offline'
  connection: string
  /** Live stage frames backing this run; also the no-activity fallback. */
  stages: StageFrame[]
  busy: boolean
  /** Durable lifecycle of this region's owner (workflow run); null when unknown. */
  ownerStatus?: string | null
  approvals: ApprovalWire[]
  /** Workspace is explicit so chat cards share the same revoke route. */
  workspace?: string
  onApprovalSettled?: (result: import('../api/types').ApprovalResolveResultWire) => void
  onSteer?: (identity: ActivityItem['identity'], text: string) => Promise<string>
  /** Test seam: pin the overview expansion instead of deriving it. */
  expandedOverride?: boolean
  /** Test seam: deterministic clock for durations. */
  nowOverride?: number
  /** P10.3: paused / needs-recovery execution — the clock stops instead of
   * running a ghost timer, and elapsed spans exclude the frozen interval. */
  frozen?: boolean
}) {
  const bodyId = useId()
  const items = run.items
  const facts = useMemo(() => settleRunFacts(items, ownerStatus ?? null), [items, ownerStatus])
  const {nodes, plain} = useMemo(() => buildRunNodes(items, content), [items, content])
  const stageNodes = useMemo(() => {
    const known = new Set(items.map(item => item.identity.node_run_id).filter(Boolean))
    return stages.filter(stage => !known.has(stage.node_run_id))
  }, [items, stages])
  const stageSummary = useMemo(() => {
    if (!stages.length) return ''
    const distinct = new Set(stages.map(stage => stage.node_run_id)).size
    if (distinct > 1) return `${distinct} 个节点执行中`
    return stageText(stages[stages.length - 1])
  }, [stages])
  // The tick counter only forces re-renders; the displayed clock comes from
  // the frozen clock so paused/disconnected spans never leak into elapsed.
  const [, setTick] = useState(0)
  const clockRef = useRef<FrozenClock | null>(null)
  if (!clockRef.current) clockRef.current = new FrozenClock(() => nowOverride ?? Date.now())
  const frozenClock = clockRef.current
  const clock = nowOverride ?? frozenClock.now()
  const [pin, setPin] = useState<boolean | null>(null)
  const [collapsedNodes, setCollapsedNodes] = useState(() => new Set<string>())
  const [steerNodes, setSteerNodes] = useState(() => new Set<string>())
  const [collapsedGroups, setCollapsedGroups] = useState(() => new Set<string>())
  const [openedGroups, setOpenedGroups] = useState(() => new Set<string>())
  const [openDetails, setOpenDetails] = useState(() => new Set<string>())
  const [openThinking, setOpenThinking] = useState(() => new Set<string>())
  const [closedAssets, setClosedAssets] = useState(() => new Set<string>())

  const timingFrozen = !!frozen || status === 'reconnecting' || status === 'offline'
  const stagesActive = stages.length > 0
  const activeNow = (facts.active || stagesActive) && !timingFrozen
  useEffect(() => {
    if (timingFrozen) {
      frozenClock.freeze()
      return
    }
    frozenClock.unfreeze()
    setTick(t => t + 1)
  }, [timingFrozen, frozenClock])
  useEffect(() => {
    if (!activeNow) return
    const timer = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(timer)
  }, [activeNow])

  const visibleApprovals = useMemo(() => uniqueApprovals(approvals), [approvals])
  const waiting = facts.waiting || visibleApprovals.length > 0
  let overview = runOverview(facts, {busy, syncing: status === 'reconnecting' || status === 'offline', stageText: stageSummary, now: clock})
  if (waiting && overview.kind !== 'settled') overview = {kind: 'waiting', text: '等待批准', tone: 'attention'}
  if (overview.kind === 'empty' && !overview.text) return null

  const expanded = expandedOverride ?? (pin ?? (activeNow || openDetails.size > 0))
  const toggle = (set: Set<string>, key: string, apply: (next: Set<string>) => void) => {
    const next = new Set(set)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    apply(next)
  }

  const renderPart = (part: RegionPart): ReactNode => {
    switch (part.kind) {
      case 'thinking': {
        const open = openThinking.has(part.item.activity_id)
        return <ThinkingBlock key={partKey(part)} content={content[part.item.activity_id]}
          open={open} onToggle={() => toggle(openThinking, part.item.activity_id, setOpenThinking)}/>
      }
      case 'stage':
        return <StageLine key={partKey(part)} item={part.item} now={clock}/>
      case 'retry':
        return <RetryLine key={partKey(part)} item={part.item} now={clock}/>
      case 'approval':
        return <ApprovalLine key={partKey(part)} item={part.item}/>
      case 'control':
      case 'compaction':
      case 'node_output':
        return <MarkerLine key={partKey(part)} item={part.item} now={clock}/>
      case 'assets': {
        const key = partKey(part)
        return <AssetGroup key={key} items={part.items} client={client}
          open={!closedAssets.has(key)} onToggle={() => toggle(closedAssets, key, setClosedAssets)}/>
      }
      case 'tools': {
        const key = partKey(part)
        const collapsed = collapsedGroups.has(key)
        const showAll = openedGroups.has(key)
        const rows = showAll ? part.items : part.items.slice(0, GROUP_PREVIEW_ROWS)
        const rest = part.items.length - rows.length
        return <div key={key} className="exec-tool-group">
          {part.items.length > 1 && <button type="button" className="exec-group-head" aria-expanded={!collapsed}
            onClick={() => toggle(collapsedGroups, key, setCollapsedGroups)}>
            <ActivityIcon name="list"/>
            <span>{toolGroupSummary(part.items)}</span>
            <ActivityIcon name="chevron" className="exec-icon exec-chev"/>
          </button>}
          {collapsed ? null : <ul className="exec-rows">
            {rows.map(item => <ToolRow key={item.activity_id} item={item} content={content[item.activity_id]}
              now={clock} open={openDetails.has(item.activity_id)} client={client}
              onToggle={() => toggle(openDetails, item.activity_id, setOpenDetails)}/>)}
            {rest > 0 && <li><button type="button" className="exec-link"
              onClick={() => toggle(openedGroups, key, setOpenedGroups)}>显示其余 {rest} 条</button></li>}
          </ul>}
        </div>
      }
    }
  }

  const renderParts = (parts: RegionPart[]) => parts.map(part => renderPart(part))

  const steerIdentity = (nodeItems: ActivityItem[]) =>
    nodeItems.find(item => item.identity.workflow_run_id && item.identity.node_run_id)?.identity ?? null

  const renderNode = (node: (typeof nodes)[number]) => {
    const collapsed = collapsedNodes.has(node.key)
    const identity = onSteer ? steerIdentity(node.items) : null
    return <section key={node.key} className="exec-node">
      <div className="exec-node-head">
        <button type="button" className="exec-node-toggle" aria-expanded={!collapsed}
          aria-controls={`${bodyId}-${node.key}`} onClick={() => toggle(collapsedNodes, node.key, setCollapsedNodes)}>
          <ActivityIcon name="chevron" className="exec-icon exec-chev"/>
          <span className="exec-node-label">{node.label && `节点 ${node.label}`}</span>
          <span className="exec-node-meta" aria-hidden="true">{nodeStatusText(node.items, clock)}</span>
        </button>
        {identity && onSteer && <button type="button" className="exec-link"
          onClick={() => toggle(steerNodes, node.key, setSteerNodes)}>{steerNodes.has(node.key) ? '收起纠偏' : '纠偏'}</button>}
      </div>
      {!collapsed && <div className="exec-node-body" id={`${bodyId}-${node.key}`}>{renderParts(node.parts)}</div>}
      {identity && onSteer && steerNodes.has(node.key) && <SteerInput identity={identity} onSteer={onSteer}/>}
    </section>
  }

  // Screen readers get the status phrase only; the per-second ticking span
  // must not re-announce every update.
  const livePhrase = overview.text.replace(/ · \d+(?: 分 \d+)? 秒$/, '')
  return <section className="exec-region" data-run-key={run.key} aria-label="执行过程">
    <div className="exec-overview" data-tone={overview.tone}>
      <button type="button" className="exec-overview-toggle" aria-expanded={expanded} aria-controls={bodyId}
        onClick={() => setPin(!expanded)}>
        <ActivityIcon name="chevron" className={`exec-icon exec-chev${expanded ? ' is-open' : ''}`}/>
        <span className="exec-overview-text">{overview.text}</span>
      </button>
    </div>
    <span className="visually-hidden" role="status">{livePhrase}</span>
    {visibleApprovals.length > 0 && <div className="exec-approval-cards" aria-label="待审批工具">
      {visibleApprovals.map(approval => <InlineApprovalCard
        key={approval.approval_id}
        approval={approval}
        client={client}
        workspace={workspace ?? run.items[0]?.identity.workspace_id ?? ''}
        onSettled={onApprovalSettled}
      />)}
    </div>}
    {expanded && <div id={bodyId} className="exec-body">
      {plain ? renderParts(nodes[0]?.parts ?? []) : nodes.map(renderNode)}
      {stageNodes.map(stage => <div key={stage.node_run_id} className="exec-row exec-row-static">
        <ActivityIcon name="node"/>
        <span className="exec-row-label">{stage.node_id ? `节点 ${stage.node_id} · ` : ''}{stageText(stage)}</span>
        <span className="exec-row-meta" aria-hidden="true">已进行 {elapsedText(stage.ts, clock) || '—'}</span>
      </div>)}
      {status === 'reconnecting' && <p className="exec-note">连接中断，正在重连。</p>}
      {connection !== 'live' && !items.length && stagesActive && <p className="exec-note">状态同步中；阶段信息可能滞后。</p>}
    </div>}
  </section>
}
