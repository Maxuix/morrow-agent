import { describe, expect, it } from 'vitest'
import { outcomeSummary } from './outcomePresentation'

describe('outcome presentation', () => {
  it('uses readable states for service fallback summaries', () => {
    expect(outcomeSummary('TaskRun task_example is accepted.', 'accepted')).toBe('已验收')
    expect(outcomeSummary("Workflow 'build' closed: node_failed.", 'failed')).toBe('步骤执行失败')
    expect(outcomeSummary("Workflow 'build' completed with result succeeded.", 'ready_for_acceptance')).toBe('待验收')
    expect(outcomeSummary("Workflow 'build' completed with result needs_revision.", 'failed')).toBe('需要修改')
    expect(outcomeSummary("Workflow 'build' requires revision.", 'failed')).toBe('需要修改')
    expect(outcomeSummary("Workflow 'build' was abandoned while blocked.", 'cancelled')).toBe('任务已放弃')
  })

  it('preserves actual answers, formatting, and text that only mentions a fallback', () => {
    const answer = '# 结果\n\n- 完成\n- 保留换行\n\nTaskRun task_example is accepted.'
    expect(outcomeSummary(answer, 'accepted')).toBe(answer)
    expect(outcomeSummary('node_failed: unable to write output', 'failed')).toBe('node_failed: unable to write output')
  })

  it('never turns an unknown or failed outcome into success', () => {
    expect(outcomeSummary("Workflow 'build' closed: unknown_reason.", 'failed')).toBe('失败：unknown_reason')
    expect(outcomeSummary('', 'new_status')).toBe('任务已结束')
  })
})
