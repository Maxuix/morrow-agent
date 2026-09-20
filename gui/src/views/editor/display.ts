import type {
  AgentDefinitionViewWire,
  AgentNodeSourceWire,
  WorkflowDefinitionSourceWire,
} from '../../api/types'

export const UNNAMED_STEP_TITLE = '未命名步骤'
export const UNNAMED_AGENT_TITLE = '未命名助手'
const DEFAULT_TITLE_LIMIT = 72

export interface StepDisplayModel {
  nodeId: string
  ordinal: number
  title: string
  fullTitle: string
  cardTitle: string
  duplicateTitle: boolean
  agentName: string
  agentAvailability: 'available' | 'revoked' | 'unpublished' | 'missing'
  versionId: string
  contentHash: string
}

function normalizedObjective(objective: string): string {
  return objective.replace(/\s+/gu, ' ').trim() || UNNAMED_STEP_TITLE
}

/** Pick the first non-empty semantic line without changing the stored objective. */
export function objectiveTitle(objective: string, limit = DEFAULT_TITLE_LIMIT): string {
  const line = objective
    .split(/\r?\n|\r/u)
    .map(value => value.replace(/\s+/gu, ' ').trim())
    .find(Boolean)
  return truncateDisplay(line ?? UNNAMED_STEP_TITLE, limit)
}

/** Truncate by Unicode code points so a surrogate pair is never split. */
export function truncateDisplay(value: string, limit = DEFAULT_TITLE_LIMIT): string {
  if (limit < 2) return Array.from(value).slice(0, Math.max(0, limit)).join('')
  const characters = Array.from(value)
  return characters.length >= limit
    ? `${characters.slice(0, limit - 1).join('')}…`
    : value
}

function agentFor(
  node: AgentNodeSourceWire,
  agents: readonly AgentDefinitionViewWire[],
): Pick<StepDisplayModel, 'agentName' | 'agentAvailability'> {
  const definition = agents.find(item => item.definition_id === node.agent_definition_ref.definition_id)
  if (definition === undefined) {
    return { agentName: UNNAMED_AGENT_TITLE, agentAvailability: 'missing' }
  }
  const name = definition.source?.name ?? definition.published_version?.source.name ?? UNNAMED_AGENT_TITLE
  if (definition.revoked) return { agentName: name, agentAvailability: 'revoked' }
  if (definition.published_version === null) return { agentName: name, agentAvailability: 'unpublished' }
  return { agentName: name, agentAvailability: 'available' }
}

/**
 * Build card-facing labels from real Source fields. Node identity, version and
 * hash remain technical details and are never used as the card title.
 */
export function buildStepDisplayModels(
  source: WorkflowDefinitionSourceWire,
  agents: readonly AgentDefinitionViewWire[],
  limit = DEFAULT_TITLE_LIMIT,
): StepDisplayModel[] {
  const rows = source.nodes.map((node, index) => {
    const fullTitle = normalizedObjective(node.task_contract.objective)
    const title = objectiveTitle(node.task_contract.objective, limit)
    return {
      node,
      ordinal: index + 1,
      title,
      fullTitle,
      ...agentFor(node, agents),
    }
  })
  const counts = new Map<string, number>()
  for (const row of rows) counts.set(row.title, (counts.get(row.title) ?? 0) + 1)
  return rows.map(row => ({
    nodeId: row.node.node_id,
    ordinal: row.ordinal,
    title: row.title,
    fullTitle: row.fullTitle,
    cardTitle: counts.get(row.title)! > 1 ? `步骤 ${row.ordinal} · ${row.title}` : row.title,
    duplicateTitle: counts.get(row.title)! > 1,
    agentName: row.agentName,
    agentAvailability: row.agentAvailability,
    versionId: row.node.agent_definition_ref.version_id,
    contentHash: row.node.agent_definition_ref.content_hash,
  }))
}
