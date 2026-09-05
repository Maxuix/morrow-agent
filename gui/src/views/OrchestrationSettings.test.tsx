import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { OrchestrationEligibility } from './OrchestrationSettings'

describe('orchestration eligibility display', () => {
  it('separates promoted evidence from saved authorization', () => {
    const html = renderToStaticMarkup(<OrchestrationEligibility eligibility={[
      { task_type: 'implementation', promoted: true, auto_run_eligible: false, auto_replan_eligible: false },
      { task_type: 'research', promoted: true, auto_run_eligible: true, auto_replan_eligible: true },
    ]} />)
    expect(html).toContain('已推广任务类型：implementation、research')
    expect(html).toContain('当前已保存策略允许自动运行：research')
    expect(html).toContain('当前已保存策略允许低风险自动重规划：research')
  })

  it('shows wildcard replan authorization without claiming promotion', () => {
    const html = renderToStaticMarkup(<OrchestrationEligibility eligibility={[
      { task_type: 'general', promoted: false, auto_run_eligible: false, auto_replan_eligible: true },
    ]} />)
    expect(html).toContain('已推广任务类型：无')
    expect(html).toContain('当前已保存策略允许自动运行：无')
    expect(html).toContain('当前已保存策略允许低风险自动重规划：general')
  })
})
