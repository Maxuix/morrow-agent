import { attachmentPath, type AttachmentWire, type FileSearch } from './attachments'
import type { ChatSettings, ChatSettingsView, ProviderSettingsView } from './settings'
export interface SessionMetadata {title:string;pinned:boolean;revision:number}
export interface WorkspaceEntry {workspace_id:string;path:string;display_name:string;available:boolean;last_used_at:string|null;git_root:string|null}
export interface WorkspaceList {items:WorkspaceEntry[];revision:number}
export interface WorkspaceResult {workspace:WorkspaceEntry;revision:number;disposition:string}
export interface DirectoryListing {roots?:string[];path?:string;parent?:string|null;items?:{name:string;path:string}[];truncated?:boolean}
import { chatPath, type ActivitySnapshot, type ChatCapabilities, type ChatSnapshot, type TimelinePage, type InteractionInput, type Receipt, type ChatSessionEnvelope, type RecoveryStatus, type RecoveryView } from './chat'
import type { ActivityRecovery } from './activity'
import type { NodeSteerReceipt } from './chat'
import type { ManagementCommand, ManagementQueries } from './management'
/**
 * Typed client for the Morrow Core API (`/v1`).
 *
 * Same-origin in production (the Core server serves the prebuilt bundle) and
 * the Vite dev proxy forwards `/v1`, so `baseUrl` is always the relative `''`.
 * A bearer token is optional for compatibility with headless `serve`; the
 * local GUI leaves it empty and relies on its same-origin loopback boundary.
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
  RerunResultWire,
  RunControlResultWire,
  AgentDefinitionVersionWire,
  AgentDefinitionViewWire,
  AgentRunEnvelopeWire,
  AgentRunObservationWire,
  ApprovalWire,
  ApprovalsListWire,
  ArtifactWire,
  EventsPageWire,
  MetaWire,
  ModelRefWire,
  NodeViewEnvelopeWire,
  NodeViewWire,
  RunViewEnvelopeWire,
  RecoveryEligibilityWire,
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
  TaskArtifactContentWire,
  TaskArtifactsWire,
  TaskPlanViewWire,
  PlanningOperationWire,
  PlanWorkflowRequestWire,
  PlanNodeEditWire,
  EditPlanRequestWire,
  StartWorkflowPlanRequestWire,
  WorkflowRunWire,
  AgentPresetCatalogWire,
  SelectionChoiceWire,
  WorkspaceFileInfoWire,
  WorkspaceFileReadWire,
  WorkspaceFileTreeWire,
  WorkspaceFileWriteWire,
  PreviewWire,
} from './types'

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
  workspaceId?: string
}

// Type aliases (not interfaces) so they stay assignable to the query()
// Record parameter without an explicit index signature.
export type ListParams = {
  cursor?: string
  limit?: number
}

export type TaskArtifactSelectors = {
  task_run_id?: string
  workflow_run_id?: string
  node_run_id?: string
}

export type WorkspaceFileTreeParams = {
  after?: string
  limit?: number
}

export class ApiClient {
  private readonly baseUrl: string
  private readonly token: string
  readonly workspaceId?: string
  private readonly fetchImpl: typeof fetch

  constructor({ baseUrl, token, fetchImpl, workspaceId }: ApiClientOptions) {
    this.baseUrl = baseUrl.replace(/\/+$/, '')
    this.token = token
    this.workspaceId = workspaceId
    this.fetchImpl = fetchImpl ?? ((...args) => fetch(...args))
  }

  scopePath(path: string): string {
    return this.workspaceId && path.startsWith('/v1/') && !path.startsWith('/v1/workspaces') && !path.startsWith('/v1/directories')
      ? `/v1/workspaces/${encodeURIComponent(this.workspaceId)}${path.slice(3)}` : path
  }
  workspaces(): Promise<WorkspaceList> { return this.get('/v1/workspaces') }
  directories(path?: string): Promise<DirectoryListing> { return this.get('/v1/directories' + query({path})) }
  registerWorkspace(body: {command_id:string;action:'open'|'create';path:string;name?:string;display_name?:string}): Promise<WorkspaceResult> { return this.post('/v1/workspaces',body) }
  manageWorkspace(workspace:string, action:string, body:{command_id:string;expected_revision:number;display_name?:string;path?:string}): Promise<WorkspaceResult> { return this.post(`/v1/workspaces/${encodeURIComponent(workspace)}/${action}`,body) }
  sessionMetadata(workspace:string, session:string, body:{command_id:string;expected_revision:number;title?:string;pinned?:boolean}): Promise<{metadata:SessionMetadata}> { return this.request('PATCH',chatPath(workspace,session)+'/metadata',body) }
  sessionLifecycle(workspace:string, session:string, action:'archive'|'unarchive', updated:string, commandId:string): Promise<{session:SessionWire}> { return this.post(chatPath(workspace,session)+'/'+action,{command_id:commandId,expected_updated_at:updated}) }
  sessionFork(workspace:string, session:string, commandId:string, options:{checkpoint_id?:string;edit_record_id?:string}={}): Promise<{session:SessionWire;empty_prefix?:boolean}> { return this.post(chatPath(workspace,session)+'/fork',{command_id:commandId,...options}) }
  sessionCheckpoints(workspace:string, session:string): Promise<{checkpoints:{checkpoint_id:string;source_end_position:number}[]}> { return this.get(chatPath(workspace,session)+'/checkpoints') }
  searchSessions(workspace:string, search='', archived=false, cursor?:string, includeExecution=false): Promise<SessionsPageWire> { return this.get(chatPath(workspace)+query({search,archived:String(archived),cursor,include_execution:String(includeExecution),limit:100})) }
  async cancelTask(taskId:string, revision:number, commandId:string): Promise<void> { await this.post(`/v1/tasks/${encodeURIComponent(taskId)}/cancel`,{command_id:commandId,expected_row_version:revision}) }
  capabilities(): Promise<ChatCapabilities> { return this.get('/v1/capabilities') }
  async createChatSession(workspace: string, commandId: string): Promise<SessionWire> {
    const value = await this.post<{result: {session: SessionWire}}>(chatPath(workspace), {command_id: commandId})
    return value.result.session
  }
  chatSession(workspace: string, session: string): Promise<ChatSessionEnvelope> {
    return this.get(chatPath(workspace, session))
  }
  chatSnapshot(workspace: string, session: string): Promise<ChatSnapshot> {
    return this.get(chatPath(workspace, session) + '/snapshot')
  }
  chatActivitySnapshot(workspace: string, session: string): Promise<ActivitySnapshot> {
    return this.get(chatPath(workspace, session) + '/snapshot?activity_schema=1')
  }
  chatActivities(workspace: string, session: string): Promise<ActivityRecovery> {
    return this.get(chatPath(workspace, session) + '/activities')
  }
  chatNodeSteer(workspace: string, session: string, body: {
    command_id: string; workflow_run_id: string; node_run_id: string; text: string
    expected_revision?: string
  }): Promise<NodeSteerReceipt> {
    return this.post(chatPath(workspace, session) + '/task-plan/node-steer', body)
  }
  chatHistory(workspace: string, session: string, before: string): Promise<TimelinePage> {
    return this.get(chatPath(workspace, session) + '/timeline' + query({before, limit: 50}))
  }
  chatContent(workspace: string, session: string, record: string): Promise<{content: string}> {
    return this.get(chatPath(workspace, session) + '/content/' + encodeURIComponent(record))
  }
  async chatSend(workspace: string, session: string, input: InteractionInput): Promise<Receipt> {
    return (await this.post<{receipt: Receipt}>(chatPath(workspace, session) + '/interactions', input)).receipt
  }
  async chatReceipt(workspace: string, session: string, id: string): Promise<Receipt> {
    return (await this.get<{receipt: Receipt}>(chatPath(workspace, session) + '/interactions/' + encodeURIComponent(id))).receipt
  }
  chatWithdraw(workspace: string, session: string, id: string, revision: number, commandId: string): Promise<unknown> {
    return this.post(chatPath(workspace, session) + '/interactions/' + encodeURIComponent(id) + '/withdraw', {command_id: commandId, expected_revision: revision})
  }
  chatControl(workspace: string, session: string, body: Record<string, unknown>): Promise<unknown> {
    return this.post(chatPath(workspace, session) + '/control', body)
  }
  chatRecovery(workspace: string, session: string, body?: Record<string, unknown>): Promise<RecoveryView> {
    const path = chatPath(workspace, session) + '/recovery'
    return body ? this.post(path, body) : this.get(path)
  }
  chatRecoveryStatus(workspace: string, session: string): Promise<RecoveryStatus> {
    return this.get(chatPath(workspace, session) + '/recovery/status')
  }
  workflowRecovery(workspace: string, session: string, workflowRunId: string, body: {
    command_id: string; resolution: 'acknowledge' | 'abort' | 'quarantine' | 'resume'
    report_id?: string | null; item_id?: string | null
  }): Promise<{report: Record<string, unknown>; disposition: string; driving: boolean}> {
    return this.post(
      chatPath(workspace, session) + `/workflow-recovery/${encodeURIComponent(workflowRunId)}`,
      body,
    )
  }
  taskArtifacts(workspace: string, session: string, params: TaskArtifactSelectors = {}): Promise<TaskArtifactsWire> {
    return this.get(chatPath(workspace, session) + '/task-artifacts' + query(params))
  }
  taskArtifactContent(workspace: string, session: string, artifact: string, params: TaskArtifactSelectors = {}): Promise<TaskArtifactContentWire> {
    return this.get(
      chatPath(workspace, session) + '/task-artifacts/' + encodeURIComponent(artifact) + '/content' + query(params),
    )
  }
  artifactRetention(
    artifact: string,
    action: 'pin' | 'release',
    expectedRowVersion: number,
    commandId: string,
  ): Promise<{artifact: ArtifactWire; disposition: string}> {
    return this.operationCommand('artifacts/' + encodeURIComponent(artifact) + '/retention', {
      action,
      expected_row_version: expectedRowVersion,
      confirmed: true,
      command_id: commandId,
    })
  }
  chatCommand(workspace: string, session: string, action: string, commandId: string, instructions = ""): Promise<{message: string}> {
    return this.post(chatPath(workspace, session) + '/commands', {action, command_id: commandId, instructions})
  }
  chatSocketUrl(workspace: string, session: string): string {
    const query = this.token === '' ? '' : `?token=${encodeURIComponent(this.token)}`
    return `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}${chatPath(workspace, session)}/stream${query}`
  }

  managementQuery<K extends keyof ManagementQueries>(kind: K, params: { scope?: string; session_id?: string; task_run_id?: string; agent_run_id?: string; page?: number } = {}): Promise<ManagementQueries[K]> {
    return this.get(`/v1/management/${kind}${query(params)}`)
  }

  async managementCommand(kind: ManagementCommand, body: Record<string, unknown>, target?: string): Promise<unknown> {
    const envelope = await this.post<{ result: unknown }>(
      `/v1/management/${kind}${target ? `/${encodeURIComponent(target)}` : ''}`, body,
    )
    return envelope.result
  }

  chatSettings(workspace: string, session: string): Promise<ChatSettingsView> {
    return this.get(chatPath(workspace, session) + '/settings')
  }
  chatPermissions(workspace: string, session: string, params: {run_id?: string; run_cursor?: string; grant_cursor?: string; scope_cursor?: string} = {}): Promise<import('./settings').ChatPermissionsView> {
    return this.get(chatPath(workspace, session) + '/permissions' + query(params))
  }
  revokeChatPermission(workspace: string, session: string, body: {command_id: string; kind: 'grant' | 'session_scope'; subject_id: string; expected_revision: number}): Promise<{disposition: string}> {
    return this.post(chatPath(workspace, session) + '/permissions', body)
  }
  saveChatSettings(workspace: string, session: string, scope: 'session' | 'workspace' | 'global', settings: ChatSettings, revision: number): Promise<ChatSettingsView> {
    return this.post(chatPath(workspace, session) + '/settings', {scope, settings, expected_revision: revision})
  }
  providerSettings(): Promise<ProviderSettingsView> { return this.get('/v1/providers') }
  providerControl(action: string, body: Record<string, unknown>, provider?: string): Promise<ProviderSettingsView> {
    return this.post(`/v1/providers${provider ? '/' + encodeURIComponent(provider) : ''}${action ? '/' + action : ''}`, body)
  }
  providerCredential(provider: string, secret: string, revision: number): Promise<{saved: boolean}> {
    return this.post(`/v1/providers/${encodeURIComponent(provider)}/credentials`, {secret, expected_revision: revision})
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

  workspaceFileTree(path = '.', params: WorkspaceFileTreeParams = {}): Promise<WorkspaceFileTreeWire> {
    return this.get(
      `/v1/workspaces/${encodeURIComponent(this.workspaceId ?? '')}/files/tree${query({ path, ...params })}`,
    )
  }

  workspaceFileContent(path: string): Promise<WorkspaceFileReadWire> {
    return this.get(
      `/v1/workspaces/${encodeURIComponent(this.workspaceId ?? '')}/files/content${query({ path })}`,
    )
  }

  workspaceFileInfo(path: string): Promise<WorkspaceFileInfoWire> {
    return this.get(
      `/v1/workspaces/${encodeURIComponent(this.workspaceId ?? '')}/files/info${query({ path })}`,
    )
  }

  /**
   * 显式保存当前工作区文本。`expectedSha256` 是读取时拿到的版本，服务端据此
   * 拒绝覆盖他人写入；同一逻辑提交重放原 `commandId`，新的修改是新命令。
   */
  writeWorkspaceFile(
    path: string,
    content: string,
    expectedSha256: string,
    commandId: string,
  ): Promise<WorkspaceFileWriteWire> {
    return this.put(
      `/v1/workspaces/${encodeURIComponent(this.workspaceId ?? '')}/files/content`,
      { command_id: commandId, path, content, expected_sha256: expectedSha256 },
    )
  }

  /** 面板不使用 Core 令牌时，下载可以交给浏览器原生流式处理。 */
  get tokenless(): boolean {
    return this.token === ''
  }

  /** 受控下载地址：服务端带 attachment 处置与 nosniff，浏览器负责流式与取消。 */
  workspaceFileDownloadUrl(path: string): string {
    return `/v1/workspaces/${encodeURIComponent(this.workspaceId ?? '')}/files/download${query({
      path,
      disposition: 'attachment',
    })}`
  }

  taskArtifactDownloadUrl(
    workspace: string,
    session: string,
    artifactId: string,
    params: TaskArtifactSelectors = {},
  ): string {
    return `/v1/workspaces/${encodeURIComponent(workspace)}/sessions/${encodeURIComponent(session)}/task-artifacts/${encodeURIComponent(artifactId)}/download${query({ ...params })}`
  }

  /** 经认证读取字节；预览对象 URL 由调用方创建并在切换/关闭时释放。 */
  async workspaceFileBytes(path: string, signal?: AbortSignal): Promise<Blob> {
    const response = await this.raw(
      `/v1/workspaces/${encodeURIComponent(this.workspaceId ?? '')}/files/download${query({ path })}`,
      true,
      signal,
    )
    return response.blob()
  }

  async taskArtifactBytes(
    workspace: string,
    session: string,
    artifactId: string,
    params: TaskArtifactSelectors = {},
    signal?: AbortSignal,
  ): Promise<Blob> {
    const response = await this.raw(
      `/v1/workspaces/${encodeURIComponent(workspace)}/sessions/${encodeURIComponent(session)}/task-artifacts/${encodeURIComponent(artifactId)}/download${query({ ...params })}`,
      true,
      signal,
    )
    return response.blob()
  }

  /** 建立一个固定内容集合的 HTML 运行预览；入口版本不符时服务端返回 409。 */
  createHtmlPreview(path: string, revision?: string): Promise<PreviewWire> {
    return this.post(
      `/v1/workspaces/${encodeURIComponent(this.workspaceId ?? '')}/previews`,
      revision === undefined ? { path } : { path, revision },
    )
  }

  /**
   * 从已登记的交付快照建立历史运行预览。
   *
   * 每次都用原始 session/task 选择器重新授权；历史集合只由该交付的
   * 快照字节组成，不读取当前工作区文件。
   */
  createHtmlPreviewFromArtifact(source: {
    sessionId: string
    artifactId: string
    taskRunId?: string | null
    workflowRunId?: string | null
    nodeRunId?: string | null
  }): Promise<PreviewWire> {
    return this.post(
      `/v1/workspaces/${encodeURIComponent(this.workspaceId ?? '')}/previews`,
      {
        artifact_id: source.artifactId,
        session_id: source.sessionId,
        ...(source.taskRunId ? { task_run_id: source.taskRunId } : {}),
        ...(source.workflowRunId ? { workflow_run_id: source.workflowRunId } : {}),
        ...(source.nodeRunId ? { node_run_id: source.nodeRunId } : {}),
      },
    )
  }

  /** 释放预览集合；面板切换/关闭与服务退出都会走这里。 */
  releaseHtmlPreview(previewId: string): Promise<{ released: boolean }> {
    return this.request(
      'DELETE',
      `/v1/workspaces/${encodeURIComponent(this.workspaceId ?? '')}/previews/${encodeURIComponent(previewId)}`,
      {},
    )
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

  async recoveryEligibility(runId: string): Promise<RecoveryEligibilityWire> {
    return this.get<RecoveryEligibilityWire>(
      `/v1/workflow-runs/${encodeURIComponent(runId)}/recovery-eligibility`,
    )
  }

  workflowOutput(runId: string, artifactId: string) {
    return this.get<{content:string;truncated:boolean;byte_size:number}>(`/v1/workflow-runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}/content`)
  }

  async stateDownload(name:string):Promise<Blob> {
    const response=await this.fetchImpl(`${this.baseUrl}${this.scopePath('/v1/state-downloads/'+encodeURIComponent(name))}`,{headers:this.token?{authorization:`Bearer ${this.token}`}:{}})
    if(!response.ok)throw await toApiError(response)
    return response.blob()
  }
  /** Authenticated binary fetch for activity preview_ref paths (asset grid). */
  async fetchBlob(path:string):Promise<Blob> {
    const response=await this.fetchImpl(`${this.baseUrl}${path}`,{headers:this.token?{authorization:`Bearer ${this.token}`}:{}})
    if(!response.ok)throw await toApiError(response)
    return response.blob()
  }
  /** Durable JSON read behind an activity's content_ref (activity-content endpoint). */
  async activityContent(path:string):Promise<{content:string;truncated:boolean}> {
    return this.get(path)
  }
  operationQuery<T>(kind:string, params:Record<string,string|number|undefined>={}) { const q=new URLSearchParams(Object.entries(params).filter(([,v])=>v!==undefined&&v!=='').map(([k,v])=>[k,String(v)])); return this.get<T>(`/v1/${kind}?${q}`) }
  operationCommand<T>(kind:string, body:unknown) { return this.post<T>(`/v1/${kind}`, body) }

  mcpQuery<T>(params:Record<string,string|number|undefined>={}) {
    const query=new URLSearchParams(Object.entries(params).filter(([,v])=>v!==undefined&&v!=='').map(([k,v])=>[k,String(v)]))
    return this.get<T>(`/v1/mcp-management?${query}`)
  }
  mcpAction(body:Record<string,unknown>) {return this.post<{status:string;command_id?:string}>('/v1/mcp-actions',body)}
  mcpCredential(body:Record<string,unknown>) {return this.post<{saved:boolean}>('/v1/mcp-credentials',body)}
  mcpJob(id:string) {return this.get<{status:string;error?:string}>(`/v1/mcp-jobs/${encodeURIComponent(id)}`)}

  skillQuery<T>(kind:'catalog'|'drafts'|'usage',params:Record<string,string|number|undefined>={}) {
    const query=new URLSearchParams(Object.entries(params).filter(([,v])=>v!==undefined&&v!=='').map(([k,v])=>[k,String(v)]))
    return this.get<T>(`/v1/skill-management/${kind}?${query}`)
  }
  skillAction<T>(body:Record<string,unknown>) { return this.post<T>('/v1/skill-actions',body) }

  knowledgeQuery<T>(kind:string,params:Record<string,string|number|boolean|undefined>={}) {
    const query=new URLSearchParams(Object.entries(params).filter(([,v])=>v!==undefined&&v!=='').map(([k,v])=>[k,String(v)]))
    return this.get<T>(`/v1/knowledge-management/${encodeURIComponent(kind)}?${query}`)
  }
  knowledgeCommand(kind:'learning'|'inbox',body:Record<string,unknown>) {
    return this.post<{status:string;command_id?:string}>(`/v1/knowledge-commands/${kind}`,body)
  }
  knowledgeJob(commandId:string) {
    return this.get<{status:string;result?:unknown;error?:string}>(`/v1/knowledge-jobs/${encodeURIComponent(commandId)}`)
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

  async getAgentRun(agentRunId: string): Promise<AgentRunObservationWire> {
    const envelope = await this.get<AgentRunEnvelopeWire>(
      `/v1/agent-runs/${encodeURIComponent(agentRunId)}`,
    )
    return envelope.observation
  }

  async listAgentDefinitions(after?:string): Promise<AgentDefinitionViewWire[]> {
    const envelope = await this.get<{ agent_definitions: AgentDefinitionViewWire[] }>(
      `/v1/catalog/agent-definitions${query({limit:100,after})}`,
    )
    return envelope.agent_definitions
  }

  async getAgentVersion(versionId: string): Promise<AgentDefinitionVersionWire> {
    const envelope = await this.get<{ agent_version: { version: AgentDefinitionVersionWire } }>(
      `/v1/catalog/agent-versions/${encodeURIComponent(versionId)}`,
    )
    return envelope.agent_version.version
  }

  async listWorkflowDefinitions(after?:string): Promise<WorkflowDefinitionViewWire[]> {
    const envelope = await this.get<{ workflow_definitions: WorkflowDefinitionViewWire[] }>(
      `/v1/catalog/workflow-definitions${query({limit:100,after})}`,
    )
    return envelope.workflow_definitions
  }

  async listWorkflowDrafts(after?:string): Promise<WorkflowDraftViewWire[]> {
    const envelope = await this.get<{ workflow_drafts: WorkflowDraftViewWire[] }>(
      `/v1/workflow-drafts${query({limit:100,after})}`,
    )
    return envelope.workflow_drafts
  }

  async getWorkflowDraft(draftId: string): Promise<WorkflowDraftViewWire> {
    const envelope = await this.get<{ workflow_draft: WorkflowDraftViewWire }>(
      `/v1/workflow-drafts/${encodeURIComponent(draftId)}`,
    )
    return envelope.workflow_draft
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

  processReplans(runId:string,commandId:string) {
    return this.post(`/v1/workflow-runs/${encodeURIComponent(runId)}/replans/process`,{command_id:commandId})
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

  chatWorkflows(workspace:string,session:string,before?:string):Promise<{items:{client_message_id:string;position:number;text:string;status:string;workflow_run_id:string|null;task_run_id:string|null;run_status:string|null;outcome:{summary:string;task_status:string}|null;workflow:{workflow_definition_id:string;workflow_revision_id:string}}[];next_cursor:string|null}> {return this.get(chatPath(workspace,session)+'/workflow-interactions'+(before?`?before=${encodeURIComponent(before)}`:''))}
  taskPlanView(workspace:string,session:string):Promise<TaskPlanViewWire> {return this.get(chatPath(workspace,session)+'/task-plan')}
  taskPlanGenerate(workspace:string,session:string,request:PlanWorkflowRequestWire):Promise<PlanningOperationWire> {return this.post(chatPath(workspace,session)+'/task-plan',request)}
  taskPlanCancel(workspace:string,session:string,operationId:string):Promise<PlanningOperationWire> {return this.post(chatPath(workspace,session)+`/task-plan/operations/${encodeURIComponent(operationId)}`,{})}
  taskPlanEdit(workspace:string,session:string,body:EditPlanRequestWire):Promise<PlanningOperationWire> {return this.post(chatPath(workspace,session)+'/task-plan/edit',body)}
  taskPlanNodeEdit(workspace:string,session:string,body:PlanNodeEditWire):Promise<PlanningOperationWire> {return this.post(chatPath(workspace,session)+'/task-plan/nodes',body)}
  taskPlanStart(workspace:string,session:string,body:StartWorkflowPlanRequestWire):Promise<{run:WorkflowRunWire;receipt:Record<string,unknown>;replayed:boolean}> {return this.post(chatPath(workspace,session)+'/task-plan/start',body)}
  taskPlanPause(workspace:string,session:string,body:{command_id:string;session_id:string;expected_run_row_version?:number|null}):Promise<{run:TaskPlanViewWire['run'];replayed:boolean;receipt:Record<string,unknown>}> {return this.post(chatPath(workspace,session)+'/task-plan/pause',body)}
  taskPlanChange(workspace:string,session:string,body:{command_id:string;session_id:string;origin_interaction_id:string;action_source:'button'|'chat_command';expected_run_row_version?:number|null}):Promise<{run:TaskPlanViewWire['run'];binding:TaskPlanViewWire['binding'];replayed:boolean;receipt:Record<string,unknown>}> {return this.post(chatPath(workspace,session)+'/task-plan/change',body)}
  taskPlanApplyChange(workspace:string,session:string,body:{command_id:string;session_id:string;decision:'accept_change'|'save_candidate'|'reject_change';action_source:'button'|'chat_command';interaction_id:string;candidate_digest:string;expected_parent_row_version:number}):Promise<{run:TaskPlanViewWire['run'];child:TaskPlanViewWire['run'];decision:string;replayed:boolean;receipt:Record<string,unknown>}> {return this.post(chatPath(workspace,session)+'/task-plan/apply-change',body)}
  taskPlanResume(workspace:string,session:string,body:{command_id:string;session_id:string;expected_run_row_version?:number|null}):Promise<{run:TaskPlanViewWire['run'];replayed:boolean;receipt:Record<string,unknown>}> {return this.post(chatPath(workspace,session)+'/task-plan/resume',body)}
  taskPlanRepair(workspace:string,session:string,body:{command_id:string;session_id:string;origin_interaction_id:string;action_source:'button'|'chat_command'}):Promise<{run:TaskPlanViewWire['run'];binding:TaskPlanViewWire['binding'];replayed:boolean;receipt:Record<string,unknown>}> {return this.post(chatPath(workspace,session)+'/task-plan/repair',body)}
  taskPlanEvents(workspace:string,session:string,after:number):Promise<Array<{sequence:number} & Record<string,unknown>>> {return this.get(chatPath(workspace,session)+`/task-plan/events?after=${after}`)}
  taskPlanControl(workspace:string,session:string,body:Record<string,unknown>):Promise<Record<string,unknown>> {return this.post(chatPath(workspace,session)+'/task-plan/control',body)}
  taskPlanControlReceipts(workspace:string,session:string,after=0,limit=50):Promise<{items:import('./types').ControlReceiptWire[]}> {return this.get(chatPath(workspace,session)+`/task-plan/control-receipts?after=${after}&limit=${limit}`)}
  /** Coordinator wiring of the frozen chat-turn pause endpoint (contract §7). */
  chatGuiPause(workspace:string,session:string,body:import('./contracts').GuiPauseRequest):Promise<{lifecycle:string;control_generation:number;command_id:string;cancelled:boolean}> {return this.post(chatPath(workspace,session)+'/pause',body)}
  taskPlanGenerationPause(workspace:string,session:string,operationId:string,body:{command_id:string;session_id:string;expected_row_version?:number|null}):Promise<import('./types').PlanningOperationWire> {return this.post(chatPath(workspace,session)+`/task-plan/operations/${encodeURIComponent(operationId)}/pause`,body)}
  taskPlanGenerationResume(workspace:string,session:string,operationId:string,body:{command_id:string;session_id:string;expected_row_version?:number|null}):Promise<import('./types').PlanningOperationWire> {return this.post(chatPath(workspace,session)+`/task-plan/operations/${encodeURIComponent(operationId)}/resume`,body)}
  agentPresets(workspace:string):Promise<AgentPresetCatalogWire> {return this.get(`/v1/workspaces/${encodeURIComponent(workspace)}/agent-presets`)}
  putAgentPreset(workspace:string,definitionId:string,body:{command_id:string;definition_id:string;preference:{definition_id:string;model?:ModelRefWire|null;generation?:SelectionChoiceWire|null};expected_revision:number}):Promise<{definition_id:string;revision:number;preference:{model?:ModelRefWire|null;generation?:SelectionChoiceWire|null}|null;receipt:Record<string,unknown>}> {return this.put(`/v1/workspaces/${encodeURIComponent(workspace)}/agent-presets/${encodeURIComponent(definitionId)}`,body)}
  quickSaveAgent(body:{command_id:string;name:string;purpose?:string;prompt?:string;tools:'all'|string[];model?:ModelRefWire|null;generation?:SelectionChoiceWire|null;expected_source_revision?:number;expected_head_revision?:number}):Promise<{definition_id:string;available_version_id:string|null;enabled:boolean;agent_definition:AgentDefinitionViewWire}> {return this.post('/v1/agent-definitions/quick-save',body)}
  definitionAction(kind:'agent'|'workflow',id:string,body:unknown):Promise<Record<string,unknown>> {return this.post(`/v1/definition-actions/${kind}/${encodeURIComponent(id)}`,body)}
  writeWorkflowSource(source:WorkflowDefinitionSourceWire,revision:number,key:string,create=false):Promise<unknown> {const path='/v1/workflow-definitions'+(create?'':`/${encodeURIComponent(source.workflow_definition_id)}`);const body={source,expected_source_revision:revision,command_id:key};return create?this.post(path,body):this.put(path,body)}
  cloneAttachment(workspace:string,source:string,target:string,id:string,key:string):Promise<AttachmentWire> {return this.post(attachmentPath(workspace,id)+'/clone',{source_session:source,target_session:target,command_id:key})}
  reserveAttachment(workspace:string, body:unknown):Promise<AttachmentWire> {return this.post(attachmentPath(workspace),body)}
  attachment(workspace:string,session:string,id:string):Promise<AttachmentWire> {return this.get(attachmentPath(workspace,id)+`?session_id=${encodeURIComponent(session)}`)}
  releaseAttachment(workspace:string,session:string,row:AttachmentWire):Promise<AttachmentWire> {return this.post(attachmentPath(workspace,row.attachment_id)+'/release',{session_id:session,expected_revision:row.revision,command_id:`cmd_release_${row.attachment_id}_${row.revision}`})}
  referenceAttachment(workspace:string,session:string,path:string,key:string):Promise<AttachmentWire> {return this.post(attachmentPath(workspace)+'/reference',{session_id:session,path,command_id:key})}
  searchAttachmentFiles(workspace:string,text:string):Promise<FileSearch> {return this.get(`/v1/workspaces/${encodeURIComponent(workspace)}/files/search?query=${encodeURIComponent(text)}`)}
  attachmentContentUrl(workspace:string,session:string,id:string,artifact:string):string {return `${this.baseUrl}${attachmentPath(workspace,id)}/content?session_id=${encodeURIComponent(session)}&artifact_id=${encodeURIComponent(artifact)}`}
  async attachmentText(workspace:string,session:string,id:string,artifact:string):Promise<string> {
    const response=await this.fetchImpl(this.attachmentContentUrl(workspace,session,id,artifact),{headers:this.token?{authorization:`Bearer ${this.token}`}:{}})
    if(!response.ok)throw await toApiError(response)
    return response.text()
  }
  async uploadAttachment(workspace:string,session:string,row:AttachmentWire,file:File,media:string):Promise<AttachmentWire> {
    const response=await this.fetchImpl(`${this.baseUrl}${attachmentPath(workspace,row.attachment_id)}/content?session_id=${encodeURIComponent(session)}&revision=${row.revision}`,{
      method:'PUT',headers:{...(this.token?{authorization:`Bearer ${this.token}`}:{ }),'content-type':media},body:file})
    if(!response.ok)throw await toApiError(response)
    return response.json() as Promise<AttachmentWire>
  }

  private async get<T>(path: string, scoped = true): Promise<T> {
    return this.request('GET', path, undefined, scoped)
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    return this.request('POST', path, body)
  }

  private async put<T>(path: string, body: unknown): Promise<T> {
    return this.request('PUT', path, body)
  }

  /** 原始响应：JSON 与字节出口共用同一处认证、错误映射与作用域规则。 */
  private async raw(path: string, scoped = true, signal?: AbortSignal): Promise<Response> {
    let response: Response
    try {
      response = await this.fetchImpl(`${this.baseUrl}${scoped ? this.scopePath(path) : path}`, {
        method: 'GET',
        headers: {
          ...(this.token ? { authorization: `Bearer ${this.token}` } : {}),
          accept: '*/*',
        },
        ...(signal === undefined ? {} : { signal }),
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
    return response
  }

  private async request<T>(method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body?: unknown, scoped = true): Promise<T> {
    let response: Response
    try {
      response = await this.fetchImpl(`${this.baseUrl}${scoped ? this.scopePath(path) : path}`, {
        method,
        headers: {
          ...(this.token ? { authorization: `Bearer ${this.token}` } : {}),
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
