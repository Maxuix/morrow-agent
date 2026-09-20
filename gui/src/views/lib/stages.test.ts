import { describe, expect, it } from 'vitest'
import type { StageFrame } from '../../api/chat'
import { elapsedText, liveStageFrames, RUN_ACTIVE, stageText } from './stages'

const frame = (over: Partial<StageFrame> = {}): StageFrame => ({
  workflow_run_id: 'wrun_1', node_run_id: 'nrun_1', node_id: 'gamma', stage: 'awaiting_model', ts: null, ...over,
})

describe('run stage copy', () => {
  it('labels factual stages without inventing progress', () => {
    expect(stageText(frame())).toBe('等待模型响应')
    expect(stageText(frame({stage: 'model_responding'}))).toBe('正在生成回复')
    expect(stageText(frame({stage: 'tool_preparing'}))).toBe('已收到工具调用片段，正在准备调用')
    expect(stageText(frame({stage: 'tool_running', tool: 'read', ordinal: 2, total: 3}))).toBe('正在执行工具 · read（2/3）')
    expect(stageText(frame({stage: 'tool_result', tool: 'bash', ok: false}))).toBe('工具调用完成 · bash · 失败')
    expect(stageText(frame({stage: 'retrying', retry_delay_seconds: 4.2}))).toBe('模型请求重试等待中（约 4 秒后重试）')
    expect(stageText(frame({stage: 'compacting'}))).toBe('正在整理上下文')
  })
  it('hides elapsed spans without a parsable server timestamp', () => {
    expect(elapsedText(null, 1000)).toBe('')
    expect(elapsedText('not-a-date', 1000)).toBe('')
    expect(elapsedText('2026-01-01T00:00:00Z', Date.parse('2026-01-01T00:00:59Z'))).toBe('59 秒')
    expect(elapsedText('2026-01-01T00:00:00Z', Date.parse('2026-01-01T00:01:05Z'))).toBe('1 分 5 秒')
  })
  it('keeps the newest frames of active runs and hides terminal runs', () => {
    const runs = new Map<string, {run: {status: string}}>([
      ['wrun_live', {run: {status: 'running'}}],
      ['wrun_done', {run: {status: 'succeeded'}}],
    ])
    const stages = [
      frame({workflow_run_id: 'wrun_done', node_run_id: 'n1', ts: '2026-01-01T00:00:01Z'}),
      frame({workflow_run_id: 'wrun_live', node_run_id: 'n2', ts: '2026-01-01T00:00:02Z'}),
      frame({workflow_run_id: 'wrun_unknown', node_run_id: 'n3', ts: '2026-01-01T00:00:03Z'}),
      frame({workflow_run_id: 'wrun_live', node_run_id: 'n4', ts: '2026-01-01T00:00:04Z'}),
    ]
    const kept = liveStageFrames(stages, runs)
    expect(kept.map(stage => stage.node_run_id)).toEqual(['n2', 'n3', 'n4'])
    for (const status of RUN_ACTIVE) expect(RUN_ACTIVE.has(status)).toBe(true)
  })
})
