/**
 * Budget display formatting for the run header and status bar. Pure.
 *
 * A continuation run shares a lineage budget root: the run-level count covers
 * only this run, while `lineage_agent_generation_request_count` accumulates
 * across the chain. When they differ we show both so the lineage consumption
 * is never mistaken for a fresh budget.
 */

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
