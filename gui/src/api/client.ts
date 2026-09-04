/**
 * Typed client for the Morrow Core API (`/v1`).
 *
 * Same-origin in production (the Core server serves the prebuilt bundle) and
 * the Vite dev proxy forwards `/v1`, so `baseUrl` is always the relative `''`.
 * Every call carries `Authorization: Bearer <token>`; the token itself is
 * bootstrapped from the URL fragment (`#token=...`) into sessionStorage and
 * the fragment is scrubbed so it cannot leak into copy-pasted URLs.
 *
 * Editor mutations use the same authenticated JSON API and Core command bus.
 */
import type {
  ReplanViewWire,  GraphPlanningRequestWire,
  TaskGraphDraftWire,
  OrchestrationPolicyWire,
  OrchestrationPoliciesWire,
  AgentDefinitionSourceWire,
  ApprovalDecisionWire,
  ApprovalResolveResultWire,
  FutureGraphPatchWire,
  PatchApplyResultWire,
  PatchValidationWire,
  PreRunSummaryWire,
  RerunResultWire,
  RunControlResultWire,
  AgentDefinitionVersionWire,
  AgentDefinitionViewWire,
  AgentRunEnvelopeWire,
  AgentRunObservationWire,
  ApprovalWire,
  ApprovalsListWire,
  ArtifactsPageWire,
  EventsPageWire,
  MetaWire,
  ModelRefWire,
  NodeViewEnvelopeWire,
  NodeViewWire,
  RunViewEnvelopeWire,
  RunViewWire,
  SessionEnvelopeWire,
  SessionWire,
  SessionsPageWire,
  SnapshotWire,
  ArtifactContractCatalogWire,
  ProviderCatalogWire,
  SkillCatalogWire,
  ToolCatalogWire,
  WorkflowDefinitionSourceWire,
  WorkflowDefinitionViewWire,
  WorkflowDraftDiagnosticWire,
  WorkflowDraftViewWire,
  TaskEnvelopeWire,
  TaskRunWire,
  TasksPageWire,
} from './types'

export const TOKEN_STORAGE_KEY = 'morrow.sessionToken'

let sessionToken: string | null = null
let tokenBootstrapped = false

/**
 * Read `#token=...` from the URL fragment once, persist it to sessionStorage,
 * and scrub the fragment from the URL bar. Safe to call repeatedly.
 */
export function bootstrapSessionToken(): string | null {
  if (tokenBootstrapped) return sessionToken
  tokenBootstrapped = true
  if (typeof window === 'undefined') return null
  const match = /#token=([^&]+)/.exec(window.location.hash)
  if (match) {
    sessionToken = decodeURIComponent(match[1])
    window.sessionStorage.setItem(TOKEN_STORAGE_KEY, sessionToken)
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
  } else {
    sessionToken = window.sessionStorage.getItem(TOKEN_STORAGE_KEY)
  }
  return sessionToken
}

export function getToken(): string | null {
  return bootstrapSessionToken()
}

export function hasToken(): boolean {
  return getToken() !== null
}

