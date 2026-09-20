import { useEffect, useState } from 'react'
import type { WorkflowBudgetWire } from '../../api/types'

export type NumericFieldKind = 'positive-integer' | 'positive-number'

export interface ParsedNullableNumber {
  value: number | null
  error: string | null
}

export function parseNullableNumber(
  raw: string,
  kind: NumericFieldKind,
): ParsedNullableNumber {
  const value = raw.trim()
  if (value === '') return { value: null, error: null }
  if (!/^(?:\d+\.?\d*|\.\d+)$/u.test(value)) {
    return { value: null, error: kind === 'positive-integer' ? '请输入正整数，或留空。' : '请输入正数，或留空。' }
  }
  const number = Number(value)
  if (!Number.isFinite(number) || number <= 0) {
    return { value: null, error: kind === 'positive-integer' ? '请输入大于 0 的正整数。' : '请输入大于 0 的有限数。' }
  }
  if (kind === 'positive-integer' && (!Number.isSafeInteger(number) || !Number.isInteger(number))) {
    return { value: null, error: '请输入不超过安全范围的正整数。' }
  }
  return { value: number, error: null }
}

function displayNumber(value: number | null): string {
  return value === null ? '' : String(value)
}

export function NullableNumberField({
  label,
  value,
  kind,
  help,
  disabled,
  onChange,
}: {
  label: string
  value: number | null
  kind: NumericFieldKind
  help: string
  disabled: boolean
  onChange: (value: number | null) => void
}) {
  const [text, setText] = useState(() => displayNumber(value))
  const [error, setError] = useState<string | null>(null)
  const [focused, setFocused] = useState(false)

  useEffect(() => {
    if (!focused) {
      setText(displayNumber(value))
      setError(null)
    }
  }, [focused, value])

  function update(raw: string) {
    setText(raw)
    const parsed = parseNullableNumber(raw, kind)
    setError(parsed.error)
    if (parsed.error === null) onChange(parsed.value)
  }

  return (
    <label className="workflow-field">
      <span>{label}</span>
      <input
        aria-label={label}
        className="editor-input"
        type="number"
        inputMode={kind === 'positive-integer' ? 'numeric' : 'decimal'}
        min="0"
        step={kind === 'positive-integer' ? '1' : 'any'}
        value={text}
        disabled={disabled}
        aria-invalid={error !== null}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onChange={event => update(event.target.value)}
      />
      {focused && <span className="workflow-field-help">{help}</span>}
      {error !== null && <span role="alert" className="workflow-field-error">{error}</span>}
    </label>
  )
}

export function RunSettings({
  budget,
  disabled,
  onChange,
}: {
  budget: WorkflowBudgetWire
  disabled: boolean
  onChange: (budget: WorkflowBudgetWire) => void
}) {
  const set = <K extends keyof WorkflowBudgetWire>(key: K, value: WorkflowBudgetWire[K]) => {
    onChange({ ...budget, [key]: value })
  }
  const customCount = [
    budget.max_agent_generation_requests,
    budget.default_node_max_agent_generation_requests,
    budget.admission_timeout_seconds,
    budget.max_concurrency === 1 ? null : budget.max_concurrency,
  ].filter(value => value !== null).length

  return (
    <details className="workflow-run-settings" open={customCount > 0}>
      <summary>
        <span>运行设置</span>
        <span className="workflow-summary-hint">{customCount === 0 ? '使用默认设置' : `已自定义 ${customCount} 项`}</span>
      </summary>
      <div className="workflow-run-settings-grid">
        <NullableNumberField
          label="整个流程的模型调用次数上限"
          value={budget.max_agent_generation_requests}
          kind="positive-integer"
          help="只计算 Agent 模型生成请求；留空表示未设置此项限制。"
          disabled={disabled}
          onChange={value => set('max_agent_generation_requests', value)}
        />
        <NullableNumberField
          label="每个步骤的默认模型调用上限"
          value={budget.default_node_max_agent_generation_requests}
          kind="positive-integer"
          help="节点未单独覆盖时使用；仍受流程总上限和助手上限约束。"
          disabled={disabled}
          onChange={value => set('default_node_max_agent_generation_requests', value)}
        />
        <NullableNumberField
          label="准入时限（秒）"
          value={budget.admission_timeout_seconds}
          kind="positive-number"
          help="启动后允许继续发起执行的窗口，不是单条请求超时或强制取消。"
          disabled={disabled}
          onChange={value => set('admission_timeout_seconds', value)}
        />
        <NullableNumberField
          label="最多同时执行的步骤"
          value={budget.max_concurrency}
          kind="positive-integer"
          help="默认 1；只有满足依赖和副作用条件的只读步骤才可能并行。"
          disabled={disabled}
          onChange={value => set('max_concurrency', value ?? 1)}
        />
      </div>

    </details>
  )
}
