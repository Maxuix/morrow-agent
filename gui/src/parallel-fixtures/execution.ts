/**
 * Lane-D test fixtures (P09/P10). Shared mock DTOs derive from the frozen
 * contracts module (P01) and the coordinator-owned wire types — a lane never
 * invents a second protocol. These builders are for lane-local tests only;
 * production paths never import them.
 */
import type { ExecutionView } from '../api/chat'

export function executionView(overrides: Partial<ExecutionView> = {}): ExecutionView {
  return {
    protocol_version: 1,
    owner: null,
    state: 'idle',
    phase: null,
    agent_run_id: null,
    workflow_run_id: null,
    root_session_id: null,
    leaf: false,
    node_run_ids: [],
    planning_operation_id: null,
    allowed_actions: [],
    accepts_chat_input: true,
    executions: [],
    revision: 1,
    updated_at: '2026-09-09T12:00:00Z',
    ...overrides,
  }
}

/** A workflow-owned execution, e.g. running or paused with server actions. */
export function workflowExecution(overrides: Partial<ExecutionView> = {}): ExecutionView {
  return executionView({
    owner: 'workflow',
    state: 'running',
    workflow_run_id: 'wfr_01J9PARQ1',
    node_run_ids: ['noderun_01J9PARQ1'],
    allowed_actions: ['stop', 'pause', 'change'],
    ...overrides,
  })
}

export function planningExecution(overrides: Partial<ExecutionView> = {}): ExecutionView {
  return executionView({
    owner: 'planning',
    state: 'running',
    planning_operation_id: 'plop_01J9PARQ1',
    allowed_actions: ['stop', 'pause'],
    ...overrides,
  })
}

export function chatExecution(overrides: Partial<ExecutionView> = {}): ExecutionView {
  return executionView({
    owner: 'chat',
    state: 'running',
    agent_run_id: 'arun_01J9PARQ1',
    allowed_actions: ['stop'],
    ...overrides,
  })
}
