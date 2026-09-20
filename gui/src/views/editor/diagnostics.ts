import type { WorkflowDraftDiagnosticWire } from '../../api/types'

interface DiagnosticCopy {
  title: string
  guidance: string
}

const DIAGNOSTIC_COPY: Record<string, DiagnosticCopy> = {
  optional_removed: {
    title: '可选工具不可用或未获授权',
    guidance: '该工具不会进入冻结版本；如步骤需要它，请检查工具目录、策略和助手权限。',
  },
  agent_version_unresolved: {
    title: '助手精确版本找不到',
    guidance: '保留当前引用以便核对；请从已发布助手版本中重新选择，或恢复对应版本。',
  },
  agent_version_revoked: {
    title: '助手版本已撤销',
    guidance: '历史引用仍可见；请选择当前允许的新版本后再检查。',
  },
  agent_definition_disabled: {
    title: '助手定义已停用',
    guidance: '启用该助手定义，或改用仍可用的已发布助手版本。',
  },
  provider_model_disabled: {
    title: 'Provider / Model 当前不可用',
    guidance: '检查模型连接和目录状态；编辑器不会替换用户指定的模型。',
  },
  skill_disabled: {
    title: 'Skill 版本不可用',
    guidance: '启用或恢复所引用的 Skill 版本，或在助手定义中换用可用版本。',
  },
  graph_cycle: {
    title: '流程存在循环依赖',
    guidance: '删除造成循环的控制依赖；工作流必须保持 DAG。',
  },
  graph_disconnected: {
    title: '步骤未连接到主流程',
    guidance: '添加明确的控制依赖，或删除不属于本流程的步骤。',
  },
  edge_endpoint_invalid: {
    title: '连接端点不存在或相同',
    guidance: '删除这条损坏连接，或从现有步骤重新建立合法连接。',
  },
  structure_invalid: {
    title: '流程结构或结果引用无效',
    guidance: '检查步骤、输入传递和最终输出；失效引用不会被自动替换。',
  },
  invoking_session_shape_invalid: {
    title: '会话范围与流程形状不匹配',
    guidance: '多步骤流程必须使用独立会话；单节点流程才可使用发起任务的对话。',
  },
  access_mode_escalation: {
    title: '步骤权限超过助手上限',
    guidance: '降低步骤权限，或改用允许写入的助手版本；编辑器不会放宽助手上限。',
  },
  required_tool_denied: {
    title: '必需工具被策略或权限拒绝',
    guidance: '调整真实的工具策略或助手权限；仅在获得授权后重新检查。',
  },
  required_tool_absent: {
    title: '必需工具不在目录中',
    guidance: '先在工具目录提供该工具，或移除助手/步骤对它的必需声明。',
  },
  tool_not_declared: {
    title: '步骤使用了助手未声明的工具',
    guidance: '步骤只能收窄助手定义的工具集合，请回到助手定义补充或移除声明。',
  },
  tool_requirement_conflict: {
    title: '工具策略互相冲突',
    guidance: '解决助手定义与步骤覆盖之间的 required / forbidden 冲突。',
  },
  complete_patch_requires_write: {
    title: '完整补丁结果需要写入权限',
    guidance: '只有能记录工作区变更的写入步骤才能产出必需的 ImplementationPatch。',
  },
  unconsumed_outputs: {
    title: '必需结果未被使用或交付',
    guidance: '确认这是有意保留的结果，或把它传递给下游步骤/加入最终交付结果。',
  },
  independent_writers: {
    title: '多个步骤拥有写入权限',
    guidance: '调度器会串行化写入；如有依赖，请用控制边明确表达执行顺序。',
  },
  model_unavailable: {
    title: '没有可用模型',
    guidance: '配置当前工作区可用的 Provider / Model 后重新检查。',
  },
  uncapturable_host_bash: {
    title: 'Host 模式 bash 无法记录完整补丁',
    guidance: '改用可捕获变更的原生沙箱，或不要把 ImplementationPatch 设为完成所需结果。',
  },
  tool_reserved: {
    title: '工具名称属于内部机制',
    guidance: '内部机制工具不能由 Agent、Skill 或 Artifact 授权，请移除该声明。',
  },
}

export interface DiagnosticPresentation {
  severityLabel: string
  targetLabel: string
  title: string
  guidance: string
  detail: string
  toolName: string | null
}

export function uniqueDiagnostics(
  diagnostics: WorkflowDraftDiagnosticWire[],
): WorkflowDraftDiagnosticWire[] {
  const seen = new Set<string>()
  return diagnostics.filter(item => {
    const key = [item.severity, item.code, item.node_id ?? '', item.edge_id ?? '', item.message].join('\u0000')
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function safeUiText(value: unknown, fallback: string): string {
  const input = typeof value === 'string' && value.trim() !== '' ? value.trim() : fallback
  const withoutTraceback = input.replace(/Traceback \(most recent call last\):[\s\S]*/i, '详细异常已省略。')
  const withoutSecrets = withoutTraceback.replace(
    /((?:api[-_]?key|authorization|token|secret|password)\s*[:=]\s*)[^\s,;]+/gi,
    '$1已隐藏',
  )
  return withoutSecrets.length > 360 ? `${withoutSecrets.slice(0, 357)}…` : withoutSecrets
}

function toolNameFrom(message: string): string | null {
  const match = message.match(/(?:optional|required)\s+tool\s+['"`]?([A-Za-z0-9_.:/-]+)/i)
    ?? message.match(/\btool(?:_name)?\s*[:=]\s*['"`]?([A-Za-z0-9_.:/-]+)/i)
  return match?.[1] ?? null
}

export function presentDiagnostic(item: WorkflowDraftDiagnosticWire): DiagnosticPresentation {
  const copy = DIAGNOSTIC_COPY[item.code] ?? {
    title: '未识别的检查项',
    guidance: '请根据 Core 说明核对该问题；未知 code 不会被前端猜测或自动修复。',
  }
  const detail = safeUiText(item.message, 'Core 未提供进一步说明。')
  const toolName = toolNameFrom(detail)
  return {
    severityLabel: item.severity === 'error' ? '阻断' : '提醒',
    targetLabel: item.edge_id !== null
      ? `连接 ${safeUiText(item.edge_id, '未知连接')}`
      : item.node_id !== null
        ? `步骤 ${safeUiText(item.node_id, '未知步骤')}`
        : '整个工作流',
    title: copy.title,
    guidance: toolName === null ? copy.guidance : `${copy.guidance} 工具：${toolName}。`,
    detail,
    toolName,
  }
}

export function diagnosticSummary(diagnostics: WorkflowDraftDiagnosticWire[]): string {
  const unique = uniqueDiagnostics(diagnostics)
  const errors = unique.filter(item => item.severity === 'error').length
  const warnings = unique.length - errors
  if (errors === 0 && warnings === 0) return '没有检查问题'
  if (errors === 0) return `${warnings} 项提醒`
  return `${errors} 项阻断${warnings > 0 ? `，${warnings} 项提醒` : ''}`
}

export function safeErrorMessage(error: unknown, fallback: string): string {
  return safeUiText(error instanceof Error ? error.message : error, fallback)
}

export function diagnosticCopy(code: string): DiagnosticCopy | null {
  return DIAGNOSTIC_COPY[code] ?? null
}