/** Test hook: forget a bootstrapped token so bootstrap re-reads the URL. */
export function resetSessionTokenForTests(): void {
  sessionToken = null
  tokenBootstrapped = false
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryAfterSeconds: number | null = null,
    readonly diagnostics: WorkflowDraftDiagnosticWire[] = [],
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export interface ApiClientOptions {
  /** Always `''` (relative, same-origin); injectable only for tests. */
  baseUrl: string
  token: string
  fetchImpl?: typeof fetch
}

// Type aliases (not interfaces) so they stay assignable to the query()
// Record parameter without an explicit index signature.
export type ListParams = {
  cursor?: string
  limit?: number
}

export type ListArtifactsParams = ListParams & {
  session_id?: string
  task_run_id?: string
}

export class ApiClient {
  private readonly baseUrl: string
  private readonly token: string
  private readonly fetchImpl: typeof fetch

  constructor({ baseUrl, token, fetchImpl }: ApiClientOptions) {
    this.baseUrl = baseUrl.replace(/\/+$/, '')
    this.token = token
    this.fetchImpl = fetchImpl ?? ((...args) => fetch(...args))
  }

  meta(): Promise<MetaWire> {
    return this.get('/v1/meta')
  }

  snapshot(): Promise<SnapshotWire> {
    return this.get('/v1/snapshot')
  }

  events(after: number, limit = 100): Promise<EventsPageWire> {
    return this.get(`/v1/events${query({ after, limit })}`)
  }

  listSessions(params: ListParams = {}): Promise<SessionsPageWire> {
    return this.get(`/v1/sessions${query(params)}`)
  }

  listTasks(sessionId: string, params: ListParams = {}): Promise<TasksPageWire> {
    return this.get(`/v1/sessions/${encodeURIComponent(sessionId)}/tasks${query(params)}`)
  }

  async getSession(sessionId: string): Promise<SessionWire> {
    const envelope = await this.get<SessionEnvelopeWire>(
      `/v1/sessions/${encodeURIComponent(sessionId)}`,
    )
    return envelope.session
  }

  async getTask(taskRunId: string): Promise<TaskRunWire> {
    const envelope = await this.get<TaskEnvelopeWire>(`/v1/tasks/${encodeURIComponent(taskRunId)}`)
    return envelope.task
  }

  async getRunView(runId: string): Promise<RunViewWire> {
    const envelope = await this.get<RunViewEnvelopeWire>(
      `/v1/workflow-runs/${encodeURIComponent(runId)}`,
    )
    return envelope.view
  }

  async getNodeView(runId: string, nodeRunId: string): Promise<NodeViewWire> {
    const envelope = await this.get<NodeViewEnvelopeWire>(
      `/v1/workflow-runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeRunId)}`,
    )
    return envelope.view
  }

  async listApprovals(pendingOnly = true): Promise<ApprovalWire[]> {
    const envelope = await this.get<ApprovalsListWire>(
      `/v1/approvals${query({ pending: pendingOnly ? 'true' : 'false' })}`,
    )
    return envelope.approvals
  }

  listArtifacts(params: ListArtifactsParams = {}): Promise<ArtifactsPageWire> {
    return this.get(`/v1/artifacts${query(params)}`)
  }

  async getAgentRun(agentRunId: string): Promise<AgentRunObservationWire> {
    const envelope = await this.get<AgentRunEnvelopeWire>(
      `/v1/agent-runs/${encodeURIComponent(agentRunId)}`,
    )
    return envelope.observation
  }

  async listAgentDefinitions(): Promise<AgentDefinitionViewWire[]> {
    const envelope = await this.get<{ agent_definitions: AgentDefinitionViewWire[] }>(
      '/v1/catalog/agent-definitions?limit=100',
    )
    return envelope.agent_definitions
  }

  async getAgentDefinition(definitionId: string): Promise<AgentDefinitionViewWire> {
    const envelope = await this.get<{ agent_definition: AgentDefinitionViewWire }>(
      `/v1/catalog/agent-definitions/${encodeURIComponent(definitionId)}`,
    )
    return envelope.agent_definition
  }

  async getAgentVersion(versionId: string): Promise<AgentDefinitionVersionWire> {
    const envelope = await this.get<{ agent_version: { version: AgentDefinitionVersionWire } }>(
      `/v1/catalog/agent-versions/${encodeURIComponent(versionId)}`,
    )
    return envelope.agent_version.version
  }

  async listWorkflowDefinitions(): Promise<WorkflowDefinitionViewWire[]> {
    const envelope = await this.get<{ workflow_definitions: WorkflowDefinitionViewWire[] }>(
      '/v1/catalog/workflow-definitions?limit=100',
    )
    return envelope.workflow_definitions
  }

  async listWorkflowDrafts(): Promise<WorkflowDraftViewWire[]> {
    const envelope = await this.get<{ workflow_drafts: WorkflowDraftViewWire[] }>(
      '/v1/workflow-drafts?limit=100',
    )
    return envelope.workflow_drafts
  }

  async planWorkflow(planning: GraphPlanningRequestWire, commandId: string): Promise<TaskGraphDraftWire> {
    const envelope = await this.post<{ result: TaskGraphDraftWire }>('/v1/workflow-planner', {
      planning, command_id: commandId,
    })
    return envelope.result
  }

  orchestrationPolicies(): Promise<OrchestrationPoliciesWire> {
    return this.get('/v1/orchestration-policies')
  }

  async putOrchestrationPolicy(policy: OrchestrationPolicyWire, expectedRevision: number, commandId: string): Promise<OrchestrationPoliciesWire> {
    const envelope = await this.put<{ result: OrchestrationPoliciesWire }>('/v1/orchestration-policies', {
      policy, expected_revision: expectedRevision, command_id: commandId,
    })
    return envelope.result
  }

  async createWorkflowDraft(
    source: WorkflowDefinitionSourceWire,
    expectedSourceRevision: number,
    commandId: string,
    draftId: string,
  ): Promise<WorkflowDraftViewWire> {
    const envelope = await this.post<{
      result: { workflow_draft: WorkflowDraftViewWire }
    }>('/v1/workflow-drafts', {
      command_id: commandId,
      draft_id: draftId,
      source,
      expected_source_revision: expectedSourceRevision,
    })
    return envelope.result.workflow_draft
  }

  async updateWorkflowDraft(
    draftId: string,
    source: WorkflowDefinitionSourceWire,
    expectedRowVersion: number,
    commandId: string,
  ): Promise<WorkflowDraftViewWire> {
    const envelope = await this.put<{
      result: { workflow_draft: WorkflowDraftViewWire }
    }>(`/v1/workflow-drafts/${encodeURIComponent(draftId)}`, {
      command_id: commandId,
      source,
      expected_row_version: expectedRowVersion,
    })
    return envelope.result.workflow_draft
  }

  async freezeWorkflowDraft(
    draftId: string,
    expectedRowVersion: number,
    commandId: string,
  ): Promise<{ workflow_draft: WorkflowDraftViewWire; workflow_revision: Record<string, unknown> }> {
    const envelope = await this.post<{
      result: {
        workflow_draft: WorkflowDraftViewWire
        workflow_revision: Record<string, unknown>
      }
    }>(`/v1/workflow-drafts/${encodeURIComponent(draftId)}/freeze`, {
      command_id: commandId,
      expected_row_version: expectedRowVersion,
    })
    return envelope.result
  }

  async createAgentDefinition(
    source: AgentDefinitionSourceWire,
    expectedSourceRevision: number,
    commandId: string,
  ): Promise<AgentDefinitionViewWire> {
    const envelope = await this.post<{
      result: { agent_definition: AgentDefinitionViewWire }
    }>('/v1/agent-definitions', {
      command_id: commandId,
      source,
      expected_source_revision: expectedSourceRevision,
    })
    return envelope.result.agent_definition
  }

  async updateAgentDefinition(
    definitionId: string,
    source: AgentDefinitionSourceWire,
    expectedSourceRevision: number,
    commandId: string,
  ): Promise<AgentDefinitionViewWire> {
    const envelope = await this.put<{
      result: { agent_definition: AgentDefinitionViewWire }
    }>(`/v1/agent-definitions/${encodeURIComponent(definitionId)}`, {
      command_id: commandId,
      source,
      expected_source_revision: expectedSourceRevision,
    })
    return envelope.result.agent_definition
  }

  async publishAgentDefinition(
    definitionId: string,
    expectedHeadRevision: number,
    commandId: string,
  ): Promise<AgentDefinitionViewWire> {
    const envelope = await this.post<{
      result: { agent_definition: AgentDefinitionViewWire }
    }>(`/v1/agent-definitions/${encodeURIComponent(definitionId)}/publish`, {
      command_id: commandId,
      expected_head_revision: expectedHeadRevision,
    })
    return envelope.result.agent_definition
  }

  async editorCatalogs(): Promise<{
    providers: ProviderCatalogWire[]
    active_model: { provider_id: string; model_id: string } | null
    skills: SkillCatalogWire[]
    tools: ToolCatalogWire[]
    contracts: ArtifactContractCatalogWire[]
  }> {
    const [providers, skills, tools, contracts] = await Promise.all([
      this.get<{ providers: ProviderCatalogWire[]; active_model: ModelRefWire | null }>(
        '/v1/catalog/providers',
      ),
      this.get<{ skills: SkillCatalogWire[] }>('/v1/catalog/skills?limit=100'),
      this.get<{ tools: ToolCatalogWire[] }>('/v1/catalog/tools'),
      this.get<{ contracts: ArtifactContractCatalogWire[] }>('/v1/catalog/artifact-contracts'),
    ])
    return {
      providers: providers.providers,
      active_model: providers.active_model,
      skills: skills.skills,
      tools: tools.tools,
      contracts: contracts.contracts,
    }
  }

  // Run control ------------------------------------------------------------
  // The same commands the CLI invokes; `commandId` makes retries idempotent.

  async pauseRun(runId: string, commandId: string): Promise<RunControlResultWire> {
    const envelope = await this.post<{ result: RunControlResultWire }>(
      `/v1/workflow-runs/${encodeURIComponent(runId)}/pause`,
      { command_id: commandId },
    )
    return envelope.result
  }

  async resumeRun(runId: string, commandId: string): Promise<RunControlResultWire> {
    const envelope = await this.post<{ result: RunControlResultWire }>(
      `/v1/workflow-runs/${encodeURIComponent(runId)}/resume`,
      { command_id: commandId, drive: true },
    )
    return envelope.result
  }

  async cancelRun(runId: string, commandId: string): Promise<RunControlResultWire> {
    const envelope = await this.post<{ result: RunControlResultWire }>(
      `/v1/workflow-runs/${encodeURIComponent(runId)}/cancel`,
      { command_id: commandId },
    )
    return envelope.result
  }

  async rerunRun(runId: string, full: boolean, commandId: string): Promise<RerunResultWire> {
    const envelope = await this.post<{ result: RerunResultWire }>(
      `/v1/workflow-runs/${encodeURIComponent(runId)}/rerun`,
      { command_id: commandId, full },
    )
    return envelope.result
  }

  async listReplans(runId: string): Promise<ReplanViewWire[]> {
    const result = await this.get<{ proposals: ReplanViewWire[] }>(`/v1/workflow-runs/${encodeURIComponent(runId)}/replans`)
    return result.proposals
  }

  async decideReplan(proposalId: string, approved: boolean, expectedRowVersion: number, commandId: string): Promise<ReplanViewWire> {
    const result = await this.post<{ result: ReplanViewWire }>(`/v1/replans/${encodeURIComponent(proposalId)}/decide`, {
      approved, expected_row_version: expectedRowVersion, command_id: commandId,
    })
    return result.result
  }

  async validatePatch(patch: FutureGraphPatchWire): Promise<PatchValidationWire> {
    const envelope = await this.post<{ result: PatchValidationWire }>('/v1/patches/validate', {
      patch,
    })
    return envelope.result
  }

  async applyPatch(patch: FutureGraphPatchWire, commandId: string): Promise<PatchApplyResultWire> {
    const envelope = await this.post<{ result: PatchApplyResultWire }>('/v1/patches/apply', {
      command_id: commandId,
      patch,
    })
    return envelope.result
  }

  async resolveApproval(
    approvalId: string,
    decision: ApprovalDecisionWire,
    commandId: string,
  ): Promise<ApprovalResolveResultWire> {
    const envelope = await this.post<{ result: ApprovalResolveResultWire }>(
      `/v1/approvals/${encodeURIComponent(approvalId)}/resolve`,
      {
        command_id: commandId,
        approved: decision !== 'deny',
        decision,
      },
    )
    return envelope.result
  }

  async runPreview(revisionId: string): Promise<PreRunSummaryWire> {
    const envelope = await this.get<{ pre_run_summary: PreRunSummaryWire }>(
      `/v1/catalog/workflow-revisions/${encodeURIComponent(revisionId)}/run-preview`,
    )
    return envelope.pre_run_summary
  }

  async acceptTask(taskRunId: string, expectedRowVersion: number, commandId: string): Promise<TaskRunWire> {
    const envelope = await this.post<{ result: { task: TaskRunWire } }>(
      `/v1/tasks/${encodeURIComponent(taskRunId)}/accept`,
      { command_id: commandId, expected_row_version: expectedRowVersion },
    )
    return envelope.result.task
  }

  async resumeTask(taskRunId: string, expectedRowVersion: number, commandId: string): Promise<TaskRunWire> {
    const envelope = await this.post<{ result: { task: TaskRunWire } }>(
      `/v1/tasks/${encodeURIComponent(taskRunId)}/resume`,
      { command_id: commandId, expected_row_version: expectedRowVersion },
    )
    return envelope.result.task
  }

  private async get<T>(path: string): Promise<T> {
    return this.request('GET', path)
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    return this.request('POST', path, body)
  }

  private async put<T>(path: string, body: unknown): Promise<T> {
    return this.request('PUT', path, body)
  }

  private async request<T>(method: 'GET' | 'POST' | 'PUT', path: string, body?: unknown): Promise<T> {
    let response: Response
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers: {
          authorization: `Bearer ${this.token}`,
          accept: 'application/json',
          ...(body === undefined ? {} : { 'content-type': 'application/json' }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch (error) {
      throw new ApiError(
        0,
        'network_error',
        error instanceof Error ? error.message : 'network request failed',
      )
    }
    if (!response.ok) {
      throw await toApiError(response)
    }
    return (await response.json()) as T
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = 'unknown'
  let message = `request failed with HTTP ${response.status}`
  let diagnostics: WorkflowDraftDiagnosticWire[] = []
  try {
    const body: unknown = await response.json()
    if (body !== null && typeof body === 'object' && 'error' in body) {
      const error = (body as { error: unknown }).error
      if (error !== null && typeof error === 'object') {
        const { code: bodyCode, message: bodyMessage, diagnostics: bodyDiagnostics } = error as {
          code?: unknown
          message?: unknown
          diagnostics?: unknown
        }
        if (typeof bodyCode === 'string') code = bodyCode
        if (typeof bodyMessage === 'string') message = bodyMessage
        if (Array.isArray(bodyDiagnostics)) {
          diagnostics = bodyDiagnostics.filter(isWorkflowDraftDiagnostic)
        }
      }
    }
  } catch {
    // Non-JSON error body; keep the status-derived defaults.
  }
  const retryAfter = response.headers.get('retry-after')
  const retryAfterSeconds = retryAfter === null ? null : Number(retryAfter)
  return new ApiError(
    response.status,
    code,
    message,
    retryAfterSeconds !== null && Number.isFinite(retryAfterSeconds) ? retryAfterSeconds : null,
    diagnostics,
  )
}

function isWorkflowDraftDiagnostic(value: unknown): value is WorkflowDraftDiagnosticWire {
  if (value === null || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return (
    (item.severity === 'error' || item.severity === 'warning') &&
    typeof item.code === 'string' &&
    typeof item.message === 'string' &&
    (item.node_id === null || typeof item.node_id === 'string') &&
    (item.edge_id === null || typeof item.edge_id === 'string')
  )
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value))
  }
  const text = search.toString()
  return text === '' ? '' : `?${text}`
}
