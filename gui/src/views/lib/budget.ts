/**
 * Budget display formatting for the run header and status bar. Pure.
 *
 * A continuation run shares a lineage budget root: the run-level count covers
 * only this run, while `lineage_agent_generation_request_count` accumulates
 * across the chain. When they differ we show both so the lineage consumption
 * is never mistaken for a fresh budget.
 */

export interface BudgetDisplay {
  /** `used / max` for the run itself, mono-rendered by callers. */
  current: string
  /** Lineage totals, only when the lineage count differs from the run count. */
  lineage: string | null
  /** Remaining requests against the run's budget snapshot. */
  remaining: number
}

export function budgetDisplay(used: number, max: number, lineageUsed: number): BudgetDisplay {
  return {
    current: `${used} / ${max}`,
    lineage: lineageUsed !== used ? `谱系累计 ${lineageUsed} / ${max}` : null,
    remaining: max - used,
  }
}
