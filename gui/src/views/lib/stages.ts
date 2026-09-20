import type { StageFrame } from '../../api/chat'

/** Content-free stage labels; unknown codes stay as-is, never invented progress. */
const STAGE_LABELS: Record<string, string> = {
  awaiting_model: '等待模型响应',
  thinking: '正在思考',
  model_responding: '正在生成回复',
  tool_preparing: '已收到工具调用片段，正在准备调用',
  tool_running: '正在执行工具',
  tool_result: '工具调用完成',
  retrying: '模型请求重试等待中',
  compacting: '正在整理上下文',
  compacted: '上下文整理完成',
}

/** Pure stage rendering; tests pin the factual, non-fabricating copy. */
export function stageText(stage: StageFrame): string {
  const base = STAGE_LABELS[stage.stage] ?? stage.stage
  if (stage.stage === 'tool_running' && stage.tool) {
    const place = stage.total !== null && stage.total !== undefined && stage.total > 1 ? `（${stage.ordinal ?? '?'}/${stage.total}）` : ''
    return `${base} · ${stage.tool}${place}`
  }
  if (stage.stage === 'tool_result') {
    return `${base} · ${stage.tool ?? ''}${stage.ok === false ? ' · 失败' : ''}`.replace(/\s+$/, '')
  }
  if (stage.stage === 'retrying' && stage.retry_delay_seconds !== null && stage.retry_delay_seconds !== undefined) {
    return `${base}（约 ${Math.round(stage.retry_delay_seconds)} 秒后重试）`
  }
  return base
}

/** Wall-clock elapsed from a server timestamp; absent/invalid hides the span. */
export function elapsedText(ts: string | null | undefined, now: number): string {
  if (!ts) return ''
  const started = Date.parse(ts)
  if (!Number.isFinite(started)) return ''
  const seconds = Math.max(0, Math.round((now - started) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
}

export const RUN_ACTIVE = new Set(['queued', 'running', 'draining'])

/**
 * Stage frames kept for the execution-region fallback: one line per node,
 * oldest first, terminal runs hidden. Formerly the RunStageCard projection —
 * the card is gone; this survives as the overview's no-activity fallback.
 */
export function liveStageFrames(stages: StageFrame[], runs: Map<string, {run: {status: string}}>): StageFrame[] {
  return [...stages]
    .sort((a, b) => (a.ts ?? '').localeCompare(b.ts ?? ''))
    .slice(-4)
    .filter(stage => {
      const run = runs.get(stage.workflow_run_id)?.run
      return run === undefined || RUN_ACTIVE.has(run.status)
    })
}
