/**
 * Budget display formatting for the run header and status bar. Pure.
 *
 * A continuation run shares a lineage budget root: the run-level count covers
 * only this run, while `lineage_agent_generation_request_count` accumulates
 * across the chain. When they differ we show both so the lineage consumption
 * is never mistaken for a fresh budget.
 */
import type { PreRunSummaryWire } from '../../api/types'

export interface BudgetDisplay {
  /** Request usage for this run; an absent cap is shown explicitly. */
  current: string
  /** Lineage totals, only when the lineage count differs from the run count. */
  lineage: string | null
  /** Remaining requests when the user configured a finite cap. */
  remaining: number | null
}

export function budgetDisplay(
  used: number,
  max: number | null,
  lineageUsed: number,
): BudgetDisplay {
  if (max === null) {
    return {
      current: `${used} / 无上限`,
      lineage: lineageUsed !== used ? `谱系累计 ${lineageUsed} / 无上限` : null,
      remaining: null,
    }
  }
  return {
    current: `${used} / ${max}`,
    lineage: lineageUsed !== used ? `谱系累计 ${lineageUsed} / ${max}` : null,
    remaining: max - used,
  }
}

/**
 * §14.1 cost-facts line for the run header: node count, models, request cap
 * (explicit "无上限" when absent), concurrency, and writer-node ids.
 * Numbers/ids are rendered in font-mono by the caller.
 */
export function preRunSummaryLine(summary: PreRunSummaryWire): string {
  const models = summary.models.length > 0 ? summary.models.join(', ') : '—'
  const cap =
    summary.max_agent_generation_requests === null
      ? '无上限'
      : `${summary.max_agent_generation_requests} 次请求`
  const writers = summary.writer_node_ids.length > 0 ? summary.writer_node_ids.join(', ') : '无'
  return `节点 ${summary.node_count} · 模型 ${models} · 上限 ${cap} · 并行度 ${summary.max_concurrency} · 写入节点：${writers}`
}
